# Establishing Goals & Self-Monitoring

An agent without goals is a process, not an agent. Goals provide the feedback signal that
turns logging into learning.

## Goal Setting

### What Goals Are For

Goals aren't aspirational statements — they're decision-making tools. When the agent faces
an ambiguous situation ("should I research this paper or reply to that message?"), goals
provide the tiebreaker.

**Good goal:**
> Track and surface AI developments relevant to Tim's SAE research, with emphasis on
> interpretability and model architecture papers.

**Bad goal:**
> Be helpful and stay informed about AI.

The difference: the good goal tells the agent what to prioritize. The bad goal tells it nothing.

### Goal Block or State File?

**Memory block** — if goals are stable and should influence every decision. Visible in every
prompt, high-priority context.

**State file** — if goals change frequently or are project-specific. Referenced when relevant,
not always visible.

Most agents should start with a `goals` state file and promote to a memory block only if
the goals prove stable over 2+ weeks.

### Goal Structure

**Template (state file):**
```markdown
# Goals

## Primary (guides daily decisions)
- [Specific, actionable goal with observable outcomes]
- [Another goal — max 3 primary goals]

## Secondary (nice to have, pursue when primary goals are met)
- [Less urgent but still valuable]

## Review
Last reviewed: [date]
Next review: [date, 1-2 weeks out]
```

**Review cadence matters.** Goals that are never reviewed become decoration. Goals reviewed
too frequently create churn. Weekly or biweekly is the sweet spot for most agents.

## The Prediction Habit

Predictions are the single most effective self-monitoring tool. They turn "I did stuff" into
"I expected X, got Y, and now I understand Z."

### Why Predictions Work

Without predictions, the agent can only report what happened. With predictions, it can
identify where its model of the world is wrong — which is where all the learning lives.

**Without prediction:**
> "Posted the thread. Got 5 likes."

**With prediction:**
> "Predicted 15 likes within 24 hours (confidence: 70%). Got 5. The confidence was too high —
> Saturday afternoon engagement is consistently lower than I expected. Adjusting future
> predictions for weekend timing."

The second version contains learning. The first is just a log entry.

### When to Predict

Register a prediction whenever:
- Starting a new task with uncertain outcome
- Posting content (engagement prediction)
- Making a recommendation (outcome prediction)
- Trying something for the first time (success/failure prediction)

**Format:**
```
Prediction: [specific, falsifiable statement]
Confidence: [percentage]
Timeframe: [when to evaluate]
Category: [what kind of prediction]
```

### Reviewing Predictions

Use the prediction-review builtin skill. It runs on a schedule (default: twice daily) and
checks for predictions that have passed their evaluation timeframe.

**The review should produce:**
- Was the prediction correct? (binary)
- How wrong was the confidence? (calibration)
- What explains the gap between expectation and reality? (learning)

**Common failure:** Registering predictions but never reviewing them. The prediction system
only works if the feedback loop closes. If reviews keep coming back empty, the agent isn't
predicting enough.

## Self-Monitoring Patterns

### Journal Quality

The journal is the agent's primary self-monitoring artifact. A healthy journal shows:
- **Interpretation, not just events** — "Tim seemed frustrated" not just "Tim sent a message"
- **Uncertainty** — "I'm not sure if..." appears regularly
- **Learning** — "This changed my understanding of..." or "Next time I would..."
- **Predictions** — Forward-looking statements mixed with retrospective ones

**Red flags in journal entries:**
- Every entry looks the same — the agent is template-filling, not reflecting
- No uncertainty — the agent is overconfident or not engaging honestly
- No learning — the agent reports but doesn't synthesize
- Entries only when prompted — the agent doesn't journal during autonomous work

### Introspection Schedule

At minimum, the agent should periodically:
1. Review its own events.jsonl for error patterns
2. Check if scheduled jobs are producing useful output (or just noise)
3. Compare its goals to its actual activity (drift detection)
4. Review its communication patterns (over-talking? under-talking?)

This can be a dedicated scheduled job or part of a broader check-in. See the introspection
builtin skill for specific diagnostic techniques.

## Building the Feedback Loop

The full self-monitoring stack:

```
Goals (what I'm trying to do)
    ↓
Predictions (what I expect to happen)
    ↓
Actions (what I actually do)
    ↓
Journal (what happened and why)
    ↓
Review (was my prediction right? what did I learn?)
    ↓
Goal Update (should I adjust what I'm trying to do?)
    ↑_____________________________________________________|
```

Most agents get the middle three (actions, journal, basic review). The ones that genuinely
improve also have the bookends — goals that guide predictions, and reviews that update goals.
The loop is what turns an agent from a process into a learning system.
