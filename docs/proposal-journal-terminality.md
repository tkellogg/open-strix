# Proposal: Journal Terminality and Turn Recovery

**Status:** Draft / discussion
**Author:** (your name)
**Date:** 2026-04-10
**Related files:** `open_strix/app.py`, `open_strix/tools.py`, `open_strix/prompts.py`

## Problem

Long-running agent turns sometimes produce **duplicate responses**: the agent sends a "task complete" message, journals it, then continues working and sends a second "task complete" message with a second journal entry. Both messages describe the same logical task completion.

### Evidence (from 2026-04-10 02:17 UTC)

From `logs/events.jsonl` — all events below occurred within a **single** `agent_invoke_start` window (line 776 in the event log; the next `agent_invoke_start` is not until line 883 at 02:20:09):

```
02:17:15  tool_call tool=send_message   ← "✅ LABELS TASK COMPLETE" (msg #1)
02:17:21  tool_call tool=journal        ← journal entry #1
02:17:28  tool_call tool=bash
02:17:30  tool_call tool=react          ← ✨ reaction
02:17:37  tool_call tool=bash
02:17:41  tool_call tool=bash
02:17:48  tool_call tool=send_message   ← "✅ Task #315 COMPLETE" (msg #2, duplicate)
02:17:55  tool_call tool=journal        ← journal entry #2, duplicate
```

`logs/journal.jsonl` confirms two entries 34 seconds apart, both describing the same label-system work with slightly different wording.

### Root Cause

This is **not** caused by the post-turn block-validation re-invoke in `app.py:846-872`. There is only one `agent_invoke_start` event for the entire duplicate window, which means both `send_message` and both `journal` tool calls happened inside a single `agent.ainvoke()`.

The LLM itself chose to call `send_message` and `journal` twice in one turn. The prompt in `prompts.py` encourages this pattern:

- Line 29: "Call `journal` exactly once per turn" — an unenforced rule.
- Line 35: "It's totally fine to send a message, do some work, and then send another message, if that's what the moment warrants." — actively encourages multi-message turns.

There is no concept of "I am done now." The agent treats each `send_message` as an independent completion, and nothing in the code or prompt prevents it from journaling twice.

## Current State Machine (Broken)

```
               ┌────────┐   event received   ┌────────┐
               │  IDLE  │──────────────────►│ WORKING│◄───────────┐
               └────────┘                    └───┬────┘            │
                    ▲                            │                 │
                    │                    ┌───────┼───────┐         │
                    │                    │       │       │         │
                    │                bash/etc  send_   journal     │
                    │                          message             │
                    │                    │       │       │         │
                    │                    │       ▼       │         │
                    │                    │  ┌─────────┐  │         │
                    │                    │  │MESSAGED │──┘         │
                    │                    │  └────┬────┘            │
                    │                    │       │                 │
                    │                    │  send_message           │
                    │                    │  (again, allowed)       │
                    │                    │       │                 │
                    │                    ▼       ▼                 │
                    │                  ┌───────────┐               │
                    │                  │ JOURNALED │───────────────┘
                    │                  └─────┬─────┘   CAN KEEP
                    │                        │          WORKING!
                    │          final text    │
                    │    ┌────────┐◄─────────┘
                    └────│  DONE  │
                         └────────┘
```

**Problems:**
- `JOURNALED → WORKING` is allowed (no guard)
- `JOURNALED → MESSAGED` is allowed (no guard)
- Multiple `journal` calls are allowed (prompt says "once", nothing enforces it)
- Nothing distinguishes a progress message from a completion announcement

## Failure Modes to Design For

| # | Failure mode | Current behavior | Severity |
|---|---|---|---|
| 1 | Agent sends "task complete" twice in one turn | Duplicates in Discord + journal (the observed bug) | Medium — wastes tokens, confuses the user |
| 2 | Agent never calls `journal` | Turn ends silently; no record of what happened | **High** — silent data loss |
| 3 | Agent hits LangGraph recursion limit (default 25) | Turn truncates silently | **High** — silent data loss |
| 4 | Agent API / tool exception mid-turn | Exception propagates; no journal | **High** — silent data loss |
| 5 | Agent journals too early (mid-task) | Not currently possible, but becomes a risk with journal-as-terminal | Medium |

**Silent failures are the worst outcome.** Any design must ensure that every turn produces a visible record, and that failures are surfaced to the user rather than swallowed.

## Proposed Design

Three principles:

1. **`journal` is terminal.** Once called, no more tool calls are allowed in the turn. This is enforced in code, not just prompt.
2. **Every turn produces a journal entry, always.** If the agent didn't journal, the system reconstructs one from the event log and marks it as system-generated.
3. **Failures are surfaced to the user.** Silent recovery is still silent failure. The user is told when a turn didn't complete cleanly.

### Complete State Machine (Proposed)

```
                       ┌────────┐   event received
                       │  IDLE  │─────────────────────┐
                       └────────┘                      │
                            ▲                          ▼
                            │                    ┌─────────┐
                            │                    │ WORKING │◄──────────┐
                            │                    └────┬────┘            │
                            │                         │                 │
                            │                ┌────────┼────────┐        │
                            │                │        │        │        │
                            │           bash/etc  send_msg   react      │
                            │                │        │        │        │
                            │                │        ▼        │        │
                            │                │   ┌────────┐    │        │
                            │                │   │MESSAGED│────┘        │
                            │                │   └───┬────┘ more work   │
                            │                │       │                  │
                            │                ▼       ▼                  │
                            │             ┌─────────────┐               │
                            │             │   journal   │               │
                            │             │ (first call)│               │
                            │             └──────┬──────┘               │
                            │                    │                      │
                            │                    ▼                      │
                            │             ┌────────────┐                │
                            │             │ JOURNALED  │                │
                            │             │ (terminal) │                │
                            │             └─────┬──────┘                │
                            │                   │                       │
                            │                   │ final text            │
                            │                   │  (any tool call       │
                            │                   │   returns error:      │
                            │                   │   "already journaled")│
                            │                   ▼                       │
                            │              ┌────────┐                   │
                            └──────────────│  DONE  │                   │
                                           └────────┘                   │
                                                                        │
    ═══════════════════════ FAILURE PATHS ══════════════════════════    │
                                                                        │
    From WORKING or MESSAGED, turn can exit WITHOUT journaling:         │
    • LangGraph recursion limit hit                                     │
    • ainvoke raises exception (API error, tool crash)                  │
    • LLM just stops calling tools (forgot to journal)                  │
                                                                        │
                                │                                       │
                                ▼                                       │
                     ┌─────────────────────┐                            │
                     │  RECOVERY_NEEDED    │                            │
                     │  (no journal yet)   │                            │
                     └──────────┬──────────┘                            │
                                │                                       │
                      1. Log anomaly event                               │
                      2. Collect tool-call history from this turn        │
                      3. Write PROVISIONAL journal entry                 │
                         (marked: agent_journaled=false)                 │
                      4. send_message to user:                           │
                         "⚠ I didn't finish cleanly. Here's what I did:  │
                          [summary]. Attempting recovery..."             │
                                │                                       │
                                ▼                                       │
                     ┌─────────────────────┐                            │
                     │ RECOVERY_IN_PROGRESS│                            │
                     │  (retry_count ≤ 2)  │                            │
                     └──────────┬──────────┘                            │
                                │                                       │
                        Fresh ainvoke with prompt:                       │
                        "Your previous turn ended without                │
                        journaling. Tool history: [...]. Either          │
                        journal what happened, or tell the user          │
                        what went wrong and why."                        │
                                │                                       │
                    ┌───────────┼───────────┐                            │
                    │           │           │                            │
                    ▼           ▼           ▼                            │
             journals       still no      exception                      │
             cleanly        journal       again                          │
                    │           │           │                            │
                    │           └─────┬─────┘                            │
                    │                 │                                  │
                    │           retry_count++                            │
                    │                 │                                  │
                    │          ┌──────┴──────┐                           │
                    │          │             │                           │
                    │      < max          >= max                         │
                    │          │             │                           │
                    │          └─────────────┼──── back to WORKING ──────┘
                    │                        │
                    ▼                        ▼
            ┌────────────┐          ┌──────────────────┐
            │ JOURNALED  │          │ HARD_FAILURE     │
            │ (recovered)│          │ • provisional    │
            └─────┬──────┘          │   journal stays  │
                  │                 │ • alert user     │
                  ▼                 │   explicitly:    │
            ┌────────┐              │   "I lost state, │
            │  DONE  │              │    please check  │
            └────────┘              │    what I did"   │
                                    └────────┬─────────┘
                                             │
                                             ▼
                                        ┌────────┐
                                        │  DONE  │
                                        └────────┘
```

### Behavior Comparison

| Scenario | Current | Proposed |
|---|---|---|
| Agent sends "task complete" twice | Duplicates (the bug) | Second tool call after journal returns error |
| Agent forgets to journal | Silent data loss | Provisional journal + user notification + retry |
| Recursion limit hit | Silent truncation | Caught → recovery path |
| API error mid-turn | Exception propagates | Caught → recovery path |
| Agent journals then tries more work | Nothing prevents it | Tool error teaches agent it's done |
| Progress updates (multiple messages) | Allowed, encouraged | Still allowed (unchanged) |

### What Stays the Same

- **Multiple `send_message` calls per turn are still fine** for genuine progress updates ("starting X", "found Y", "done"). The fix targets duplicate *completions*, not interactivity.
- **Existing `send_message` circuit breaker** (`tools.py:235`) stays in place as a secondary rate limit.
- **Post-turn block validation re-invoke** (`app.py:846-872`) is untouched — it runs as a separate mini-turn with its own journal scope.
- **Post-turn git sync** (`app.py:874`) is untouched.

## Implementation Plan

Three changes, implementable in stages. Each stage is independently valuable and testable.

### Stage 1 — Journal terminality (fixes the observed bug)

**File:** `open_strix/tools.py`

1. Add instance state: `self._has_journaled_this_turn = False`.
2. In the `journal` tool function: if `_has_journaled_this_turn` is already true, return an error string without writing. Otherwise write the journal entry and set the flag.
3. In every *other* tool function, check `_has_journaled_this_turn` at the top and return an error string if set: `"This turn has already been journaled. No further actions are allowed. End your turn."`

**File:** `open_strix/app.py`

4. In `_process_event`, reset `_has_journaled_this_turn = False` at turn start (alongside the existing `_current_turn_sent_messages = []` reset on line 823).

### Stage 2 — Prompt update (reinforces the new rule)

**File:** `open_strix/prompts.py`

Replace the Flow section (lines 25-30) with:

```
Flow:
1. Read files from `state/` as necessary to remember any context needed
2. Perform actions & write files
3. Call `send_message` to respond to the user, or `react` to quietly acknowledge.
   You may send multiple messages per turn for progress updates.
4. When ALL work for this turn is complete, call `journal` exactly once.
   This is your FINAL action. After journaling, no other tools will work —
   any attempt will return an error. Journal = "I am done with this turn."
5. Write final response. This will be discarded. Your human won't see it.
```

Replace line 35 with:

```
- Multiple messages in a turn are fine for progress updates (e.g., "starting X",
  "found something interesting", "done"). But never send the same status twice —
  if you already said the task is complete, don't say it again. Journal once,
  at the very end.
```

### Stage 3 — Recovery flow (fixes silent data loss)

**File:** `open_strix/app.py`

1. In `_process_event`, wrap `agent.ainvoke()` in `try/except`. Catch `Exception` and route to the recovery path.
2. After `ainvoke` returns (or raises), check `_has_journaled_this_turn`. If false:
   - Collect tool-call history for this turn from `events.jsonl` (events since the last `agent_invoke_start`).
   - Build a provisional journal entry: `user_wanted = event.prompt`, `agent_did = <summary of tool calls>`, with a new field `agent_journaled: false`.
   - Write the provisional journal entry via `append_journal`.
   - Send a user-visible message: `"⚠ I didn't finish cleanly. Attempting recovery..."`
   - Re-invoke the agent with a recovery prompt that includes the tool-call summary and asks it to either journal cleanly or explain what went wrong.
   - Bound recovery to **at most 2 retries**.
3. If recovery still fails after max retries:
   - Log `turn_hard_failure` event.
   - Keep the provisional journal entry in place.
   - Send a user-visible message: `"⚠ I lost state during this turn. Please review what I did: [summary]"`.

**New helper functions:**

- `_collect_turn_tool_history(since_ts) -> list[dict]` — reads `events.jsonl` tail and filters events since the given timestamp.
- `_summarize_tool_history(events) -> str` — builds a human-readable summary for the provisional journal and recovery prompt.
- `_write_provisional_journal(event, tool_history) -> None` — writes the journal entry with `agent_journaled=false`.

**Journal schema addition:**

Add an optional `agent_journaled: bool` field to journal entries (default `true` for backward compatibility). Future turns reading the journal can see `agent_journaled=false` entries and know that turn didn't complete cleanly.

## Open Questions

1. **Should the recovery prompt get the tool history verbatim, or a compressed summary?** Verbatim is more faithful but burns tokens; a summary is cheaper but might omit the detail the agent needs to explain what went wrong.

2. **Should `agent_journaled=false` entries be shown in future turns' context differently?** E.g., highlighted as "this turn failed" so the agent knows to be extra careful or to ask the user for confirmation.

3. **Should the `HARD_FAILURE` path also attempt to roll back side effects?** Probably out of scope — we don't have transaction boundaries around file writes and message sends. Best we can do is alert the user.

4. **Recursion limit tuning.** LangGraph's default is 25 steps. For long tasks, that may be too low. Worth making configurable, separate from this proposal.

5. **Does the LLM, after receiving the "already journaled" error, actually stop gracefully?** Worth testing — if the model gets confused and thrashes, we may need a harder cutoff at the graph level (e.g., short-circuit the LangGraph loop once `_has_journaled_this_turn` is set, rather than letting the model see tool errors).

## Testing Strategy

- **Unit tests** for the journal terminality guard: call `journal` twice, assert second call returns error; call `bash` after `journal`, assert error.
- **Unit tests** for `_collect_turn_tool_history` and `_summarize_tool_history` against a fixture `events.jsonl`.
- **Integration test** that simulates a recursion-limit-exceeded turn and verifies the provisional journal + user notification appear.
- **Integration test** that simulates a clean turn and verifies only one journal entry is written.
- **Manual test** replaying a long-running task like the 2026-04-10 label task and confirming no duplicates occur.

## Rollout

Stages 1 and 2 can ship together and independently fix the duplicate bug. Stage 3 is a larger change and can ship separately once Stage 1 is validated in practice.
