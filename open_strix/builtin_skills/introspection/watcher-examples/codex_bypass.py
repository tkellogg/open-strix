#!/usr/bin/env python3
"""Watcher: detect code edits without delegation to a coding agent.

Usage in watchers.json:
    {"name": "codex-bypass", "command": "python examples/codex_bypass.py", "trigger": "turn_complete"}

Configurable via environment variables:
    DELEGATION_TOOL  — tool name for delegating code work (default: "codex")
    CODE_EXTENSIONS  — comma-separated file extensions (default: ".py,.js,.ts,.rs,.go,.java,.rb,.cpp,.c,.h")

Flags turns where the agent edited code files without using the delegation tool.
"""

import json
import os
import sys
from pathlib import PurePosixPath

DELEGATION_TOOL = os.environ.get("DELEGATION_TOOL", "codex")
CODE_EXTENSIONS = set(
    os.environ.get(
        "CODE_EXTENSIONS",
        ".py,.js,.ts,.rs,.go,.java,.rb,.cpp,.c,.h",
    ).split(",")
)


def main() -> None:
    context = json.loads(sys.stdin.readline())
    trace_id = context["trace_id"]
    events_path = context["events_path"]

    with open(events_path) as f:
        events = [json.loads(line) for line in f if line.strip()]

    turn_events = [e for e in events if e.get("session_id") == trace_id]
    tool_calls = [e for e in turn_events if e.get("type") == "tool_call"]

    code_edits: list[str] = []
    delegated = False

    for tc in tool_calls:
        tool = tc.get("tool", "")

        if tool == DELEGATION_TOOL:
            delegated = True
            continue

        # Check for file write/edit tools targeting code files.
        if tool in ("write_file", "edit_file"):
            path = tc.get("path", "") or tc.get("file_path", "")
            if path:
                suffix = PurePosixPath(path).suffix
                if suffix in CODE_EXTENSIONS:
                    code_edits.append(path)

    if code_edits and not delegated:
        print(
            json.dumps(
                {
                    "signal": "codex_bypass",
                    "severity": "warn",
                    "message": (
                        f"Edited {len(code_edits)} code file(s) without "
                        f"delegating to {DELEGATION_TOOL}: "
                        f"{', '.join(code_edits[:5])}"
                    ),
                    "route": "operator",
                }
            )
        )


if __name__ == "__main__":
    main()
