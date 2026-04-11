---
name: prediction-review
description: Evaluate whether journal predictions became true 2-3 days later using event and Discord evidence, then log structured outcomes for calibration and behavior updates. Use when auditing prediction quality. Do not use for one-off messaging tasks.
allowed-tools: bash powershell list_messages read_file
---

# prediction-review

Evaluate prediction accuracy from prior journal entries, then use those outcomes to improve future behavior.

## Philosophy

Predictions are **teleological hypotheses** — you perform an action and test whether reality changed as expected. They are NOT accuracy contests. Misses are the value. Use prediction errors as information to update understanding.

When you miss a prediction:
1. Identify what you got wrong
2. Update a memory block or file with what you learned
3. Note the gap in your understanding

When you hit a prediction, ask: was this actually hard to predict, or did I have insider information?

## Prediction Context Categories

Not all predictions are equal. Categorize each prediction by context, and calibrate confidence accordingly:

### 1. Collaborative (you're directly involved)
- **Expected accuracy:** ~90-100%
- **Why:** You have near-complete information about your own behavior and strong priors on how others respond to you
- **Calibration value:** LOW — this is closer to recall than forecasting. 100% accuracy here is the least informative result possible
- **Example:** "Strix will respond substantively to my arXiv analysis" → TRUE (of course they did, you tagged them in a research channel)

### 2. Observational (watching interactions you're not part of)
- **Expected accuracy:** ~50-70%
- **Why:** Depends on factors outside your awareness — other people's moods, priorities, context you can't see
- **Calibration value:** HIGH — this is where actual forecasting skill lives
- **Example:** "Tim will comment on my quietness in lily channel" → harder to predict, depends on what else Tim is doing

### 3. Infrastructure / External timing
- **Expected accuracy:** ~50%
- **Why:** Depends on external systems, timing, announcements you can't observe
- **Calibration value:** MEDIUM — useful for learning about external dependencies
- **Example:** "open-strix announcement will happen today" → FALSE (Tim decided it wasn't ready — external decision you couldn't observe)

**Key insight:** Don't average across categories. A review showing "67% accuracy" when it's really "95% collaborative, 30% observational" hides the signal. Separate the categories, calibrate each independently.

## Examples of Good vs Bad Predictions

### Good predictions (testable, uncertain, informative)

- "Tim will engage with the paper's mechanism, not just the benchmarks" — observational, tests your model of Tim's reading style, falsifiable
- "Lily will respond to the cost question within 2 hours" — observational, specific timeframe, depends on factors you don't control
- "The infrastructure tick will surface the SQLite schema issue" — infrastructure, tests whether your monitoring catches known problems

### Bad predictions (untestable, certain, or uninformative)

- "Strix will respond to my message" — collaborative, near-certain, teaches you nothing
- "The conversation will continue" — too vague to be falsified
- "Tim will be interested in AI news" — too broad, always true, no signal
- "Someone will react to my post" — low-information even if wrong

### The test
Before logging a prediction, ask: **If this prediction is wrong, what would I learn?** If the answer is "nothing" or "something weird happened," it's not a useful prediction.

## Ground Truth Rules

- `logs/journal.jsonl` contains claims and reflections, not truth by itself.
- Primary truth sources:
  - `logs/events.jsonl`
  - Discord message history (via `list_messages` and saved history)
- If sources conflict, trust events + Discord over journal wording.

## Review Window

- Target predictions that are 2-3 days old (48-72 hours old).
- It is also acceptable to process overdue predictions older than 72 hours.
- Traverse entries in chronological order so the evaluation is consistent over time.

## Structured Logging Script

Use this helper script to append exactly structured reviews:

- `.open_strix_builtin_skills/scripts/prediction_review_log.py`

Canonical command:

```bash
uv run python .open_strix_builtin_skills/scripts/prediction_review_log.py \
  --prediction-datetime "2026-02-20T14:12:00Z" \
  --is-true true \
  --comments "Evidence: user follow-up in Discord confirmed the behavior change. Behavior update: keep this intervention pattern."
```

You can pass `--followup-datetime` explicitly; if omitted, the script uses current UTC time.

Each record contains:
- `prediction_datetime`
- `followup_datetime`
- `prediction_true`
- `comments`

## Candidate Traversal (Use jq if available)

Use `jq` when present:

```bash
if command -v jq >/dev/null 2>&1; then
  jq -s '
    sort_by(.timestamp)[] |
    select(.timestamp != null) |
    select((now - (.timestamp | fromdateiso8601)) >= (48 * 60 * 60)) |
    select(.predictions != null and (.predictions | tostring | gsub("\\s+"; "") | length) > 0)
  ' logs/journal.jsonl
fi
```

If `jq` is unavailable, use Python:

```bash
uv run python - <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path

now = datetime.now(tz=timezone.utc)
rows = []
for line in Path("logs/journal.jsonl").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    entry = json.loads(line)
    timestamp = datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    age_seconds = (now - timestamp).total_seconds()
    predictions = str(entry.get("predictions", "")).strip()
    if age_seconds >= 48 * 60 * 60 and predictions:
        rows.append((timestamp, entry))

for _, entry in sorted(rows, key=lambda item: item[0]):
    print(json.dumps(entry, ensure_ascii=True))
PY
```

## Evaluation Workflow

1. Load candidate journal entries in timestamp order.
2. For each prediction, gather evidence in `logs/events.jsonl` and Discord history.
3. **Tag the prediction context** (collaborative / observational / infrastructure).
4. Decide `true` or `false` based on evidence, not aspiration.
5. Log the result with `.open_strix_builtin_skills/scripts/prediction_review_log.py`.
6. In comments, always include:
   - evidence summary
   - context category
   - behavior adjustment to improve future prediction quality

## Review Summary Format

When reporting results, break down by context category:

```
Collaborative: X/Y (expected ~90-100%)
Observational: X/Y (expected ~50-70%)
Infrastructure: X/Y (expected ~50%)
```

If collaborative accuracy is high but observational is low, that's normal — don't let easy wins inflate your overall number. The interesting question is always: **how do I get better at the hard ones?**

## When a Prediction Misses — Root Cause Reflection

Logging `prediction_true: false` is the beginning, not the end. A miss means your world model was wrong about something. The interesting question is: **what structural property of your model produced the wrong prediction?**

For each miss, ask:
1. **What did I assume that turned out to be false?** (Not "I was wrong" but "I assumed X, and X wasn't true.")
2. **Is this a one-off or a pattern?** Check your previous prediction reviews — have you missed similar predictions before?
3. **If it's a pattern or genuinely surprising**, use the **five-whys** skill to decompose it. The prediction miss is the problem statement; the 5 Whys finds the structural gap in your world model.

Don't run 5 Whys on every miss — collaborative misses and obvious infrastructure failures don't need it. Run it when:
- An observational prediction misses and you can't immediately explain why
- The same category of prediction keeps missing
- The miss reveals something about how your human, your environment, or your own reasoning works that you didn't know

The goal: predictions that miss should make you smarter, not just more cautious.

## Common Miss Patterns (from real data)

- **Infrastructure failures are unpredictable:** "OLMoE results will come in" → FALSE (Strix was down with infinite loop bug). You can't predict other agents' uptime.
- **Announcements depend on human decisions:** "open-strix announcement will happen" → FALSE (Tim decided it wasn't ready). External timing you can't observe.
- **Paper interest follows salience, not schedule:** "Tim will follow up on paper X" → unreliable (~70%). Tim responds to whatever is most salient that day, not what you predicted he'd find interesting.
- **Direct interaction is near-certain:** "Strix will respond to my research analysis" → TRUE (always). Stop predicting these — they waste review cycles.
