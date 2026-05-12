from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from open_strix.config import AppConfig
from open_strix.tools import ToolsMixin
from open_strix.ui_plugins import UIPluginManager


class FakeLayout:
    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"


class FakeApp(ToolsMixin):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = FakeLayout(home)
        self.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig()
        self.web_search_enabled = False
        self.events: list[dict[str, Any]] = []
        self.ui_plugins = UIPluginManager(self)

    def log_event(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, **payload})


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    (tmp_path / "skills").mkdir()
    return tmp_path


def write_ui_json(skill_dir: Path, payload: object) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "ui.json").write_text(json.dumps(payload), encoding="utf-8")


def python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} {script}"


def test_discover_valid_uijson(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "clock"
    write_ui_json(
        skill_dir,
        {
            "uis": [
                {
                    "name": "widget-name",
                    "command": "python server.py",
                    "env": {"OPTIONAL": "VAR"},
                },
            ],
        },
    )

    app = FakeApp(tmp_home)
    plugins = app.ui_plugins.discover()

    assert len(plugins) == 1
    assert plugins[0].name == "widget-name"
    assert plugins[0].command == "python server.py"
    assert plugins[0].env == {"OPTIONAL": "VAR"}
    assert plugins[0].skill_dir == skill_dir


def test_discover_invalid_json(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "ui.json").write_text("not json {{{", encoding="utf-8")

    app = FakeApp(tmp_home)
    assert app.ui_plugins.discover() == []
    assert any(event["type"] == "ui_invalid_json" for event in app.events)


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "x"}],
        {"name": "x"},
        {"uis": {"name": "x"}},
    ],
)
def test_discover_wrong_format(tmp_home: Path, payload: object) -> None:
    write_ui_json(tmp_home / "skills" / "bad", payload)

    app = FakeApp(tmp_home)
    assert app.ui_plugins.discover() == []
    assert any(event["type"] == "ui_invalid_format" for event in app.events)


def test_discover_missing_fields(tmp_home: Path) -> None:
    write_ui_json(
        tmp_home / "skills" / "incomplete",
        {
            "uis": [
                {"name": "missing-command"},
                {"command": "python server.py"},
                {"name": "ok", "command": "python ok.py"},
            ],
        },
    )

    app = FakeApp(tmp_home)
    plugins = app.ui_plugins.discover()

    assert [plugin.name for plugin in plugins] == ["ok"]
    assert sum(event["type"] == "ui_missing_fields" for event in app.events) == 2


def test_port_allocation(tmp_home: Path) -> None:
    write_ui_json(
        tmp_home / "skills" / "multi",
        {
            "uis": [
                {"name": "one", "command": "python one.py"},
                {"name": "two", "command": "python two.py"},
            ],
        },
    )

    app = FakeApp(tmp_home)
    plugins = app.ui_plugins.discover()

    assert len({plugin.port for plugin in plugins}) == 2
    assert all(plugin.port > 0 for plugin in plugins)


@pytest.mark.asyncio
async def test_env_vars_passed(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "env"
    skill_dir.mkdir(parents=True)
    (skill_dir / "server.py").write_text(
        "import json, os, time\n"
        "keys = ['OPEN_STRIX_PORT', 'STATE_DIR', 'UI_NAME', 'EXTRA']\n"
        "open('env.json', 'w').write(json.dumps({k: os.environ[k] for k in keys}))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    write_ui_json(
        skill_dir,
        {"uis": [{"name": "env-ui", "command": python_command("server.py"), "env": {"EXTRA": "yes"}}]},
    )
    app = FakeApp(tmp_home)
    app.ui_plugins.discover()

    await app.ui_plugins.start_all()
    try:
        env_file = skill_dir / "env.json"
        for _ in range(100):
            if env_file.exists():
                break
            await asyncio.sleep(0.02)
        data = json.loads(env_file.read_text(encoding="utf-8"))
        plugin = app.ui_plugins.find("env-ui")
        assert plugin is not None
        assert data == {
            "OPEN_STRIX_PORT": str(plugin.port),
            "STATE_DIR": str(skill_dir),
            "UI_NAME": "env-ui",
            "EXTRA": "yes",
        }
    finally:
        await app.ui_plugins.stop_all()


@pytest.mark.asyncio
async def test_lifecycle_start_stop(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "sleepy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "server.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    write_ui_json(
        skill_dir,
        {"uis": [{"name": "sleepy", "command": python_command("server.py")}]},
    )
    app = FakeApp(tmp_home)
    app.ui_plugins.discover()

    await app.ui_plugins.start_all()
    plugin = app.ui_plugins.find("sleepy")
    assert plugin is not None
    assert plugin.process is not None
    assert plugin.process.returncode is None
    assert plugin.state == "running"

    await app.ui_plugins.stop_all()
    assert plugin.process is None


@pytest.mark.asyncio
async def test_restart_on_exit(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "flaky"
    skill_dir.mkdir(parents=True)
    (skill_dir / "server.py").write_text(
        "from pathlib import Path\n"
        "p = Path('starts.txt')\n"
        "p.write_text(str((int(p.read_text()) if p.exists() else 0) + 1))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    write_ui_json(
        skill_dir,
        {"uis": [{"name": "flaky", "command": python_command("server.py")}]},
    )
    app = FakeApp(tmp_home)
    app.ui_plugins.discover()

    await app.ui_plugins.start_all()
    try:
        plugin = app.ui_plugins.find("flaky")
        assert plugin is not None
        for _ in range(500):
            if plugin.state == "dead":
                break
            await asyncio.sleep(0.02)
        assert plugin.state == "dead"
        assert int((skill_dir / "starts.txt").read_text(encoding="utf-8")) == 4
        assert any(event["type"] == "ui_dead" for event in app.events)
    finally:
        await app.ui_plugins.stop_all()


@pytest.mark.asyncio
async def test_reload_tool(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "reload"
    skill_dir.mkdir(parents=True)
    (skill_dir / "server.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    write_ui_json(
        skill_dir,
        {"uis": [{"name": "reload-ui", "command": python_command("server.py")}]},
    )
    app = FakeApp(tmp_home)
    app.ui_plugins.discover()
    await app.ui_plugins.start_all()
    first_plugin = app.ui_plugins.find("reload-ui")
    assert first_plugin is not None
    first_pid = first_plugin.process.pid if first_plugin.process is not None else None

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["reload_uis"].ainvoke({})

    try:
        second_plugin = app.ui_plugins.find("reload-ui")
        assert second_plugin is not None
        second_pid = second_plugin.process.pid if second_plugin.process is not None else None
        assert "1 UI(s) registered: reload-ui" in result
        assert first_pid is not None
        assert second_pid is not None
        assert second_pid != first_pid
        assert any(event["type"] == "tool_call" and event["tool"] == "reload_uis" for event in app.events)
    finally:
        await app.ui_plugins.stop_all()
