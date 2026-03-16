from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .builtin_skills import BUILTIN_HOME_DIRNAME, sync_builtin_skills_home
from .mcp_client import MCPServerConfig, parse_mcp_server_configs

DEFAULT_MODEL = "MiniMax-M2.5"
DEFAULT_MODEL_PROVIDER = "anthropic"
STATE_DIR_NAME = "state"
DEFAULT_WEB_UI_HOST = "127.0.0.1"
DEFAULT_WEB_UI_CHANNEL_ID = "local-web"

DEFAULT_FOLDERS: dict[str, str] = {
    "state": "rw",
    "skills": "rw",
    "blocks": "ro",
    "scripts": "ro",
    "logs": "ro",
}

DEFAULT_CONFIG = """\
model: MiniMax-M2.5
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
discord_channel_allowlist: []
api_port: 0
web_ui_port: 0
web_ui_host: 127.0.0.1
web_ui_channel_id: local-web
folders:
  state: rw
  skills: rw
  blocks: ro
  scripts: ro
  logs: ro
"""

DEFAULT_SCHEDULER = """\
jobs:
  - name: prediction-review-twice-daily
    cron: "0 9,21 * * *"
    prompt: |
      Run the prediction-review skill.
      Review journal predictions from 2-3 days ago.
      Use `logs/events.jsonl` and Discord history as ground truth.
      For each reviewed prediction, append a structured entry:
      `uv run python .open_strix_builtin_skills/scripts/prediction_review_log.py --prediction-datetime ... --is-true ... --comments ...`
      Include evidence and behavior adjustments in comments.
"""

DEFAULT_INIT_BLOCK = """\
name: init
sort_order: -100
text: |
  You're a new agent. Read the onboarding skill to learn how to get started.
  Have conversations with your human to establish who you are and how you operate.
  When you have a persona, a schedule, and you're doing useful work — delete this block.
"""

DEFAULT_PHONE_BOOK_EXTRA = """\
# Phone Book — Manual Notes

This file is for manually curated context that the auto-generated `phone-book.md` can't capture.
Edit freely — this file is never overwritten by the bot.

## Channel Notes

<!-- Add notes about what each channel is for, who hangs out there, and any conventions. -->
<!-- Example:
| Channel | Purpose | Notes |
|---------|---------|-------|
| #general | Main chat | Keep it casual, Tim checks this most |
| #research | Paper discussion | Verge posts arXiv finds here |
-->

## External Comms

<!-- Other communication channels outside Discord — Bluesky, Slack, email, etc. -->
<!-- Example:
| Platform | Handle/Link | Notes |
|----------|-------------|-------|
| Bluesky | @handle.bsky.social | Main public posting account |
-->

## People Notes

<!-- Context about people that IDs and names don't capture — roles, preferences, relationships. -->
<!-- Example:
- **Tim** — The human. Eastern time. ADHD. Prefers autonomy-supportive language.
- **Lily** — Runs Atlas. Marketing ops. Based in Atlanta.
-->
"""

DEFAULT_PRE_COMMIT_SCRIPT = """\
def main() -> None:
    # Placeholder script for project-specific pre-commit checks.
    pass


if __name__ == "__main__":
    main()
"""


@dataclass(frozen=True)
class RepoLayout:
    home: Path
    state_dir_name: str

    @property
    def state_dir(self) -> Path:
        return self.home / self.state_dir_name

    @property
    def phone_book_file(self) -> Path:
        return self.state_dir / "phone-book.md"

    @property
    def phone_book_extra_file(self) -> Path:
        return self.state_dir / "phone-book.extra.md"

    @property
    def blocks_dir(self) -> Path:
        return self.home / "blocks"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def scripts_dir(self) -> Path:
        return self.home / "scripts"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def sessions_dir(self) -> Path:
        return self.logs_dir / "sessions"

    @property
    def events_log(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def journal_log(self) -> Path:
        return self.logs_dir / "journal.jsonl"

    @property
    def chat_history_log(self) -> Path:
        return self.logs_dir / "chat-history.jsonl"

    @property
    def scheduler_file(self) -> Path:
        return self.home / "scheduler.yaml"

    @property
    def config_file(self) -> Path:
        return self.home / "config.yaml"

    @property
    def checkpoint_file(self) -> Path:
        return self.home / "checkpoint.md"

    @property
    def env_file(self) -> Path:
        return self.home / ".env"


@dataclass
class AppConfig:
    model: str = DEFAULT_MODEL
    name: str = ""
    journal_entries_in_prompt: int = 90
    discord_messages_in_prompt: int = 10
    discord_token_env: str = "DISCORD_TOKEN"
    always_respond_bot_ids: set[str] = field(default_factory=set)
    discord_channel_allowlist: set[str] = field(default_factory=set)
    session_log_retention_days: int = 30
    api_port: int = 0
    web_ui_port: int = 0
    web_ui_host: str = DEFAULT_WEB_UI_HOST
    web_ui_channel_id: str = DEFAULT_WEB_UI_CHANNEL_ID
    folders: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FOLDERS))
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    disable_builtin_skills: set[str] = field(default_factory=set)

    @property
    def writable_dirs(self) -> list[str]:
        return [name for name, mode in self.folders.items() if mode == "rw"]

    @property
    def all_dirs(self) -> list[str]:
        return list(self.folders.keys())


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


def _normalize_id_list(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
        return {item for item in raw_items if item}
    if isinstance(value, list):
        normalized = {
            str(item).strip()
            for item in value
            if str(item).strip()
        }
        return normalized
    return set()


def _parse_folders(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_FOLDERS)
    folders: dict[str, str] = {}
    for name, mode in raw.items():
        name_str = str(name).strip()
        mode_str = str(mode).strip().lower()
        if mode_str not in ("rw", "ro"):
            mode_str = "ro"
        if name_str:
            folders[name_str] = mode_str
    return folders if folders else dict(DEFAULT_FOLDERS)


def load_config(layout: RepoLayout) -> AppConfig:
    loaded = yaml.safe_load(layout.config_file.read_text(encoding="utf-8")) or {}
    model_raw = loaded.get("model", DEFAULT_MODEL)
    model = str(model_raw).strip() if model_raw is not None else ""
    if not model:
        model = DEFAULT_MODEL
    return AppConfig(
        model=model,
        name=str(loaded.get("name", "")).strip(),
        journal_entries_in_prompt=int(loaded.get("journal_entries_in_prompt", 90)),
        discord_messages_in_prompt=int(loaded.get("discord_messages_in_prompt", 10)),
        discord_token_env=str(loaded.get("discord_token_env", "DISCORD_TOKEN")),
        always_respond_bot_ids=_normalize_id_list(loaded.get("always_respond_bot_ids")),
        discord_channel_allowlist=_normalize_id_list(loaded.get("discord_channel_allowlist")),
        session_log_retention_days=int(loaded.get("session_log_retention_days", 30)),
        api_port=int(loaded.get("api_port", 0)),
        web_ui_port=int(loaded.get("web_ui_port", 0)),
        web_ui_host=str(loaded.get("web_ui_host", DEFAULT_WEB_UI_HOST)).strip() or DEFAULT_WEB_UI_HOST,
        web_ui_channel_id=str(loaded.get("web_ui_channel_id", DEFAULT_WEB_UI_CHANNEL_ID)).strip()
        or DEFAULT_WEB_UI_CHANNEL_ID,
        folders=_parse_folders(loaded.get("folders")),
        mcp_servers=parse_mcp_server_configs(loaded.get("mcp_servers")),
        disable_builtin_skills=_normalize_id_list(loaded.get("disable_builtin_skills")),
    )


def _ensure_config_defaults(config_file: Path) -> None:
    loaded = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        loaded = {}

    changed = False
    model_raw = loaded.get("model")
    model = str(model_raw).strip() if model_raw is not None else ""
    if not model:
        loaded["model"] = DEFAULT_MODEL
        changed = True

    if "always_respond_bot_ids" not in loaded:
        loaded["always_respond_bot_ids"] = []
        changed = True

    if "api_port" not in loaded:
        loaded["api_port"] = 0
        changed = True

    if "web_ui_port" not in loaded:
        loaded["web_ui_port"] = 0
        changed = True

    if "web_ui_host" not in loaded:
        loaded["web_ui_host"] = DEFAULT_WEB_UI_HOST
        changed = True

    if "web_ui_channel_id" not in loaded:
        loaded["web_ui_channel_id"] = DEFAULT_WEB_UI_CHANNEL_ID
        changed = True

    if "git_sync_after_turn" in loaded:
        loaded.pop("git_sync_after_turn", None)
        changed = True

    if "folders" not in loaded:
        loaded["folders"] = dict(DEFAULT_FOLDERS)
        changed = True

    if changed:
        config_file.write_text(yaml.safe_dump(loaded, sort_keys=False), encoding="utf-8")


def bootstrap_home_repo(
    layout: RepoLayout,
    checkpoint_text: str,
    *,
    disabled_builtin_skills: set[str] | None = None,
) -> None:
    # Ensure config exists first so we can read folders from it.
    _write_if_missing(layout.config_file, DEFAULT_CONFIG)
    _ensure_config_defaults(layout.config_file)

    # Create directories from config.
    loaded = yaml.safe_load(layout.config_file.read_text(encoding="utf-8")) or {}
    folders = _parse_folders(loaded.get("folders"))
    for name in folders:
        folder_path = layout.home / name
        folder_path.mkdir(parents=True, exist_ok=True)
        (folder_path / ".gitkeep").touch(exist_ok=True)

    # Sessions subdirectory under logs.
    if "logs" in folders:
        layout.sessions_dir.mkdir(parents=True, exist_ok=True)
        layout.events_log.touch(exist_ok=True)
        layout.journal_log.touch(exist_ok=True)
        layout.chat_history_log.touch(exist_ok=True)

    if "blocks" in folders:
        _write_if_missing(layout.blocks_dir / "init.yaml", DEFAULT_INIT_BLOCK)
    _write_if_missing(layout.phone_book_extra_file, DEFAULT_PHONE_BOOK_EXTRA)
    _write_if_missing(layout.scheduler_file, DEFAULT_SCHEDULER)
    _write_if_missing(layout.checkpoint_file, checkpoint_text)
    if "scripts" in folders:
        _write_if_missing(layout.scripts_dir / "pre_commit.py", DEFAULT_PRE_COMMIT_SCRIPT)
    sync_builtin_skills_home(layout.home, disabled_skills=disabled_builtin_skills)
    _cleanup_legacy_builtin_scripts(layout)
    _install_git_hook(layout.home)
    _ensure_logs_ignored(layout.home)


def _cleanup_legacy_builtin_scripts(layout: RepoLayout) -> None:
    legacy_names = [
        "prediction_review_log.py",
        "memory_dashboard.py",
        "file_frequency_report.py",
    ]
    builtin_scripts_dir = layout.home / BUILTIN_HOME_DIRNAME / "scripts"
    for name in legacy_names:
        legacy_path = layout.scripts_dir / name
        if not legacy_path.exists() or not legacy_path.is_file():
            continue
        builtin_path = builtin_scripts_dir / name
        if not builtin_path.exists() or not builtin_path.is_file():
            continue
        if legacy_path.read_text(encoding="utf-8") != builtin_path.read_text(encoding="utf-8"):
            continue
        legacy_path.unlink()


def _install_git_hook(home: Path) -> None:
    hooks_dir = home / ".git" / "hooks"
    if not hooks_dir.exists():
        return
    pre_commit = hooks_dir / "pre-commit"
    hook = """#!/bin/sh
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

# Hooks can run with a minimal PATH, so prefer explicit locations first.
if [ -x "$repo_root/.venv/bin/uv" ]; then
  exec "$repo_root/.venv/bin/uv" run python scripts/pre_commit.py
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run python scripts/pre_commit.py
fi

if [ -x "$HOME/.local/bin/uv" ]; then
  exec "$HOME/.local/bin/uv" run python scripts/pre_commit.py
fi

if command -v python3 >/dev/null 2>&1 && python3 -c "import uv" >/dev/null 2>&1; then
  exec python3 -m uv run python scripts/pre_commit.py
fi

# Last resort: run the script directly with Python.
if [ -x "$repo_root/.venv/bin/python" ]; then
  exec "$repo_root/.venv/bin/python" scripts/pre_commit.py
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/pre_commit.py
fi
if command -v python >/dev/null 2>&1; then
  exec python scripts/pre_commit.py
fi

echo "[open-strix pre-commit] uv/python not found; cannot run scripts/pre_commit.py" >&2
exit 1
"""
    pre_commit.write_text(hook, encoding="utf-8")
    pre_commit.chmod(0o755)


def _ensure_logs_ignored(home: Path) -> None:
    gitignore_path = home / ".gitignore"
    required_entries = [
        "logs/",
        "logs/chat-history.jsonl",
        ".env",
        f"{BUILTIN_HOME_DIRNAME}/",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.webp",
        "*.svg",
        "*.bmp",
        "*.tif",
        "*.tiff",
        "*.avif",
        "*.heic",
        "*.ico",
    ]
    if not gitignore_path.exists():
        gitignore_path.write_text("\n".join(required_entries) + "\n", encoding="utf-8")
        return

    lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    normalized = {line.strip() for line in lines}
    missing = [entry for entry in required_entries if entry not in normalized]
    if not missing:
        return
    with gitignore_path.open("a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        for entry in missing:
            f.write(f"{entry}\n")
