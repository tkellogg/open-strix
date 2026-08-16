"""Tests for the extended agent_turn_missing_send_message detector.

Covers the three cases added in the interactive-turns fix:
1. Empty-final interactive turn → fires (all 4 interactive event_types)
2. React-only silent acknowledgement → stays silent (react is valid per prompts.py:33)
3. Poller/scheduler silent turn → stays silent (intentional silence)
4. Original prose-without-send-message case → still fires (no regression)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import open_strix.app as app_mod
from open_strix.models import AgentEvent


def _read_events(tmp_path: Path) -> list[dict]:
    log = tmp_path / "logs" / "events.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: None)


# ---------------------------------------------------------------------------
# Case 1: empty-final interactive turns → fires for all interactive event_types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event_type", [
    "discord_message",
    "web_message",
    "web_continue",
    "stdin_message",
])
@pytest.mark.asyncio
async def test_logs_missing_send_on_empty_final_interactive_turn(
    event_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any interactive turn that produces no send_message and no final_text
    should fire agent_turn_missing_send_message."""
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            return {"messages": []}

    app.agent = FakeAgent()

    await app._process_event(
        AgentEvent(
            event_type=event_type,
            prompt="hello",
            channel_id="123",
            author="user",
            author_id="42",
            source_id="777",
        ),
    )

    events = _read_events(tmp_path)
    missing = [e for e in events if e.get("type") == "agent_turn_missing_send_message"]
    assert len(missing) == 1, (
        f"expected 1 missing-send event for {event_type!r}, got {missing}"
    )


# ---------------------------------------------------------------------------
# Case 2: react-only silence → stays silent (react is a valid acknowledgement)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_react_only_silent_ack_does_not_fire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A react call with no prose and no send_message is a valid ack.
    The detector should NOT fire."""
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            from langchain_core.messages import AIMessage
            msg = AIMessage(
                content="",
                tool_calls=[
                    {"name": "react", "args": {"emoji": "thumbsup"}, "id": "tc1", "type": "tool_call"},
                ],
            )
            return {"messages": [msg]}

    app.agent = FakeAgent()

    await app._process_event(
        AgentEvent(
            event_type="discord_message",
            prompt="lgtm",
            channel_id="123",
            author="user",
            author_id="42",
            source_id="777",
        ),
    )

    events = _read_events(tmp_path)
    missing = [e for e in events if e.get("type") == "agent_turn_missing_send_message"]
    assert missing == [], (
        f"react-only turn should not fire missing-send detector, got {missing}"
    )


# ---------------------------------------------------------------------------
# Case 3: poller / scheduler silent turns → stays silent (intentional silence)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event_type", [
    "scheduler",
    "poller",
])
@pytest.mark.asyncio
async def test_silent_scheduler_poller_turn_does_not_fire(
    event_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler and poller turns that produce nothing are intentionally silent."""
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            return {"messages": []}

    app.agent = FakeAgent()

    await app._process_event(
        AgentEvent(
            event_type=event_type,
            prompt="tick",
            channel_id="",
            author="",
            author_id="",
            source_id="",
        ),
    )

    events = _read_events(tmp_path)
    missing = [e for e in events if e.get("type") == "agent_turn_missing_send_message"]
    assert missing == [], (
        f"silent {event_type!r} turn should not fire missing-send detector, got {missing}"
    )


# ---------------------------------------------------------------------------
# Case 4: original behaviour — prose without send_message → still fires
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prose_without_send_message_still_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Original #88 case: model composed final_text but did not call send_message."""
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            from langchain_core.messages import AIMessage
            return {"messages": [AIMessage(content="Here is my reply.")]}

    app.agent = FakeAgent()

    await app._process_event(
        AgentEvent(
            event_type="discord_message",
            prompt="hello",
            channel_id="123",
            author="user",
            author_id="42",
            source_id="777",
        ),
    )

    events = _read_events(tmp_path)
    missing = [e for e in events if e.get("type") == "agent_turn_missing_send_message"]
    assert len(missing) == 1, f"expected prose-without-send to fire, got {missing}"
    assert missing[0]["final_text"] == "Here is my reply."
