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


def test_cli_no_args_does_not_auto_setup_when_not_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)

    def fail_setup(*_: object, **__: object) -> None:
        raise AssertionError("setup_home should not be called on normal run path")

    def fake_run_open_strix(home: Path | None = None) -> None:
        called["run_home"] = home

    monkeypatch.setattr(cli_mod, "setup_home", fail_setup)
    monkeypatch.setattr(cli_mod, "run_open_strix", fake_run_open_strix)
    cli_mod.main([])

    assert called["run_home"] is None


def test_setup_home_github_missing_user_continues_without_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    called: dict[str, bool] = {"github_remote": False}

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        if name == "gh":
            return None
        return None

    monkeypatch.setattr(cli_mod.shutil, "which", fake_which)
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "3")
    monkeypatch.setattr(cli_mod, "_ensure_git_repo", lambda _: None)
    monkeypatch.setattr(cli_mod, "bootstrap_home_repo", lambda **_: None)
    monkeypatch.setattr(cli_mod, "_print_setup_walkthrough", lambda _: None)
    monkeypatch.setattr(
        cli_mod,
        "_ensure_github_remote",
        lambda **_: called.__setitem__("github_remote", True),
    )

    cli_mod.setup_home(home=home, github=True, repo_name=None)

    assert called["github_remote"] is False
    assert (home / ".env").exists()


def test_setup_home_github_missing_user_abandons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    called: dict[str, bool] = {"ensure_git_repo": False}

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        if name == "gh":
            return None
        return None

    monkeypatch.setattr(cli_mod.shutil, "which", fake_which)
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "2")
    monkeypatch.setattr(
        cli_mod,
        "_ensure_git_repo",
        lambda _: called.__setitem__("ensure_git_repo", True),
    )

    with pytest.raises(RuntimeError, match="aborted"):
        cli_mod.setup_home(home=home, github=True, repo_name=None)

    assert called["ensure_git_repo"] is False


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
