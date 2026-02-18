from __future__ import annotations

from pathlib import Path
import shutil

import pytest

import open_strix.cli as cli_mod


def test_cli_no_args_runs_open_strix(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_run_open_strix(home: Path | None = None) -> None:
        called["home"] = home

    monkeypatch.setattr(cli_mod, "run_open_strix", fake_run_open_strix)
    cli_mod.main([])

    assert called["home"] is None


def test_cli_setup_scaffolds_home(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    home = tmp_path / "agent-home"
    cli_mod.main(["setup", "--home", str(home)])

    assert (home / ".git").exists()
    assert (home / "config.yaml").exists()
    assert (home / "scheduler.yaml").exists()
    assert (home / "checkpoint.md").exists()
    assert (home / "state" / ".gitkeep").exists()
    assert (home / ".env").exists()

    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" in env_text
    assert "DISCORD_TOKEN=" in env_text

    # Idempotent: second setup run should preserve existing files and not fail.
    cli_mod.main(["setup", "--home", str(home)])
    assert (home / "config.yaml").exists()
