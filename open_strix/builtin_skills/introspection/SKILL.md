---
name: introspection
description: Diagnose agent behavior using event logs, journal history, and scheduler state. Use when you need to understand why something went wrong, review your own patterns, audit scheduled jobs, or debug communication issues. Do not use for one-off messaging or memory management (use the memory skill instead).
---

# Introspection

You are a stateful agent. Your behavior leaves traces in structured logs. This skill
teaches you to read those traces to diagnose problems, understand your own patterns,
and improve.

## Source of Truth Hierarchy

1. **`logs/events.jsonl`** — Ground truth. Every tool call, error, and scheduler
   event is recorded here with timestamps and session IDs.
2. **`logs/journal.jsonl`** — Your interpretation of what happened. Useful for
   intent and predictions, but it's narrative, not fact.
3. **Discord message history** — What was actually sent and received. Use
   `list_messages` to verify.
4. **`scheduler.yaml`** — Current scheduled job definitions.
5. **Memory blocks** — Your current beliefs about the world. May be stale.

When sources conflict, trust events > Discord history > journal > memory blocks.

## Key Log Schemas

### events.jsonl

Each line is a JSON object:

```json
{
  "timestamp": "2026-03-01T12:00:00+00:00",
  "type": "tool_call",
  "session_id": "abc123",
  "tool": "send_message",
  "channel_id": "123456",
  "sent": true,
  "text_preview": "first 300 chars..."
}
```

Common event types:
- `tool_call` — Any tool invocation (check `tool` field for which one)
- `tool_call_error` — A tool that failed (check `error_type`)
- `send_message_loop_detected` — Circuit breaker caught repeated messages
- `send_message_loop_hard_stop` — Turn terminated for safety
- `scheduler_reloaded` — Jobs were reloaded from scheduler.yaml
- `scheduler_invalid_job` — A job failed validation
- `scheduler_invalid_cron` — Bad cron expression
- `scheduler_invalid_time` — Bad time_of_day value

### journal.jsonl

Each line:

```json
{
  "timestamp": "2026-03-01T12:00:00+00:00",
  "session_id": "abc123",
  "channel_id": "123456",
  "user_wanted": "what the human asked for",
  "agent_did": "what you actually did",
  "predictions": "what you think will happen next"
}
```

### scheduler.yaml

```yaml
jobs:
  - name: my-job
    prompt: "Do the thing"
    cron: "0 */2 * * *"        # OR time_of_day, not both
    channel_id: "123456"       # optional
```

Cron expressions are evaluated in **UTC**. `time_of_day` is `HH:MM` in UTC.

## How to Query Events

### With jq (preferred)

```bash
# Last 20 events
tail -n 20 logs/events.jsonl | jq .

# All errors in the last session
jq -s 'sort_by(.timestamp) | group_by(.session_id) | last | map(select(.type | test("error")))' logs/events.jsonl

# All send_message calls in a session
jq -s 'map(select(.session_id == "SESSION_ID" and .tool == "send_message"))' logs/events.jsonl

# Events by type, counted
jq -s 'group_by(.type) | map({type: .[0].type, count: length}) | sort_by(-.count)' logs/events.jsonl

# Scheduler events only
jq -s 'map(select(.type | startswith("scheduler")))' logs/events.jsonl

# Find sessions with errors
jq -s '[.[] | select(.type | test("error"))] | group_by(.session_id) | map({session: .[0].session_id, errors: length}) | sort_by(-.errors)' logs/events.jsonl
```

### With Python (if jq unavailable)

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from collections import Counter

events = [json.loads(line) for line in Path("logs/events.jsonl").read_text().splitlines() if line.strip()]
type_counts = Counter(e.get("type", "unknown") for e in events)
for t, c in type_counts.most_common(20):
    print(f"{c:>6}  {t}")
PY
```

## Cross-Referencing with Memory Skill

The memory skill (`/.open_strix_builtin_skills/memory/SKILL.md`) covers:
- **When and how to write memory blocks** — criteria for block vs file storage
- **Maintenance** — block size monitoring, pruning, file frequency analysis
- **File organization** — cross-references between blocks and state files

Use introspection to find problems. Use memory to fix the persistent ones (update
blocks, reorganize files, add cross-references).

The file frequency report (`/.open_strix_builtin_skills/scripts/file_frequency_report.py`)
bridges both skills — it reads events.jsonl to find which files you access most,
informing both debugging (are you reading the same file repeatedly?) and memory
optimization (should hot files become blocks?).

## Companion Guides

For specific debugging workflows, read these files:

- **Scheduled job issues?** → Read `/.open_strix_builtin_skills/introspection/debugging-jobs.md`
  Covers: job not firing, firing at wrong time, cron vs time_of_day, timezone traps,
  validation errors, prompt failures

- **Communication pattern issues?** → Read `/.open_strix_builtin_skills/introspection/debugging-communication.md`
  Covers: messages not sending, circuit breaker triggers, silent failures,
  duplicate messages, channel confusion, engagement pattern analysis
