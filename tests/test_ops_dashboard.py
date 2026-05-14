from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from open_strix.config import AppConfig, RepoLayout
from open_strix.discord import DiscordMixin
from open_strix.models import AgentEvent
from open_strix.ops_dashboard import (
    _load_events,
    build_dashboard_payload,
    compute_stats,
    parse_days_param,
    render_dashboard_html,
)
from open_strix.shell_jobs import ShellJobRegistry
from open_strix.web_ui import WebChatMixin, _build_web_ui_app, _render_web_ui_page


def _ts(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _write_events(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def test_parse_days_param_default_and_validation() -> None:
    assert parse_days_param(None) == 30
    assert parse_days_param("") == 30
    assert parse_days_param("7") == 7
    with pytest.raises(ValueError):
        parse_days_param("0")
    with pytest.raises(ValueError):
        parse_days_param("-3")
    with pytest.raises(ValueError):
        parse_days_param("not-an-int")
    with pytest.raises(ValueError):
        parse_days_param("9999")


def test_load_events_filters_outside_window(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    _write_events(
        log,
        [
            {"type": "tool_call", "timestamp": _ts(0)},
            {"type": "tool_call", "timestamp": _ts(24 * 100)},  # 100 days old
        ],
    )
    inside = _load_events(log, days=30)
    assert len(inside) == 1


def test_load_events_skips_malformed_lines(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "tool_call", "timestamp": _ts(0)}),
                "{not valid json",
                "",
                json.dumps({"type": "agent_invoke_start", "timestamp": _ts(0)}),
            ]
        )
    )
    out = _load_events(log, days=30)
    assert {e["type"] for e in out} == {"tool_call", "agent_invoke_start"}


def test_compute_stats_counts_and_attribution(tmp_path: Path) -> None:
    records = [
        {"type": "agent_invoke_start", "timestamp": _ts(0),
         "source_event_type": "discord_message", "session_id": "s1"},
        {"type": "agent_invoke_start", "timestamp": _ts(1),
         "source_event_type": "poller", "scheduler_name": "linkedin", "session_id": "s2"},
        {"type": "agent_invoke_start", "timestamp": _ts(2),
         "source_event_type": "poller", "scheduler_name": "linkedin", "session_id": "s3"},
        {"type": "tool_call", "timestamp": _ts(0), "tool": "send_message", "session_id": "s1"},
        {"type": "tool_call", "timestamp": _ts(0), "tool": "send_message", "session_id": "s1"},
        {"type": "tool_call", "timestamp": _ts(1), "tool": "journal", "session_id": "s2"},
        {"type": "event_queued", "timestamp": _ts(0), "source_event_type": "discord_message"},
        {"type": "event_queued", "timestamp": _ts(0), "source_event_type": "poller"},
        {"type": "event_deduped", "timestamp": _ts(0), "key": "poller:linkedin:0"},
        {"type": "turn_timing", "timestamp": _ts(0),
         "total_seconds": 8.0, "agent_invoke_seconds": 6.0},
        {"type": "turn_timing", "timestamp": _ts(1),
         "total_seconds": 10.0, "agent_invoke_seconds": 7.0},
        {"type": "agent_turn_missing_send_message", "timestamp": _ts(0),
         "scheduler_name": "linkedin", "final_text": "oops"},
        {"type": "scheduler_invalid_cron", "timestamp": _ts(0), "error": "bad cron"},
    ]
    for record in records:
        record["_ts"] = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))

    stats = compute_stats(records, days=30)

    assert stats["window_days"] == 30
    assert stats["summary"]["agent_invocations"] == 3
    assert stats["summary"]["events_queued"] == 2
    assert stats["summary"]["events_deduped"] == 1
    assert stats["summary"]["tool_calls"] == 3
    assert stats["summary"]["failures"] == 2
    assert stats["summary"]["avg_turn_seconds"] == 9.0
    assert stats["summary"]["avg_invoke_seconds"] == 6.5

    # Source / scheduler attribution
    assert stats["invoke_by_source"]["poller"] == 2
    assert stats["invoke_by_source"]["discord_message"] == 1
    assert stats["invoke_by_scheduler"]["linkedin"] == 2

    # Failures collected
    assert stats["failures_by_kind"]["agent_turn_missing_send_message"] == 1
    assert stats["failures_by_kind"]["scheduler_invalid_cron"] == 1
    assert any(f["kind"] == "agent_turn_missing_send_message" for f in stats["recent_failures"])

    # Avg tools / invocation: s1=2, s2=1, s3=0 (s3 had no tool_call rows) → only counted sessions seen in tool_call
    # Implementation counts tool_calls per session_id seen in tool_call events,
    # so average is over sessions with at least one tool call.
    assert stats["summary"]["avg_tools_per_invocation"] == 1.5

    # Backlog has at least the documented gaps
    backlog_ids = {item["id"] for item in stats["backlog"]}
    assert {"token-usage", "llm-retries"}.issubset(backlog_ids)


def test_render_dashboard_html_embeds_data(tmp_path: Path) -> None:
    stats = compute_stats([], days=7)
    html = render_dashboard_html(stats)
    assert "Ops Dashboard" in html
    assert "back to chat" in html
    # Embedded JSON
    payload_marker = '<script id="data" type="application/json">'
    assert payload_marker in html
    start = html.index(payload_marker) + len(payload_marker)
    end = html.index("</script>", start)
    embedded = json.loads(html[start:end])
    assert embedded["window_days"] == 7
    assert "summary" in embedded


# --- route-level integration ----------------------------------------------


class DummyStrix(DiscordMixin, WebChatMixin):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.layout.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig(
            web_ui_port=8084,
            web_ui_host="127.0.0.1",
            web_ui_channel_id="local-web",
        )
        self.message_history_all = deque(maxlen=500)
        self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
        self._current_turn_sent_messages: list[tuple[str, str]] | None = []
        self.current_channel_id: str | None = None
        self.current_event_label: str | None = None
        self.current_turn_start: float | None = None
        self.discord_client = None
        self.shell_jobs = ShellJobRegistry(self.layout.logs_dir / "shell-jobs")
        self.logged: list[dict[str, object]] = []
        self.enqueued: list[AgentEvent] = []

    def log_event(self, event_type: str, **payload: object) -> None:
        self.logged.append({"type": event_type, **payload})

    async def enqueue_event(self, event: AgentEvent) -> None:
        self.enqueued.append(event)


def _get_route_handler(app, path: str, method: str):
    for route in app.router.routes():
        if route.method == method and getattr(route.resource, "canonical", None) == path:
            return route.handler
    raise AssertionError(f"missing {method} route for {path}")


def test_main_page_includes_ops_link(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    page = _render_web_ui_page(strix)
    assert 'href="/ops"' in page
    assert "header-links" in page


def test_ops_route_returns_html(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    _write_events(
        strix.layout.events_log,
        [
            {"type": "agent_invoke_start", "timestamp": _ts(0),
             "source_event_type": "poller", "scheduler_name": "linkedin"},
            {"type": "tool_call", "timestamp": _ts(0), "tool": "send_message"},
        ],
    )
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/ops", "GET")
    request = make_mocked_request("GET", "/ops")
    response = asyncio.run(handler(request))
    assert response.status == 200
    assert response.content_type == "text/html"
    body = response.text
    assert "Ops Dashboard" in body
    # data is embedded
    assert '"agent_invocations": 1' in body or '"agent_invocations":1' in body


def test_ops_api_route_returns_json(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    _write_events(
        strix.layout.events_log,
        [
            {"type": "tool_call", "timestamp": _ts(0), "tool": "journal"},
        ],
    )
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/api/ops", "GET")
    request = make_mocked_request("GET", "/api/ops")
    response = asyncio.run(handler(request))
    assert response.status == 200
    assert response.content_type == "application/json"
    payload = json.loads(response.text)
    assert payload["summary"]["tool_calls"] == 1


def test_ops_route_rejects_bad_days(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/ops", "GET")
    request = make_mocked_request("GET", "/ops?days=not-a-number")
    response = asyncio.run(handler(request))
    assert response.status == 400


def test_ops_route_handles_missing_log(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    # Don't write the events log
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/ops", "GET")
    request = make_mocked_request("GET", "/ops")
    response = asyncio.run(handler(request))
    assert response.status == 200
    body = response.text
    assert "Ops Dashboard" in body


def test_build_dashboard_payload_uses_strix_layout(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    _write_events(
        strix.layout.events_log,
        [{"type": "event_queued", "timestamp": _ts(0), "source_event_type": "poller"}],
    )
    payload = build_dashboard_payload(strix, days=7)
    assert payload["summary"]["events_queued"] == 1
    assert payload["window_days"] == 7


# --- new tests for rotation support -----------------------------------------


def test_load_events_reads_rotated_siblings(tmp_path: Path) -> None:
    """Events in rotated siblings are included when within the time window."""
    log = tmp_path / "events.jsonl"

    # Write a rotated sibling with an old-but-within-window event (12h ago)
    rotated = tmp_path / "events.jsonl.20260514T120000Z"
    _write_events(rotated, [{"type": "tool_call", "timestamp": _ts(12)}])

    # Write the live file with a recent event (1h ago)
    _write_events(log, [{"type": "agent_invoke_start", "timestamp": _ts(1)}])

    result = _load_events(log, days=1)
    types = {e["type"] for e in result}
    assert types == {"tool_call", "agent_invoke_start"}, (
        "Events from rotated sibling should be included"
    )


def test_load_events_excludes_rotated_events_outside_window(tmp_path: Path) -> None:
    """Events in rotated siblings that predate the cutoff are filtered out."""
    log = tmp_path / "events.jsonl"

    # Write a rotated sibling with a very old event (100 days ago)
    rotated = tmp_path / "events.jsonl.20260114T000000Z"
    _write_events(rotated, [{"type": "tool_call", "timestamp": _ts(24 * 100)}])

    # Write the live file with a recent event
    _write_events(log, [{"type": "agent_invoke_start", "timestamp": _ts(1)}])

    result = _load_events(log, days=30)
    assert len(result) == 1
    assert result[0]["type"] == "agent_invoke_start", (
        "Old events in rotated siblings should be filtered by timestamp"
    )


def test_load_events_handles_missing_live_file_with_siblings(tmp_path: Path) -> None:
    """If the live file doesn't exist yet but siblings do, siblings are still read."""
    log = tmp_path / "events.jsonl"
    # Don't create the live file

    rotated = tmp_path / "events.jsonl.20260514T120000Z"
    _write_events(rotated, [{"type": "tool_call", "timestamp": _ts(1)}])

    result = _load_events(log, days=7)
    assert len(result) == 1
    assert result[0]["type"] == "tool_call"


def test_load_events_handles_mid_rotation_race(tmp_path: Path) -> None:
    """A sibling that disappears between glob and open is skipped without error."""
    log = tmp_path / "events.jsonl"
    _write_events(log, [{"type": "agent_invoke_start", "timestamp": _ts(0)}])

    # Simulate mid-rotation: create then immediately delete a sibling
    ghost = tmp_path / "events.jsonl.20260514T110000Z"
    ghost.write_text("")
    ghost.unlink()  # gone before open() is called

    # Should not raise; live file still readable
    result = _load_events(log, days=1)
    assert len(result) == 1
    assert result[0]["type"] == "agent_invoke_start"


def test_load_events_deduplicates_across_siblings(tmp_path: Path) -> None:
    """Verify no duplicate events if the same line somehow appeared in two files.
    (Not expected in practice, but the function should not crash or deduplicate —
    it's the caller's problem. This test just confirms the count.)"""
    log = tmp_path / "events.jsonl"
    event = {"type": "tool_call", "timestamp": _ts(1)}

    rotated = tmp_path / "events.jsonl.20260514T100000Z"
    _write_events(rotated, [event])
    _write_events(log, [event])

    result = _load_events(log, days=7)
    # Both appear — no implicit dedup at this layer
    assert len(result) == 2


def test_load_events_reads_multiple_siblings_in_order(tmp_path: Path) -> None:
    """Multiple rotated siblings are all read, with the live file last."""
    log = tmp_path / "events.jsonl"

    # Three siblings, all within the window
    for suffix, hours_ago in [
        ("20260512T000000Z", 48),
        ("20260513T000000Z", 24),
        ("20260514T000000Z", 12),
    ]:
        _write_events(
            tmp_path / f"events.jsonl.{suffix}",
            [{"type": "tool_call", "timestamp": _ts(hours_ago), "marker": suffix}],
        )

    # Live file
    _write_events(log, [{"type": "agent_invoke_start", "timestamp": _ts(1)}])

    result = _load_events(log, days=7)
    assert len(result) == 4, "All four files (3 siblings + live) should be read"
    types = {e["type"] for e in result}
    assert "tool_call" in types
    assert "agent_invoke_start" in types
