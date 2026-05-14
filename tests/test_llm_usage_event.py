"""Tests for llm_usage event emission on each agent.ainvoke (#108).

The event is emitted by _log_agent_trace, which is called once per ainvoke
call (including the optional block-repair reinvoke). One event per ainvoke
means per-call granularity in the event log — callers that want totals can
aggregate.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import open_strix.app as app_mod


# ---------------------------------------------------------------------------
# Helpers — reuse the same build_app / collect_events patterns from
# test_turn_timing.py so the tests are structurally consistent.
# ---------------------------------------------------------------------------

def _build_app(tmp_path: Path, monkeypatch, *, agent: Any) -> app_mod.OpenStrixApp:
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


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class _UsageAgent:
    """Returns a single AIMessage with Anthropic-style usage_metadata."""

    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        self._usage = usage or {
            "input_tokens": 1200,
            "output_tokens": 340,
            "total_tokens": 1540,
            "input_token_details": {"cache_read": 890, "cache_creation": 220},
        }

    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content="ok", usage_metadata=self._usage)]}


class _NoUsageAgent:
    """Returns a single AIMessage without usage_metadata (older providers)."""

    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content="ok")]}


class _MultiStepAgent:
    """Returns two AIMessages (two LLM steps in one invoke) each with usage."""

    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        step1 = AIMessage(
            content="step 1",
            usage_metadata={"input_tokens": 500, "output_tokens": 100, "total_tokens": 600},
        )
        step2 = AIMessage(
            content="step 2",
            usage_metadata={
                "input_tokens": 700,
                "output_tokens": 200,
                "total_tokens": 900,
                "input_token_details": {"cache_read": 300, "cache_creation": 50},
            },
        )
        return {"messages": [step1, step2]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_llm_usage_event_emitted_with_correct_fields(tmp_path: Path, monkeypatch) -> None:
    """A well-formed llm_usage event is emitted for a normal turn."""
    app = _build_app(tmp_path, monkeypatch, agent=_UsageAgent())
    events = _collect_events(app)

    asyncio.run(
        app._process_event(
            app_mod.AgentEvent(event_type="discord_message", prompt="hi", channel_id="1")
        )
    )

    usage_events = [e for e in events if e["type"] == "llm_usage"]
    assert len(usage_events) == 1, f"expected 1 llm_usage event, got {len(usage_events)}"
    ev = usage_events[0]
    assert ev["input_tokens"] == 1200
    assert ev["output_tokens"] == 340
    assert ev["total_tokens"] == 1540
    assert ev["cache_read_input_tokens"] == 890
    assert ev["cache_creation_input_tokens"] == 220
    assert "model" in ev  # config.model present — value depends on config default


def test_llm_usage_event_not_emitted_when_no_usage_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """No llm_usage event is emitted when the provider does not supply usage."""
    app = _build_app(tmp_path, monkeypatch, agent=_NoUsageAgent())
    events = _collect_events(app)

    asyncio.run(
        app._process_event(
            app_mod.AgentEvent(event_type="discord_message", prompt="hi", channel_id="1")
        )
    )

    usage_events = [e for e in events if e["type"] == "llm_usage"]
    assert len(usage_events) == 0


def test_llm_usage_aggregates_across_multi_step_invoke(
    tmp_path: Path, monkeypatch
) -> None:
    """Token counts from multiple AIMessages in one invoke are summed."""
    app = _build_app(tmp_path, monkeypatch, agent=_MultiStepAgent())
    events = _collect_events(app)

    asyncio.run(
        app._process_event(
            app_mod.AgentEvent(event_type="discord_message", prompt="hi", channel_id="1")
        )
    )

    usage_events = [e for e in events if e["type"] == "llm_usage"]
    assert len(usage_events) == 1
    ev = usage_events[0]
    # step1 + step2 totals
    assert ev["input_tokens"] == 1200         # 500 + 700
    assert ev["output_tokens"] == 300          # 100 + 200
    assert ev["total_tokens"] == 1500          # 600 + 900
    assert ev["cache_read_input_tokens"] == 300
    assert ev["cache_creation_input_tokens"] == 50


def test_llm_usage_minimal_fields_no_cache(tmp_path: Path, monkeypatch) -> None:
    """Usage without cache details produces zero-valued cache fields."""
    agent = _UsageAgent(usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    app = _build_app(tmp_path, monkeypatch, agent=agent)
    events = _collect_events(app)

    asyncio.run(
        app._process_event(
            app_mod.AgentEvent(event_type="scheduler", prompt="tick", channel_id=None)
        )
    )

    usage_events = [e for e in events if e["type"] == "llm_usage"]
    assert len(usage_events) == 1
    ev = usage_events[0]
    assert ev["input_tokens"] == 100
    assert ev["output_tokens"] == 50
    assert ev["total_tokens"] == 150
    assert ev["cache_read_input_tokens"] == 0
    assert ev["cache_creation_input_tokens"] == 0


def test_llm_usage_two_events_when_block_repair_fires(tmp_path: Path, monkeypatch) -> None:
    """If block repair fires, two llm_usage events are emitted — one per ainvoke."""

    class _BrokenBlockAgent(_UsageAgent):
        """First invoke produces a broken memory block; repair invoke fixes it."""

        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0

        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            self.call_count += 1
            return {"messages": [AIMessage(content="ok", usage_metadata={
                "input_tokens": 50 * self.call_count,
                "output_tokens": 10,
                "total_tokens": 50 * self.call_count + 10,
            })]}

    agent = _BrokenBlockAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)

    # Inject a broken block so _validate_memory_blocks returns an error,
    # forcing the repair reinvoke.
    original_validate = app._validate_memory_blocks

    validate_calls = [0]

    def _broken_once() -> list[str]:
        validate_calls[0] += 1
        if validate_calls[0] == 1:
            return ["some_block: expected a YAML mapping, got str"]
        return original_validate()

    app._validate_memory_blocks = _broken_once  # type: ignore[assignment]

    events = _collect_events(app)

    asyncio.run(
        app._process_event(
            app_mod.AgentEvent(event_type="discord_message", prompt="hi", channel_id="1")
        )
    )

    usage_events = [e for e in events if e["type"] == "llm_usage"]
    assert len(usage_events) == 2, (
        f"expected 2 llm_usage events (main + repair), got {len(usage_events)}"
    )
    # First invoke has 50 input tokens, second has 100.
    assert usage_events[0]["input_tokens"] == 50
    assert usage_events[1]["input_tokens"] == 100
