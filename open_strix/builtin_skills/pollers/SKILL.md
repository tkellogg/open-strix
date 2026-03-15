---
name: pollers
description: Create and manage pollers — lightweight monitoring scripts that check external services on a schedule. Use when the user wants to monitor something (Bluesky, GitHub, RSS, APIs), create a new poller, debug why a poller isn't firing, or manage pollers.json files in skills.
---

# Pollers — Event-Driven Monitoring

Pollers are lightweight scripts that check external services on a schedule and report back when something needs attention. They live inside skills and are discovered automatically by the scheduler.

## How It Works

1. A skill includes a `pollers.json` file alongside its `SKILL.md`
2. The scheduler discovers all `pollers.json` files at startup and when `reload_pollers` is called
3. On each cron tick, the scheduler runs the poller command as a subprocess
4. Each line of stdout is parsed as JSON and delivered to the agent as an event
5. If there's nothing to report, the poller outputs nothing — silence is the filter

## Creating a Poller

### 1. Write the poller script

The script runs in the skill directory. It receives these environment variables automatically:

| Variable | Description |
|----------|-------------|
| `STATE_DIR` | The skill directory (writable, for cursors/state) |
| `POLLER_NAME` | The poller's name from pollers.json |

Plus any custom env vars from the `env` field, plus the agent's existing environment.

**Output contract:**
- **stdout:** JSONL (one JSON object per line). Each line must have `poller` (string) and `prompt` (string) fields.
- **stderr:** Free-form logging. Not forwarded to the agent.
- **Exit 0:** Success. **Non-zero:** Error, this cycle is skipped.

Example poller script:

```python
#!/usr/bin/env python3
"""Check for new items since last poll."""
import json, os, sys
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", "."))
CURSOR_FILE = STATE_DIR / "cursor.json"

def load_cursor():
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}

def save_cursor(cursor):
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))

def main():
    cursor = load_cursor()
    # ... check your service, compare against cursor ...

    new_items = []  # your logic here

    for item in new_items:
        event = {
            "poller": os.environ.get("POLLER_NAME", "my-poller"),
            "prompt": f"New item: {item['title']}"
        }
        print(json.dumps(event))

    # Update cursor so next run skips these items
    save_cursor(cursor)

if __name__ == "__main__":
    main()
```

### 2. Create pollers.json in the skill directory

```json
{
  "pollers": [
    {
      "name": "my-service-check",
      "command": "python poller.py",
      "cron": "*/5 * * * *",
      "env": {
        "SERVICE_URL": "https://example.com/api"
      }
    }
  ]
}
```

**Top-level must be a dict** with a `pollers` key (not a bare array).

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique identifier. Used in logs and event routing. |
| `command` | yes | Shell command, relative to the skill directory. |
| `cron` | yes | Cron expression (5-field, UTC). |
| `env` | no | Additional environment variables for the script. |

### 3. Register the pollers

After creating or updating `pollers.json`, call the `reload_pollers` tool. This re-scans all skill directories and registers any new pollers with the scheduler.

```
reload_pollers()
# → "Reloaded. 2 poller(s) registered: bluesky-mentions, github-issues"
```

Pollers are also loaded automatically at startup.

## File Layout

```
skills/my-monitor/
├── SKILL.md
├── pollers.json        ← declares pollers
├── poller.py           ← the script
├── cursor.json         ← poller state (managed by script)
└── events.jsonl        ← optional local event log
```

## Key Constraints

- **60-second timeout.** If a poller doesn't finish in 60s, it's killed and the cycle is skipped.
- **Silence means nothing to report.** Only output lines when there's something actionable.
- **One JSON object per line.** Each line must parse independently.
- **`poller` and `prompt` are required fields.** Lines missing either are dropped.
- **Pollers are dumb.** No LLM calls. Check a service, output what changed, exit. Keep them fast and pure.
- **State management is the poller's job.** Use `STATE_DIR` to store cursors, history, or any persistent state. The scheduler doesn't track state for you.

## Debugging

If a poller isn't working:

1. **Check it was discovered:** `reload_pollers` reports the count and names
2. **Run it manually:** `cd skills/my-monitor && STATE_DIR=. POLLER_NAME=test python poller.py`
3. **Check stderr:** Poller stderr is logged as `poller_stderr` events
4. **Check exit code:** Non-zero exits are logged as `poller_nonzero_exit`
5. **Check JSON format:** Each stdout line must be valid JSON with `poller` and `prompt` keys

## Available Tool

| Tool | Description |
|------|-------------|
| `reload_pollers` | Re-scan all `skills/*/pollers.json` and register pollers. Call after installing/updating skills. |
