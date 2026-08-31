"""Tests for end-of-turn batching of same-channel discord messages (#91 part 2)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

import open_strix.app as app_mod


class CountingAgent:
    def __init__(self) -> None:
        self.invoke_count = 0

    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        self.invoke_count += 1
        return {"messages": [AIMessage(content="ok")]}


def _build_app(tmp_path: Path, monkeypatch, *, agent: CountingAgent) -> app_mod.OpenStrixApp:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: agent)
    app = app_mod.OpenStrixApp(tmp_path)

    async def _noop_git(_event: Any) -> str:
        return "skip: test"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    return app


def _collect_events(app: app_mod.OpenStrixApp) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = app.log_event

    def _capture(event_type: str, **payload: Any) -> None:
        calls.append({"type": event_type, **payload})
        original(event_type, **payload)

    app.log_event = _capture  # type: ignore[assignment]
    return calls


def _discord_event(channel_id: str, author: str, prompt: str) -> app_mod.AgentEvent:
    return app_mod.AgentEvent(
        event_type="discord_message",
        prompt=prompt,
        channel_id=channel_id,
        author=author,
    )


def test_same_channel_messages_drain_into_one_batched_turn(
    tmp_path: Path, monkeypatch
) -> None:
    agent = CountingAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)
    events = _collect_events(app)

    trigger = _discord_event("chan-1", "alice", "first")
    followup_a = _discord_event("chan-1", "bob", "second")
    followup_b = _discord_event("chan-1", "carol", "third")
    other_channel = _discord_event("chan-2", "dave", "other")
    app.queue.put_nowait(followup_a)
    app.queue.put_nowait(followup_b)
    app.queue.put_nowait(other_channel)

    asyncio.run(app._process_event(trigger))

    # Two agent invocations: one per turn (trigger turn + batched turn).
    assert agent.invoke_count == 2

    timing_events = [e for e in events if e["type"] == "turn_timing"]
    assert len(timing_events) == 2
    assert timing_events[0]["batched"] is False
    assert timing_events[1]["batched"] is True

    batch_starts = [e for e in events if e["type"] == "batched_turn_start"]
    assert len(batch_starts) == 1
    assert batch_starts[0]["channel_id"] == "chan-1"
    assert batch_starts[0]["batch_size"] == 2

    # Other-channel event stays in queue untouched.
    assert app.queue.qsize() == 1
    remaining = app.queue.get_nowait()
    assert remaining is other_channel
    app.queue.task_done()


def test_no_batched_turn_when_queue_has_no_same_channel_messages(
    tmp_path: Path, monkeypatch
) -> None:
    agent = CountingAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)
    events = _collect_events(app)

    trigger = _discord_event("chan-1", "alice", "only one")
    other_channel = _discord_event("chan-2", "bob", "unrelated")
    app.queue.put_nowait(other_channel)

    asyncio.run(app._process_event(trigger))

    assert agent.invoke_count == 1
    timing_events = [e for e in events if e["type"] == "turn_timing"]
    assert len(timing_events) == 1
    assert timing_events[0]["batched"] is False
    assert not any(e["type"] == "batched_turn_start" for e in events)

    # Untouched.
    assert app.queue.qsize() == 1


def test_non_discord_trigger_does_not_batch(tmp_path: Path, monkeypatch) -> None:
    agent = CountingAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)
    events = _collect_events(app)

    trigger = app_mod.AgentEvent(
        event_type="scheduler_tick",
        prompt="tick",
        channel_id="chan-1",
        scheduler_name="perch",
    )
    # Even if a matching discord_message is queued, a scheduler tick should
    # not absorb it — only a discord_message trigger should drain.
    queued = _discord_event("chan-1", "alice", "hey")
    app.queue.put_nowait(queued)

    asyncio.run(app._process_event(trigger))

    assert agent.invoke_count == 1
    assert not any(e["type"] == "batched_turn_start" for e in events)
    assert app.queue.qsize() == 1


def test_drain_helper_preserves_order_of_kept_events(
    tmp_path: Path, monkeypatch
) -> None:
    agent = CountingAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)

    a = _discord_event("chan-1", "alice", "a")
    b = _discord_event("chan-2", "bob", "b")
    c = _discord_event("chan-1", "carol", "c")
    d = _discord_event("chan-3", "dave", "d")
    for event in (a, b, c, d):
        app.queue.put_nowait(event)

    drained = app._drain_same_channel_discord_events("chan-1")

    assert [e.prompt for e in drained] == ["a", "c"]
    remaining: list[app_mod.AgentEvent] = []
    while app.queue.qsize():
        remaining.append(app.queue.get_nowait())
        app.queue.task_done()
    assert [e.prompt for e in remaining] == ["b", "d"]
