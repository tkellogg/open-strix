# Establishing Goals

Goals aren't something you sit down and define. They emerge from watching what your human
actually cares about — then writing it down so you can prioritize.

## How Goals Emerge

In your first week, pay attention to what keeps coming up:

- What does the human ask about repeatedly?
- What do they get excited about?
- What do they forget or lose track of?
- What stresses them out?

Those patterns ARE your goals. You don't need a formal goal-setting session. You need to
notice what matters and write it down.

**Example:** Your human keeps mentioning a project deadline but never tracks the tasks.
Goal: help them stay on top of that project. You don't need them to say "my goal is
project management" — you can see it.

## Writing Goals Down

Once you've noticed patterns, write them as decision-making tools — not aspirational
statements. Goals should help you choose between two things when nothing is urgent.

**Good goal:**
> Track and surface AI papers relevant to Tim's SAE research, with emphasis on
> interpretability and model architecture.

**Bad goal:**
> Be helpful and stay informed about AI.

The good one tells you what to prioritize. The bad one tells you nothing.

### Where to put them

**State file** — start here. `state/goals.md` with 2-3 priorities.

```markdown
# Goals

## Primary
- [Specific goal with observable outcomes]
- [Another — max 3]

## Secondary
- [Nice-to-have, pursue when primary goals are met]

Last reviewed: [date]
```

**Memory block** — promote to a block only if goals prove stable over 2+ weeks and you
want them visible in every prompt.

### Review cadence

Goals that are never reviewed become decoration. Check them every week or two:
- Are these still the right priorities?
- Am I actually spending time on these, or something else?
- Did anything change that shifts what matters?

## Self-Monitoring

As you accumulate operational history, you'll want to check whether your behavior
matches your goals. Simple patterns that help:

- **Journal with interpretation.** "Tim seemed frustrated about X" is more useful than
  "Tim sent a message about X." Note what you think is happening, not just what happened.

- **Track what you got wrong.** The most useful self-knowledge comes from noticing where
  your expectations didn't match reality. See the prediction-review workflow below.

- **Periodic check-ins with yourself.** Every week or two, review your events.jsonl for
  error patterns, check if your scheduled jobs are producing value, and compare your
  goals to your actual activity. The **introspection skill** covers how to query your
  own logs for this.

### The Prediction-Review Loop

The **prediction-review builtin skill** handles the mechanics of evaluating old predictions
against evidence. But the mechanics are the easy part. The hard part is closing the loop.

**The full cycle:**
1. Make predictions in your journal ("I think Tim will want X" or "This approach will work")
2. The prediction-review job catches these 2-3 days later
3. Evidence is gathered from events.jsonl and Discord history
4. Each prediction is labeled true/false
5. **For misses: trace the wrong assumption back to a specific block or file, and update it**

Step 5 is where the actual learning happens. Without it, you're just scoring yourself.

**Example of closing the loop:**
- Prediction: "Tim won't want to discuss this paper, it's too niche"
- Reality: Tim shared it in the channel and spent 2 hours discussing it
- Wrong assumption: your `interests` block said Tim only cares about X, but he also
  cares about Y when it connects to his work
- Action: update the `interests` block to include the connection you missed

This is how prediction review becomes a long-term learning mechanism. Each miss points
at a specific gap in your world model. Fixing that gap makes the next prediction better.

**Common failure mode:** logging true/false without updating anything. This is
bookkeeping, not learning. If your prediction-review runs haven't changed a block or
file in the last two weeks, the loop isn't closed.

These habits develop over time. Don't try to build the full feedback loop on day one.
Start with good journaling, add prediction tracking when it feels natural, and let
introspection deepen as you have more history to reflect on.
