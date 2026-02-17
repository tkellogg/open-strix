from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

import open_strix.app as app_mod

# Load repo-root .env so ANTHROPIC_API_KEY is available during local test runs.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)


def _require_anthropic_key() -> None:
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return
    pytest.skip("ANTHROPIC_API_KEY is not set")


def _read_events(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


@pytest.mark.asyncio
async def test_live_agent_loop_processes_event_with_anthropic_model(tmp_path: Path) -> None:
    _require_anthropic_key()

    model = os.getenv("OPEN_STRIX_TEST_MODEL", "anthropic:claude-sonnet-4-5-20250929")
    (tmp_path / "config.yaml").write_text(f"model: {model}\n", encoding="utf-8")

    app = app_mod.OpenStrixApp(tmp_path)
    worker = asyncio.create_task(app._event_worker())
    try:
        await app.enqueue_event(
            app_mod.AgentEvent(
                event_type="agent_loop_live_test",
                prompt="Do not call any tools. Reply with a short acknowledgement only.",
                channel_id="test",
                author="loop-test",
            ),
        )
        await asyncio.wait_for(app.queue.join(), timeout=180)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    events = _read_events(app.layout.events_log)
    starts = [
        event
        for event in events
        if event.get("type") == "agent_invoke_start"
        and event.get("source_event_type") == "agent_loop_live_test"
    ]
    finals = [
        event
        for event in events
        if event.get("type") == "agent_final_message_discarded"
        and event.get("source_event_type") == "agent_loop_live_test"
    ]
    errors = [
        event
        for event in events
        if event.get("type") == "error"
        and event.get("source_event_type") == "agent_loop_live_test"
    ]

    assert starts, "agent loop did not log invoke start"
    if errors:
        msg = str(errors[-1].get("error", ""))
        lowered = msg.lower()
        transient_or_account = (
            "insufficient balance" in lowered
            or "credit" in lowered
            or "billing" in lowered
            or "rate limit" in lowered
            or "overloaded" in lowered
        )
        if transient_or_account:
            pytest.skip(f"Anthropic unavailable for live loop test: {msg}")
        pytest.fail(f"agent loop errored: {msg}")
    assert finals, "agent loop did not log final message discard"
    final_text = str(finals[-1].get("final_text", "")).strip()
    assert final_text, "model returned empty final text"
