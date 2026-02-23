---
name: prediction-review
description: Evaluate whether journal predictions became true 2-3 days later using event and Discord evidence, then log structured outcomes for calibration and behavior updates. Use when auditing prediction quality. Do not use for one-off messaging tasks.
allowed-tools: bash powershell list_messages read_file
---

# prediction-review

Evaluate prediction accuracy from prior journal entries, then use those outcomes to improve future behavior.

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
3. Decide `true` or `false` based on evidence, not aspiration.
4. Log the result with `.open_strix_builtin_skills/scripts/prediction_review_log.py`.
5. In comments, always include:
   - evidence summary
   - behavior adjustment to improve future prediction quality

## Improvement Standard

Predictions are teleological hypotheses: you perform an action and test whether reality changed as expected.
Do not stop at labeling true/false. Use misses (and partial hits) to adjust strategy, memory blocks, and execution patterns.
