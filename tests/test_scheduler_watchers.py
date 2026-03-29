"""Tests for watchers.json discovery and execution in the scheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from open_strix.scheduler import (
    VALID_WATCHER_TRIGGERS,
    SchedulerMixin,
    WatcherConfig,
)


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


# --- Discovery tests ---


class TestDiscoverWatchers:
    def test_no_skills_dir(self, tmp_path: Path) -> None:
        app = FakeApp(tmp_path)
        assert app._discover_watchers() == []

    def test_empty_skills_dir(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        assert app._discover_watchers() == []

    def test_valid_cron_watcher(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "monitoring"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {
                            "name": "daily-health",
                            "command": "python health.py",
                            "cron": "0 12 * * *",
                        }
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == 1
        assert watchers[0].name == "daily-health"
        assert watchers[0].command == "python health.py"
        assert watchers[0].cron == "0 12 * * *"
        assert watchers[0].trigger is None
        assert watchers[0].skill_dir == skill_dir

    def test_valid_event_watcher(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "drift"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {
                            "name": "codex-bypass",
                            "command": "python check.py",
                            "trigger": "turn_complete",
                        }
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == 1
        assert watchers[0].name == "codex-bypass"
        assert watchers[0].trigger == "turn_complete"
        assert watchers[0].cron is None

    def test_mixed_cron_and_event_watchers(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "mix"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {"name": "scheduled", "command": "python a.py", "cron": "*/10 * * * *"},
                        {"name": "event-driven", "command": "python b.py", "trigger": "turn_complete"},
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == 2
        cron_w = [w for w in watchers if w.cron]
        event_w = [w for w in watchers if w.trigger]
        assert len(cron_w) == 1
        assert len(event_w) == 1

    def test_rejects_both_cron_and_trigger(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "bad"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {
                            "name": "confused",
                            "command": "python x.py",
                            "cron": "* * * * *",
                            "trigger": "turn_complete",
                        }
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []
        assert any(e["type"] == "watcher_missing_fields" for e in app.events)

    def test_rejects_neither_cron_nor_trigger(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {"name": "missing", "command": "python x.py"}
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []
        assert any(e["type"] == "watcher_missing_fields" for e in app.events)

    def test_rejects_invalid_trigger(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "bad-trigger"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {
                            "name": "invalid",
                            "command": "python x.py",
                            "trigger": "on_banana",
                        }
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []
        assert any(e["type"] == "watcher_invalid_trigger" for e in app.events)

    def test_invalid_json(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text("not json {{{")

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []
        assert any(e["type"] == "watcher_invalid_json" for e in app.events)

    def test_bare_array_rejected(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "array"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(json.dumps([{"name": "x"}]))

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []
        assert any(e["type"] == "watcher_invalid_format" for e in app.events)

    def test_env_passthrough(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "envtest"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {
                            "name": "with-env",
                            "command": "python x.py",
                            "trigger": "turn_complete",
                            "env": {"MY_VAR": "hello"},
                        }
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == 1
        assert watchers[0].env == {"MY_VAR": "hello"}

    def test_missing_name_rejected(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "noname"
        skill_dir.mkdir(parents=True)
        (skill_dir / "watchers.json").write_text(
            json.dumps(
                {
                    "watchers": [
                        {"command": "python x.py", "trigger": "turn_complete"}
                    ]
                }
            )
        )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert watchers == []

    def test_multiple_skills_with_watchers(self, tmp_home: Path) -> None:
        for name in ["alpha", "beta"]:
            skill_dir = tmp_home / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "watchers.json").write_text(
                json.dumps(
                    {
                        "watchers": [
                            {
                                "name": f"{name}-watcher",
                                "command": f"python {name}.py",
                                "trigger": "turn_complete",
                            }
                        ]
                    }
                )
            )

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == 2
        names = {w.name for w in watchers}
        assert names == {"alpha-watcher", "beta-watcher"}

    def test_all_valid_triggers_accepted(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "alltriggers"
        skill_dir.mkdir(parents=True)
        entries = [
            {"name": f"w-{t}", "command": "python x.py", "trigger": t}
            for t in sorted(VALID_WATCHER_TRIGGERS)
        ]
        (skill_dir / "watchers.json").write_text(json.dumps({"watchers": entries}))

        app = FakeApp(tmp_home)
        watchers = app._discover_watchers()
        assert len(watchers) == len(VALID_WATCHER_TRIGGERS)


# --- Subprocess execution tests ---


class TestRunWatcherSubprocess:
    @pytest.mark.asyncio
    async def test_successful_watcher_with_findings(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "ctx = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'signal': 'test', 'severity': 'warn', "
            "'message': f\"trace={ctx['trace_id']}\", 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="test-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "abc123", "events_path": "/tmp/events.jsonl"}
        )

        assert len(findings) == 1
        assert findings[0]["signal"] == "test"
        assert findings[0]["message"] == "trace=abc123"

    @pytest.mark.asyncio
    async def test_watcher_no_output(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "quiet"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "ctx = json.loads(sys.stdin.readline())\n"
            "# no findings\n"
        )

        watcher = WatcherConfig(
            name="quiet-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "abc", "events_path": "/tmp/e.jsonl"}
        )

        assert findings == []

    @pytest.mark.asyncio
    async def test_watcher_nonzero_exit(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "fail"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text("import sys; sys.exit(1)\n")

        watcher = WatcherConfig(
            name="fail-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "x", "events_path": "/tmp/e.jsonl"}
        )

        assert findings == []
        assert any(e["type"] == "watcher_nonzero_exit" for e in app.events)

    @pytest.mark.asyncio
    async def test_watcher_env_vars(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "envtest"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, os, sys\n"
            "sys.stdin.readline()  # consume stdin\n"
            "sd = os.environ.get('STATE_DIR', '')\n"
            "wn = os.environ.get('WATCHER_NAME', '')\n"
            "mv = os.environ.get('MY_VAR', '')\n"
            "print(json.dumps({'signal': 'env', 'message': f'{sd}|{wn}|{mv}', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="env-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={"MY_VAR": "hello"},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "x", "events_path": "/tmp/e.jsonl"}
        )

        assert len(findings) == 1
        parts = findings[0]["message"].split("|")
        assert parts[0] == str(skill_dir)
        assert parts[1] == "env-watcher"
        assert parts[2] == "hello"

    @pytest.mark.asyncio
    async def test_watcher_multiple_findings(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "for i in range(3):\n"
            "    print(json.dumps({'signal': f's{i}', 'message': f'finding {i}', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="multi-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "x", "events_path": "/tmp/e.jsonl"}
        )

        assert len(findings) == 3
        assert findings[0]["signal"] == "s0"
        assert findings[2]["signal"] == "s2"

    @pytest.mark.asyncio
    async def test_watcher_invalid_json_line_skipped(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "mixed"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "print('not json')\n"
            "print(json.dumps({'signal': 'valid', 'message': 'ok', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="mixed-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "x", "events_path": "/tmp/e.jsonl"}
        )

        assert len(findings) == 1
        assert findings[0]["signal"] == "valid"
        assert any(e["type"] == "watcher_invalid_line" for e in app.events)

    @pytest.mark.asyncio
    async def test_watcher_stderr_logged(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "stderr"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "print('debug info', file=sys.stderr)\n"
            "print(json.dumps({'signal': 'ok', 'message': 'fine', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="stderr-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        findings = await app._run_watcher_subprocess(
            watcher, {"trigger": "turn_complete", "trace_id": "x", "events_path": "/tmp/e.jsonl"}
        )

        assert len(findings) == 1
        assert any(e["type"] == "watcher_stderr" for e in app.events)


# --- fire_watchers integration tests ---


class TestFireWatchers:
    @pytest.mark.asyncio
    async def test_fire_watchers_no_registered(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        app._event_watchers = {}

        findings = await app.fire_watchers(
            "turn_complete",
            session_id="abc",
            events_path="/tmp/e.jsonl",
        )
        assert findings == []

    @pytest.mark.asyncio
    async def test_fire_watchers_with_findings(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "ctx = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'signal': 'test', 'severity': 'warn', "
            "'message': 'found something', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="test-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        app._event_watchers = {"turn_complete": [watcher]}

        findings = await app.fire_watchers(
            "turn_complete",
            session_id="abc123",
            events_path="/tmp/events.jsonl",
        )

        assert len(findings) == 1
        assert findings[0]["signal"] == "test"
        assert findings[0]["watcher"] == "test-watcher"
        assert any(e["type"] == "watcher_findings" for e in app.events)
        assert any(e["type"] == "watchers_complete" for e in app.events)

    @pytest.mark.asyncio
    async def test_fire_watchers_wrong_trigger_type(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        app._event_watchers = {
            "turn_complete": [
                WatcherConfig(
                    name="x", command="echo", cron=None, trigger="turn_complete",
                    env={}, skill_dir=tmp_home,
                )
            ]
        }

        findings = await app.fire_watchers(
            "session_start",
            session_id="abc",
            events_path="/tmp/e.jsonl",
        )
        assert findings == []

    @pytest.mark.asyncio
    async def test_fire_watchers_adds_watcher_name(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "named"
        skill_dir.mkdir(parents=True)
        (skill_dir / "check.py").write_text(
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "print(json.dumps({'signal': 'x', 'message': 'y', 'route': 'log'}))\n"
        )

        watcher = WatcherConfig(
            name="my-watcher",
            command="python check.py",
            cron=None,
            trigger="turn_complete",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        app._event_watchers = {"turn_complete": [watcher]}

        findings = await app.fire_watchers(
            "turn_complete",
            session_id="s",
            events_path="/tmp/e.jsonl",
        )

        assert findings[0]["watcher"] == "my-watcher"

    @pytest.mark.asyncio
    async def test_fire_watchers_multiple_watchers(self, tmp_home: Path) -> None:
        watchers = []
        for i in range(2):
            skill_dir = tmp_home / "skills" / f"w{i}"
            skill_dir.mkdir(parents=True)
            (skill_dir / "check.py").write_text(
                f"import json, sys\n"
                f"sys.stdin.readline()\n"
                f"print(json.dumps({{'signal': 'w{i}', 'message': 'from {i}', 'route': 'log'}}))\n"
            )
            watchers.append(
                WatcherConfig(
                    name=f"watcher-{i}",
                    command="python check.py",
                    cron=None,
                    trigger="turn_complete",
                    env={},
                    skill_dir=skill_dir,
                )
            )

        app = FakeApp(tmp_home)
        app._event_watchers = {"turn_complete": watchers}

        findings = await app.fire_watchers(
            "turn_complete",
            session_id="s",
            events_path="/tmp/e.jsonl",
        )

        assert len(findings) == 2
        signals = {f["signal"] for f in findings}
        assert signals == {"w0", "w1"}
