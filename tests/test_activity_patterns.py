"""Tests for activity patterns dashboard."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from open_strix.builtin_skills.scripts.activity_patterns import (
    DayMetrics,
    compute_daily_metrics,
    load_events,
    render_text_report,
)


# ── DayMetrics unit tests ──────────────────────────────────────────

class TestDayMetrics:
    def test_empty_day(self):
        m = DayMetrics(day=date(2026, 3, 3))
        assert m.input_messages == 0
        assert m.input_chars == 0
        assert m.quantity_ratio is None
        assert m.effective_sources == 0.0
        assert m.qr_region == "no-input"

    def test_single_source(self):
        m = DayMetrics(
            day=date(2026, 3, 3),
            source_messages={"tim": 10},
            source_chars={"tim": 5000},
            agent_messages=20,
            agent_chars=15000,
        )
        assert m.input_messages == 10
        assert m.input_chars == 5000
        assert m.quantity_ratio == pytest.approx(3.0)
        assert m.effective_sources == pytest.approx(1.0)
        assert m.qr_region == "chatty"

    def test_two_equal_sources(self):
        m = DayMetrics(
            day=date(2026, 3, 3),
            source_messages={"tim": 10, "lily": 10},
            source_chars={"tim": 3000, "lily": 3000},
            agent_messages=10,
            agent_chars=6000,
        )
        assert m.effective_sources == pytest.approx(2.0)
        assert m.quantity_ratio == pytest.approx(1.0)
        assert m.qr_region == "conversational"

    def test_three_unequal_sources(self):
        m = DayMetrics(
            day=date(2026, 3, 3),
            source_messages={"tim": 20, "lily": 5, "verge": 1},
            source_chars={"tim": 8000, "lily": 2000, "verge": 500},
            agent_messages=15,
            agent_chars=5000,
        )
        # Effective sources should be between 1 and 3
        assert 1.0 < m.effective_sources < 3.0
        assert m.quantity_ratio == pytest.approx(5000 / 10500)
        assert m.qr_region == "absorbing"

    def test_qr_regions(self):
        def make_day(qr: float) -> DayMetrics:
            return DayMetrics(
                day=date(2026, 3, 3),
                source_messages={"tim": 1},
                source_chars={"tim": 1000},
                agent_messages=1,
                agent_chars=int(1000 * qr),
            )

        assert make_day(0.3).qr_region == "absorbing"
        assert make_day(1.0).qr_region == "conversational"
        assert make_day(2.0).qr_region == "productive"
        assert make_day(4.0).qr_region == "chatty"
        assert make_day(6.0).qr_region == "monologue"

    def test_minimal_input_returns_none_qr(self):
        m = DayMetrics(
            day=date(2026, 3, 3),
            source_messages={"tim": 1},
            source_chars={"tim": 5},  # < 10 chars
            agent_messages=1,
            agent_chars=100,
        )
        assert m.quantity_ratio is None


# ── Event parsing tests ──────────────────────────────────────────

def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    events_dir = tmp_path / "logs"
    events_dir.mkdir(parents=True, exist_ok=True)
    events_path = events_dir / "events.jsonl"
    with open(events_path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return events_path


def _make_discord_message(author: str, content: str, ts: datetime | None = None) -> dict:
    if ts is None:
        ts = datetime.now()
    return {
        "timestamp": ts.isoformat(),
        "type": "discord_message",
        "author": author,
        "author_is_bot": False,
        "content": content,
    }


def _make_send_message(text: str, ts: datetime | None = None) -> dict:
    if ts is None:
        ts = datetime.now()
    return {
        "timestamp": ts.isoformat(),
        "type": "tool_call",
        "tool": "send_message",
        "sent": True,
        "text_preview": text,
    }


class TestLoadEvents:
    def test_loads_recent_events(self, tmp_path):
        now = datetime.now()
        events = [
            _make_discord_message("tim", "hello", now),
            _make_send_message("hi there", now),
        ]
        events_path = _write_events(tmp_path, events)
        loaded = load_events(events_path, days_back=1)
        assert len(loaded) == 2

    def test_filters_old_events(self, tmp_path):
        old = datetime.now() - timedelta(days=30)
        now = datetime.now()
        events = [
            _make_discord_message("tim", "old message", old),
            _make_discord_message("tim", "new message", now),
        ]
        events_path = _write_events(tmp_path, events)
        loaded = load_events(events_path, days_back=7)
        assert len(loaded) == 1
        assert loaded[0]["content"] == "new message"

    def test_handles_missing_file(self, tmp_path):
        missing = tmp_path / "logs" / "events.jsonl"
        loaded = load_events(missing, days_back=7)
        assert loaded == []

    def test_handles_malformed_json(self, tmp_path):
        events_dir = tmp_path / "logs"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_path = events_dir / "events.jsonl"
        now = datetime.now()
        with open(events_path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps(_make_discord_message("tim", "valid", now)) + "\n")
        loaded = load_events(events_path, days_back=1)
        assert len(loaded) == 1


class TestComputeDailyMetrics:
    def test_basic_day(self, tmp_path):
        now = datetime.now()
        events = [
            _make_discord_message("tim", "hello world", now),
            _make_discord_message("lily", "hi there", now),
            _make_send_message("greetings to all", now),
        ]
        daily = compute_daily_metrics(events)
        assert len(daily) == 1
        m = daily[0]
        assert m.source_messages == {"tim": 1, "lily": 1}
        assert m.agent_messages == 1
        assert m.effective_sources == pytest.approx(2.0)

    def test_multi_day(self, tmp_path):
        day1 = datetime(2026, 3, 1, 12, 0)
        day2 = datetime(2026, 3, 2, 12, 0)
        events = [
            _make_discord_message("tim", "day 1", day1),
            _make_send_message("reply 1", day1),
            _make_discord_message("tim", "day 2", day2),
            _make_send_message("reply 2", day2),
        ]
        daily = compute_daily_metrics(events)
        assert len(daily) == 2
        assert daily[0].day == date(2026, 3, 1)
        assert daily[1].day == date(2026, 3, 2)

    def test_unsent_messages_excluded(self):
        now = datetime.now()
        events = [
            _make_discord_message("tim", "hello", now),
            {
                "timestamp": now.isoformat(),
                "type": "tool_call",
                "tool": "send_message",
                "sent": False,
                "text_preview": "failed message",
            },
        ]
        daily = compute_daily_metrics(events)
        assert len(daily) == 1
        assert daily[0].agent_messages == 0

    def test_non_message_events_ignored(self):
        now = datetime.now()
        events = [
            _make_discord_message("tim", "hello", now),
            {
                "timestamp": now.isoformat(),
                "type": "tool_call",
                "tool": "get_block",
                "block": "persona",
            },
            _make_send_message("reply", now),
        ]
        daily = compute_daily_metrics(events)
        assert len(daily) == 1
        assert daily[0].input_messages == 1
        assert daily[0].agent_messages == 1


class TestTextReport:
    def test_renders_without_crash(self):
        daily = [
            DayMetrics(
                day=date(2026, 3, 1),
                source_messages={"tim": 5},
                source_chars={"tim": 2000},
                agent_messages=8,
                agent_chars=6000,
            ),
            DayMetrics(
                day=date(2026, 3, 2),
                source_messages={"tim": 10, "lily": 3},
                source_chars={"tim": 4000, "lily": 1200},
                agent_messages=15,
                agent_chars=12000,
            ),
        ]
        report = render_text_report(daily)
        assert "Activity Patterns Report" in report
        assert "2026-03-01" in report
        assert "2026-03-02" in report
        assert "tim" in report
        assert "lily" in report

    def test_empty_data(self):
        report = render_text_report([])
        assert "No activity data" in report

    def test_includes_qr_stats(self):
        daily = [
            DayMetrics(
                day=date(2026, 3, i),
                source_messages={"tim": 5},
                source_chars={"tim": 1000},
                agent_messages=5,
                agent_chars=2000,
            )
            for i in range(1, 5)
        ]
        report = render_text_report(daily)
        assert "QR mean" in report
