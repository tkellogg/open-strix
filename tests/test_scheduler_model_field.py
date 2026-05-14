"""Tests for per-job model field on SchedulerJob (issue #120)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from open_strix.models import AgentEvent
from open_strix.scheduler import SchedulerJob, SchedulerMixin, _is_valid_model_string


class FakeLayout:
    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def scheduler_file(self) -> Path:
        return self.home / "scheduler.yaml"


class FakeApp(SchedulerMixin):
    def __init__(self, home: Path) -> None:
        self.layout = FakeLayout(home)
        self.events: list[dict] = []
        self.enqueued: list[AgentEvent] = []

    def log_event(self, event_type: str, **payload) -> None:
        self.events.append({"type": event_type, **payload})

    async def enqueue_event(self, event: AgentEvent) -> None:
        self.enqueued.append(event)


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return tmp_path


# ── _is_valid_model_string ─────────────────────────────────────────────────

class TestIsValidModelString:
    def test_provider_colon_name_is_valid(self) -> None:
        assert _is_valid_model_string("anthropic:claude-haiku-4-5")

    def test_openai_provider_is_valid(self) -> None:
        assert _is_valid_model_string("openai:gpt-4o-mini")

    def test_arbitrary_colon_string_is_valid(self) -> None:
        # Any string with a colon passes; langchain resolves the rest.
        assert _is_valid_model_string("custom:my-model")

    def test_plain_word_without_colon_is_invalid(self) -> None:
        assert not _is_valid_model_string("claude-haiku-4-5")

    def test_empty_string_is_invalid(self) -> None:
        assert not _is_valid_model_string("")

    def test_whitespace_only_is_invalid(self) -> None:
        assert not _is_valid_model_string("   ")


# ── SchedulerJob.model field ───────────────────────────────────────────────

class TestSchedulerJobModelField:
    def test_model_field_defaults_to_none(self) -> None:
        job = SchedulerJob(name="j", prompt="p", cron="* * * * *")
        assert job.model is None

    def test_model_field_round_trips_through_to_dict(self) -> None:
        job = SchedulerJob(
            name="haiku-job",
            prompt="do something cheap",
            cron="0 * * * *",
            model="anthropic:claude-haiku-4-5",
        )
        d = job.to_dict()
        assert d["model"] == "anthropic:claude-haiku-4-5"

    def test_no_model_key_in_to_dict_when_none(self) -> None:
        job = SchedulerJob(name="j", prompt="p", cron="* * * * *")
        assert "model" not in job.to_dict()


# ── _load_scheduler_jobs ──────────────────────────────────────────────────

class TestLoadSchedulerJobsModel:
    def test_valid_model_field_is_parsed(self, tmp_home: Path) -> None:
        scheduler_yaml = tmp_home / "scheduler.yaml"
        scheduler_yaml.write_text(yaml.dump({"jobs": [
            {
                "name": "cheap-scan",
                "prompt": "Run the RSS scan",
                "time_of_day": "09:00",
                "model": "anthropic:claude-haiku-4-5",
            }
        ]}))
        app = FakeApp(tmp_home)
        jobs = app._load_scheduler_jobs()
        assert len(jobs) == 1
        assert jobs[0].model == "anthropic:claude-haiku-4-5"

    def test_missing_model_field_defaults_to_none(self, tmp_home: Path) -> None:
        scheduler_yaml = tmp_home / "scheduler.yaml"
        scheduler_yaml.write_text(yaml.dump({"jobs": [
            {"name": "default-job", "prompt": "Do something", "time_of_day": "10:00"}
        ]}))
        app = FakeApp(tmp_home)
        jobs = app._load_scheduler_jobs()
        assert jobs[0].model is None

    def test_invalid_model_string_logs_warning_and_falls_back_to_none(
        self, tmp_home: Path
    ) -> None:
        """A model string without a colon (e.g. a typo) is rejected at load time."""
        scheduler_yaml = tmp_home / "scheduler.yaml"
        scheduler_yaml.write_text(yaml.dump({"jobs": [
            {
                "name": "typo-job",
                "prompt": "Run something",
                "time_of_day": "11:00",
                "model": "claude-haiku-4-5",  # missing provider prefix
            }
        ]}))
        app = FakeApp(tmp_home)
        import logging
        with patch.object(logging.getLogger("open_strix.scheduler"), "warning") as mock_warn:
            jobs = app._load_scheduler_jobs()
        assert jobs[0].model is None
        mock_warn.assert_called_once()
        call_args = mock_warn.call_args[0]
        assert "typo-job" in str(call_args)
        assert "claude-haiku-4-5" in str(call_args)

    def test_empty_model_field_treated_as_none(self, tmp_home: Path) -> None:
        scheduler_yaml = tmp_home / "scheduler.yaml"
        scheduler_yaml.write_text(yaml.dump({"jobs": [
            {"name": "j", "prompt": "p", "time_of_day": "08:00", "model": ""}
        ]}))
        app = FakeApp(tmp_home)
        jobs = app._load_scheduler_jobs()
        assert jobs[0].model is None


# ── _get_agent_for_scheduler_model / cache ────────────────────────────────


class FakeOpenStrixApp:
    """Minimal stub that exercises the cache logic without the full app stack."""

    def __init__(self) -> None:
        self._scheduler_agent_cache: dict[str, Any] = {}
        self._create_agent_calls: list[str | None] = []

    def _create_agent(self, model_override: str | None = None) -> MagicMock:
        self._create_agent_calls.append(model_override)
        agent = MagicMock()
        agent.model_override = model_override  # distinguish instances
        return agent

    def _get_agent_for_scheduler_model(self, model_name: str) -> Any:
        if model_name not in self._scheduler_agent_cache:
            self._scheduler_agent_cache[model_name] = self._create_agent(
                model_override=model_name
            )
        return self._scheduler_agent_cache[model_name]

    def _reload_scheduler_jobs(self) -> None:
        self._scheduler_agent_cache.clear()


class TestAgentCache:
    def test_cache_hit_returns_same_instance(self) -> None:
        """Repeated calls with the same model string return the same agent object."""
        app = FakeOpenStrixApp()
        first = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        second = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        assert first is second
        assert len(app._create_agent_calls) == 1

    def test_cache_miss_calls_create_agent(self) -> None:
        """Distinct model strings each trigger a separate _create_agent call."""
        app = FakeOpenStrixApp()
        haiku = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        sonnet = app._get_agent_for_scheduler_model("anthropic:claude-sonnet-4-6")
        assert haiku is not sonnet
        assert app._create_agent_calls == [
            "anthropic:claude-haiku-4-5",
            "anthropic:claude-sonnet-4-6",
        ]

    def test_reload_scheduler_jobs_clears_cache(self) -> None:
        """_reload_scheduler_jobs drops all cached agents; next call rebuilds."""
        app = FakeOpenStrixApp()
        before = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        app._reload_scheduler_jobs()
        assert app._scheduler_agent_cache == {}
        after = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        assert before is not after  # rebuilt after cache clear
        assert len(app._create_agent_calls) == 2


# ── _on_scheduler_fire ────────────────────────────────────────────────────

class TestOnSchedulerFireModel:
    @pytest.mark.asyncio
    async def test_model_flows_through_to_agent_event(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        await app._on_scheduler_fire(
            name="cheap-job",
            prompt="do something",
            channel_id=None,
            model="anthropic:claude-haiku-4-5",
        )
        assert len(app.enqueued) == 1
        event = app.enqueued[0]
        assert event.scheduler_model == "anthropic:claude-haiku-4-5"
        assert event.scheduler_name == "cheap-job"

    @pytest.mark.asyncio
    async def test_no_model_leaves_scheduler_model_none(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        await app._on_scheduler_fire(name="default-job", prompt="p")
        assert app.enqueued[0].scheduler_model is None
