from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import tool

from open_strix.ambient_memory import AmbientMemoryService, AmbientSnippet, FakeAmbientBackend
from open_strix.config import AmbientMemoryConfig, RepoLayout
from open_strix.hooks import HookManager


class FakeApp:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.config = type(
            "Config",
            (),
            {"ambient_memory": AmbientMemoryConfig(enabled=True)},
        )()
        self.session_id = "session"
        self.current_channel_id = "local-web"
        self.current_event_label = "unit-test"
        self.events: list[dict[str, Any]] = []
        self._ambient_turn_sequence = 1
        self._ambient_boundary_sequence = 0
        self.ambient_memory = AmbientMemoryService(
            layout=self.layout,
            config=self.config.ambient_memory,
            backend=FakeAmbientBackend(
                [
                    AmbientSnippet(
                        record_id="r1",
                        source="state/cold.md:1",
                        text="cold fact from tool boundary",
                        score=0.9,
                    )
                ]
            ),
            log_event=self.log_event,
        )

    def log_event(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, **payload})

    def ambient_pre_tool_call(self, tool_name: str, args: dict[str, Any]) -> Any:
        from open_strix.app import OpenStrixApp

        return OpenStrixApp.ambient_pre_tool_call(self, tool_name, args)

    def ambient_post_tool_success(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        *,
        ambient_context: Any,
        return_direct: bool,
    ) -> Any:
        from open_strix.app import OpenStrixApp

        return OpenStrixApp.ambient_post_tool_success(
            self,
            tool_name,
            args,
            result,
            ambient_context=ambient_context,
            return_direct=return_direct,
        )


@pytest.mark.asyncio
async def test_ambient_tool_injection_after_post_hooks(tmp_path: Path) -> None:
    (tmp_path / "skills" / "mutator").mkdir(parents=True)
    skill_dir = tmp_path / "skills" / "mutator"
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "if event['type'] == 'post_tool_call':\n"
        "    event['result'] = event['result'] + ' post-hook'\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} hook.py"
    (skill_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "name": "mutator",
                        "command": command,
                        "events": ["post_tool_call"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    app = FakeApp(tmp_path)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "base"})

    assert "base post-hook" in result
    assert "Ambient memory surfaced" in result
    assert result.index("base post-hook") < result.index("Ambient memory")


@pytest.mark.asyncio
async def test_ambient_tool_injection_skips_send_message(tmp_path: Path) -> None:
    @tool("send_message")
    async def send_message(text: str) -> str:
        """Send message."""
        return "sent"

    app = FakeApp(tmp_path)
    manager = HookManager(app)
    hooked = manager.wrap_tool(send_message)

    result = await hooked.ainvoke({"text": "hello"})

    assert result == "sent"
