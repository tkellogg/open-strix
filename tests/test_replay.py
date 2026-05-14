"""Tests for the events.jsonl startup replay hook."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from open_strix.models import AgentEvent
from open_strix.replay import (
    REPLAYABLE_EVENT_TYPES,
    _build_agent_event,
    _candidate_events,
    _parse_ts,
    _read_jsonl_tail,
    replay_unprocessed_events,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 13, 22, 0, 0, tzinfo=timezone.utc)


def _iso(minutes_before_now: int) -> str:
    from datetime import timedelta

    return (NOW - timedelta(minutes=minutes_before_now)).isoformat()


def _write_events(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------


def test_parse_ts_z_suffix():
    dt = _parse_ts("2026-05-13T21:00:00Z")
    assert dt is not None and dt.tzinfo is not None
    assert dt == datetime(2026, 5, 13, 21, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_invalid():
    assert _parse_ts(None) is None
    assert _parse_ts("") is None
    assert _parse_ts("not-a-date") is None
    assert _parse_ts(12345) is None


def test_read_jsonl_tail_skips_malformed(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"type": "a", "n": 1}\n'
        'this is not json\n'
        '{"type": "b", "n": 2}\n'
        '\n'
        '{"type": "c", "n": 3}\n'
    )
    out = _read_jsonl_tail(path)
    assert [r["type"] for r in out] == ["a", "b", "c"]


def test_read_jsonl_tail_missing_file(tmp_path: Path):
    assert _read_jsonl_tail(tmp_path / "nope.jsonl") == []


def test_build_event_web_message_ok():
    rec = {
        "type": "web_message",
        "session_id": "s1",
        "channel_id": "local-web",
        "channel_name": "Local Web",
        "channel_conversation_type": "dm",
        "channel_visibility": "private",
        "author": "local_user",
        "author_id": "u",
        "attachment_names": ["a.png"],
        "source_id": "web-abc",
        "content": "hello world",
    }
    event = _build_agent_event(rec)
    assert isinstance(event, AgentEvent)
    assert event.event_type == "web_message"
    assert event.prompt == "hello world"
    assert event.channel_id == "local-web"
    assert event.attachment_names == ["a.png"]
    assert event.source_id == "web-abc"


def test_build_event_web_message_missing_content():
    rec = {"type": "web_message", "session_id": "s1", "channel_id": "c"}
    assert _build_agent_event(rec) is None


def test_build_event_shell_job_complete():
    rec = {
        "type": "shell_job_complete",
        "session_id": "s1",
        "job_id": "j_abc",
        "channel_id": "local-web",
    }
    event = _build_agent_event(rec)
    assert event is not None
    assert event.event_type == "shell_job_complete"
    assert event.source_id == "shell_job:j_abc"
    assert event.dedupe_key == "shell_job_complete:j_abc"
    assert "j_abc" in event.prompt


def test_build_event_shell_job_complete_uses_source_id_fallback():
    rec = {
        "type": "shell_job_complete",
        "session_id": "s1",
        "source_id": "shell_job:fromsrc",
    }
    event = _build_agent_event(rec)
    assert event is not None
    assert event.source_id == "shell_job:fromsrc"


def test_build_event_scheduler_tick_is_skipped():
    rec = {"type": "scheduler_tick", "scheduler_name": "x", "session_id": "s1"}
    assert _build_agent_event(rec) is None


def test_build_event_unknown_type_is_none():
    assert _build_agent_event({"type": "tool_call"}) is None


def test_replayable_types_freezeset_is_known_set():
    assert "web_message" in REPLAYABLE_EVENT_TYPES
    assert "discord_message" in REPLAYABLE_EVENT_TYPES
    assert "shell_job_complete" in REPLAYABLE_EVENT_TYPES
    assert "agent_invoke_start" not in REPLAYABLE_EVENT_TYPES
    assert "app_started" not in REPLAYABLE_EVENT_TYPES


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------


def test_candidate_skips_current_session():
    records = [
        {
            "type": "web_message",
            "session_id": "current",
            "timestamp": _iso(2),
            "channel_id": "c",
            "content": "fresh",
        },
    ]
    out = _candidate_events(
        records,
        current_session_id="current",
        window_start=NOW.replace(hour=21, minute=50),
    )
    assert out == []


def test_candidate_keeps_unacknowledged_prior_session():
    records = [
        {
            "type": "web_message",
            "session_id": "old",
            "timestamp": _iso(3),
            "channel_id": "c",
            "content": "missed",
            "source_id": "web-1",
        },
    ]
    out = _candidate_events(
        records,
        current_session_id="new",
        window_start=NOW.replace(hour=21, minute=50),
    )
    assert len(out) == 1
    assert out[0]["source_id"] == "web-1"


def test_candidate_drops_acknowledged_event():
    records = [
        {
            "type": "web_message",
            "session_id": "old",
            "timestamp": _iso(3),
            "channel_id": "c",
            "content": "handled",
            "source_id": "web-1",
        },
        {
            "type": "agent_invoke_start",
            "session_id": "old",
            "timestamp": _iso(2),
            "source_event_type": "web_message",
            "channel_id": "c",
        },
    ]
    out = _candidate_events(
        records,
        current_session_id="new",
        window_start=NOW.replace(hour=21, minute=50),
    )
    assert out == []


def test_candidate_invoke_must_match_channel():
    # An agent_invoke_start on a different channel does NOT acknowledge.
    records = [
        {
            "type": "web_message",
            "session_id": "old",
            "timestamp": _iso(3),
            "channel_id": "channel-A",
            "content": "missed",
            "source_id": "web-1",
        },
        {
            "type": "agent_invoke_start",
            "session_id": "old",
            "timestamp": _iso(2),
            "source_event_type": "web_message",
            "channel_id": "channel-B",
        },
    ]
    out = _candidate_events(
        records,
        current_session_id="new",
        window_start=NOW.replace(hour=21, minute=50),
    )
    assert len(out) == 1


def test_candidate_outside_window_skipped():
    records = [
        {
            "type": "web_message",
            "session_id": "old",
            "timestamp": _iso(30),  # 30 min ago, window is 10 min
            "channel_id": "c",
            "content": "ancient",
            "source_id": "web-old",
        },
    ]
    from datetime import timedelta

    window_start = NOW - timedelta(minutes=10)
    out = _candidate_events(
        records,
        current_session_id="new",
        window_start=window_start,
    )
    assert out == []


# ---------------------------------------------------------------------------
# end-to-end via replay_unprocessed_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_enqueues_unprocessed_web_messages(tmp_path: Path):
    events_log = tmp_path / "events.jsonl"
    _write_events(
        events_log,
        [
            # Old session: web_message followed by agent_invoke_start = handled.
            {
                "type": "web_message",
                "session_id": "s_old",
                "timestamp": _iso(8),
                "channel_id": "c",
                "content": "handled",
                "source_id": "web-handled",
            },
            {
                "type": "agent_invoke_start",
                "session_id": "s_old",
                "timestamp": _iso(7),
                "source_event_type": "web_message",
                "channel_id": "c",
            },
            # Old session: web_message with NO downstream invoke = missed.
            {
                "type": "web_message",
                "session_id": "s_old",
                "timestamp": _iso(5),
                "channel_id": "c",
                "content": "you stalled, recover",
                "source_id": "web-missed",
                "author": "local_user",
            },
            # Current session: app_started + fresh web_message = not replayed.
            {
                "type": "app_started",
                "session_id": "s_current",
                "timestamp": _iso(1),
            },
            {
                "type": "web_message",
                "session_id": "s_current",
                "timestamp": _iso(0),
                "channel_id": "c",
                "content": "current msg, do not replay",
                "source_id": "web-current",
            },
        ],
    )

    enqueued: list[AgentEvent] = []

    async def fake_enqueue(event: AgentEvent) -> None:
        enqueued.append(event)

    logs: list[tuple[str, dict]] = []

    def fake_log(event_type: str, **payload):
        logs.append((event_type, payload))

    count = await replay_unprocessed_events(
        events_log_path=events_log,
        current_session_id="s_current",
        enqueue_event=fake_enqueue,
        window_seconds=600,
        now=NOW,
        log_event=fake_log,
    )

    assert count == 1
    assert len(enqueued) == 1
    assert enqueued[0].prompt == "you stalled, recover"
    assert enqueued[0].source_id == "web-missed"
    assert enqueued[0].event_type == "web_message"

    # We should have logged scan + at least one enqueued event.
    log_types = [t for t, _ in logs]
    assert "replay_scan" in log_types
    assert "replay_enqueued" in log_types


@pytest.mark.asyncio
async def test_replay_empty_log_returns_zero(tmp_path: Path):
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("")

    async def fake_enqueue(event: AgentEvent) -> None:
        raise AssertionError("should not enqueue")

    count = await replay_unprocessed_events(
        events_log_path=events_log,
        current_session_id="s_current",
        enqueue_event=fake_enqueue,
        now=NOW,
    )
    assert count == 0


@pytest.mark.asyncio
async def test_replay_missing_log_returns_zero(tmp_path: Path):
    async def fake_enqueue(event: AgentEvent) -> None:
        raise AssertionError("should not enqueue")

    count = await replay_unprocessed_events(
        events_log_path=tmp_path / "nope.jsonl",
        current_session_id="s_current",
        enqueue_event=fake_enqueue,
        now=NOW,
    )
    assert count == 0


@pytest.mark.asyncio
async def test_replay_shell_job_complete_roundtrip(tmp_path: Path):
    events_log = tmp_path / "events.jsonl"
    _write_events(
        events_log,
        [
            {
                "type": "shell_job_complete",
                "session_id": "s_old",
                "timestamp": _iso(3),
                "job_id": "j_xyz",
                "channel_id": "local-web",
                "source_id": "shell_job:j_xyz",
            },
            # No agent_invoke_start follows → must be replayed.
        ],
    )

    enqueued: list[AgentEvent] = []

    async def fake_enqueue(event: AgentEvent) -> None:
        enqueued.append(event)

    count = await replay_unprocessed_events(
        events_log_path=events_log,
        current_session_id="s_current",
        enqueue_event=fake_enqueue,
        now=NOW,
    )

    assert count == 1
    assert enqueued[0].event_type == "shell_job_complete"
    assert enqueued[0].source_id == "shell_job:j_xyz"
    assert "j_xyz" in enqueued[0].prompt


@pytest.mark.asyncio
async def test_replay_does_not_double_enqueue_when_invoke_present(tmp_path: Path):
    events_log = tmp_path / "events.jsonl"
    _write_events(
        events_log,
        [
            {
                "type": "shell_job_complete",
                "session_id": "s_old",
                "timestamp": _iso(3),
                "job_id": "j_abc",
                "channel_id": "local-web",
            },
            {
                "type": "agent_invoke_start",
                "session_id": "s_old",
                "timestamp": _iso(2),
                "source_event_type": "shell_job_complete",
                "channel_id": "local-web",
            },
        ],
    )

    async def fake_enqueue(event: AgentEvent) -> None:
        raise AssertionError("should not enqueue")

    count = await replay_unprocessed_events(
        events_log_path=events_log,
        current_session_id="s_current",
        enqueue_event=fake_enqueue,
        now=NOW,
    )
    assert count == 0
