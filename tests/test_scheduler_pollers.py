"""Tests for pollers.json discovery and execution in the scheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from open_strix.scheduler import PollerConfig, SchedulerMixin


class FakeLayout:
    """Minimal layout stub for testing."""

    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def scheduler_file(self) -> Path:
        return self.home / "scheduler.yaml"


class FakeApp(SchedulerMixin):
    """Minimal app stub that satisfies SchedulerMixin's protocol."""

    def __init__(self, home: Path) -> None:
        self.layout = FakeLayout(home)
        self.events: list[dict] = []
        self.enqueued: list = []

    def log_event(self, event_type: str, **payload) -> None:
        self.events.append({"type": event_type, **payload})

    async def enqueue_event(self, event) -> None:
        self.enqueued.append(event)


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """Create a temporary home directory with skills dir."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return tmp_path


class TestDiscoverPollers:
    def test_no_skills_dir(self, tmp_path: Path) -> None:
        app = FakeApp(tmp_path)
        # skills dir doesn't exist
        assert app._discover_pollers() == []

    def test_empty_skills_dir(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        assert app._discover_pollers() == []

    def test_valid_pollers_json(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "bluesky"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {
                "name": "bluesky-mentions",
                "command": "python poller.py",
                "cron": "*/5 * * * *",
                "env": {"BLUESKY_HANDLE": "test.bsky.social"},
            }
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].name == "bluesky-mentions"
        assert pollers[0].command == "python poller.py"
        assert pollers[0].cron == "*/5 * * * *"
        assert pollers[0].env == {"BLUESKY_HANDLE": "test.bsky.social"}
        assert pollers[0].skill_dir == skill_dir

    def test_multiple_pollers_in_one_file(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "monitoring"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "check-a", "command": "python a.py", "cron": "*/10 * * * *"},
            {"name": "check-b", "command": "python b.py", "cron": "0 * * * *"},
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 2
        assert pollers[0].name == "check-a"
        assert pollers[1].name == "check-b"

    def test_multiple_skills_with_pollers(self, tmp_home: Path) -> None:
        for name in ["alpha", "beta"]:
            skill_dir = tmp_home / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
                {"name": f"{name}-poller", "command": f"python {name}.py", "cron": "*/5 * * * *"}
            ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 2
        names = {p.name for p in pollers}
        assert names == {"alpha-poller", "beta-poller"}

    def test_invalid_json(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text("not json {{{")

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []
        assert any(e["type"] == "poller_invalid_json" for e in app.events)

    def test_bare_array_rejected(self, tmp_home: Path) -> None:
        """Top-level must be a dict, not an array."""
        skill_dir = tmp_home / "skills" / "bad"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps([{"name": "x"}]))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []
        assert any(e["type"] == "poller_invalid_format" for e in app.events)

    def test_dict_without_pollers_key(self, tmp_home: Path) -> None:
        """Dict without 'pollers' key yields no pollers (empty list from .get)."""
        skill_dir = tmp_home / "skills" / "nokey"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"name": "x"}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []

    def test_missing_required_fields(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "missing-command"},
            {"command": "python x.py", "cron": "* * * * *"},  # missing name
            {"name": "ok", "command": "python ok.py", "cron": "*/5 * * * *"},
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].name == "ok"

    def test_no_env_defaults_empty(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "minimal"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "simple", "command": "echo hi", "cron": "*/5 * * * *"}
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].env == {}


class TestOnPollerFire:
    @pytest.mark.asyncio
    async def test_successful_poller_with_output(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "test"
        skill_dir.mkdir(parents=True)

        # Write a script that outputs valid JSONL
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "something happened"}))\n'
        )

        poller = PollerConfig(
            name="test-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        event = app.enqueued[0]
        assert event.event_type == "poller"
        assert event.prompt == "something happened"
        assert event.scheduler_name == "test-poller"

    @pytest.mark.asyncio
    async def test_poller_no_output(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "quiet"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text("pass\n")

        poller = PollerConfig(
            name="quiet-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 0
        # Silent when no output — no log noise for routine empty polls
        assert not any(e["type"] == "poller_no_output" for e in app.events)

    @pytest.mark.asyncio
    async def test_poller_nonzero_exit(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "failing"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text("import sys; sys.exit(1)\n")

        poller = PollerConfig(
            name="fail-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 0
        assert any(e["type"] == "poller_nonzero_exit" for e in app.events)

    @pytest.mark.asyncio
    async def test_poller_env_vars(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "env-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json, os\n'
            'print(json.dumps({"poller": "env-test", "prompt": os.environ.get("MY_VAR", "missing")}))\n'
        )

        poller = PollerConfig(
            name="env-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={"MY_VAR": "hello"},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].prompt == "hello"

    @pytest.mark.asyncio
    async def test_poller_state_dir_and_poller_name_env(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "state-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json, os\n'
            'sd = os.environ.get("STATE_DIR", "")\n'
            'pn = os.environ.get("POLLER_NAME", "")\n'
            'print(json.dumps({"poller": pn, "prompt": f"dir={sd} name={pn}"}))\n'
        )

        poller = PollerConfig(
            name="state-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert f"dir={skill_dir}" in app.enqueued[0].prompt
        assert "name=state-poller" in app.enqueued[0].prompt

    @pytest.mark.asyncio
    async def test_poller_multiple_lines(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'for i in range(3):\n'
            '    print(json.dumps({"poller": "multi", "prompt": f"event {i}"}))\n'
        )

        poller = PollerConfig(
            name="multi-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 3
        assert app.enqueued[0].prompt == "event 0"
        assert app.enqueued[1].prompt == "event 1"
        assert app.enqueued[2].prompt == "event 2"

    @pytest.mark.asyncio
    async def test_poller_invalid_json_line_skipped(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "mixed"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print("not json")\n'
            'print(json.dumps({"poller": "mixed", "prompt": "valid line"}))\n'
        )

        poller = PollerConfig(
            name="mixed-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].prompt == "valid line"
        assert any(e["type"] == "poller_invalid_line" for e in app.events)

    @pytest.mark.asyncio
    async def test_poller_source_platform_passthrough(self, tmp_home: Path) -> None:
        """source_platform from poller JSONL flows through to AgentEvent."""
        skill_dir = tmp_home / "skills" / "platform"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "bsky", "source_platform": "bluesky", "prompt": "new reply"}))\n'
        )

        poller = PollerConfig(
            name="platform-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].source_platform == "bluesky"
        assert app.enqueued[0].prompt == "new reply"

    @pytest.mark.asyncio
    async def test_poller_no_source_platform_defaults_none(self, tmp_home: Path) -> None:
        """Missing source_platform in JSONL results in None on AgentEvent."""
        skill_dir = tmp_home / "skills" / "noplatform"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "event without platform"}))\n'
        )

        poller = PollerConfig(
            name="noplatform-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].source_platform is None
