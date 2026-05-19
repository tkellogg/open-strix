from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import open_strix.app as app_mod
from open_strix.hooks import HookManager


class FakeLayout:
    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"


class FakeApp:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = FakeLayout(home)
        self.session_id = "test-session"
        self.current_channel_id = "channel-1"
        self.current_event_label = "unit-test"
        self.message_history_all = [
            {
                "channel_id": "channel-1",
                "author": "alice",
                "content": "vector search should see this",
                "source": "web",
            },
            {
                "channel_id": "channel-2",
                "author": "bob",
                "content": "other channel",
                "source": "discord",
            },
        ]
        self.message_history_by_channel = {
            "channel-1": [self.message_history_all[0]],
            "channel-2": [self.message_history_all[1]],
        }
        self.events: list[dict[str, Any]] = []

    def log_event(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, **payload})


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    (tmp_path / "skills").mkdir()
    return tmp_path


def python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} {script}"


def write_hooks_json(skill_dir: Path, payload: object) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")


def test_discover_valid_hooks_json(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "audit"
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "tool-audit",
                    "command": "python hook.py",
                    "events": ["pre_tool_call", "post_tool_call"],
                    "env": {"MODE": "audit"},
                    "timeout_seconds": 3,
                    "include_conversation": True,
                },
            ],
        },
    )

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    hooks = manager.discover()

    assert len(hooks) == 1
    assert hooks[0].name == "tool-audit"
    assert hooks[0].command == "python hook.py"
    assert hooks[0].events == frozenset({"pre_tool_call", "post_tool_call"})
    assert hooks[0].env == {"MODE": "audit"}
    assert hooks[0].timeout_seconds == 3
    assert hooks[0].include_conversation is True
    assert hooks[0].skill_dir == skill_dir


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "x"}],
        {"name": "x"},
        {"hooks": {"name": "x"}},
    ],
)
def test_discover_invalid_format(tmp_home: Path, payload: object) -> None:
    write_hooks_json(tmp_home / "skills" / "bad", payload)

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    assert manager.discover() == []
    assert any(event["type"] == "hook_invalid_format" for event in app.events)


def test_discover_missing_fields_and_invalid_events(tmp_home: Path) -> None:
    write_hooks_json(
        tmp_home / "skills" / "incomplete",
        {
            "hooks": [
                {"name": "missing-command", "events": ["pre_tool_call"]},
                {"command": "python hook.py", "events": ["pre_tool_call"]},
                {"name": "bad-event", "command": "python hook.py", "events": ["sometimes"]},
                {"name": "ok", "command": "python ok.py", "event": "post_startup"},
            ],
        },
    )

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    hooks = manager.discover()

    assert [hook.name for hook in hooks] == ["ok"]
    assert sum(event["type"] == "hook_missing_fields" for event in app.events) == 3


@pytest.mark.asyncio
async def test_tool_hooks_can_mutate_args_and_result(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "mutator"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "if event['type'] == 'pre_tool_call' and event.get('tool') == 'echo':\n"
        "    event['args']['text'] = event['args']['text'].upper()\n"
        "elif event['type'] == 'post_tool_call' and event.get('tool') == 'echo':\n"
        "    event['result'] = event['result'] + ' hooked'\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "mutator",
                    "command": python_command("hook.py"),
                    "events": ["pre_tool_call", "post_tool_call"],
                },
            ],
        },
    )

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "hello"})

    assert result == "HELLO hooked"


@pytest.mark.asyncio
async def test_empty_hook_output_is_noop(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "quiet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import sys\n"
        "sys.stdin.readline()\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "quiet",
                    "command": python_command("hook.py"),
                    "events": ["pre_tool_call", "post_tool_call"],
                },
            ],
        },
    )

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "hello"})

    assert result == "hello"
    assert app.events == []


@pytest.mark.asyncio
async def test_hook_stderr_logs_without_colliding_with_event_type(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "stderr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "print('minor hook warning', file=sys.stderr)\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "stderr-hook",
                    "command": python_command("hook.py"),
                    "events": ["pre_prompt"],
                },
            ],
        },
    )

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()

    result = await manager.run_event("pre_prompt", {"prompt": "hello"})

    assert result["prompt"] == "hello"
    assert app.events == [
        {
            "type": "hook_stderr",
            "name": "stderr-hook",
            "hook_event_type": "pre_prompt",
            "stderr": "minor hook warning",
        },
    ]


@pytest.mark.asyncio
async def test_include_conversation_is_opt_in(tmp_home: Path) -> None:
    skill_dir = tmp_home / "skills" / "conversation"
    skill_dir.mkdir(parents=True)
    log_path = skill_dir / "seen.jsonl"
    (skill_dir / "hook.py").write_text(
        "import json, os, pathlib, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "path = pathlib.Path(os.environ['STATE_DIR']) / 'seen.jsonl'\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'has_conversation': 'conversation' in event, 'event': event['type'], 'channel_count': len(event.get('conversation', {}).get('channel_messages', []))}) + '\\n')\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "without-conversation",
                    "command": python_command("hook.py"),
                    "events": ["pre_prompt"],
                },
                {
                    "name": "with-conversation",
                    "command": python_command("hook.py"),
                    "events": ["pre_prompt"],
                    "include_conversation": True,
                },
            ],
        },
    )

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()

    result = await manager.run_event("pre_prompt", {"prompt": "hello"})

    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert result["prompt"] == "hello"
    assert rows == [
        {"has_conversation": False, "event": "pre_prompt", "channel_count": 0},
        {"has_conversation": True, "event": "pre_prompt", "channel_count": 1},
    ]


@pytest.mark.asyncio
async def test_pre_prompt_hook_can_append_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapturingAgent:
        def __init__(self) -> None:
            self.prompt = ""

        async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.prompt = payload["messages"][0].content
            return {"messages": [AIMessage(content="ok")]}

    agent = CapturingAgent()
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: agent)

    skill_dir = tmp_path / "skills" / "prompt"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "event['append_prompt'] = 'OOB retrieval context: similar content found.'\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "prompt-context",
                    "command": python_command("hook.py"),
                    "events": ["pre_prompt"],
                },
            ],
        },
    )

    app = app_mod.OpenStrixApp(tmp_path)

    async def _noop_git(_event: Any) -> str:
        return "skip: test"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    await app._process_event(
        app_mod.AgentEvent(
            event_type="web_message",
            prompt="hello",
            channel_id="local-web",
            author="local_user",
        ),
    )

    assert "OOB retrieval context: similar content found." in agent.prompt


@pytest.mark.asyncio
async def test_lifecycle_hooks_run_from_open_strix_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            return {"messages": [AIMessage(content="ok")]}

    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    skill_dir = tmp_path / "skills" / "lifecycle"
    skill_dir.mkdir(parents=True)
    log_path = skill_dir / "lifecycle.jsonl"
    (skill_dir / "hook.py").write_text(
        "import json, os, pathlib, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "path = pathlib.Path(os.environ['STATE_DIR']) / 'lifecycle.jsonl'\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'type': event['type']}) + '\\n')\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "lifecycle",
                    "command": python_command("hook.py"),
                    "events": ["pre_startup", "post_startup", "pre_shutdown", "post_shutdown"],
                },
            ],
        },
    )

    app = app_mod.OpenStrixApp(tmp_path)
    app.config.web_ui_port = 0
    app.config.api_port = 0

    async def _no_stdin() -> None:
        return None

    app._stdin_mode = _no_stdin  # type: ignore[method-assign]

    await app.run()
    await app.shutdown()

    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["type"] for row in rows] == [
        "pre_startup",
        "post_startup",
        "pre_shutdown",
        "post_shutdown",
    ]


@pytest.mark.asyncio
async def test_pre_tool_call_hook_can_block_with_synthetic_result(tmp_home: Path) -> None:
    """A pre_tool_call hook may return `_block` to short-circuit the call.

    The wrapped tool MUST NOT be invoked. The synthetic `result` is returned
    to the agent as if it were the tool's own return value. A
    `hook_blocked_tool_call` event is logged and post_tool_call hooks see
    `status="blocked"` with the same synthetic result.
    """
    skill_dir = tmp_home / "skills" / "blocker"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "if event['type'] == 'pre_tool_call' and event.get('tool') == 'echo':\n"
        "    event['_block'] = {\n"
        "        'reason': 'test-block',\n"
        "        'result': 'BLOCKED: do not call echo',\n"
        "    }\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "blocker",
                    "command": python_command("hook.py"),
                    "events": ["pre_tool_call", "post_tool_call"],
                },
            ],
        },
    )

    invocations: list[str] = []

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        invocations.append(text)
        return text

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "hello"})

    # Wrapped tool was NOT invoked; agent sees the synthetic result.
    assert invocations == []
    assert result == "BLOCKED: do not call echo"

    # hook_blocked_tool_call was logged.
    block_events = [e for e in app.events if e["type"] == "hook_blocked_tool_call"]
    assert len(block_events) == 1
    assert block_events[0]["tool"] == "echo"
    assert block_events[0]["reason"] == "test-block"


@pytest.mark.asyncio
async def test_pre_tool_call_invalid_block_falls_through(tmp_home: Path) -> None:
    """An invalid `_block` (missing fields, wrong types) is logged and ignored.

    The wrapped tool runs normally and the agent gets the real result. This
    guards against accidental silent drops from malformed hook output.
    """
    skill_dir = tmp_home / "skills" / "bad-block"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "if event['type'] == 'pre_tool_call':\n"
        # Missing 'result' field — invalid spec.
        "    event['_block'] = {'reason': 'incomplete'}\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    write_hooks_json(
        skill_dir,
        {
            "hooks": [
                {
                    "name": "bad-block",
                    "command": python_command("hook.py"),
                    "events": ["pre_tool_call"],
                },
            ],
        },
    )

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    app = FakeApp(tmp_home)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "hello"})

    # Wrapped tool ran normally.
    assert result == "hello"
    # Invalid-block event was logged.
    invalid_events = [e for e in app.events if e["type"] == "hook_invalid_block"]
    assert len(invalid_events) == 1
    assert invalid_events[0]["tool"] == "echo"
