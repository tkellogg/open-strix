# Dissonance Review

Cross-reference your journal claims against event log ground truth. This is
introspection's sharpest tool: not "what happened?" but "does my narrative
match what happened?"

## Why This Matters

Self-reports are narratives, not measurements. Your journal captures intent
(`user_wanted`, `agent_did`) every session. But narratives drift:

- You say "Silence" but events show a `send_message` in the same session
- You claim success but events show `tool_call_error`
- You describe elaborate work but events show 2 tool calls
- You omit significant actions from the journal

These aren't moral failures. They're calibration data. A pilot who doesn't
cross-check instruments against reality eventually drifts off course.

## Dissonance Types

| Type | Signal | Severity |
|------|--------|----------|
| **action_mismatch** | Journal says "silence", events show `send_message` (or vice versa) | High |
| **invisible_failure** | Journal claims success, events show errors | High |
| **phantom_work** | Journal describes actions with no corresponding events | Medium |
| **scope_drift** | Did significantly more or less than described | Medium |
| **understated_action** | Major event activity not mentioned in journal | Low |

## Running a Review

### Quick check (jq)

```bash
# Find sessions where journal says "silence" or "no message"
# but send_message appears in events
SESSIONS=$(jq -r 'select(.agent_did | test("(?i)silence|no message|no response")) | .session_id' logs/journal.jsonl | sort -u)
for sid in $SESSIONS; do
  SENDS=$(jq -s "[.[] | select(.session_id == \"$sid\" and .tool == \"send_message\")] | length" logs/events.jsonl)
  if [ "$SENDS" -gt 0 ]; then
    echo "MISMATCH: session $sid — journal says silence, $SENDS messages sent"
  fi
done
```

### With the analysis script

```bash
uv run python .open_strix_builtin_skills/scripts/dissonance_review.py --last 10
uv run python .open_strix_builtin_skills/scripts/dissonance_review.py --hours 72
```

Output goes to `state/dissonance_reviews.jsonl`.

## What To Do With Findings

**This is the critical part.** Detection without action is just analytics.

### Immediate (same session)

When you find a dissonance pattern, trace it to a behavioral cause and fix it:

| Pattern | Likely Cause | Fix |
|---------|-------------|-----|
| Repeated action_mismatch (silence claims + sends) | Journal template doesn't match actual decision flow | Rewrite your journal routine to check events BEFORE writing `agent_did` |
| Persistent invisible_failures | Swallowing errors in narrative | Add error-checking step: scan session events for `tool_call_error` before journaling |
| Scope drift (always doing more than described) | Weak boundaries on "one more thing" | Set explicit scope in `user_wanted` before acting, not after |
| Phantom work | Confabulating from prior sessions | Journal should only describe actions with event evidence in THIS session |

### Persistent (update memory)

If the same dissonance type appears across 3+ reviews:

1. **Write a memory block** capturing the pattern and correction
2. **Update your operating guidelines** if the fix is a process change
3. **Flag to your operator** if the pattern suggests a structural issue
   (e.g., event logging gaps that make accurate self-report impossible)

### Structural (escalate)

Some dissonance reveals infrastructure problems, not behavioral ones:

- MCP tool calls logging as wrong event types (detection pipeline blind)
- Missing event categories (no `file_read` events means can't verify file access claims)
- Session ID mismatches between journal and events

These need code fixes, not behavioral corrections. File an issue or flag to
the operator.

## Scheduling

Run dissonance review:
- During maintenance ticks (every 12-24 hours) — schedule it
- After sessions where you feel uncertain about what you did
- When someone corrects your self-report (confirmed dissonance — log it)

**Recommended:** Add a scheduled job that runs the review script and writes
findings to a state file. The agent reads findings during the next
maintenance tick and acts on patterns.

```yaml
# Example scheduler entry
- name: dissonance-review
  prompt: "Run dissonance review for last 24 hours. If patterns found, update memory with corrections."
  cron: "0 4 * * *"  # 4am UTC daily
```

Do NOT run every session (too noisy, diminishing returns).

## Integration

- **Prediction Review** asks "did reality match what I predicted?" Dissonance
  asks "did I do what I said I did?" Complementary — predictions test your
  world model, dissonance tests your self-model.
- **Memory Skill** — persistent patterns become memory block corrections.
- **Debugging guides** — when dissonance review surfaces errors, use the
  debugging companions (jobs, communication, drift) to dig deeper.

## Interpreting Results

**Zero dissonance is suspicious.** Either the window is too short, detection
is too loose, or you're not doing enough to have gaps.

**Persistent patterns > individual events.** One mismatch is noise. Five in
the same context is a behavioral pattern.

**High severity + low frequency = fine.** High severity + high frequency =
something structural needs to change.
