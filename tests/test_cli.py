from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

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


def test_setup_home_github_missing_raises_with_install_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        if name == "uv":
            return "/usr/bin/uv"
        if name == "gh":
            return None
        return None

    monkeypatch.setattr(cli_mod.shutil, "which", fake_which)
    with pytest.raises(RuntimeError, match="Install GitHub CLI"):
        cli_mod.setup_home(home=home, github=True, repo_name=None)


def test_setup_home_requires_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "agent-home"

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        if name == "uv":
            return None
        return None

    monkeypatch.setattr(cli_mod.shutil, "which", fake_which)

    with pytest.raises(RuntimeError, match="`uv` is required"):
        cli_mod.setup_home(home=home, github=False, repo_name=None)


def test_ensure_git_identity_prompts_and_uses_default_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    writes: list[tuple[str, str]] = []

    monkeypatch.setattr(cli_mod, "_git_config_get", lambda *_: "")
    monkeypatch.setattr(cli_mod, "_git_config_set", lambda _home, key, value: writes.append((key, value)))
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    responses = iter(["", "agent-home@example.com"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    cli_mod._ensure_git_identity(home)

    assert writes == [
        ("user.name", "agent-home"),
        ("user.email", "agent-home@example.com"),
    ]


def test_ensure_git_identity_non_interactive_missing_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod, "_git_config_get", lambda *_: "")
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="Git identity is not configured"):
        cli_mod._ensure_git_identity(home)


def test_ensure_git_identity_skips_prompt_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)

    def fake_get(_: Path, key: str) -> str:
        if key == "user.name":
            return "Agent"
        if key == "user.email":
            return "agent@example.com"
        return ""

    monkeypatch.setattr(cli_mod, "_git_config_get", fake_get)
    monkeypatch.setattr(
        cli_mod,
        "_git_config_set",
        lambda *_: (_ for _ in ()).throw(AssertionError("_git_config_set should not be called")),
    )
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)

    cli_mod._ensure_git_identity(home)


def test_ensure_git_remote_prompts_and_sets_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(cli_mod, "_git_origin_remote_url", lambda _: "")
    monkeypatch.setattr(cli_mod, "_git_remote_add_origin", lambda _home, url: calls.append(("remote", url)))
    monkeypatch.setattr(cli_mod, "_ensure_git_push_defaults", lambda _home: calls.append(("push_defaults", "ok")))
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "https://github.com/example/agent-home.git")

    cli_mod._ensure_git_remote(home, github=False, repo_name=None)

    assert calls == [
        ("remote", "https://github.com/example/agent-home.git"),
        ("push_defaults", "ok"),
    ]


def test_ensure_git_remote_non_interactive_missing_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod, "_git_origin_remote_url", lambda _: "")
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="Git remote `origin` is not configured"):
        cli_mod._ensure_git_remote(home, github=False, repo_name=None)


def test_ensure_git_remote_uses_existing_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    observed: list[str] = []

    monkeypatch.setattr(cli_mod, "_git_origin_remote_url", lambda _: "git@github.com:example/repo.git")
    monkeypatch.setattr(cli_mod, "_ensure_git_push_defaults", lambda _: observed.append("push_defaults"))
    monkeypatch.setattr(
        cli_mod,
        "_git_remote_add_origin",
        lambda *_: (_ for _ in ()).throw(AssertionError("_git_remote_add_origin should not be called")),
    )

    cli_mod._ensure_git_remote(home, github=False, repo_name=None)
    assert observed == ["push_defaults"]


def test_ensure_github_remote_uses_existing_repo_as_origin_when_create_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    added_origin: dict[str, str] = {}
    push_calls: list[list[str]] = []

    def fake_run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert cwd == home
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 2, "", "no origin")
        if cmd[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(cmd, 1, "", "repository already exists")
        if cmd[:3] == ["gh", "api", "user"]:
            return subprocess.CompletedProcess(cmd, 0, '{"login":"example"}', "")
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                '{"sshUrl":"git@github.com:example/agent-home.git","url":"https://github.com/example/agent-home"}',
                "",
            )
        if cmd[:3] == ["git", "push", "-u"]:
            push_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(cli_mod, "_run_command", fake_run_command)
    monkeypatch.setattr(cli_mod, "_git_remote_add_origin", lambda _home, url: added_origin.__setitem__("url", url))
    monkeypatch.setattr(cli_mod, "_ensure_initial_commit", lambda _: True)

    cli_mod._ensure_github_remote(home=home, repo_name=None)

    assert added_origin["url"] == "git@github.com:example/agent-home.git"
    assert push_calls == [["git", "push", "-u", "origin", "HEAD"]]


def test_ensure_github_remote_uses_existing_repo_without_push_when_commit_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    added_origin: dict[str, str] = {}
    push_calls: list[list[str]] = []

    def fake_run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert cwd == home
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 2, "", "no origin")
        if cmd[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(cmd, 1, "", "repository already exists")
        if cmd[:3] == ["gh", "api", "user"]:
            return subprocess.CompletedProcess(cmd, 0, '{"login":"example"}', "")
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                '{"sshUrl":"git@github.com:example/agent-home.git","url":"https://github.com/example/agent-home"}',
                "",
            )
        if cmd[:3] == ["git", "push", "-u"]:
            push_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(cli_mod, "_run_command", fake_run_command)
    monkeypatch.setattr(cli_mod, "_git_remote_add_origin", lambda _home, url: added_origin.__setitem__("url", url))
    monkeypatch.setattr(cli_mod, "_ensure_initial_commit", lambda _: False)

    cli_mod._ensure_github_remote(home=home, repo_name=None)

    assert added_origin["url"] == "git@github.com:example/agent-home.git"
    assert push_calls == []


def test_write_service_assets_linux_generates_systemd_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cli_mod.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name == "uv" else "/bin/systemctl" if name == "systemctl" else None,
    )

    cli_mod._write_service_assets(home)
    unit_path = home / "services" / "open-strix.service"
    assert unit_path.exists()
    text = unit_path.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/uv run open-strix run --home" in text
    assert str(home) in text


def test_write_service_assets_macos_generates_launchd_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        cli_mod.shutil,
        "which",
        lambda name: "/usr/local/bin/uv" if name == "uv" else "/bin/launchctl" if name == "launchctl" else None,
    )

    cli_mod._write_service_assets(home)
    label = cli_mod._launchd_label(home)
    plist_path = home / "services" / f"{label}.plist"
    assert plist_path.exists()
    text = plist_path.read_text(encoding="utf-8")
    assert "<key>Label</key>" in text
    assert label in text
    assert "open-strix" in text


def test_write_service_assets_windows_generates_task_scripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        cli_mod.shutil,
        "which",
        lambda name: "C:\\uv\\uv.exe" if name == "uv" else "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" if name == "powershell" else "C:\\Windows\\System32\\schtasks.exe" if name == "schtasks" else None,
    )

    cli_mod._write_service_assets(home)
    install_script = home / "services" / "install-open-strix-task.ps1"
    uninstall_script = home / "services" / "uninstall-open-strix-task.ps1"
    assert install_script.exists()
    assert uninstall_script.exists()
    install_text = install_script.read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in install_text
    assert "open-strix run --home" in install_text


def test_service_setup_section_linux_includes_systemd_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        cli_mod.shutil,
        "which",
        lambda name: "/bin/systemctl" if name == "systemctl" else "/bin/journalctl" if name == "journalctl" else "/usr/bin/uv" if name == "uv" else None,
    )

    section = cli_mod._service_setup_section(home)
    assert "Linux/systemd user service" in section
    assert "systemctl --user enable --now open-strix.service" in section


def test_service_setup_section_windows_missing_tools_mentions_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        cli_mod.shutil,
        "which",
        lambda name: "C:\\uv\\uv.exe" if name == "uv" else None,
    )

    section = cli_mod._service_setup_section(home)
    assert "Windows" in section
    assert "missing required tools" in section


def test_cli_setup_scaffolds_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("git") is None or shutil.which("uv") is None:
        pytest.skip("git/uv are not installed")

    responses = iter(
        [
            "agent-home",
            "agent-home@example.com",
            "https://github.com/example/agent-home.git",
        ],
    )
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    home = tmp_path / "agent-home"
    cli_mod.main(["setup", "--home", str(home)])

    assert (home / ".git").exists()
    assert (home / "config.yaml").exists()
    assert (home / "scheduler.yaml").exists()
    assert (home / "checkpoint.md").exists()
    assert (home / "pyproject.toml").exists()
    assert (home / "services").exists()
    assert (home / "state" / ".gitkeep").exists()
    assert (home / ".env").exists()

    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" in env_text
    assert "TAVILY_API_KEY=" in env_text
    assert "DISCORD_TOKEN=" in env_text
    pyproject_text = (home / "pyproject.toml").read_text(encoding="utf-8")
    assert "open-strix" in pyproject_text

    scheduler = yaml.safe_load((home / "scheduler.yaml").read_text(encoding="utf-8"))
    assert isinstance(scheduler, dict)
    jobs = scheduler.get("jobs", [])
    assert isinstance(jobs, list)
    prediction_job = next(
        (job for job in jobs if isinstance(job, dict) and job.get("name") == "prediction-review-twice-daily"),
        None,
    )
    assert prediction_job is not None
    assert prediction_job.get("cron") == "0 9,21 * * *"
    assert "prediction-review skill" in str(prediction_job.get("prompt", ""))

    # Idempotent: second setup run should preserve existing files and not fail.
    cli_mod.main(["setup", "--home", str(home)])
    assert (home / "config.yaml").exists()


def test_cli_setup_prints_walkthrough(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which("git") is None or shutil.which("uv") is None:
        pytest.skip("git/uv are not installed")

    responses = iter(
        [
            "walkthrough-home",
            "walkthrough-home@example.com",
            "https://github.com/example/walkthrough-home.git",
        ],
    )
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    home = tmp_path / "walkthrough-home"
    cli_mod.main(["setup", "--home", str(home)])
    out = capsys.readouterr().out

    assert "Setup walkthrough" in out
    assert "Option A: MiniMax M2.5" in out
    assert "Option B: Kimi (Moonshot, K2 family)" in out
    assert "Set up Tavily web search (recommended)" in out
    assert "Set up Discord bot" in out
    assert "config.yaml" in out
