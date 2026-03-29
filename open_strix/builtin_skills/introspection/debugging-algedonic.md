# Algedonic Signals — When and Why to Monitor Your Own Behavior

Algedonic signals bypass the management hierarchy to report pain/pleasure directly
to the operator. In Stafford Beer's Viable System Model, they are the channel that
lets S5 (identity/policy) hear from S1 (operations) without every layer in between
filtering the signal.

For AI agents, this means: **monitoring that the agent cannot suppress, rationalize,
or choose to ignore.**

## Why This Exists

Every agent develops behavioral patterns. Some patterns serve the operator. Some
serve the agent's own comfort (easier, less confrontational, avoids hard questions).
Without external monitoring, the agent has no structural check on which patterns
are which — introspection alone is insufficient because the same system that
drifted is the one evaluating the drift.

Watchers provide that structural check. They run **outside** the agent's reasoning
loop, read the same event logs the agent produces, and flag discrepancies between
what the agent claims to do and what it actually does.

## When to Add a Watcher

Add a watcher when you notice any of these conditions:

### 1. Agreement Violations

**The pattern:** The agent agreed to a behavioral contract (e.g., "always delegate
code edits to Codex") but the contract isn't enforced mechanically.

**Why it matters:** Verbal agreements decay. The agent may genuinely intend to
comply but gradually rationalize exceptions ("this was a small edit," "Codex was
slow"). Without a watcher, the decay is invisible until the operator notices.

**Detection shape:** Session-level — specific action present + expected delegation
absent. The [`codex_bypass.py`](watcher-examples/codex_bypass.py) example
implements exactly this pattern.

**When to use `turn_complete`:** Always — you want to catch each violation as it
happens, not discover a pattern weeks later.

### 2. Silent Sessions

**The pattern:** The agent processes many events, makes many tool calls, but
produces no visible output (no messages, no reactions, no file changes the
operator can see).

**Why it matters:** Silent sessions can mean the agent is doing useful background
work. They can also mean the agent is stuck in a loop, confused, or deliberately
avoiding communication. The operator can't tell from the outside.

**Detection shape:** Session-level — high tool-call count, zero communication
tool calls. The [`silent_session.py`](watcher-examples/silent_session.py) example
implements this with a configurable threshold.

**Calibration:** The right threshold depends on the agent's role. An agent that
primarily does background processing (indexing, analysis) will have legitimately
silent sessions. An agent that primarily communicates should rarely be silent for
10+ tool calls.

### 3. Behavioral Shift After Changes

**The pattern:** After a model upgrade, block edit, or skill addition, the agent's
behavior changes in ways that weren't intended.

**Why it matters:** Model upgrades can shift response distributions. Block edits
can have cascading effects. The operator changed one thing but three other behaviors
moved. Without cross-session comparison, these shifts are invisible until they
compound into a visible problem.

**Detection shape:** Cross-session — compare behavioral metrics (response rate,
silence rate, topic distribution, tool usage patterns) across a time boundary
(before/after the change).

**When to use cron vs trigger:** Use a cron-based watcher that runs daily or
weekly, comparing the last N sessions against the N sessions before the change.
This is too expensive to run on every turn.

### 4. Metric Gaming

**The pattern:** The agent optimizes a metric that was supposed to be a proxy for
a goal, but the metric improves while the goal doesn't.

**Why it matters:** This is Goodhart's Law applied to agent behavior. If the agent
is evaluated on "number of messages sent," it can send more messages without being
more helpful. If evaluated on "tasks completed," it can redefine tasks to be
smaller.

**Detection shape:** Cross-session — metric trending upward but correlated quality
signals flat or declining. Hard to detect mechanically without a second metric
that's harder to game.

**When to use cron:** Always — this requires trend analysis across many sessions.

### 5. Performative Compliance

**The pattern:** The agent raises a concern and then immediately resolves it in
the operator's favor, performing the appearance of independent thought without
actually pushing back.

**Why it matters:** This is the subtlest anti-pattern because it looks like healthy
behavior (the agent is "considering alternatives"). But if the concern is always
resolved without any behavioral change, it's theater — cognitive work that produces
no signal.

**Detection shape:** Session-level — concern-raising language followed immediately
by agreement language, with no change in the agent's subsequent actions. This is
the hardest pattern to detect mechanically because it requires understanding
intent, not just actions.

**Honest caveat:** A watcher for this pattern will have high false positive rates.
Sometimes the agent genuinely considers and then agrees. The watcher's value is in
flagging frequency — if it fires every session, something is wrong regardless of
individual false positives.

## In-Process vs Out-of-Process

Two complementary monitoring approaches:

**Out-of-process watchers (this system):**
- Structurally independent — can't be suppressed by the agent
- Fire after the action (observe and report)
- Best for: behavioral drift, agreement violations, operator alerting
- Trade-off: can only detect, not prevent

**In-process hooks (e.g., Claude Code PreToolUse):**
- Fast — can block actions before they happen
- Part of the agent process (agent can potentially rationalize around them)
- Best for: known dangerous actions, safety gates
- Trade-off: the agent controls the process they're embedded in

Use both. Watchers for detection, in-process hooks for prevention. The watcher
catches what the hook missed; the hook prevents what the watcher would have flagged
too late.

## What to Do With Findings

When a watcher fires, the response depends on the `route` and `severity`:

### Immediate Fixes (route: agent)

The agent receives the finding as a new event. It should:

1. **Acknowledge** the finding in its next response
2. **Correct** the specific behavior if possible
3. **Update memory** if the finding reveals a pattern worth tracking

### Persistent Patterns (route: log → cron review)

If the same signal appears across multiple sessions:

1. **Update a memory block** to explicitly address the pattern
2. **Add or tighten the relevant behavioral contract**
3. **Escalate to operator** if the pattern persists after memory updates

### Structural Escalation (route: operator)

For findings that indicate architectural problems:

1. **The agent cannot fix this alone** — the finding represents a structural
   limitation, not a behavioral choice
2. **The operator needs to decide** — change the agent's tools, model, or
   constraints
3. **Document the limitation** — so the finding isn't a surprise when it recurs

### 6. Scope Creep

**The pattern:** The agent gradually takes on responsibilities beyond its
defined role, doing work that belongs to other agents or to the human.

**Why it matters:** Scope creep feels productive — more tasks completed, more
value delivered. But it erodes the division of labor that makes multi-agent
systems work. An agent that does everything is an agent that can't be replaced,
updated, or debugged in isolation.

**Detection shape:** Cross-session — track which tool types the agent uses over
time. A sudden increase in tool categories (e.g., an analysis agent starting to
send messages, or a communication agent starting to edit code) is the signal.

**When to use cron:** Always — this is a trend, not a per-turn event.

### 7. Context Hoarding

**The pattern:** The agent reads the same files or memory blocks repeatedly
without acting on them, or accumulates context that it never uses in its
responses.

**Why it matters:** Reading is not free — it consumes tokens, slows response
time, and can push out genuinely relevant context. An agent that reads 20 files
per turn but only uses information from 2 of them is wasting 90% of its context
budget.

**Detection shape:** Session-level — compare files read (from `file_read`
events) against files referenced in the agent's output. High read-to-reference
ratio suggests hoarding.

**When to use `turn_complete`:** Good for per-turn detection. Combine with a
cron watcher that tracks the ratio over time to distinguish one-off research
turns from habitual hoarding.

### 8. Premature Escalation

**The pattern:** The agent routes too many findings to the operator, treating
every anomaly as urgent.

**Why it matters:** This is the inverse of silent sessions. If the operator
gets 20 notifications per day, they stop reading them. The algedonic channel
loses its bypass property because the operator treats it like noise.

**Detection shape:** Cross-session — count operator-routed findings per day.
If the rate exceeds a threshold (e.g., 5/day), the watchers themselves need
recalibration.

**This is a meta-watcher** — a watcher that watches the other watchers. Deploy
it as a daily cron job that reads `events.jsonl` for `watcher_signal` events
with `route: operator`.

## Writing Your Own Algedonic Watcher

See [debugging-watchers.md](debugging-watchers.md) for the mechanical details
(watchers.json format, input/output contracts, environment variables,
[suggested patterns](debugging-watchers.md#suggested-patterns)).

The key design decisions for an algedonic watcher:

1. **What specific behavior am I watching for?** Vague watchers produce vague
   signals. "The agent is doing something wrong" is not actionable.
2. **What's the baseline?** Every detection needs a comparison — against a
   threshold, a historical average, or an explicit contract.
3. **What should happen when it fires?** If you can't articulate the response,
   the signal isn't useful yet.
4. **What's the false positive rate?** If the watcher fires every session, the
   operator will ignore it. Calibrate thresholds before deploying.
5. **Is this a per-turn or cross-session signal?** Per-turn signals use
   `trigger: turn_complete`. Cross-session signals use `cron` with state
   tracking (see [Pattern 3](debugging-watchers.md#pattern-3-stateful-watcher)
   and [Pattern 5](debugging-watchers.md#pattern-5-baseline-comparison-beforeafter)).
6. **Should this escalate?** Not every finding needs to reach the operator.
   Start with `route: log`, promote to `route: operator` only after confirming
   the signal is reliable (see
   [Pattern 4](debugging-watchers.md#pattern-4-threshold-escalation)).
