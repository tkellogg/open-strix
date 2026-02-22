from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from open_strix.builtin_skills import materialize_builtin_skills
from open_strix.config import RepoLayout, STATE_DIR_NAME, bootstrap_home_repo


def test_materialized_builtin_skills_include_prediction_review_assets() -> None:
    root = materialize_builtin_skills()

    skill_path = root / "prediction-review" / "SKILL.md"
    logger_path = root / "prediction-review" / "log_prediction_review.py"

    assert skill_path.exists()
    assert logger_path.exists()

    skill_text = skill_path.read_text(encoding="utf-8")
    assert "name: prediction-review" in skill_text
    assert "logs/events.jsonl" in skill_text
    assert "scripts/prediction_review_log.py" in skill_text


def test_prediction_review_logger_script_appends_structured_jsonl(tmp_path: Path) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout, checkpoint_text="checkpoint")

    logger_path = home / "scripts" / "prediction_review_log.py"
    assert logger_path.exists()

    proc = subprocess.run(
        [
            sys.executable,
            str(logger_path),
            "--prediction-datetime",
            "2026-02-19T10:30:00Z",
            "--followup-datetime",
            "2026-02-22T12:00:00Z",
            "--is-true",
            "false",
            "--comments",
            "Evidence: no observed behavior shift in events. Behavior update: tighten intervention criteria.",
        ],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    output_path = home / "state" / "prediction_reviews.jsonl"
    assert output_path.exists()

    rows = output_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record == {
        "prediction_datetime": "2026-02-19T10:30:00+00:00",
        "followup_datetime": "2026-02-22T12:00:00+00:00",
        "prediction_true": False,
        "comments": "Evidence: no observed behavior shift in events. Behavior update: tighten intervention criteria.",
    }
