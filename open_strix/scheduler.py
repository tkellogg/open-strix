from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
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

    def _reload_scheduler_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("open_strix:"):
                self.scheduler.remove_job(job.id)

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
        self.log_event("scheduler_reloaded", jobs=len(self._load_scheduler_jobs()))

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
