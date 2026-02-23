from __future__ import annotations

from datetime import date
import json
import subprocess
import sys
from pathlib import Path

from open_strix.builtin_skills import materialize_builtin_skills
from open_strix.config import RepoLayout, STATE_DIR_NAME, bootstrap_home_repo


def test_materialized_builtin_skills_include_prediction_review_assets() -> None:
    root = materialize_builtin_skills()

    skill_path = root / "prediction-review" / "SKILL.md"
    memory_skill_path = root / "memory" / "SKILL.md"
    memory_maintenance_path = root / "memory" / "maintenance.md"
    logger_path = root / "scripts" / "prediction_review_log.py"
    memory_dashboard_path = root / "scripts" / "memory_dashboard.py"
    file_frequency_report_path = root / "scripts" / "file_frequency_report.py"

    assert skill_path.exists()
    assert memory_skill_path.exists()
    assert memory_maintenance_path.exists()
    assert logger_path.exists()
    assert memory_dashboard_path.exists()
    assert file_frequency_report_path.exists()

    skill_text = skill_path.read_text(encoding="utf-8")
    assert "name: prediction-review" in skill_text
    assert "logs/events.jsonl" in skill_text
    assert ".open_strix_builtin_skills/scripts/prediction_review_log.py" in skill_text

    dashboard_text = memory_dashboard_path.read_text(encoding="utf-8")
    assert "matplotlib" in dashboard_text
    assert "state" in dashboard_text
    assert "dashboards" in dashboard_text

    frequency_text = file_frequency_report_path.read_text(encoding="utf-8")
    assert "session_id" in frequency_text
    assert "events.jsonl" in frequency_text

    memory_skill_text = memory_skill_path.read_text(encoding="utf-8")
    assert "logs/journal.jsonl" in memory_skill_text
    assert "logs/events.jsonl" in memory_skill_text
    assert "/.open_strix_builtin_skills/memory/maintenance.md" in memory_skill_text

    maintenance_text = memory_maintenance_path.read_text(encoding="utf-8")
    assert "./.open_strix_builtin_skills/scripts/memory_dashboard.py" in maintenance_text
    assert "./.open_strix_builtin_skills/scripts/file_frequency_report.py" in maintenance_text
    assert "logs/events.jsonl" in maintenance_text


def test_prediction_review_logger_script_appends_structured_jsonl(tmp_path: Path) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout, checkpoint_text="checkpoint")

    logger_path = home / ".open_strix_builtin_skills" / "scripts" / "prediction_review_log.py"
    memory_dashboard_path = home / ".open_strix_builtin_skills" / "scripts" / "memory_dashboard.py"
    file_frequency_report_path = home / ".open_strix_builtin_skills" / "scripts" / "file_frequency_report.py"
    assert logger_path.exists()
    assert memory_dashboard_path.exists()
    assert file_frequency_report_path.exists()

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


def test_memory_dashboard_script_prints_output_file_and_text_report(tmp_path: Path) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout, checkpoint_text="checkpoint")

    (home / "blocks" / "persona.yaml").write_text(
        "name: persona\ntext: concise and practical\n",
        encoding="utf-8",
    )

    dashboard_script = home / ".open_strix_builtin_skills" / "scripts" / "memory_dashboard.py"
    assert dashboard_script.exists()

    proc = subprocess.run(
        [sys.executable, str(dashboard_script), "--repo-root", str(home)],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    output_path = home / "state" / "dashboards" / f"{date.today().isoformat()}-memory.png"
    assert output_path.exists()
    assert f"wrote memory dashboard: {output_path}" in proc.stdout
    assert f"output_file: {output_path}" in proc.stdout
    assert "Current memory block sizes (chars):" in proc.stdout
    assert "- persona: 21" in proc.stdout


def test_file_frequency_report_groups_file_access_by_session(tmp_path: Path) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout, checkpoint_text="checkpoint")

    (home / "logs" / "fetch-cache").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "fetch-cache" / "a.txt").write_text("alpha", encoding="utf-8")
    (home / "state" / "attachments").mkdir(parents=True, exist_ok=True)
    (home / "state" / "attachments" / "123-notes.txt").write_text("notes", encoding="utf-8")
    (home / "blocks" / "persona.yaml").write_text("name: persona\ntext: example\n", encoding="utf-8")
    (home / "blocks" / "goals.yaml").write_text("name: goals\ntext: focus\n", encoding="utf-8")

    events = [
        {
            "timestamp": "2026-02-22T10:00:00+00:00",
            "type": "tool_call",
            "session_id": "session-a",
            "tool": "fetch_url",
            "file_path": "/logs/fetch-cache/a.txt",
        },
        {
            "timestamp": "2026-02-22T10:01:00+00:00",
            "type": "discord_message",
            "session_id": "session-a",
            "attachment_names": ["state/attachments/123-notes.txt"],
        },
        {
            "timestamp": "2026-02-22T10:02:00+00:00",
            "type": "tool_call",
            "session_id": "session-a",
            "tool": "update_memory_block",
            "block_id": "persona",
        },
        {
            "timestamp": "2026-02-22T11:00:00+00:00",
            "type": "tool_call",
            "session_id": "session-b",
            "tool": "fetch_url",
            "file_path": "/logs/fetch-cache/a.txt",
        },
        {
            "timestamp": "2026-02-22T11:01:00+00:00",
            "type": "tool_call",
            "session_id": "session-b",
            "tool": "create_memory_block",
            "block_id": "goals",
        },
    ]
    events_path = home / "logs" / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as handle:
        for row in events:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    script_path = home / ".open_strix_builtin_skills" / "scripts" / "file_frequency_report.py"
    assert script_path.exists()

    proc = subprocess.run(
        [sys.executable, str(script_path), "--repo-root", str(home), "--top", "10"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    output_path = home / "state" / "dashboards" / f"{date.today().isoformat()}-file-frequency-report.json"
    plot_path = home / "state" / "dashboards" / f"{date.today().isoformat()}-file-frequency-scatter.png"
    assert output_path.exists()
    assert plot_path.exists()
    assert f"wrote file frequency report: {output_path}" in proc.stdout
    assert f"wrote file frequency scatter: {plot_path}" in proc.stdout
    assert f"output_file: {output_path}" in proc.stdout
    assert f"plot_file: {plot_path}" in proc.stdout
    assert "session_id=session-a" in proc.stdout
    assert "session_id=session-b" in proc.stdout

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["plot_file"] == str(plot_path)
    assert payload["plot_points"] >= 4
    assert payload["heatmap_file_count"] >= 4
    assert payload["coaccess_top_pairs"]
    assert payload["session_count"] == 2
    assert payload["total_events"] == 5

    sessions = {row["session_id"]: row for row in payload["sessions"]}
    assert "session-a" in sessions
    assert "session-b" in sessions
    top_a = {row["path"]: row["count"] for row in sessions["session-a"]["top_files"]}
    top_b = {row["path"]: row["count"] for row in sessions["session-b"]["top_files"]}
    assert top_a["logs/fetch-cache/a.txt"] == 1
    assert top_a["state/attachments/123-notes.txt"] == 1
    assert top_a["blocks/persona.yaml"] == 1
    assert top_b["logs/fetch-cache/a.txt"] == 1
    assert top_b["blocks/goals.yaml"] == 1


def test_bootstrap_cleans_legacy_builtin_script_copies(tmp_path: Path) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout, checkpoint_text="checkpoint")

    builtin_script = home / ".open_strix_builtin_skills" / "scripts" / "prediction_review_log.py"
    legacy_script = home / "scripts" / "prediction_review_log.py"
    legacy_script.write_text(builtin_script.read_text(encoding="utf-8"), encoding="utf-8")
    assert legacy_script.exists()

    bootstrap_home_repo(layout, checkpoint_text="checkpoint")
    assert not legacy_script.exists()
