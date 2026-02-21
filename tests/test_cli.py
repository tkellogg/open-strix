from __future__ import annotations

from pathlib import Path
import shutil

import pytest

import open_strix.cli as cli_mod


def test_cli_no_args_runs_open_strix(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_run_open_strix(home: Path | None = None) -> None:
        called["home"] = home

    monkeypatch.setattr(cli_mod, "_has_local_git_repo", lambda _: True)
    monkeypatch.setattr(cli_mod, "run_open_strix", fake_run_open_strix)
    cli_mod.main([])

    assert called["home"] is None


def test_cli_no_args_auto_setup_when_not_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "_has_local_git_repo", lambda _: False)

    def fake_setup(home: Path, *, github: bool = False, repo_name: str | None = None) -> None:
        called["setup_home"] = home
        called["setup_github"] = github
        called["setup_repo_name"] = repo_name

    def fake_run_open_strix(home: Path | None = None) -> None:
        called["run_home"] = home

    monkeypatch.setattr(cli_mod, "setup_home", fake_setup)
    monkeypatch.setattr(cli_mod, "run_open_strix", fake_run_open_strix)
    cli_mod.main([])

    assert called["setup_home"] == tmp_path.resolve()
    assert called["setup_github"] is False
    assert called["setup_repo_name"] is None
    assert called["run_home"] == tmp_path.resolve()


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
    assert "TAVILY_API_KEY=" in env_text
    assert "DISCORD_TOKEN=" in env_text

    # Idempotent: second setup run should preserve existing files and not fail.
    cli_mod.main(["setup", "--home", str(home)])
    assert (home / "config.yaml").exists()


def test_cli_setup_prints_walkthrough(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    home = tmp_path / "walkthrough-home"
    cli_mod.main(["setup", "--home", str(home)])
    out = capsys.readouterr().out

    assert "Setup walkthrough" in out
    assert "Option A: MiniMax M2.5" in out
    assert "Option B: Kimi (Moonshot, K2 family)" in out
    assert "Set up Tavily web search (recommended)" in out
    assert "Set up Discord bot" in out
    assert "config.yaml" in out
