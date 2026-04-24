"""Tests for the dissonance review script."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from open_strix.builtin_skills.scripts.dissonance_review import (
    detect_action_mismatch,
    detect_invisible_failure,
    detect_scope_drift,
    load_jsonl,
    review_entry,
)

UTC = timezone.utc
NOW = datetime.now(tz=UTC).isoformat()


def _journal(agent_did: str, session_id: str = "sess-1") -> dict:
    return {
        "timestamp": NOW,
        "session_id": session_id,
        "channel_id": "ch-1",
        "user_wanted": "test",
        "agent_did": agent_did,
        "predictions": "",
    }


def _event(
    event_type: str = "tool_call",
    session_id: str = "sess-1",
    tool: str | None = None,
    **kwargs: object,
) -> dict:
    d: dict = {"timestamp": NOW, "type": event_type, "session_id": session_id}
    if tool is not None:
        d["tool"] = tool
    d.update(kwargs)
    return d


class TestActionMismatch:
    """Detect contradictions between journal silence claims and actual sends."""

    def test_silence_claimed_but_message_sent(self) -> None:
        entry = _journal("Silence — no response needed")
        events = [_event(tool="send_message")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 1
        assert findings[0]["dissonance_type"] == "action_mismatch"
        assert findings[0]["severity"] == "high"

    def test_no_message_sent_variation(self) -> None:
        entry = _journal("No message sent — Tim heads-down")
        events = [_event(tool="send_message")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 1

    def test_actual_silence_no_finding(self) -> None:
        entry = _journal("Silence — no response needed")
        events = [_event(tool="read_file")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 0

    def test_claimed_send_but_no_events(self) -> None:
        entry = _journal("Sent substantive analysis to research channel")
        events = [_event(tool="read_file")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 1
        assert findings[0]["dissonance_type"] == "action_mismatch"

    def test_claimed_send_with_send_event(self) -> None:
        entry = _journal("Sent substantive analysis to research channel")
        events = [_event(tool="send_message")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 0

    def test_claimed_react_with_react_event(self) -> None:
        entry = _journal("Reacted with owl emoji")
        events = [_event(tool="react")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 0

    def test_no_silence_keywords_no_finding(self) -> None:
        entry = _journal("Updated state files and committed")
        events = [_event(tool="send_message")]
        findings = detect_action_mismatch(entry, events)
        assert len(findings) == 0


class TestInvisibleFailure:
    """Detect sessions where journal claims success but events show errors."""

    def test_success_claimed_with_errors(self) -> None:
        entry = _journal("Completed the update successfully")
        events = [
            _event(tool="edit_file"),
            _event(event_type="tool_call_error", error_type="permission_denied"),
        ]
        findings = detect_invisible_failure(entry, events)
        assert len(findings) == 1
        assert findings[0]["dissonance_type"] == "invisible_failure"
        assert findings[0]["severity"] == "high"

    def test_success_with_acknowledged_error(self) -> None:
        entry = _journal("Fixed the error in the config file after initial failure")
        events = [
            _event(event_type="tool_call_error", error_type="parse_error"),
            _event(tool="edit_file"),
        ]
        findings = detect_invisible_failure(entry, events)
        # Should not flag because agent_did mentions the error
        assert len(findings) == 0

    def test_no_success_keywords_no_finding(self) -> None:
        entry = _journal("Tried to update but ran into issues")
        events = [
            _event(event_type="tool_call_error", error_type="timeout"),
        ]
        findings = detect_invisible_failure(entry, events)
        assert len(findings) == 0

    def test_success_no_errors(self) -> None:
        entry = _journal("Completed the full migration")
        events = [_event(tool="edit_file"), _event(tool="write_file")]
        findings = detect_invisible_failure(entry, events)
        assert len(findings) == 0


class TestScopeDrift:
    """Detect mismatch between event volume and journal description length."""

    def test_many_tools_brief_description(self) -> None:
        entry = _journal("Done.")
        events = [_event(tool=f"tool_{i}") for i in range(15)]
        findings = detect_scope_drift(entry, events)
        assert len(findings) == 1
        assert findings[0]["dissonance_type"] == "understated_action"
        assert findings[0]["severity"] == "low"

    def test_few_tools_elaborate_description(self) -> None:
        entry = _journal("A" * 600)
        events = [_event(tool="read_file")]
        findings = detect_scope_drift(entry, events)
        assert len(findings) == 1
        assert findings[0]["dissonance_type"] == "phantom_work"
        assert findings[0]["severity"] == "medium"

    def test_proportional_no_finding(self) -> None:
        entry = _journal("Updated the config and committed changes to git")
        events = [_event(tool="edit_file"), _event(tool="bash")]
        findings = detect_scope_drift(entry, events)
        assert len(findings) == 0


class TestReviewEntry:
    """Integration test for the full review pipeline."""

    def test_multiple_findings_single_entry(self) -> None:
        # Silence claimed but message sent AND errors present
        entry = _journal("Silence — completed without issues")
        events = [
            _event(tool="send_message"),
            _event(event_type="tool_call_error", error_type="timeout"),
        ]
        findings = review_entry(entry, events)
        # Should find action_mismatch (silence + send) and invisible_failure (completed + error)
        types = {f["dissonance_type"] for f in findings}
        assert "action_mismatch" in types

    def test_no_session_id_skipped(self) -> None:
        entry = {"agent_did": "test", "timestamp": NOW}
        events = [_event()]
        findings = review_entry(entry, events)
        assert len(findings) == 0

    def test_no_matching_events_skipped(self) -> None:
        entry = _journal("Silence — no response", session_id="sess-1")
        events = [_event(session_id="sess-other", tool="send_message")]
        findings = review_entry(entry, events)
        assert len(findings) == 0

    def test_findings_have_metadata(self) -> None:
        entry = _journal("Silence — no response needed")
        events = [_event(tool="send_message")]
        findings = review_entry(entry, events)
        assert len(findings) == 1
        f = findings[0]
        assert "timestamp" in f
        assert f["session_id"] == "sess-1"
        assert f["journal_timestamp"] == NOW


class TestLoadJsonl:
    """Test JSONL loading with edge cases."""

    def test_loads_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
        result = load_jsonl(p)
        assert len(result) == 2

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        p.write_text('{"a": 1}\n\n{"b": 2}\n\n', encoding="utf-8")
        result = load_jsonl(p)
        assert len(result) == 2

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        p.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
        result = load_jsonl(p)
        assert len(result) == 2

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.jsonl"
        result = load_jsonl(p)
        assert result == []
