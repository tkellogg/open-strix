#!/usr/bin/env python3
"""Watcher: detect turns with many tool calls but no communication.

Usage in watchers.json:
    {"name": "silent-session", "command": "python examples/silent_session.py", "trigger": "turn_complete"}

Reads events.jsonl, filters to the current turn by trace_id, and flags
sessions with 10+ tool calls but no send_message or react calls.
"""

import json
import sys

TOOL_CALL_THRESHOLD = 10
COMMUNICATION_TOOLS = {"send_message", "react"}


def main() -> None:
    context = json.loads(sys.stdin.readline())
    trace_id = context["trace_id"]
    events_path = context["events_path"]

    with open(events_path) as f:
        events = [json.loads(line) for line in f if line.strip()]

    turn_events = [e for e in events if e.get("session_id") == trace_id]
    tool_calls = [e for e in turn_events if e.get("type") == "tool_call"]
    comms = [e for e in tool_calls if e.get("tool") in COMMUNICATION_TOOLS]

    if len(tool_calls) >= TOOL_CALL_THRESHOLD and not comms:
        print(
            json.dumps(
                {
                    "signal": "silent_session",
                    "severity": "warn",
                    "message": (
                        f"Session had {len(tool_calls)} tool calls but "
                        f"sent no messages or reactions"
                    ),
                    "route": "log",
                }
            )
        )


if __name__ == "__main__":
    main()
