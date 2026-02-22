from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

SKILL_CREATOR_MD = """\
---
name: skill-creator
description: Create or update reusable skills for this agent. Use this skill ONLY when the user asks to create a new skill, edit an existing skill, improve a SKILL.md, or capture a repeated workflow as a reusable skill. Do not use this skill for one-off tasks.
---

# skill-creator

Create or update local skills in this agent home repo.

## Where Skills Go

User-editable skills belong in:
- `skills/<skill-name>/SKILL.md`

Example:
- `skills/triage-issues/SKILL.md`

Built-in skills are exposed at:
- `/.open_strix_builtin_skills/<skill-name>/SKILL.md`

Treat built-in skills as read-only.

## Critical Rule: Trigger Description

The YAML frontmatter `description` is the trigger signal. It must make it obvious
when the skill should be used.

Every skill description should include:
- what the skill does
- exact "when to use" triggers
- what it should not be used for

Bad description:
- `Helps with docs.`

Good description:
- `Create and update release notes from git history. Use when the user asks for changelogs, release summaries, or version notes. Do not use for code changes.`

## Authoring Checklist

1. Write frontmatter with `name` and a high-signal `description`.
2. Add concise execution steps in the SKILL body.
3. Include concrete paths/commands the agent should run.
4. Keep scope narrow; split broad domains into multiple skills.
5. Prefer deterministic instructions over generic advice.
"""

PREDICTION_REVIEW_LOGGER_PY = """\
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def _parse_iso_datetime(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise argparse.ArgumentTypeError("datetime value cannot be empty")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO datetime: {raw!r}. Use e.g. 2026-02-22T12:34:56Z",
        ) from exc

    # Normalize all timestamps to UTC for stable comparisons.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.isoformat()


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "t", "1", "yes", "y"}:
        return True
    if value in {"false", "f", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("is-true must be one of: true,false,1,0,yes,no")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append a structured prediction review entry to a JSONL file.",
    )
    parser.add_argument(
        "--prediction-datetime",
        required=True,
        type=_parse_iso_datetime,
        help="ISO datetime for when the prediction was originally made.",
    )
    parser.add_argument(
        "--followup-datetime",
        required=False,
        type=_parse_iso_datetime,
        default=None,
        help="ISO datetime for when the prediction was reviewed (defaults to now in UTC).",
    )
    parser.add_argument(
        "--is-true",
        required=True,
        type=_parse_bool,
        help="Whether the prediction turned out true or false.",
    )
    parser.add_argument(
        "--comments",
        required=True,
        help="Freeform notes explaining evidence and behavior adjustment.",
    )
    parser.add_argument(
        "--output",
        default="state/prediction_reviews.jsonl",
        help="JSONL target path. Defaults to state/prediction_reviews.jsonl.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    comments = args.comments.strip()
    if not comments:
        raise SystemExit("--comments must be non-empty")

    followup_datetime = args.followup_datetime
    if followup_datetime is None:
        followup_datetime = datetime.now(tz=UTC).isoformat()

    record = {
        "prediction_datetime": args.prediction_datetime,
        "followup_datetime": followup_datetime,
        "prediction_true": args.is_true,
        "comments": comments,
    }

    target = Path(args.output).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\\n")
    print(f"appended prediction review to {target}")


if __name__ == "__main__":
    main()
"""

PREDICTION_REVIEW_MD = """\
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

- `scripts/prediction_review_log.py`

Canonical command:

```bash
uv run python scripts/prediction_review_log.py \
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
    select(.predictions != null and (.predictions | tostring | gsub("\\\\s+"; "") | length) > 0)
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
4. Log the result with `scripts/prediction_review_log.py`.
5. In comments, always include:
   - evidence summary
   - behavior adjustment to improve future prediction quality

## Improvement Standard

Predictions are teleological hypotheses: you perform an action and test whether reality changed as expected.
Do not stop at labeling true/false. Use misses (and partial hits) to adjust strategy, memory blocks, and execution patterns.
"""


BUILTIN_SKILLS: dict[str, str] = {
    "skill-creator/SKILL.md": SKILL_CREATOR_MD,
    "prediction-review/SKILL.md": PREDICTION_REVIEW_MD,
    "prediction-review/log_prediction_review.py": PREDICTION_REVIEW_LOGGER_PY,
}

BUILTIN_HELPER_SCRIPTS: dict[str, str] = {
    "prediction_review_log.py": PREDICTION_REVIEW_LOGGER_PY,
}


def materialize_builtin_skills() -> Path:
    payload = json.dumps(BUILTIN_SKILLS, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / "open-strix" / "builtin-skills" / digest
    root.mkdir(parents=True, exist_ok=True)

    for rel_path, content in BUILTIN_SKILLS.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        target.write_text(content, encoding="utf-8")
    return root
