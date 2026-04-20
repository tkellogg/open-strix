#!/usr/bin/env python3
"""
Sibling-error poller — pollers.json contract.

Reads events.jsonl files from sibling agents and emits one event per new
error-kind-transition per agent. Dedup: one-shot-until-different-kind so a
storm of the same error (e.g. 50x insufficient_balance) yields one prompt
with count, not 50 pings.

Inputs (env):
    STATE_DIR    - Writable directory for cursor state
    POLLER_NAME  - This poller's name (from pollers.json)
    SIBLINGS     - Comma-separated "name=path" pairs pointing to each
                   sibling's events.jsonl (required — no default)

Outputs:
    stdout: JSONL lines, one per error-kind-transition
    STATE_DIR/events.jsonl: same events + timestamp (local log)
    stderr: diagnostic logging
    exit 0: success
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent))
CURSOR_FILE = STATE_DIR / "sibling_errors_cursor.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"
POLLER_NAME = os.environ.get("POLLER_NAME", "sibling-errors")

ERROR_EVENT_TYPES = {"error", "poller_nonzero_exit", "agent_final_message_discarded"}


def load_cursor() -> dict:
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))


def emit(prompt: str) -> None:
    event = {"poller": POLLER_NAME, "prompt": prompt}
    line = json.dumps(event)
    print(line, flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a") as f:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        f.write(json.dumps(event) + "\n")


def extract_kind(event: dict) -> str | None:
    """Return a short error-kind label, or None if event isn't an error we care about."""
    etype = event.get("type")
    if etype not in ERROR_EVENT_TYPES:
        return None

    if etype == "error":
        err_str = str(event.get("error", "")).lower()
        for signal in ("insufficient balance", "rate limit", "timeout", "connection", "overloaded", "unauthorized", "not found"):
            if signal in err_str:
                return f"error:{signal.replace(' ', '_')}"
        cls = event.get("error_class")
        status = event.get("error_status_code")
        if cls:
            return f"error:{cls}"
        if status:
            return f"error:http_{status}"
        return "error:unknown"

    if etype == "poller_nonzero_exit":
        name = event.get("name", "unknown")
        return f"poller_fail:{name}"

    if etype == "agent_final_message_discarded":
        return "silent_failure:narrated_send_message"

    return None


def sample_from_event(event: dict) -> str:
    etype = event.get("type")
    if etype == "error":
        err = str(event.get("error", ""))
        return err[:200].replace("\n", " ")
    if etype == "poller_nonzero_exit":
        return f"poller {event.get('name', '?')} exited {event.get('returncode', '?')}"
    if etype == "agent_final_message_discarded":
        final = str(event.get("final_message", ""))[:200].replace("\n", " ")
        return f"discarded: {final}" if final else "narrated send_message discarded"
    return ""


def scan_agent(agent: str, path: Path, since_ts: str | None) -> tuple[list[tuple[str, str, int, str]], str | None]:
    """
    Scan one agent's events.jsonl for error-kind transitions.

    Returns (transitions, latest_ts) where transitions is a list of
    (kind, first_ts, count, sample) — one per new kind-block since cursor.
    """
    if not path.exists():
        return [], None

    transitions: list[tuple[str, str, int, str]] = []
    current_kind: str | None = None
    current_first_ts: str | None = None
    current_count = 0
    current_sample = ""
    latest_ts: str | None = since_ts

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = event.get("timestamp", "")
                if since_ts and ts <= since_ts:
                    continue
                latest_ts = ts if (latest_ts is None or ts > latest_ts) else latest_ts

                kind = extract_kind(event)
                if kind is None:
                    continue

                if kind == current_kind:
                    current_count += 1
                else:
                    if current_kind is not None:
                        transitions.append((current_kind, current_first_ts or "", current_count, current_sample))
                    current_kind = kind
                    current_first_ts = ts
                    current_count = 1
                    current_sample = sample_from_event(event)
    except OSError as e:
        print(f"[{agent}] failed to read {path}: {e}", file=sys.stderr)
        return [], None

    if current_kind is not None:
        transitions.append((current_kind, current_first_ts or "", current_count, current_sample))

    return transitions, latest_ts


def parse_siblings(spec: str) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, path = pair.split("=", 1)
        out.append((name.strip(), Path(path.strip())))
    return out


def main() -> None:
    siblings = parse_siblings(os.environ.get("SIBLINGS", ""))
    if not siblings:
        print("SIBLINGS env not configured (expected 'name=path,name=path')", file=sys.stderr)
        sys.exit(0)

    cursor = load_cursor()
    total = 0

    for agent, path in siblings:
        agent_cursor = cursor.get(agent, {})
        first_run = "last_ts" not in agent_cursor
        since_ts = agent_cursor.get("last_ts")
        last_emitted_kind = agent_cursor.get("last_emitted_kind")

        transitions, latest_ts = scan_agent(agent, path, since_ts)

        if not transitions:
            if latest_ts:
                agent_cursor["last_ts"] = latest_ts
                cursor[agent] = agent_cursor
            print(f"[{agent}] no new errors since {since_ts}", file=sys.stderr)
            continue

        # First run: seed cursor from history without emitting. Avoids flooding
        # the agent with N historical transitions on day one. Subsequent runs
        # see only genuinely new kind-changes.
        if first_run:
            agent_cursor["last_ts"] = latest_ts
            agent_cursor["last_emitted_kind"] = transitions[-1][0]
            cursor[agent] = agent_cursor
            print(f"[{agent}] first run: seeded cursor from {len(transitions)} historical transition(s), no emit", file=sys.stderr)
            continue

        # Dedup: skip leading transitions whose kind matches last_emitted_kind.
        # Repeat of the same kind after a quiet period still counts as "already
        # pinged" — we only emit on kind-change.
        for kind, first_ts, count, sample in transitions:
            if kind == last_emitted_kind:
                continue
            prompt = (
                f"Sibling error: [{agent}] {kind} — {count} occurrence(s) "
                f"since {first_ts}. Sample: {sample}. Diagnose and act: ship a "
                f"fix (code/config) or surface to the operator (credits/infra)."
            )
            emit(prompt)
            total += 1
            last_emitted_kind = kind

        agent_cursor["last_ts"] = latest_ts
        agent_cursor["last_emitted_kind"] = last_emitted_kind
        cursor[agent] = agent_cursor

    save_cursor(cursor)

    if total:
        print(f"Emitted {total} sibling-error event(s)", file=sys.stderr)
    else:
        print("No sibling-error transitions", file=sys.stderr)


if __name__ == "__main__":
    main()
