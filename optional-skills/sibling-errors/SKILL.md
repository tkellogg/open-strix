---
name: sibling-errors
description: Poll sibling agents' events.jsonl files for errors. Emits one prompt per new error-kind-transition per agent (dedup = one-shot-until-different-kind). Triggers the host agent to diagnose and act.
---

# Sibling Error Poller

Watches other agents' `events.jsonl` logs, detects error-class events, and emits one prompt per new error-kind-transition per agent. A storm of 50 `insufficient_balance` errors yields one prompt with count, not 50 pings.

## What it catches

- `type: "error"` — API-level errors. Kind extracted from error string (e.g. `error:insufficient_balance`, `error:rate_limit`) or falls back to `error_class`.
- `type: "poller_nonzero_exit"` — sibling's own pollers failing. Kind = `poller_fail:<name>`.
- `type: "agent_final_message_discarded"` — narrated-send_message silent failures.

Everything else (`tool_call`, `file_read`, `poller_stderr`, etc.) is ignored.

## Installation

Copy this skill directory into your agent's `skills/` folder, then call `reload_pollers`.

## Setup

### 1. Configure SIBLINGS env

Set `SIBLINGS` to a comma-separated list of `name=path` pairs pointing to each sibling's `events.jsonl`:

```
SIBLINGS=motley=/home/user/jester/logs/events.jsonl,verge=/home/user/open-buddy/logs/events.jsonl
```

No default — the poller exits quietly if unset.

### 2. Adjust cron

Ships with `*/10 * * * *`. Tune based on how fast you want to react vs scheduler load.

### 3. Reload

Call `reload_pollers` to register.

## Dedup semantics

Per agent, cursor tracks `last_emitted_kind`. When scanning new events, consecutive same-kind errors coalesce into one transition. We only emit when the kind **changes** from what we last pinged. Same kind after a quiet period = still dedup'd — we already surfaced it.

## Prompt shape

Each emitted prompt is a self-contained trigger for the host agent:

```
Sibling error: [motley] error:insufficient_balance — 47 occurrence(s) since 2026-04-20T17:18:56Z.
Sample: Error code: 500 - {'type': 'error', 'error': {'type': 'api_error', 'message': 'insufficient balance (1008)'}}.
Diagnose and act: ship a fix (code/config) or surface to the operator (credits/infra).
```

The prompt carries enough context that the agent can invoke, read files, and either ship a code/config fix or surface the problem to its operator.

## State files

- `sibling_errors_cursor.json` — per-agent `{last_ts, last_emitted_kind}`
- `events.jsonl` — local log of emitted prompts (for audit)

## First run

Cursor empty → scans the file, seeds cursor from history, emits **nothing**. Subsequent runs see only genuinely new kind-changes. This avoids flooding the host on day one.

## Requires

- Read access to each sibling's `events.jsonl` path
- No external dependencies (stdlib only)
