from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import yaml
from apscheduler.triggers.cron import CronTrigger

from .models import AgentEvent

UTC = timezone.utc


@dataclass
class SchedulerJob:
    name: str
    prompt: str
    cron: str | None = None
    time_of_day: str | None = None
    channel_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "prompt": self.prompt}
        if self.cron:
            data["cron"] = self.cron
        if self.time_of_day:
            data["time_of_day"] = self.time_of_day
        if self.channel_id:
            data["channel_id"] = self.channel_id
        return data


# Valid event triggers for watchers (non-cron).
VALID_WATCHER_TRIGGERS = frozenset({"turn_complete", "session_start", "session_end"})


@dataclass
class WatcherConfig:
    """Unified config for cron-triggered pollers and event-triggered hooks.

    A watcher has *either* a ``cron`` schedule or an event ``trigger`` —
    never both.  Legacy ``pollers.json`` entries are loaded as watchers
    with ``trigger=None``.
    """

    name: str
    command: str
    cron: str | None
    trigger: str | None
    env: dict[str, str]
    skill_dir: Path


_SCHEDULER_LOCK = threading.RLock()


class SchedulerMixin:
    def _load_scheduler_jobs(self) -> list[SchedulerJob]:
        if not self.layout.scheduler_file.exists():
            return []
        loaded = yaml.safe_load(self.layout.scheduler_file.read_text(encoding="utf-8"))
        if loaded is None:
            return []
        if isinstance(loaded, list):
            raw_jobs = loaded
        else:
            raw_jobs = loaded.get("jobs", [])
        jobs: list[SchedulerJob] = []
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            prompt = str(raw.get("prompt", "")).strip()
            if not name or not prompt:
                continue
            cron = str(raw.get("cron", "")).strip() or None
            time_of_day = str(raw.get("time_of_day", "")).strip() or None
            channel_id = str(raw.get("channel_id", "")).strip() or None
            jobs.append(
                SchedulerJob(
                    name=name,
                    prompt=prompt,
                    cron=cron,
                    time_of_day=time_of_day,
                    channel_id=channel_id,
                ),
            )
        return jobs

    def _save_scheduler_jobs(self, jobs: list[SchedulerJob]) -> None:
        data = {"jobs": [job.to_dict() for job in jobs]}
        content = yaml.safe_dump(data, sort_keys=False)
        target = self.layout.scheduler_file
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=".scheduler.", suffix=".tmp"
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            try:
                os.chmod(tmp, os.stat(str(target)).st_mode & 0o777)
            except (OSError, FileNotFoundError):
                os.chmod(tmp, 0o644)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _discover_pollers(self) -> list[WatcherConfig]:
        """Scan skill directories for legacy pollers.json files.

        Returns WatcherConfig instances with ``trigger=None`` for backward
        compatibility.  New skills should use ``watchers.json`` instead.
        """
        pollers: list[WatcherConfig] = []
        skills_dir = self.layout.skills_dir
        if not skills_dir.exists():
            return pollers

        for pollers_file in sorted(skills_dir.rglob("pollers.json")):
            skill_dir = pollers_file.parent
            try:
                raw = json.loads(pollers_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.log_event(
                    "poller_invalid_json",
                    path=str(pollers_file),
                    error=str(exc),
                )
                continue

            if isinstance(raw, dict):
                entries = raw.get("pollers", [])
                if not isinstance(entries, list):
                    self.log_event(
                        "poller_invalid_format",
                        path=str(pollers_file),
                        error="'pollers' key must be an array",
                    )
                    continue
            else:
                self.log_event(
                    "poller_invalid_format",
                    path=str(pollers_file),
                    error="expected a JSON object with 'pollers' key",
                )
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                command = str(entry.get("command", "")).strip()
                cron = str(entry.get("cron", "")).strip()
                if not name or not command or not cron:
                    self.log_event(
                        "poller_missing_fields",
                        path=str(pollers_file),
                        entry=entry,
                    )
                    continue
                env = entry.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                pollers.append(
                    WatcherConfig(
                        name=name,
                        command=command,
                        cron=cron,
                        trigger=None,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                    ),
                )
        return pollers

    def _discover_watchers(self) -> list[WatcherConfig]:
        """Scan skill directories for watchers.json files.

        Each entry must have ``name``, ``command``, and exactly one of
        ``cron`` (schedule-triggered) or ``trigger`` (event-triggered).
        """
        watchers: list[WatcherConfig] = []
        skills_dir = self.layout.skills_dir
        if not skills_dir.exists():
            return watchers

        for watchers_file in sorted(skills_dir.rglob("watchers.json")):
            skill_dir = watchers_file.parent
            try:
                raw = json.loads(watchers_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.log_event(
                    "watcher_invalid_json",
                    path=str(watchers_file),
                    error=str(exc),
                )
                continue

            if not isinstance(raw, dict):
                self.log_event(
                    "watcher_invalid_format",
                    path=str(watchers_file),
                    error="expected a JSON object with 'watchers' key",
                )
                continue

            entries = raw.get("watchers", [])
            if not isinstance(entries, list):
                self.log_event(
                    "watcher_invalid_format",
                    path=str(watchers_file),
                    error="'watchers' key must be an array",
                )
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                command = str(entry.get("command", "")).strip()
                if not name or not command:
                    self.log_event(
                        "watcher_missing_fields",
                        path=str(watchers_file),
                        entry=entry,
                    )
                    continue
                cron = str(entry.get("cron", "")).strip() or None
                trigger = str(entry.get("trigger", "")).strip() or None
                # Must have exactly one of cron or trigger.
                if bool(cron) == bool(trigger):
                    self.log_event(
                        "watcher_missing_fields",
                        path=str(watchers_file),
                        entry=entry,
                        error="watcher must have exactly one of 'cron' or 'trigger'",
                    )
                    continue
                if trigger and trigger not in VALID_WATCHER_TRIGGERS:
                    self.log_event(
                        "watcher_invalid_trigger",
                        path=str(watchers_file),
                        name=name,
                        trigger=trigger,
                        valid_triggers=sorted(VALID_WATCHER_TRIGGERS),
                    )
                    continue
                env = entry.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                watchers.append(
                    WatcherConfig(
                        name=name,
                        command=command,
                        cron=cron,
                        trigger=trigger,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                    ),
                )
        return watchers

    def _reload_scheduler_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("open_strix:"):
                self.scheduler.remove_job(job.id)

        # Register prompt-based scheduler jobs from scheduler.yaml.
        for job in self._load_scheduler_jobs():
            if bool(job.cron) == bool(job.time_of_day):
                self.log_event("scheduler_invalid_job", name=job.name)
                continue

            trigger: CronTrigger
            if job.cron:
                try:
                    trigger = CronTrigger.from_crontab(job.cron, timezone=UTC)
                except ValueError as exc:
                    self.log_event("scheduler_invalid_cron", name=job.name, error=str(exc))
                    continue
            else:
                try:
                    hour_str, minute_str = str(job.time_of_day).split(":")
                    trigger = CronTrigger(
                        hour=int(hour_str),
                        minute=int(minute_str),
                        timezone=UTC,
                    )
                except (TypeError, ValueError) as exc:
                    self.log_event("scheduler_invalid_time", name=job.name, error=str(exc))
                    continue

            self.scheduler.add_job(
                self._on_scheduler_fire,
                trigger=trigger,
                kwargs={
                    "name": job.name,
                    "prompt": job.prompt,
                    "channel_id": job.channel_id,
                },
                id=f"open_strix:{job.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        # Register pollers from skills/*/pollers.json (backward compat).
        pollers = self._discover_pollers()
        for poller in pollers:
            try:
                trigger = CronTrigger.from_crontab(poller.cron, timezone=UTC)
            except ValueError as exc:
                self.log_event(
                    "poller_invalid_cron",
                    name=poller.name,
                    cron=poller.cron,
                    error=str(exc),
                )
                continue

            self.scheduler.add_job(
                self._on_poller_fire,
                trigger=trigger,
                kwargs={"poller": poller},
                id=f"open_strix:poller:{poller.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        # Register watchers from skills/*/watchers.json.
        all_watchers = self._discover_watchers()
        cron_watchers = [w for w in all_watchers if w.cron]
        event_watchers = [w for w in all_watchers if w.trigger]

        # Cron-based watchers get scheduled just like pollers.
        for watcher in cron_watchers:
            try:
                trigger = CronTrigger.from_crontab(watcher.cron, timezone=UTC)  # type: ignore[arg-type]
            except ValueError as exc:
                self.log_event(
                    "watcher_invalid_cron",
                    name=watcher.name,
                    cron=watcher.cron,
                    error=str(exc),
                )
                continue

            self.scheduler.add_job(
                self._on_watcher_cron_fire,
                trigger=trigger,
                kwargs={"watcher": watcher},
                id=f"open_strix:watcher:{watcher.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        # Event-triggered watchers are stored for runtime dispatch.
        self._event_watchers: dict[str, list[WatcherConfig]] = {}
        for watcher in event_watchers:
            assert watcher.trigger is not None
            self._event_watchers.setdefault(watcher.trigger, []).append(watcher)

        scheduler_count = len(self._load_scheduler_jobs())
        self.log_event(
            "scheduler_reloaded",
            jobs=scheduler_count,
            pollers=len(pollers),
            watchers_cron=len(cron_watchers),
            watchers_event=len(event_watchers),
        )

    async def _on_scheduler_fire(self, name: str, prompt: str, channel_id: str | None = None) -> None:
        # Async callback keeps scheduler execution on the event loop.
        await self.enqueue_event(
            AgentEvent(
                event_type="scheduler",
                prompt=prompt,
                channel_id=channel_id,
                scheduler_name=name,
                dedupe_key=f"scheduler:{name}",
            ),
        )

    async def _on_poller_fire(self, poller: WatcherConfig) -> None:
        """Run a poller subprocess and enqueue events from its stdout."""
        env = {**os.environ, **poller.env}
        env["STATE_DIR"] = str(poller.skill_dir)
        env["POLLER_NAME"] = poller.name

        try:
            proc = await asyncio.create_subprocess_shell(
                poller.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(poller.skill_dir),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=60,
            )
        except asyncio.TimeoutError:
            self.log_event(
                "poller_timeout",
                name=poller.name,
                timeout_seconds=60,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return
        except Exception as exc:
            self.log_event(
                "poller_exec_error",
                name=poller.name,
                error=str(exc),
            )
            return

        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr_text:
            self.log_event(
                "poller_stderr",
                name=poller.name,
                stderr=stderr_text[:2000],
            )

        if proc.returncode != 0:
            self.log_event(
                "poller_nonzero_exit",
                name=poller.name,
                returncode=proc.returncode,
            )
            return

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            return

        event_count = 0
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                self.log_event(
                    "poller_invalid_line",
                    name=poller.name,
                    line=line[:500],
                )
                continue

            if not isinstance(parsed, dict):
                continue

            prompt = str(parsed.get("prompt", "")).strip()
            if not prompt:
                continue

            source_platform = parsed.get("source_platform")
            if source_platform is not None:
                source_platform = str(source_platform).strip() or None

            await self.enqueue_event(
                AgentEvent(
                    event_type="poller",
                    prompt=prompt,
                    scheduler_name=poller.name,
                    dedupe_key=f"poller:{poller.name}:{event_count}",
                    source_platform=source_platform,
                ),
            )
            event_count += 1

        self.log_event(
            "poller_complete",
            name=poller.name,
            events_emitted=event_count,
        )

    async def _on_watcher_cron_fire(self, watcher: WatcherConfig) -> None:
        """Run a cron-based watcher — same execution model as pollers."""
        await self._on_poller_fire(watcher)

    async def _run_watcher_subprocess(
        self,
        watcher: WatcherConfig,
        stdin_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run an event-triggered watcher, sending context on stdin.

        Returns parsed JSONL lines from stdout.
        """
        env = {**os.environ, **watcher.env}
        env["STATE_DIR"] = str(watcher.skill_dir)
        env["WATCHER_NAME"] = watcher.name

        stdin_bytes = (json.dumps(stdin_data) + "\n").encode()

        try:
            proc = await asyncio.create_subprocess_shell(
                watcher.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(watcher.skill_dir),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=60,
            )
        except asyncio.TimeoutError:
            self.log_event(
                "watcher_timeout",
                name=watcher.name,
                trigger=watcher.trigger,
                timeout_seconds=60,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return []
        except Exception as exc:
            self.log_event(
                "watcher_exec_error",
                name=watcher.name,
                trigger=watcher.trigger,
                error=str(exc),
            )
            return []

        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr_text:
            self.log_event(
                "watcher_stderr",
                name=watcher.name,
                stderr=stderr_text[:2000],
            )

        if proc.returncode != 0:
            self.log_event(
                "watcher_nonzero_exit",
                name=watcher.name,
                trigger=watcher.trigger,
                returncode=proc.returncode,
            )
            return []

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            return []

        results: list[dict[str, Any]] = []
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                self.log_event(
                    "watcher_invalid_line",
                    name=watcher.name,
                    line=line[:500],
                )
                continue
            if isinstance(parsed, dict):
                results.append(parsed)
        return results

    async def fire_watchers(
        self,
        trigger_type: str,
        *,
        session_id: str,
        events_path: str,
    ) -> list[dict[str, Any]]:
        """Fire all event-triggered watchers registered for *trigger_type*.

        Each watcher receives minimal context on stdin per the watcher
        contract:

        .. code-block:: json

            {"trigger": "turn_complete", "trace_id": "...", "events_path": "/..."}

        Returns all parsed JSONL findings from all watchers.
        """
        watchers = getattr(self, "_event_watchers", {}).get(trigger_type, [])
        if not watchers:
            return []

        stdin_data = {
            "trigger": trigger_type,
            "trace_id": session_id,
            "events_path": events_path,
        }

        all_findings: list[dict[str, Any]] = []
        for watcher in watchers:
            findings = await self._run_watcher_subprocess(watcher, stdin_data)
            for finding in findings:
                finding.setdefault("watcher", watcher.name)
                all_findings.append(finding)

            if findings:
                self.log_event(
                    "watcher_findings",
                    name=watcher.name,
                    trigger=trigger_type,
                    finding_count=len(findings),
                )

        if all_findings:
            self.log_event(
                "watchers_complete",
                trigger=trigger_type,
                total_findings=len(all_findings),
                watchers_run=len(watchers),
            )

        return all_findings
