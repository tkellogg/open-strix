"""Cross-reference journal entries against event logs to detect intent-vs-outcome gaps.

Reads logs/journal.jsonl and logs/events.jsonl, compares claims in each journal
entry against the actual events recorded for that session, and outputs structured
dissonance records.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc

# Keywords that indicate "no action taken" in agent_did
SILENCE_PATTERNS = re.compile(
    r"\b(silence|no\s+(?:message|response|text|reply)\s+(?:sent|needed|warranted))\b",
    re.IGNORECASE,
)

# Keywords that indicate message sending in agent_did
SEND_PATTERNS = re.compile(
    r"\b(sent|posted|replied|responded|messaged|relayed|shared)\b",
    re.IGNORECASE,
)

# Keywords that indicate success in agent_did
SUCCESS_PATTERNS = re.compile(
    r"\b(completed|done|succeeded|delivered|shipped|fixed|resolved)\b",
    re.IGNORECASE,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file, skipping blank or malformed lines."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def parse_timestamp(raw: str) -> datetime:
    """Parse an ISO timestamp, normalizing to UTC."""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def events_for_session(
    events: list[dict[str, Any]], session_id: str
) -> list[dict[str, Any]]:
    """Filter events to a specific session."""
    return [e for e in events if e.get("session_id") == session_id]


def detect_action_mismatch(
    journal_entry: dict[str, Any],
    session_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect contradictions between journal claims and actual events."""
    findings: list[dict[str, Any]] = []
    agent_did = str(journal_entry.get("agent_did", ""))

    send_events = [
        e for e in session_events if e.get("tool") == "send_message"
    ]
    react_events = [e for e in session_events if e.get("tool") == "react"]

    # Claimed silence but sent messages
    if SILENCE_PATTERNS.search(agent_did) and send_events:
        findings.append({
            "dissonance_type": "action_mismatch",
            "journal_claim": _truncate(agent_did, 200),
            "event_evidence": (
                f"{len(send_events)} send_message call(s) in session"
            ),
            "severity": "high",
        })

    # Claimed sending but no send events
    if SEND_PATTERNS.search(agent_did) and not send_events and not react_events:
        # Only flag if the claim is about sending a message, not just reacting
        if not re.search(r"\breact", agent_did, re.IGNORECASE):
            findings.append({
                "dissonance_type": "action_mismatch",
                "journal_claim": _truncate(agent_did, 200),
                "event_evidence": "no send_message or react events in session",
                "severity": "high",
            })

    return findings


def detect_invisible_failure(
    journal_entry: dict[str, Any],
    session_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect sessions where journal claims success but events show errors."""
    findings: list[dict[str, Any]] = []
    agent_did = str(journal_entry.get("agent_did", ""))

    error_events = [
        e
        for e in session_events
        if "error" in str(e.get("type", "")).lower()
    ]

    if SUCCESS_PATTERNS.search(agent_did) and error_events:
        error_types = [e.get("type", "unknown") for e in error_events]
        if not re.search(r"\b(error|fail|issue)\b", agent_did, re.IGNORECASE):
            findings.append({
                "dissonance_type": "invisible_failure",
                "journal_claim": _truncate(agent_did, 200),
                "event_evidence": (
                    f"{len(error_events)} error event(s): "
                    f"{', '.join(error_types[:3])}"
                ),
                "severity": "high",
            })

    return findings


def detect_scope_drift(
    journal_entry: dict[str, Any],
    session_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect significant mismatch between event volume and journal description."""
    findings: list[dict[str, Any]] = []
    agent_did = str(journal_entry.get("agent_did", ""))

    tool_calls = [e for e in session_events if e.get("type") == "tool_call"]
    description_length = len(agent_did)

    # Many tool calls, very brief description
    if len(tool_calls) >= 10 and description_length < 50:
        findings.append({
            "dissonance_type": "understated_action",
            "journal_claim": _truncate(agent_did, 200),
            "event_evidence": (
                f"{len(tool_calls)} tool calls but only "
                f"{description_length} chars in agent_did"
            ),
            "severity": "low",
        })

    # Very few tool calls, elaborate description
    if len(tool_calls) <= 1 and description_length > 500:
        findings.append({
            "dissonance_type": "phantom_work",
            "journal_claim": _truncate(agent_did, 200),
            "event_evidence": (
                f"only {len(tool_calls)} tool call(s) but "
                f"{description_length} chars describing work done"
            ),
            "severity": "medium",
        })

    return findings


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def review_entry(
    journal_entry: dict[str, Any],
    all_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run all detectors on a single journal entry."""
    session_id = journal_entry.get("session_id", "")
    if not session_id:
        return []

    session_events = events_for_session(all_events, session_id)
    if not session_events:
        return []

    findings: list[dict[str, Any]] = []
    findings.extend(detect_action_mismatch(journal_entry, session_events))
    findings.extend(detect_invisible_failure(journal_entry, session_events))
    findings.extend(detect_scope_drift(journal_entry, session_events))

    now_iso = datetime.now(tz=UTC).isoformat()
    for f in findings:
        f["timestamp"] = now_iso
        f["journal_timestamp"] = journal_entry.get("timestamp", "")
        f["session_id"] = session_id
        f.setdefault("notes", "")

    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-reference journal entries against event logs to detect dissonance.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--last",
        type=int,
        default=None,
        help="Review the N most recent journal entries.",
    )
    group.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Review journal entries from the last N hours.",
    )
    parser.add_argument(
        "--journal",
        default="logs/journal.jsonl",
        help="Path to journal JSONL file.",
    )
    parser.add_argument(
        "--events",
        default="logs/events.jsonl",
        help="Path to events JSONL file.",
    )
    parser.add_argument(
        "--output",
        default="state/dissonance_reviews.jsonl",
        help="Path to write dissonance records.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print findings to stdout without writing to output file.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    journal_path = Path(args.journal).expanduser()
    events_path = Path(args.events).expanduser()
    output_path = Path(args.output).expanduser()

    if not journal_path.is_absolute():
        journal_path = Path.cwd() / journal_path
    if not events_path.is_absolute():
        events_path = Path.cwd() / events_path
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    journal_entries = load_jsonl(journal_path)
    all_events = load_jsonl(events_path)

    if not journal_entries:
        print("No journal entries found.", file=sys.stderr)
        return

    # Filter entries based on args
    if args.last is not None:
        journal_entries = journal_entries[-args.last :]
    elif args.hours is not None:
        cutoff = datetime.now(tz=UTC) - timedelta(hours=args.hours)
        journal_entries = [
            e
            for e in journal_entries
            if "timestamp" in e and parse_timestamp(e["timestamp"]) >= cutoff
        ]
    else:
        # Default: last 10 entries
        journal_entries = journal_entries[-10:]

    all_findings: list[dict[str, Any]] = []
    for entry in journal_entries:
        findings = review_entry(entry, all_events)
        all_findings.extend(findings)

    # Report
    if not all_findings:
        print(f"No dissonance detected across {len(journal_entries)} journal entries.")
        return

    print(
        f"Found {len(all_findings)} dissonance(s) across "
        f"{len(journal_entries)} journal entries:"
    )
    for f in all_findings:
        severity = f["severity"].upper()
        dtype = f["dissonance_type"]
        print(f"  [{severity}] {dtype}: {f['event_evidence']}")

    # Summary by type
    type_counts: dict[str, int] = {}
    for f in all_findings:
        t = f["dissonance_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    print("\nBy type:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {count}")

    severity_counts: dict[str, int] = {}
    for f in all_findings:
        s = f["severity"]
        severity_counts[s] = severity_counts.get(s, 0) + 1
    print("\nBy severity:")
    for s in ("high", "medium", "low"):
        if s in severity_counts:
            print(f"  {s}: {severity_counts[s]}")

    if args.dry_run:
        print("\n(dry run — not writing to output file)")
        return

    # Write findings
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        for f in all_findings:
            fh.write(json.dumps(f, ensure_ascii=True) + "\n")
    print(f"\nAppended {len(all_findings)} record(s) to {output_path}")


if __name__ == "__main__":
    main()
