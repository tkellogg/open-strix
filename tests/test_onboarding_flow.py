from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path, env: dict[str, str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )


def test_onboarding_flow_bootstraps_expected_home_repo(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "new-agent"
    home.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # Force no-Discord path to verify first-run local onboarding behavior.
    env.pop("DISCORD_TOKEN", None)

    _run(["uv", "init", "--python", "3.11", "--no-readme"], cwd=home, env=env)
    _run(["uv", "add", "--editable", str(repo_root)], cwd=home, env=env)
    first_run = _run(["uv", "run", "open-strix"], cwd=home, env=env, stdin="")

    assert "No Discord token configured. Running in stdin mode." in first_run.stdout

    expected_paths = [
        home / "state" / ".gitkeep",
        home / "skills" / ".gitkeep",
        home / "blocks" / ".gitkeep",
        home / "logs" / "events.jsonl",
        home / "logs" / "journal.jsonl",
        home / "scheduler.yaml",
        home / "config.yaml",
        home / "checkpoint.md",
        home / "scripts" / "pre_commit.py",
        home / ".open_strix_builtin_skills" / "scripts" / "prediction_review_log.py",
        home / ".open_strix_builtin_skills" / "scripts" / "memory_dashboard.py",
        home / ".open_strix_builtin_skills" / "scripts" / "file_frequency_report.py",
        home / ".git" / "hooks" / "pre-commit",
    ]
    missing = [path for path in expected_paths if not path.exists()]
    assert not missing, f"missing onboarding files: {missing}"

    hook_text = (home / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "scripts/pre_commit.py" in hook_text
    assert "command -v uv" in hook_text
    assert ".venv/bin/uv" in hook_text

    gitignore_lines = {
        line.strip()
        for line in (home / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "logs/" in gitignore_lines
    assert ".env" in gitignore_lines

    config_text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "model: MiniMax-M2.5" in config_text
    assert "always_respond_bot_ids: []" in config_text
    assert "default_reply_channel" not in config_text
    assert "state_dir" not in config_text
    assert "git_sync_before_send" not in config_text
    assert "git_sync_after_turn" not in config_text
    assert "skills_sources" not in config_text

    scheduler_text = (home / "scheduler.yaml").read_text(encoding="utf-8")
    assert "prediction-review-twice-daily" in scheduler_text
    assert 'cron: "0 9,21 * * *"' in scheduler_text

    # Second run should be idempotent and still work.
    second_run = _run(["uv", "run", "open-strix"], cwd=home, env=env, stdin="")
    assert "No Discord token configured. Running in stdin mode." in second_run.stdout
