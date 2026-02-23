from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from textwrap import dedent
import tomllib
from typing import Sequence

from .app import run_open_strix
from .config import RepoLayout, STATE_DIR_NAME, bootstrap_home_repo
from .prompts import DEFAULT_CHECKPOINT

DEFAULT_ENV = """\
# Anthropic-compatible endpoint
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic

# Optional web search (Tavily). Without this, web_search is disabled.
TAVILY_API_KEY=

# Discord bot token
DISCORD_TOKEN=

# Optional live test settings
DISCORD_TEST_CHANNEL_ID=
OPEN_STRIX_TEST_MODEL=
"""

MINIMAX_PLATFORM_URL = "https://platform.minimax.io"
MINIMAX_ANTHROPIC_DOC_URL = "https://platform.minimax.io/docs/api-reference/text-anthropic-api"
MINIMAX_CODING_DOC_URL = "https://platform.minimax.io/docs/guides/text-ai-coding-tools"
MOONSHOT_PLATFORM_URL = "https://platform.moonshot.ai"
MOONSHOT_DOCS_URL = "https://platform.moonshot.ai/docs/overview"
MOONSHOT_K2_POST_URL = "https://platform.moonshot.ai/blog/posts/Kimi_API_Newsletter"
DISCORD_DEV_PORTAL_URL = "https://discord.com/developers/applications"
DISCORD_GETTING_STARTED_URL = "https://docs.discord.com/developers/quick-start/getting-started"
DISCORD_OAUTH_URL = "https://docs.discord.com/developers/topics/oauth2"
DISCORD_PERMISSIONS_URL = "https://docs.discord.com/developers/topics/permissions"
DISCORD_GATEWAY_URL = "https://docs.discord.com/developers/events/gateway"
TAVILY_PLATFORM_URL = "https://tavily.com/"


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


def _run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _platform_key() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "unknown"


def _service_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug or "open-strix"


def _service_tools() -> dict[str, bool]:
    return {
        "systemctl": shutil.which("systemctl") is not None,
        "journalctl": shutil.which("journalctl") is not None,
        "launchctl": shutil.which("launchctl") is not None,
        "schtasks": shutil.which("schtasks") is not None,
        "pwsh": shutil.which("pwsh") is not None,
        "powershell": shutil.which("powershell") is not None,
    }


def _service_uv_bin() -> str:
    uv_bin = shutil.which("uv")
    if uv_bin:
        return uv_bin
    return "uv"


def _systemd_unit_text(home: Path) -> str:
    uv_bin = _service_uv_bin()
    return dedent(
        f"""\
        [Unit]
        Description=open-strix agent ({home.name})
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        WorkingDirectory={home}
        ExecStart={uv_bin} run open-strix run --home {home}
        Restart=on-failure
        RestartSec=5
        Environment=PYTHONUNBUFFERED=1

        [Install]
        WantedBy=default.target
        """,
    )


def _launchd_label(home: Path) -> str:
    return f"ai.open-strix.{_service_slug(home.name)}"


def _launchd_plist_text(home: Path) -> str:
    uv_bin = _service_uv_bin()
    label = _launchd_label(home)
    stdout_path = home / "logs" / "service.stdout.log"
    stderr_path = home / "logs" / "service.stderr.log"
    return dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>{label}</string>
          <key>ProgramArguments</key>
          <array>
            <string>{uv_bin}</string>
            <string>run</string>
            <string>open-strix</string>
            <string>run</string>
            <string>--home</string>
            <string>{home}</string>
          </array>
          <key>WorkingDirectory</key>
          <string>{home}</string>
          <key>RunAtLoad</key>
          <true/>
          <key>KeepAlive</key>
          <true/>
          <key>StandardOutPath</key>
          <string>{stdout_path}</string>
          <key>StandardErrorPath</key>
          <string>{stderr_path}</string>
        </dict>
        </plist>
        """,
    )


def _windows_task_name(home: Path) -> str:
    return f"OpenStrix-{_service_slug(home.name)}"


def _windows_task_install_ps1(home: Path) -> str:
    uv_bin = _service_uv_bin().replace('"', '`"')
    home_value = str(home).replace('"', '`"')
    task_name = _windows_task_name(home)
    return dedent(
        f"""\
        $ErrorActionPreference = "Stop"
        $TaskName = "{task_name}"
        $HomeDir = "{home_value}"
        $Uv = "{uv_bin}"
        $Args = "run open-strix run --home `"$HomeDir`""

        $Action = New-ScheduledTaskAction -Execute $Uv -Argument $Args
        $Trigger = New-ScheduledTaskTrigger -AtLogOn
        $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "open-strix agent" -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Installed and started task: $TaskName"
        """,
    )


def _windows_task_uninstall_ps1(home: Path) -> str:
    task_name = _windows_task_name(home)
    return dedent(
        f"""\
        $ErrorActionPreference = "Stop"
        $TaskName = "{task_name}"
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {{
          Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
          Write-Host "Removed task: $TaskName"
        }} else {{
          Write-Host "Task not found: $TaskName"
        }}
        """,
    )


def _write_service_assets(home: Path) -> None:
    services_dir = home / "services"
    services_dir.mkdir(parents=True, exist_ok=True)

    platform = _platform_key()
    if platform == "linux":
        _write_if_missing(services_dir / "open-strix.service", _systemd_unit_text(home))
        return
    if platform == "macos":
        _write_if_missing(
            services_dir / f"{_launchd_label(home)}.plist",
            _launchd_plist_text(home),
        )
        return
    if platform == "windows":
        _write_if_missing(
            services_dir / "install-open-strix-task.ps1",
            _windows_task_install_ps1(home),
        )
        _write_if_missing(
            services_dir / "uninstall-open-strix-task.ps1",
            _windows_task_uninstall_ps1(home),
        )


def _service_setup_section(home: Path) -> str:
    platform = _platform_key()
    tools = _service_tools()
    services_dir = home / "services"

    if platform == "linux":
        unit_path = services_dir / "open-strix.service"
        if tools["systemctl"]:
            log_cmd = "journalctl --user -u open-strix.service -f"
            if not tools["journalctl"]:
                log_cmd = "systemctl --user status open-strix.service"
            return dedent(
                f"""\
                6) Optional: run as a service (Linux/systemd user service)
                - generated file: {unit_path}
                - install commands:
                  - mkdir -p ~/.config/systemd/user
                  - cp "{unit_path}" ~/.config/systemd/user/open-strix.service
                  - systemctl --user daemon-reload
                  - systemctl --user enable --now open-strix.service
                  - {log_cmd}
                - if you need it to run without an active login session:
                  - loginctl enable-linger "$USER"
                """
            ).strip("\n")
        return dedent(
            f"""\
            6) Optional: run as a service (Linux)
            - generated file: {unit_path}
            - `systemctl` was not detected in PATH, so automatic systemd commands are omitted.
            - if your distro uses systemd, install `systemctl` tooling and use the unit above.
            """
        ).strip("\n")

    if platform == "macos":
        plist_path = services_dir / f"{_launchd_label(home)}.plist"
        label = _launchd_label(home)
        if tools["launchctl"]:
            return dedent(
                f"""\
                6) Optional: run as a service (macOS launchd)
                - generated file: {plist_path}
                - install commands:
                  - mkdir -p ~/Library/LaunchAgents
                  - cp "{plist_path}" ~/Library/LaunchAgents/{label}.plist
                  - launchctl unload ~/Library/LaunchAgents/{label}.plist
                  - launchctl load ~/Library/LaunchAgents/{label}.plist
                  - launchctl start {label}
                """
            ).strip("\n")
        return dedent(
            f"""\
            6) Optional: run as a service (macOS)
            - generated file: {plist_path}
            - `launchctl` was not detected in PATH, so launchd install commands are omitted.
            """
        ).strip("\n")

    if platform == "windows":
        install_script = services_dir / "install-open-strix-task.ps1"
        uninstall_script = services_dir / "uninstall-open-strix-task.ps1"
        powershell_bin = "pwsh" if tools["pwsh"] else "powershell" if tools["powershell"] else ""
        if tools["schtasks"] and powershell_bin:
            return dedent(
                f"""\
                6) Optional: run as a service (Windows Task Scheduler)
                - generated files:
                  - {install_script}
                  - {uninstall_script}
                - install command:
                  - {powershell_bin} -ExecutionPolicy Bypass -File "{install_script}"
                - uninstall command:
                  - {powershell_bin} -ExecutionPolicy Bypass -File "{uninstall_script}"
                """
            ).strip("\n")
        return dedent(
            f"""\
            6) Optional: run as a service (Windows)
            - generated files:
              - {install_script}
              - {uninstall_script}
            - missing required tools (`schtasks` and/or PowerShell). Install them, then run the script.
            """
        ).strip("\n")

    return dedent(
        """\
        6) Optional: run as a service
        - Service bootstrap files were not generated because this OS was not recognized.
        """
    ).strip("\n")


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower().strip()


def _requirement_distribution_name(requirement: str) -> str:
    text = requirement.split(";", maxsplit=1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", text)
    if match is None:
        return ""
    return _normalize_distribution_name(match.group(1))


def _project_depends_on_open_strix(pyproject_path: Path) -> bool:
    if not pyproject_path.exists():
        return False
    try:
        loaded = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False

    project = loaded.get("project", {})
    if not isinstance(project, dict):
        return False
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list):
        return False

    for raw in dependencies:
        dep_name = _requirement_distribution_name(str(raw))
        if dep_name == "open-strix":
            return True
    return False


def _ensure_uv_project(home: Path) -> None:
    pyproject_path = home / "pyproject.toml"
    if not pyproject_path.exists():
        init_proc = _run_command(
            ["uv", "init", "--bare", "--python", "3.11", "--vcs", "none", "--no-workspace"],
            cwd=home,
        )
        if init_proc.returncode != 0:
            message = init_proc.stderr.strip() or init_proc.stdout.strip() or "unknown error"
            raise RuntimeError(f"uv init failed: {message}")

    if _project_depends_on_open_strix(pyproject_path):
        return

    add_proc = _run_command(["uv", "add", "open-strix"], cwd=home)
    if add_proc.returncode != 0:
        message = add_proc.stderr.strip() or add_proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"uv add open-strix failed: {message}")


def _ensure_git_repo(home: Path) -> None:
    if (home / ".git").exists():
        return

    init_proc = _run_command(["git", "init", "-b", "main"], cwd=home)
    if init_proc.returncode == 0:
        return

    fallback_proc = _run_command(["git", "init"], cwd=home)
    if fallback_proc.returncode != 0:
        raise RuntimeError(f"git init failed: {fallback_proc.stderr.strip()}")
    _run_command(["git", "branch", "-M", "main"], cwd=home)


def _git_config_get(home: Path, key: str) -> str:
    proc = _run_command(["git", "config", "--get", key], cwd=home)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _git_config_set(home: Path, key: str, value: str) -> None:
    proc = _run_command(["git", "config", key, value], cwd=home)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"git config {key} failed: {message}")


def _ensure_git_identity(home: Path) -> None:
    existing_name = _git_config_get(home, "user.name")
    existing_email = _git_config_get(home, "user.email")
    if existing_name and existing_email:
        return

    if not sys.stdin.isatty():
        missing: list[str] = []
        if not existing_name:
            missing.append("user.name")
        if not existing_email:
            missing.append("user.email")
        raise RuntimeError(
            "Git identity is not configured ({missing}). "
            "Run `open-strix setup` interactively to provide git commit identity.".format(
                missing=", ".join(missing),
            ),
        )

    default_name = existing_name or home.name
    print(
        "Git commit identity is missing for this repo. "
        "Provide values to avoid commit failures.",
        flush=True,
    )

    try:
        raw_name = input(f"Git commit user name [{default_name}]: ").strip()
    except EOFError as exc:
        raise RuntimeError("Setup aborted: input stream closed.") from exc
    except KeyboardInterrupt as exc:
        raise RuntimeError("Setup aborted by user.") from exc
    resolved_name = raw_name or default_name
    if not resolved_name:
        raise RuntimeError("Git commit user name cannot be empty.")

    email_default = existing_email
    while True:
        email_prompt = (
            f"Git commit email [{email_default}]: "
            if email_default
            else "Git commit email: "
        )
        try:
            raw_email = input(email_prompt).strip()
        except EOFError as exc:
            raise RuntimeError("Setup aborted: input stream closed.") from exc
        except KeyboardInterrupt as exc:
            raise RuntimeError("Setup aborted by user.") from exc
        resolved_email = raw_email or email_default
        if resolved_email and "@" in resolved_email:
            break
        print("Please enter a valid email address.", flush=True)

    _git_config_set(home, "user.name", resolved_name)
    _git_config_set(home, "user.email", resolved_email)


def _ensure_initial_commit(home: Path) -> bool:
    add_proc = _run_command(["git", "add", "-A"], cwd=home)
    if add_proc.returncode != 0:
        return False

    status_proc = _run_command(["git", "status", "--porcelain"], cwd=home)
    if status_proc.returncode != 0:
        return False
    if not status_proc.stdout.strip():
        return True

    commit_proc = _run_command(
        ["git", "commit", "-m", "Initial open-strix scaffold"],
        cwd=home,
    )
    return commit_proc.returncode == 0


def _github_login(home: Path) -> str:
    proc = _run_command(["gh", "api", "user"], cwd=home)
    if proc.returncode != 0:
        return ""
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    return str(payload.get("login", "")).strip()


def _github_repo_ref(home: Path, repo: str) -> str:
    if "/" in repo:
        return repo
    login = _github_login(home)
    if not login:
        return repo
    return f"{login}/{repo}"


def _github_existing_repo_remote_url(home: Path, repo: str) -> str:
    repo_ref = _github_repo_ref(home, repo)
    view_proc = _run_command(
        ["gh", "repo", "view", repo_ref, "--json", "sshUrl,url"],
        cwd=home,
    )
    if view_proc.returncode != 0:
        return ""

    try:
        payload = json.loads(view_proc.stdout or "{}")
    except json.JSONDecodeError:
        return ""

    ssh_url = str(payload.get("sshUrl", "")).strip()
    https_url = str(payload.get("url", "")).strip()
    return ssh_url or https_url


def _ensure_github_remote(home: Path, repo_name: str | None = None) -> None:
    if shutil.which("gh") is None:
        print("`gh` not found; skipping GitHub remote setup.", flush=True)
        return

    existing_origin = _run_command(["git", "remote", "get-url", "origin"], cwd=home)
    if existing_origin.returncode == 0:
        return

    auth_status = _run_command(["gh", "auth", "status"], cwd=home)
    if auth_status.returncode != 0:
        print("GitHub CLI is not authenticated; run `gh auth login` then rerun setup with `--github`.", flush=True)
        return

    repo = repo_name.strip() if repo_name else home.name
    create_proc = _run_command(
        ["gh", "repo", "create", repo, "--private", "--source=.", "--remote=origin"],
        cwd=home,
    )
    if create_proc.returncode != 0:
        existing_repo_url = _github_existing_repo_remote_url(home, repo)
        if existing_repo_url:
            try:
                _git_remote_add_origin(home, existing_repo_url)
            except RuntimeError as exc:
                print(str(exc), flush=True)
                return
            print(
                f"GitHub repo `{repo}` already exists; configured origin to `{existing_repo_url}`.",
                flush=True,
            )
            if _ensure_initial_commit(home):
                push_proc = _run_command(["git", "push", "-u", "origin", "HEAD"], cwd=home)
                if push_proc.returncode != 0:
                    print(
                        f"Existing GitHub repo linked, but initial push failed: {push_proc.stderr.strip()}",
                        flush=True,
                    )
            else:
                print(
                    "Existing GitHub repo linked. Initial commit/push skipped (check git user.name/user.email).",
                    flush=True,
                )
            return
        print(
            f"Failed to create private GitHub repo `{repo}`: {create_proc.stderr.strip()}",
            flush=True,
        )
        return

    if _ensure_initial_commit(home):
        push_proc = _run_command(["git", "push", "-u", "origin", "HEAD"], cwd=home)
        if push_proc.returncode != 0:
            print(f"GitHub remote created, but initial push failed: {push_proc.stderr.strip()}", flush=True)
    else:
        print("GitHub remote created. Initial commit/push skipped (check git user.name/user.email).", flush=True)


def _git_origin_remote_url(home: Path) -> str:
    proc = _run_command(["git", "remote", "get-url", "origin"], cwd=home)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _git_remote_add_origin(home: Path, remote_url: str) -> None:
    proc = _run_command(["git", "remote", "add", "origin", remote_url], cwd=home)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"git remote add origin failed: {message}")


def _ensure_git_push_defaults(home: Path) -> None:
    # Make plain `git push` work predictably in this repo.
    if not _git_config_get(home, "remote.pushDefault"):
        _git_config_set(home, "remote.pushDefault", "origin")
    if not _git_config_get(home, "push.default"):
        _git_config_set(home, "push.default", "current")
    if not _git_config_get(home, "push.autoSetupRemote"):
        _git_config_set(home, "push.autoSetupRemote", "true")


def _ensure_git_remote(home: Path, *, github: bool = False, repo_name: str | None = None) -> None:
    existing_origin = _git_origin_remote_url(home)
    if existing_origin:
        _ensure_git_push_defaults(home)
        return

    if github:
        _ensure_github_remote(home=home, repo_name=repo_name)
        existing_origin = _git_origin_remote_url(home)
        if existing_origin:
            _ensure_git_push_defaults(home)
            return

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Git remote `origin` is not configured. Run setup interactively to provide a remote URL "
            "or rerun with --github after installing/authenticating `gh`.",
        )

    print(
        "Git remote `origin` is missing. open-strix auto-push needs a remote destination.",
        flush=True,
    )
    print(
        "Enter a remote URL (example: git@github.com:you/repo.git or https://github.com/you/repo.git).",
        flush=True,
    )
    while True:
        try:
            remote_url = input("Origin remote URL: ").strip()
        except EOFError as exc:
            raise RuntimeError("Setup aborted: input stream closed.") from exc
        except KeyboardInterrupt as exc:
            raise RuntimeError("Setup aborted by user.") from exc
        if remote_url:
            break
        print("Origin remote URL is required.", flush=True)

    _git_remote_add_origin(home, remote_url)
    _ensure_git_push_defaults(home)


def _raise_missing_gh_install_instructions() -> None:
    raise RuntimeError(
        "`gh` is required for --github but was not found in PATH.\n"
        "Install GitHub CLI, then rerun setup.\n"
        "Examples:\n"
        "  macOS (Homebrew): brew install gh\n"
        "  Ubuntu/Debian:     sudo apt install gh\n"
        "  Windows (winget):  winget install --id GitHub.cli\n"
        "Docs: https://cli.github.com/",
    )


def setup_home(home: Path, *, github: bool = False, repo_name: str | None = None) -> None:
    if shutil.which("git") is None:
        raise RuntimeError("`git` is required for setup but was not found in PATH.")
    if shutil.which("uv") is None:
        raise RuntimeError("`uv` is required for setup but was not found in PATH.")

    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)

    if github and shutil.which("gh") is None:
        _raise_missing_gh_install_instructions()

    _ensure_git_repo(home)
    _ensure_git_identity(home)
    _ensure_uv_project(home)

    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout=layout, checkpoint_text=DEFAULT_CHECKPOINT)
    _write_if_missing(home / ".env", DEFAULT_ENV)
    _write_service_assets(home)

    _ensure_git_remote(home=home, github=github, repo_name=repo_name)

    print(f"open-strix setup complete: {home}", flush=True)
    _print_setup_walkthrough(home)


def _print_setup_walkthrough(home: Path) -> None:
    env_path = home / ".env"
    config_path = home / "config.yaml"
    service_section = _service_setup_section(home)
    text = dedent(
        f"""

        Setup walkthrough

        Files to edit:
        - env: {env_path}
        - config: {config_path}

        1) Choose model provider

        Option A: MiniMax M2.5 (default in config)
        - open platform: {MINIMAX_PLATFORM_URL}
        - docs (Anthropic-compatible API): {MINIMAX_ANTHROPIC_DOC_URL}
        - coding model context: {MINIMAX_CODING_DOC_URL}
        - create API key in MiniMax console, then set:
          - ANTHROPIC_API_KEY=<your_minimax_key>
          - ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
        - keep config.yaml model as:
          - model: MiniMax-M2.5

        Option B: Kimi (Moonshot, K2 family)
        - open platform: {MOONSHOT_PLATFORM_URL}
        - docs: {MOONSHOT_DOCS_URL}
        - K2 update post: {MOONSHOT_K2_POST_URL}
        - create API key in Moonshot console, then set:
          - ANTHROPIC_API_KEY=<your_moonshot_key>
          - ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic
        - update config.yaml model to your current Kimi model ID from Moonshot docs/console.

        2) Set up Tavily web search (recommended)
        - open platform: {TAVILY_PLATFORM_URL}
        - create an API key, then set in .env:
          - TAVILY_API_KEY=<your_tavily_key>
        - if omitted, open-strix starts normally but the `web_search` tool is disabled.

        3) Set up Discord bot
        - First click: {DISCORD_DEV_PORTAL_URL}
        - General Information: set bot name and basic app metadata.
        - Installation: set Install Link to None, then save.
        - OAuth2 -> URL Generator:
          - check `bot`
          - choose practical bot permissions (Most things from Text Permissions):
            View Channels, Send Messages, Send Messages in Threads, Read Message History, Add Reactions, Attach Files, Create Threads, Attach Files, Embed Links.
          - Copy URL and paste into your browser where you're logged into Discord. Select a server, Continue, Authorize. Might as well start a DM with the bot now.
        - Bot tab:
          - disable Public Bot
          - enable Message Content Intent
          - (later) set avatar/profile polish
        - Bot tab -> Reset Token:
          - copy token immediately and set in `.env`:
            DISCORD_TOKEN=<your_discord_bot_token>
        - helpful docs:
          - create app + bot: {DISCORD_DEV_PORTAL_URL}
          - getting started: {DISCORD_GETTING_STARTED_URL}
          - OAuth scopes docs: {DISCORD_OAUTH_URL}
          - permissions docs: {DISCORD_PERMISSIONS_URL}
          - gateway/intents docs: {DISCORD_GATEWAY_URL}

        4) Config walkthrough (config.yaml)
        - model: model name or provider:model
        - journal_entries_in_prompt: number of journal rows injected into prompt
        - discord_messages_in_prompt: number of recent Discord messages in prompt
        - discord_token_env: env var to read Discord token from (default DISCORD_TOKEN)
        - always_respond_bot_ids: bot IDs this agent is allowed to respond to

        5) Run
        - start agent: uv run open-strix
        - if no token is set, open-strix runs stdin mode.

        {service_section}
        """
    ).strip("\n")
    print(text, flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="open-strix")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the open-strix event loop.")
    run_parser.add_argument("--home", type=Path, default=None, help="Agent home directory (default: cwd).")

    setup_parser = subparsers.add_parser(
        "setup",
        help="Scaffold an open-strix home (uv project + git + config + state + .env).",
    )
    setup_parser.add_argument(
        "--home",
        type=Path,
        default=Path.cwd(),
        help="Target directory to scaffold (default: cwd).",
    )
    setup_parser.add_argument(
        "--github",
        action="store_true",
        help="Also create a private GitHub repo with `gh` and configure origin.",
    )
    setup_parser.add_argument(
        "--repo-name",
        type=str,
        default=None,
        help="GitHub repo name override (default: directory name).",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command in (None, "run"):
        run_home = getattr(args, "home", None)
        run_open_strix(home=run_home)
        return

    if args.command == "setup":
        try:
            setup_home(home=args.home, github=bool(args.github), repo_name=args.repo_name)
        except RuntimeError as exc:
            print(str(exc), flush=True)
            sys.exit(1)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
