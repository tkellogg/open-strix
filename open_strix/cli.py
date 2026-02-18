from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

from .app import run_open_strix
from .config import RepoLayout, STATE_DIR_NAME, bootstrap_home_repo
from .prompts import DEFAULT_CHECKPOINT

DEFAULT_ENV = """\
# Anthropic-compatible endpoint
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic

# Discord bot token
DISCORD_TOKEN=

# Optional live test settings
DISCORD_TEST_CHANNEL_ID=
OPEN_STRIX_TEST_MODEL=
"""


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


def setup_home(home: Path, *, github: bool = False, repo_name: str | None = None) -> None:
    if shutil.which("git") is None:
        raise RuntimeError("`git` is required for setup but was not found in PATH.")

    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)

    _ensure_git_repo(home)

    layout = RepoLayout(home=home, state_dir_name=STATE_DIR_NAME)
    bootstrap_home_repo(layout=layout, checkpoint_text=DEFAULT_CHECKPOINT)
    _write_if_missing(home / ".env", DEFAULT_ENV)

    if github:
        _ensure_github_remote(home=home, repo_name=repo_name)

    print(f"open-strix setup complete: {home}", flush=True)
    print("Next steps:", flush=True)
    print("1) Fill in .env (at least ANTHROPIC_API_KEY and DISCORD_TOKEN).", flush=True)
    print("2) Start with: uvx open-strix", flush=True)


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
