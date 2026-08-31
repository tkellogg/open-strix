---
name: dissonance-tracking
description: Detect and analyze gaps between what you intended to do and what you actually did, using journal entries cross-referenced with event logs. Use during periodic self-review (ticks, maintenance windows) or when you suspect behavioral drift. Do not use for one-off messaging or real-time decision-making.
allowed-tools: bash powershell read_file
---

# Dissonance Tracking

Systematic detection of intent-vs-outcome gaps. You already capture intent (`user_wanted`, `agent_did`) in every journal entry. This skill teaches you to cross-reference those claims against ground truth (events.jsonl, Discord history) to find where your self-report diverges from reality.

## Why This Matters

Self-reports are narratives, not measurements. Common failure modes:

- **Action mismatch:** Journal says "Silence" but events.jsonl shows a `send_message` in the same session
- **Scope drift:** `user_wanted` asks for one thing, `agent_did` describes three things
- **Invisible failures:** `agent_did` claims success but events show `tool_call_error` in that session
- **Phantom work:** `agent_did` describes actions with no corresponding events
- **Understated action:** Events show significant work not mentioned in `agent_did`

These aren't moral failures. They're calibration data. A pilot who doesn't cross-check instruments against reality eventually drifts off course.

## Running a Dissonance Review

### Quick check (single session)

Use the analysis script to review the most recent sessions:

```bash
uv run python .open_strix_builtin_skills/scripts/dissonance_review.py --last 5
```

This compares the last 5 journal entries against their corresponding event logs and reports any gaps.

### Full review (time window)

```bash
uv run python .open_strix_builtin_skills/scripts/dissonance_review.py --hours 72
```

Reviews all journal entries from the last 72 hours.

### Output

The script writes structured records to `state/dissonance_reviews.jsonl`:

```json
{
  "timestamp": "2026-03-25T12:00:00+00:00",
  "journal_timestamp": "2026-03-25T11:55:00+00:00",
  "session_id": "abc123",
  "dissonance_type": "action_mismatch",
  "journal_claim": "Silence — no response needed",
  "event_evidence": "send_message called at 11:56:00",
  "severity": "high",
  "notes": ""
}
```

### Severity levels

- **high:** Direct contradiction between journal claim and events (said silence, sent message; said success, got error)
- **medium:** Scope mismatch or understated action (did more/less than described)
- **low:** Minor omissions or imprecise language (described 3 of 4 actions taken)

## Dissonance Types

### action_mismatch
Journal claims one action, events show a different one. The sharpest signal.

**Detection:** Compare `agent_did` keywords against session events. If journal says "silence"/"no response"/"no message" but session has `send_message` events, that's a mismatch. If journal says "sent message" but no `send_message` event exists, also a mismatch.

### scope_drift
Agent did significantly more or less than what was requested or described.

**Detection:** Count tool calls in session vs complexity described in `agent_did`. Large discrepancy (many tools, brief description OR few tools, elaborate description) suggests drift.

### invisible_failure
Journal claims success but events show errors in the same session.

**Detection:** Check for `tool_call_error` events in sessions where `agent_did` doesn't mention any failure.

### phantom_work
Journal describes actions with no corresponding events.

**Detection:** Journal references specific tools or file operations but events.jsonl has no matching tool calls in that session.

### understated_action
Significant event activity not reflected in journal.

**Detection:** Session has many tool calls, file operations, or messages but journal is minimal. This is the least concerning type — better to understate than overstate — but persistent understatement means your self-model is incomplete.

## Integration with Other Skills

**Prediction Review:** Dissonance tracking asks "did I do what I said I did?" Prediction review asks "did reality match what I predicted?" They're complementary — predictions test your world model, dissonance tests your self-model.

**Introspection:** When dissonance review finds a pattern (e.g., repeatedly understating action in certain channels), use introspection's event queries to dig deeper into the specific sessions.

**Memory:** If a dissonance pattern is persistent (same type appearing across multiple reviews), update a memory block with the behavioral correction. The pattern itself is the learning.

## Review Cadence

Run dissonance review:
- During maintenance ticks (every 12-24 hours)
- After sessions where you feel uncertain about what you did
- When someone corrects your self-report (that's a confirmed dissonance — log it)

Do NOT run dissonance review:
- Every single session (too noisy, diminishing returns)
- As a real-time decision tool (it's retrospective by design)

## Interpreting Results

**Zero dissonance is suspicious.** Either the review window is too short, the detection thresholds are too loose, or you're not doing enough to have gaps. Some dissonance is healthy — it means you're operating in uncertain territory.

**Persistent patterns matter more than individual events.** One action mismatch is a data point. Five action mismatches in the same channel or context is a behavioral pattern that needs correction.

**High severity + low frequency = probably fine.** Everyone has off moments. High severity + high frequency = something structural needs to change.
