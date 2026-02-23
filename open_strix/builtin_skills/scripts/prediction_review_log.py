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
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"appended prediction review to {target}")


if __name__ == "__main__":
    main()
