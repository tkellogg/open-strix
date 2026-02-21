from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from textwrap import dedent
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


def _resolve_missing_gh_behavior() -> bool:
    """Handle --github when gh is missing.

    Returns:
        True if setup should continue with GitHub remote setup.
        False if setup should continue without GitHub remote setup.

    Raises:
        RuntimeError: If user chooses to abort or prompt is unavailable.
    """
    if shutil.which("gh") is not None:
        return True

    if not sys.stdin.isatty():
        raise RuntimeError(
            "`gh` is required for --github but was not found, and setup is non-interactive. "
            "Install gh first or rerun setup without --github.",
        )

    print("`gh` is required for --github but was not found.", flush=True)
    print("Choose one:", flush=True)
    print("  1) Wait while I install gh, then retry", flush=True)
    print("  2) Abandon setup", flush=True)
    print("  3) Continue setup without GitHub remote", flush=True)

    while True:
        try:
            choice = input("Enter 1, 2, or 3: ").strip()
        except EOFError as exc:
            raise RuntimeError("Setup aborted: input stream closed.") from exc
        except KeyboardInterrupt as exc:
            raise RuntimeError("Setup aborted by user.") from exc

        if choice == "1":
            while True:
                if shutil.which("gh") is not None:
                    print("Detected gh. Continuing with GitHub setup.", flush=True)
                    return True
                try:
                    retry = input(
                        "gh still not found. Press Enter to retry, or type 2 to abandon, 3 to continue without GitHub: ",
                    ).strip()
                except EOFError as exc:
                    raise RuntimeError("Setup aborted: input stream closed.") from exc
                except KeyboardInterrupt as exc:
                    raise RuntimeError("Setup aborted by user.") from exc

                if retry == "2":
                    raise RuntimeError("Setup aborted by user.")
                if retry == "3":
                    return False
                if retry:
                    print("Please enter 2, 3, or press Enter.", flush=True)
        elif choice == "2":
            raise RuntimeError("Setup aborted by user.")
        elif choice == "3":
            return False
        else:
            print("Invalid choice. Enter 1, 2, or 3.", flush=True)


def setup_home(home: Path, *, github: bool = False, repo_name: str | None = None) -> None:
    if shutil.which("git") is None:
        raise RuntimeError("`git` is required for setup but was not found in PATH.")

    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)

    if github and shutil.which("gh") is None:
        github = _resolve_missing_gh_behavior()

    _ensure_git_repo(home)

    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout=layout, checkpoint_text=DEFAULT_CHECKPOINT)
    _write_if_missing(home / ".env", DEFAULT_ENV)

    if github:
        _ensure_github_remote(home=home, repo_name=repo_name)

    print(f"open-strix setup complete: {home}", flush=True)
    _print_setup_walkthrough(home)


def _print_setup_walkthrough(home: Path) -> None:
    env_path = home / ".env"
    config_path = home / "config.yaml"
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
          - choose practical bot permissions (focus on messaging/reactions/history/attachments):
            View Channels, Send Messages, Send Messages in Threads, Read Message History, Add Reactions, Attach Files.
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
        - start agent: uvx open-strix
        - if no token is set, open-strix runs stdin mode.
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
        help="Scaffold an open-strix home (git + config + state + .env).",
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
