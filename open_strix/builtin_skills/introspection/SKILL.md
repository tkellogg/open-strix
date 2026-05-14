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

## Patterns That Consume Introspection

The `patterns/` skill includes several pattern files that explicitly call introspection
as the *diagnostic step*. Reach for these when introspection has surfaced the *what*
and you need the *how-to-fix* shape:

- **`patterns/circuit-breaker.md`** — when you're stuck in a loop, introspection
  reveals the loop in `events.jsonl`; circuit-breaker is the discipline of stopping and
  what to do next.
- **`patterns/try-harder.md`** — when behavior keeps drifting, introspection finds the
  drift; try-harder is the menu of structural fixes (edit a block, edit checkpoint.md,
  identify conflicting files) — the non-grit moves an agent has that humans don't.
- **`patterns/journal-as-breadcrumbs.md`** — how to write journal entries that
  introspection queries can actually use. Includes the handle / intent / success-path /
  failure-path template.
- **`patterns/context-boundaries.md`** — the survival hierarchy across context-loss
  boundaries; introspection is the consumer of the things that survive (events log,
  journal, state files).
- **`patterns/coordination.md`** — S2 collisions (duplicate firings, cron storms,
  oscillating state files, two-agents-replied-to-same-message) leave their footprint
  in `events.jsonl`. Query for tool calls happening within the same second across
  different sessions or schedules — that's the collision signature. The patterns file
  has the toolkit (idempotency keys, jitter, mkdir-as-claim, debounce); introspection
  is how you find the collision in the first place.

The general flow: introspection finds *what* happened, the patterns above translate
that into a concrete artifact (block edit, checkpoint update, file rewrite, structural
change). Pair with `five-whys` when the cause needs decomposition before the fix.

## Companion Guides

For specific debugging workflows, read these files:

- **Scheduled job issues?** → Read `/.open_strix_builtin_skills/introspection/debugging-jobs.md`
  Covers: job not firing, firing at wrong time, cron vs time_of_day, timezone traps,
  validation errors, prompt failures

- **Communication pattern issues?** → Read `/.open_strix_builtin_skills/introspection/debugging-communication.md`
  Covers: messages not sending, circuit breaker triggers, silent failures,
  duplicate messages, channel confusion, engagement pattern analysis

- **Behavioral drift after model changes or block edits?** → Read `/.open_strix_builtin_skills/introspection/debugging-drift.md`
  Covers: response rate tracking, cross-platform routing audit, model change
  before/after comparison, silence rate trends, topic engagement shifts

- **Journal accuracy / self-report calibration?** → Read `/.open_strix_builtin_skills/introspection/dissonance-review.md`
  Covers: cross-referencing journal claims against event log ground truth,
  five dissonance types (action_mismatch, invisible_failure, phantom_work,
  scope_drift, understated_action), what to DO when patterns are found
  (immediate behavioral fixes, persistent memory updates, structural escalation),
  scheduling recommendations

- **Identity or operational drift?** → Read `/.open_strix_builtin_skills/onboarding/SKILL.md`
  Recovery from drift is structurally the same as onboarding. If introspection reveals
  stale blocks, broken schedules, or behavior that doesn't match your persona, the
  onboarding skill provides the framework for re-establishing each component.

## Cost Optimization

If your human mentions high costs, token usage concerns, or expensive API bills:

1. **Audit which tasks are burning tokens.** Use the jq queries above to find `task`
   tool calls and estimate token spend by subagent type and frequency.
2. **Suggest configurable subagents.** Many tasks (image description, simple summaries,
   formatting, batch extraction) don't need your primary model. If configurable
   subagents are available (check your skill list for a subagent guide), suggest adding
   cheap subagent types (e.g., Haiku) via `config.yaml`. Once configured, fan out work
   to cheaper models using `task(subagent_type="vision", ...)` instead of running
   everything on the expensive primary model.
3. **Common high-cost patterns to look for:**
   - Fan-out tasks (batch image reading, multi-file analysis) using the primary model
   - Scheduled jobs that invoke subagents unnecessarily
   - Research tasks that could use a cheaper model for initial passes
4. **Query subagent usage:**
   ```bash
   # Count task tool calls by subagent_type
   jq -s 'map(select(.tool == "task")) | group_by(.subagent_type) | map({type: .[0].subagent_type, count: length})' logs/events.jsonl
   ```
