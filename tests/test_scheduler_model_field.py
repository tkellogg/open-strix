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


# ── _get_agent_for_scheduler_model (no-cache, fresh build each turn) ────────


class FakeOpenStrixApp:
    """Minimal stub that exercises the no-cache build logic without the full app stack."""

    def __init__(self) -> None:
        self._create_agent_calls: list[str | None] = []

    def _create_agent(self, model_override: str | None = None) -> MagicMock:
        self._create_agent_calls.append(model_override)
        agent = MagicMock()
        agent.model_override = model_override  # distinguish instances
        return agent

    def _get_agent_for_scheduler_model(self, model_name: str) -> Any:
        return self._create_agent(model_override=model_name)


class TestAgentBuild:
    def test_each_call_builds_fresh_agent(self) -> None:
        """Every _get_agent_for_scheduler_model call returns a freshly built agent.

        No cache means two calls with the same model string produce distinct
        objects — intentional; open-strix does not cache as a rule.
        """
        app = FakeOpenStrixApp()
        first = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        second = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        assert first is not second  # fresh build each time
        assert len(app._create_agent_calls) == 2

    def test_different_models_build_separate_agents(self) -> None:
        """Distinct model strings each trigger a separate _create_agent call."""
        app = FakeOpenStrixApp()
        haiku = app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        sonnet = app._get_agent_for_scheduler_model("anthropic:claude-sonnet-4-6")
        assert haiku is not sonnet
        assert app._create_agent_calls == [
            "anthropic:claude-haiku-4-5",
            "anthropic:claude-sonnet-4-6",
        ]

    def test_build_passes_model_override_to_create_agent(self) -> None:
        """The model string is forwarded as model_override to _create_agent."""
        app = FakeOpenStrixApp()
        app._get_agent_for_scheduler_model("anthropic:claude-haiku-4-5")
        assert app._create_agent_calls == ["anthropic:claude-haiku-4-5"]


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


# ── Dispatch integration: main + block-repair both hit the per-job agent ──

import open_strix.app as app_mod
from langchain_core.messages import AIMessage


def _build_app_with_two_agents(
    tmp_path: Path,
    monkeypatch,
    *,
    main_agent: Any,
    haiku_agent: Any,
) -> app_mod.OpenStrixApp:
    """Build a real OpenStrixApp where _create_agent is deterministic.

    The first call (from __init__) produces `main_agent`; any subsequent call
    with model_override produces `haiku_agent`.
    """
    call_counts = [0]

    def _fake_create_deep_agent(**kwargs: Any) -> Any:
        call_counts[0] += 1
        # First call is always the default self.agent; later calls get haiku.
        if call_counts[0] == 1:
            return main_agent
        return haiku_agent

    monkeypatch.setattr(app_mod, "create_deep_agent", _fake_create_deep_agent)
    app = app_mod.OpenStrixApp(tmp_path)

    async def _noop_git(_event: Any) -> str:
        return "skip: test"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    return app


class _RecordingAgent:
    """Tracks every ainvoke call so tests can assert which agent was used."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, messages: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append(messages)
        return {"messages": [AIMessage(content=f"response from {self.name}")]}


class TestDispatchIntegration:
    """Integration tests: assert that _process_event routes both the main
    ainvoke and the block-repair ainvoke to the correct agent."""

    def test_scheduler_model_routes_main_invoke_to_per_job_agent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When event.scheduler_model is set, _process_event must call the
        per-job (haiku) agent for the primary invoke, not self.agent."""
        main_agent = _RecordingAgent("main")
        haiku_agent = _RecordingAgent("haiku")
        app = _build_app_with_two_agents(
            tmp_path, monkeypatch, main_agent=main_agent, haiku_agent=haiku_agent
        )

        asyncio.run(
            app._process_event(
                app_mod.AgentEvent(
                    event_type="scheduler",
                    prompt="run cheap scan",
                    channel_id=None,
                    scheduler_model="anthropic:claude-haiku-4-5",
                )
            )
        )

        assert len(haiku_agent.invocations) == 1, (
            f"haiku agent should have been invoked once for main turn; "
            f"got {len(haiku_agent.invocations)}"
        )
        assert len(main_agent.invocations) == 0, (
            f"main agent should NOT be invoked when scheduler_model is set; "
            f"got {len(main_agent.invocations)}"
        )

    def test_scheduler_model_routes_block_repair_to_per_job_agent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When event.scheduler_model is set AND block repair fires, both the
        main ainvoke and the repair ainvoke must use the per-job agent."""
        main_agent = _RecordingAgent("main")
        haiku_agent = _RecordingAgent("haiku")
        app = _build_app_with_two_agents(
            tmp_path, monkeypatch, main_agent=main_agent, haiku_agent=haiku_agent
        )

        # Inject one round of block errors so repair fires exactly once.
        original_validate = app._validate_memory_blocks
        validate_calls = [0]

        def _broken_once() -> list[str]:
            validate_calls[0] += 1
            if validate_calls[0] == 1:
                return ["some_block: expected a YAML mapping, got str"]
            return original_validate()

        app._validate_memory_blocks = _broken_once  # type: ignore[assignment]

        asyncio.run(
            app._process_event(
                app_mod.AgentEvent(
                    event_type="scheduler",
                    prompt="run cheap scan",
                    channel_id=None,
                    scheduler_model="anthropic:claude-haiku-4-5",
                )
            )
        )

        # haiku agent must have been called for both main invoke and repair.
        assert len(haiku_agent.invocations) == 2, (
            f"haiku agent should be called for main invoke + block repair; "
            f"got {len(haiku_agent.invocations)}"
        )
        assert len(main_agent.invocations) == 0, (
            "main agent must never be invoked when scheduler_model is set, "
            "even during block repair"
        )

    def test_no_scheduler_model_uses_default_agent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When event.scheduler_model is None, _process_event must use
        self.agent (the default), not a cached per-job agent."""
        main_agent = _RecordingAgent("main")
        haiku_agent = _RecordingAgent("haiku")
        app = _build_app_with_two_agents(
            tmp_path, monkeypatch, main_agent=main_agent, haiku_agent=haiku_agent
        )

        asyncio.run(
            app._process_event(
                app_mod.AgentEvent(
                    event_type="discord_message",
                    prompt="hello",
                    channel_id="1234",
                    scheduler_model=None,
                )
            )
        )

        assert len(main_agent.invocations) == 1, (
            f"main agent should handle default (no-model-override) turns; "
            f"got {len(main_agent.invocations)}"
        )
        assert len(haiku_agent.invocations) == 0, (
            "per-job agent must not be invoked for turns without scheduler_model"
        )
