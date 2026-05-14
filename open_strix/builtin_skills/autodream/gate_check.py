#!/usr/bin/env python3
"""autodream gate check — lightweight watcher that decides whether to trigger a dream.

Reads minimal state (timestamp file + session count) and exits silently most
turns. Only emits a JSONL finding when all gate conditions are met.

Receives on stdin (watcher contract):
    {"trigger": "turn_complete", "trace_id": "...", "events_path": "/..."}

Emits on stdout (when gate opens):
    {"route": "agent", "message": "...", "severity": "info"}
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# Gate thresholds — tune these per deployment
MIN_SESSIONS = int(os.environ.get("AUTODREAM_MIN_SESSIONS", "5"))
MIN_HOURS = float(os.environ.get("AUTODREAM_MIN_HOURS", "24"))
LOCK_STALE_SECONDS = 3600  # 1 hour


def _home_dir() -> Path:
    """Resolve the agent home directory from STATE_DIR or watcher env."""
    state_dir = os.environ.get("STATE_DIR", "")
    if state_dir:
        return Path(state_dir).parent
    return Path.cwd()


def _read_last_dream(home: Path) -> datetime | None:
    """Read the last dream timestamp, or None if never dreamed."""
    ts_file = home / "state" / "autodream-last.txt"
    if not ts_file.exists():
        return None
    try:
        text = ts_file.read_text().strip()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, OSError):
        return None


def _check_lock(home: Path) -> bool:
    """Return True if a dream is currently running (lock is fresh)."""
    lock = home / "state" / "autodream.lock"
    if not lock.exists():
        return False
    try:
        age = time.time() - lock.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            # Stale lock — previous dream died. Remove it.
            lock.unlink(missing_ok=True)
            return False
        return True  # Fresh lock — dream in progress
    except OSError:
        return False


def _count_sessions_since(events_path: Path, since: datetime | None) -> int:
    """Count distinct session IDs in events.jsonl since a given timestamp.

    Reads the file backwards (tail) for efficiency — recent events are at the
    end. Stops when it hits an event older than `since`.
    """
    if not events_path.exists():
        return 0

    session_ids: set[str] = set()
    since_ts = since.isoformat() if since else ""

    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = event.get("timestamp", "")
                if since_ts and ts < since_ts:
                    continue

                sid = event.get("session_id", "")
                if sid:
                    session_ids.add(sid)
    except OSError:
        return 0

    return len(session_ids)


def main() -> None:
    # Read watcher stdin contract
    try:
        stdin_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        stdin_data = {}

    events_path_str = stdin_data.get("events_path", "")
    events_path = Path(events_path_str) if events_path_str else None

    home = _home_dir()

    # Gate 1: Not currently dreaming
    if _check_lock(home):
        return  # Silent exit — dream in progress

    # Gate 2: Enough time since last dream
    last_dream = _read_last_dream(home)
    now = datetime.now(tz=timezone.utc)

    if last_dream is not None:
        hours_since = (now - last_dream).total_seconds() / 3600
        if hours_since < MIN_HOURS:
            return  # Silent exit — too soon

    # Gate 3: Enough sessions
    if events_path and events_path.exists():
        session_count = _count_sessions_since(events_path, last_dream)
    else:
        # Fallback: check home events.jsonl
        fallback = home / "logs" / "events.jsonl"
        session_count = _count_sessions_since(fallback, last_dream)

    if session_count < MIN_SESSIONS:
        return  # Silent exit — not enough activity

    # All gates passed — emit finding
    finding = {
        "route": "agent",
        "severity": "info",
        "message": (
            f"autodream gate open: {session_count} sessions since last dream"
            f" ({hours_since:.0f}h ago). "
            f"Use the autodream skill to run memory consolidation."
            if last_dream
            else f"autodream gate open: {session_count} sessions, first dream. "
            f"Use the autodream skill to run memory consolidation."
        ),
    }
    print(json.dumps(finding))


if __name__ == "__main__":
    main()
