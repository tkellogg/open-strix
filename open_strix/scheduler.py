from __future__ import annotations

import asyncio
import json
import os
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


@dataclass
class PollerConfig:
    """A poller declared in a skill's pollers.json."""

    name: str
    command: str
    cron: str
    env: dict[str, str]
    skill_dir: Path


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
        self.layout.scheduler_file.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

    def _discover_pollers(self) -> list[PollerConfig]:
        """Scan skill directories for pollers.json files."""
        pollers: list[PollerConfig] = []
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
                    PollerConfig(
                        name=name,
                        command=command,
                        cron=cron,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                    ),
                )
        return pollers

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

        # Register pollers from skills/*/pollers.json.
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

        scheduler_count = len(self._load_scheduler_jobs())
        self.log_event(
            "scheduler_reloaded",
            jobs=scheduler_count,
            pollers=len(pollers),
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

    async def _on_poller_fire(self, poller: PollerConfig) -> None:
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
