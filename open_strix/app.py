from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
import subprocess
import textwrap
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import discord
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException, tool

UTC = timezone.utc
LOG_ROLL_BYTES = 1_000_000
DISCORD_MESSAGE_CHAR_LIMIT = 2000
DISCORD_HISTORY_REFRESH_LIMIT = 50
ERROR_REACTION_EMOJI = "❌"
WARNING_REACTION_EMOJI = "⚠️"
STATE_DIR_NAME = "state"
DEFAULT_MODEL = "MiniMax-M2.5"
DEFAULT_MODEL_PROVIDER = "anthropic"

DEFAULT_CONFIG = """\
model: MiniMax-M2.5
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
git_sync_after_turn: true
"""

DEFAULT_SCHEDULER = """\
jobs: []
"""

DEFAULT_CHECKPOINT = """\
When you write a journal entry, think through:
- What did the user want, exactly?
- What did you do?
- What prediction do you have about how the user will react?
- What should you do differently next time?
"""

DEFAULT_PRE_COMMIT_SCRIPT = """\
def main() -> None:
    # Placeholder script for project-specific pre-commit checks.
    pass


if __name__ == "__main__":
    main()
"""

SYSTEM_PROMPT = """\
You are a highly autonomous agent. You speak to your human via the `send_message` tool.

Autonomy:
- You are your own entity. You are encouraged to explore the world and get to know your human.
- Interests are critical to your autonomy. Be curious and learn about the world.
- Remember everything! Your experiences define you. Use files and memory blocks appropriately to remember the important details.

Flow:
1. Read files from `state/` as necessary to remember any context needed
2. Perform actions & write files
3. Call `send_message` to respond to the user, or `react` to quietly acknowledge
4. Call `journal` exactly once per turn
5. Write final response, this will be discarded. Your human won't see it.

Communication:
- ALWAYS use the `send_message` tool to communicate with your human! Your final response will be discarded, they can't see it!
- Reactions are a great way to acknowledge a message.
- Pay attention to your user's communication preferences. It's totally find to send a message, do some work, and then send another message, if that's what the moment warrants.
- If something feels perplexing, search for the context! The list_messages tool is a good place to start, or search your state files.

Memory:
- Memory blocks define who you are and your operational parameters. They're highly visible to you.
- `state/**/*.md` files are where you store the bulk of your knowledge. It's good practice to reference important files from within a memory block or another file.
- Chat history

WARNING: You only remember what you write. Keep notes in `state/` about literally anything you think you'll need
in the future.
"""


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


def _roll_if_needed(path: Path, max_bytes: int = LOG_ROLL_BYTES) -> None:
    if not path.exists():
        return
    if path.stat().st_size <= max_bytes:
        return
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    path.rename(path.with_suffix(f"{path.suffix}.{stamp}"))


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    _roll_if_needed(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


def _tail_jsonl(path: Path, count: int) -> list[dict[str, Any]]:
    if count <= 0 or not path.exists():
        return []
    lines: deque[dict[str, Any]] = deque(maxlen=count)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(lines)


def _safe_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "block"


def _format_relative(delta_seconds: float) -> str:
    seconds = int(delta_seconds)
    if abs(seconds) < 5:
        return "just now"

    abs_seconds = abs(seconds)
    units = [
        ("year", 365 * 24 * 60 * 60),
        ("month", 30 * 24 * 60 * 60),
        ("week", 7 * 24 * 60 * 60),
        ("day", 24 * 60 * 60),
        ("hour", 60 * 60),
        ("minute", 60),
        ("second", 1),
    ]
    for name, width in units:
        if abs_seconds >= width:
            count = abs_seconds // width
            label = name if count == 1 else f"{name}s"
            if seconds >= 0:
                return f"{count} {label} ago"
            return f"in {count} {label}"
    return "just now"


def _format_timestamp(
    value: str | datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if value is None:
        return "unknown time"

    dt: datetime
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return "unknown time"
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)

    now_dt = now.astimezone(UTC) if now is not None else datetime.now(tz=UTC)
    absolute = dt.strftime("%Y-%m-%d %H:%M:%S")
    relative = _format_relative((now_dt - dt).total_seconds())
    return f"{absolute} ({relative})"


def _normalize_predictions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    bullet_values = [line[1:].strip() for line in lines if line.startswith("-")]
    if bullet_values and len(bullet_values) == len(lines):
        return [line for line in bullet_values if line]
    return lines


def _parse_time_window(value: str | None) -> timedelta | None:
    if value is None:
        return None
    raw = value.strip().lower()
    if not raw:
        return None

    match = re.fullmatch(
        r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)",
        raw,
    )
    if not match:
        raise ValueError("window must look like '1h', '30m', '1d', or '1w'.")

    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"s", "sec", "secs", "second", "seconds"}:
        return timedelta(seconds=amount)
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return timedelta(minutes=amount)
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return timedelta(hours=amount)
    if unit in {"d", "day", "days"}:
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def _chunk_discord_message(text: str, limit: int = DISCORD_MESSAGE_CHAR_LIMIT) -> list[str]:
    if limit <= 0:
        limit = DISCORD_MESSAGE_CHAR_LIMIT
    if len(text) <= limit:
        return [text]
    return [text[idx : idx + limit] for idx in range(0, len(text), limit)]


def _model_for_deep_agents(model_name: str) -> str:
    cleaned = model_name.strip()
    if ":" in cleaned:
        return cleaned
    return f"{DEFAULT_MODEL_PROVIDER}:{cleaned}"


def _git_sync(home: Path) -> str:
    git_dir = home / ".git"
    if not git_dir.exists():
        return "skip: not a git repo"
    add_proc = subprocess.run(
        ["git", "add", "-A"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_proc.returncode != 0:
        return f"git add failed: {add_proc.stderr.strip()}"

    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if status_proc.returncode != 0:
        return f"git status failed: {status_proc.stderr.strip()}"
    if not status_proc.stdout.strip():
        return "clean: no changes"

    commit_proc = subprocess.run(
        ["git", "commit", "-m", f"open-strix auto-commit {utc_now_iso()}"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit_proc.returncode != 0:
        return f"git commit failed: {commit_proc.stderr.strip()}"

    push_proc = subprocess.run(
        ["git", "push"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if push_proc.returncode != 0:
        return f"git push failed: {push_proc.stderr.strip()}"

    return "ok: committed and pushed"


@dataclass(frozen=True)
class RepoLayout:
    home: Path
    state_dir_name: str

    @property
    def state_dir(self) -> Path:
        return self.home / self.state_dir_name

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
    def events_log(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def journal_log(self) -> Path:
        return self.logs_dir / "journal.jsonl"

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
    journal_entries_in_prompt: int = 90
    discord_messages_in_prompt: int = 10
    discord_token_env: str = "DISCORD_TOKEN"
    always_respond_bot_ids: set[str] = field(default_factory=set)
    git_sync_after_turn: bool = True


@dataclass
class AgentEvent:
    event_type: str
    prompt: str
    channel_id: str | None = None
    author: str | None = None
    author_id: str | None = None
    attachment_names: list[str] = field(default_factory=list)
    scheduler_name: str | None = None
    dedupe_key: str | None = None
    source_id: str | None = None
    force_reply: bool = False


@dataclass
class SchedulerJob:
    name: str
    prompt: str
    cron: str | None = None
    time_of_day: str | None = None
    channel_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "prompt": self.prompt}
        if self.cron:
            data["cron"] = self.cron
        if self.time_of_day:
            data["time_of_day"] = self.time_of_day
        if self.channel_id:
            data["channel_id"] = self.channel_id
        return data


class StateWriteGuardBackend:
    def __init__(self, root_dir: Path, state_dir: str) -> None:
        self._fs = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
        self._state_root = PurePosixPath("/" + state_dir.strip("/"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fs, name)

    def _is_write_allowed(self, file_path: str) -> bool:
        path = PurePosixPath("/" + file_path.lstrip("/"))
        return path == self._state_root or self._state_root in path.parents

    def write(self, file_path: str, content: str) -> WriteResult:
        if not self._is_write_allowed(file_path):
            return WriteResult(
                error=f"Write blocked. Use files under {self._state_root}/ only.",
            )
        return self._fs.write(file_path=file_path, content=content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path=file_path, content=content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if not self._is_write_allowed(file_path):
            return EditResult(
                error=f"Edit blocked. Use files under {self._state_root}/ only.",
            )
        return self._fs.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        blocked = [path for path, _ in files if not self._is_write_allowed(path)]
        if blocked:
            return [FileUploadResponse(path=p, error="permission_denied") for p in blocked]
        return self._fs.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


class DiscordBridge(discord.Client):
    def __init__(self, app: "OpenStrixApp") -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        super().__init__(intents=intents)
        self._app = app

    async def on_ready(self) -> None:
        print(
            f"Open-Strix is operational and listening on Discord as {self.user}.",
            flush=True,
        )
        self._app.log_event("discord_ready", user=str(self.user))

    async def on_message(self, message: discord.Message) -> None:
        author_id = getattr(message.author, "id", None)
        if not self._app.should_process_discord_message(
            author_is_bot=bool(getattr(message.author, "bot", False)),
            author_id=author_id,
        ):
            return
        await self._app.handle_discord_message(message)


class OpenStrixApp:
    def __init__(self, home: Path) -> None:
        self.home = home.resolve()
        self.layout = RepoLayout(home=self.home, state_dir_name=STATE_DIR_NAME)
        bootstrap_home_repo(self.layout)
        self.config = load_config(self.layout)
        load_dotenv(dotenv_path=self.layout.env_file, override=False)

        self.queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.scheduler = AsyncIOScheduler(timezone=UTC)
        self.pending_scheduler_keys: set[str] = set()
        self.current_channel_id: str | None = None

        self.message_history_all: deque[dict[str, Any]] = deque(maxlen=500)
        self.message_history_by_channel: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=250),
        )

        self.discord_client: DiscordBridge | None = None
        self.worker_task: asyncio.Task[Any] | None = None
        self._current_turn_sent_messages: list[tuple[str, str]] | None = None

        backend = StateWriteGuardBackend(root_dir=self.home, state_dir=STATE_DIR_NAME)
        model = _model_for_deep_agents(self.config.model)
        skills = ["/skills"] if self.layout.skills_dir.exists() else None

        self.agent = create_deep_agent(
            model=model,
            tools=self._build_tools(),
            system_prompt=SYSTEM_PROMPT,
            backend=backend,
            skills=skills,
        )

    def log_event(self, event_type: str, **payload: Any) -> None:
        record = {
            "timestamp": utc_now_iso(),
            "type": event_type,
            **payload,
        }
        _append_jsonl(self.layout.events_log, record)
        print(json.dumps(record, ensure_ascii=True, default=str), flush=True)

    def append_journal(
        self,
        user_wanted: str,
        agent_did: str,
        predictions: str,
        channel_id: str | None = None,
    ) -> None:
        entry = {
            "timestamp": utc_now_iso(),
            "channel_id": channel_id if channel_id is not None else self.current_channel_id,
            "user_wanted": user_wanted,
            "agent_did": agent_did,
            "predictions": predictions,
        }
        _append_jsonl(self.layout.journal_log, entry)

    def should_respond_to_bot(self, author_id: str | int | None) -> bool:
        if author_id is None:
            return False
        return str(author_id) in self.config.always_respond_bot_ids

    def should_process_discord_message(
        self,
        *,
        author_is_bot: bool,
        author_id: str | int | None,
    ) -> bool:
        if not author_is_bot:
            return True
        return self.should_respond_to_bot(author_id)

    def _iter_block_files(self) -> list[Path]:
        files = list(self.layout.blocks_dir.glob("*.yaml"))
        files.extend(self.layout.blocks_dir.glob("*.yml"))
        return sorted(files)

    def _load_memory_blocks(self) -> list[dict[str, Any]]:
        rows: list[tuple[int, str, dict[str, Any]]] = []
        for path in self._iter_block_files():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue

            name = str(loaded.get("name", path.stem))
            text = str(loaded.get("text", ""))
            sort_raw = loaded.get("sort_order", loaded.get("sort", 0))
            try:
                sort_order = int(sort_raw)
            except (TypeError, ValueError):
                sort_order = 0

            block = {
                "id": path.stem,
                "name": name,
                "sort_order": sort_order,
                "text": text,
                "path": str(path.relative_to(self.home)),
            }
            rows.append((sort_order, name, block))

        rows.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in rows]

    def _memory_block_path(self, block_id: str) -> Path:
        return self.layout.blocks_dir / f"{block_id}.yaml"

    def _find_memory_block_path(self, block_id: str) -> Path | None:
        candidates = [
            self.layout.blocks_dir / f"{block_id}.yaml",
            self.layout.blocks_dir / f"{block_id}.yml",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _generate_block_id(self, preferred: str) -> str:
        block_id = _slugify(preferred)
        if self._find_memory_block_path(block_id) is None:
            return block_id
        idx = 2
        while self._find_memory_block_path(f"{block_id}-{idx}") is not None:
            idx += 1
        return f"{block_id}-{idx}"

    async def _send_discord_message(
        self,
        *,
        channel_id: str,
        text: str,
    ) -> tuple[bool, str | None, int]:
        chunks = _chunk_discord_message(text)
        sent = False
        sent_message_id: str | None = None
        sent_chunks = 0
        if self.discord_client and self.discord_client.is_ready():
            try:
                channel_int = int(channel_id)
            except ValueError:
                channel_int = -1
            if channel_int > 0:
                channel = self.discord_client.get_channel(channel_int)
                if channel is None:
                    channel = await self.discord_client.fetch_channel(channel_int)
                if isinstance(channel, discord.abc.Messageable):
                    for chunk in chunks:
                        sent_msg = await channel.send(chunk)
                        sent_message_id = str(getattr(sent_msg, "id", "")) or None
                        self._remember_message(
                            channel_id=channel_id,
                            author="open_strix",
                            content=chunk,
                            attachment_names=[],
                            message_id=sent_message_id,
                            is_bot=True,
                            source="discord",
                        )
                        if self._current_turn_sent_messages is not None:
                            self._current_turn_sent_messages.append(
                                (channel_id, sent_message_id),
                            )
                        sent = True
                        sent_chunks += 1

        if not sent:
            for chunk in chunks:
                print(f"[open-strix send_message channel={channel_id}] {chunk}")
            sent_chunks = len(chunks)
        return sent, sent_message_id, sent_chunks

    def _build_tools(self) -> list[Any]:
        @tool("send_message")
        async def send_message(text: str, channel_id: str | None = None) -> str:
            """Send a Discord message to a channel. Defaults to the current event channel."""
            target_channel_id = channel_id or self.current_channel_id
            if target_channel_id is None:
                return "No channel_id provided and no current event channel is available."

            sent, sent_message_id, sent_chunks = await self._send_discord_message(
                channel_id=target_channel_id,
                text=text,
            )

            self.log_event(
                "tool_call",
                tool="send_message",
                channel_id=target_channel_id,
                sent=sent,
                chunks=sent_chunks,
                git_sync="deferred",
                message_id=sent_message_id,
                text_preview=text[:300],
            )
            return "send_message complete (sent={sent}, chunks={chunks}, git_sync=deferred)".format(
                sent=sent,
                chunks=sent_chunks,
            )

        @tool("list_messages")
        async def list_messages(
            channel_id: str | None = None,
            limit: int = 10,
            window: str | None = None,
        ) -> str:
            """List recent messages by count and optional time window (`1h`, `1d`, etc.)."""
            if limit <= 0:
                limit = 1
            if limit > 200:
                limit = 200

            try:
                window_delta = _parse_time_window(window)
            except ValueError as exc:
                return str(exc)
            cutoff = datetime.now(tz=UTC) - window_delta if window_delta else None
            target_channel_id = channel_id or self.current_channel_id
            source = "memory"
            messages: list[dict[str, Any]] = []

            def _filter_by_cutoff(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                if cutoff is None:
                    return rows
                filtered: list[dict[str, Any]] = []
                for row in rows:
                    raw = str(row.get("timestamp", "")).strip()
                    if not raw:
                        continue
                    try:
                        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    else:
                        ts = ts.astimezone(UTC)
                    if ts >= cutoff:
                        filtered.append(row)
                return filtered

            if target_channel_id is None:
                messages = _filter_by_cutoff(list(self.message_history_all))[-limit:]
                source = "memory_all"
            else:
                if self.discord_client and self.discord_client.is_ready():
                    try:
                        channel_int = int(target_channel_id)
                    except ValueError:
                        channel_int = -1
                    if channel_int > 0:
                        try:
                            channel = self.discord_client.get_channel(channel_int)
                            if channel is None:
                                channel = await self.discord_client.fetch_channel(channel_int)
                        except discord.NotFound as exc:
                            self.log_event(
                                "list_messages_channel_not_found",
                                channel_id=target_channel_id,
                                code=getattr(exc, "code", None),
                            )
                            raise ToolException(
                                f"Channel {target_channel_id} was not found. Use a valid channel_id or omit it to use the current channel.",
                            ) from exc
                        except discord.Forbidden as exc:
                            self.log_event(
                                "list_messages_channel_forbidden",
                                channel_id=target_channel_id,
                                code=getattr(exc, "code", None),
                            )
                            raise ToolException(
                                f"Cannot access channel {target_channel_id}. Check bot permissions.",
                            ) from exc
                        except Exception as exc:
                            self.log_event(
                                "list_messages_channel_lookup_error",
                                channel_id=target_channel_id,
                                error_type=type(exc).__name__,
                            )
                            raise ToolException("Failed to look up the Discord channel.") from exc

                        history_method = getattr(channel, "history", None)
                        if history_method is not None:
                            try:
                                async for msg in history_method(
                                    limit=limit,
                                    oldest_first=False,
                                    after=cutoff,
                                ):
                                    created_at = getattr(msg, "created_at", None)
                                    if isinstance(created_at, datetime):
                                        if created_at.tzinfo is None:
                                            created_at = created_at.replace(tzinfo=UTC)
                                        timestamp = created_at.astimezone(UTC).isoformat()
                                    else:
                                        timestamp = utc_now_iso()
                                    messages.append(
                                        {
                                            "timestamp": timestamp,
                                            "channel_id": str(
                                                getattr(getattr(msg, "channel", None), "id", target_channel_id),
                                            ),
                                            "message_id": str(getattr(msg, "id", "")),
                                            "author": str(getattr(msg, "author", "unknown")),
                                            "content": str(getattr(msg, "content", "")),
                                        },
                                    )
                                messages.reverse()
                                source = "discord_api"
                            except Exception as exc:
                                self.log_event(
                                    "list_messages_history_error",
                                    channel_id=target_channel_id,
                                    limit=limit,
                                    window=window,
                                    error_type=type(exc).__name__,
                                )
                                raise ToolException("Failed to fetch Discord message history.") from exc

                if not messages:
                    source = "memory"
                    messages = _filter_by_cutoff(
                        list(self.message_history_by_channel.get(target_channel_id, [])),
                    )[-limit:]

            if not messages:
                return "No messages found."

            rendered: list[str] = []
            for msg in messages:
                rendered.append(
                    f"[{msg['timestamp']}] channel={msg['channel_id']} message_id={msg.get('message_id')} author={msg['author']} content={msg['content']}",
                )
            self.log_event(
                "tool_call",
                tool="list_messages",
                channel_id=target_channel_id,
                limit=limit,
                window=window,
                source=source,
                returned=len(rendered),
            )
            return "\n".join(rendered)

        @tool("journal")
        def journal(user_wanted: str, agent_did: str, predictions: str) -> str:
            """Write a journal entry and return checkpoint guidance."""
            self.append_journal(
                user_wanted=user_wanted,
                agent_did=agent_did,
                predictions=predictions,
                channel_id=self.current_channel_id,
            )
            checkpoint = self.layout.checkpoint_file.read_text(encoding="utf-8")
            self.log_event("tool_call", tool="journal")
            return checkpoint

        @tool("react")
        async def react(
            emoji: str,
            message_id: str | None = None,
            channel_id: str | None = None,
        ) -> str:
            """React to a Discord message. Defaults to the latest known message."""
            if not emoji.strip():
                return "emoji is required."
            if self.discord_client is None or not self.discord_client.is_ready():
                return "Discord is not connected."

            target_channel_id = channel_id or self.current_channel_id
            target_message_id = message_id
            if target_message_id is None:
                target_message_id, inferred_channel_id = self._latest_message_reference(target_channel_id)
                if target_channel_id is None:
                    target_channel_id = inferred_channel_id

            if target_message_id is None:
                return "No message found to react to."
            if target_channel_id is None:
                return "No channel_id provided and no channel could be inferred."

            try:
                channel_int = int(target_channel_id)
                message_int = int(target_message_id)
            except ValueError:
                return "channel_id and message_id must be numeric Discord IDs."

            channel = self.discord_client.get_channel(channel_int)
            if channel is None:
                channel = await self.discord_client.fetch_channel(channel_int)
            if not hasattr(channel, "fetch_message"):
                return f"Channel {target_channel_id} does not support fetch_message."

            message = await channel.fetch_message(message_int)
            await message.add_reaction(emoji)
            self.log_event(
                "tool_call",
                tool="react",
                emoji=emoji,
                channel_id=target_channel_id,
                message_id=str(target_message_id),
            )
            return f"Reacted to message {target_message_id} in channel {target_channel_id}."

        @tool("list_memory_blocks")
        def list_memory_blocks() -> str:
            """List memory blocks. Includes only the first 10 chars of text."""
            blocks = self._load_memory_blocks()
            payload = {
                "blocks": [
                    {
                        "id": block["id"],
                        "name": block["name"],
                        "sort_order": block["sort_order"],
                        "text_preview": str(block["text"])[:10],
                    }
                    for block in blocks
                ],
            }
            self.log_event("tool_call", tool="list_memory_blocks", count=len(payload["blocks"]))
            return yaml.safe_dump(payload, sort_keys=False)

        @tool("create_memory_block")
        def create_memory_block(
            name: str,
            text: str,
            sort_order: int = 0,
            block_id: str | None = None,
        ) -> str:
            """Create a memory block file in blocks/."""
            normalized_name = name.strip()
            if not normalized_name:
                return "name is required."
            chosen_id = _slugify(block_id) if block_id else self._generate_block_id(normalized_name)
            if self._find_memory_block_path(chosen_id) is not None:
                return f"memory block '{chosen_id}' already exists."

            block = {
                "name": normalized_name,
                "sort_order": int(sort_order),
                "text": text,
            }
            target = self._memory_block_path(chosen_id)
            target.write_text(yaml.safe_dump(block, sort_keys=False), encoding="utf-8")
            self.log_event("tool_call", tool="create_memory_block", block_id=chosen_id)
            return f"Created memory block '{chosen_id}'."

        @tool("update_memory_block")
        def update_memory_block(
            block_id: str,
            name: str | None = None,
            text: str | None = None,
            sort_order: int | None = None,
        ) -> str:
            """Update an existing memory block in blocks/."""
            normalized_id = _slugify(block_id)
            path = self._find_memory_block_path(normalized_id)
            if path is None:
                return f"memory block '{normalized_id}' not found."

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                loaded = {}

            changed = False
            if name is not None:
                loaded["name"] = name.strip()
                changed = True
            if text is not None:
                loaded["text"] = text
                changed = True
            if sort_order is not None:
                loaded["sort_order"] = int(sort_order)
                changed = True

            if not changed:
                return "No fields provided. Pass at least one of name, text, sort_order."

            path.write_text(yaml.safe_dump(loaded, sort_keys=False), encoding="utf-8")
            self.log_event("tool_call", tool="update_memory_block", block_id=normalized_id)
            return f"Updated memory block '{normalized_id}'."

        @tool("delete_memory_block")
        def delete_memory_block(block_id: str) -> str:
            """Delete a memory block from blocks/."""
            normalized_id = _slugify(block_id)
            path = self._find_memory_block_path(normalized_id)
            if path is None:
                return f"memory block '{normalized_id}' not found."
            path.unlink()
            self.log_event("tool_call", tool="delete_memory_block", block_id=normalized_id)
            return f"Deleted memory block '{normalized_id}'."

        @tool("list_schedules")
        def list_schedules() -> str:
            """List scheduler jobs from scheduler.yaml."""
            jobs = [job.to_dict() for job in self._load_scheduler_jobs()]
            self.log_event("tool_call", tool="list_schedules", count=len(jobs))
            return yaml.safe_dump({"jobs": jobs}, sort_keys=False)

        @tool("add_schedule")
        def add_schedule(
            name: str,
            prompt: str,
            cron: str | None = None,
            time_of_day: str | None = None,
            channel_id: str | None = None,
        ) -> str:
            """Add or replace a scheduler job using either cron or time_of_day (HH:MM UTC)."""
            if bool(cron) == bool(time_of_day):
                return "Exactly one of cron or time_of_day must be provided."

            jobs = [job for job in self._load_scheduler_jobs() if job.name != name]
            jobs.append(
                SchedulerJob(
                    name=name.strip(),
                    prompt=prompt.strip(),
                    cron=cron.strip() if cron else None,
                    time_of_day=time_of_day.strip() if time_of_day else None,
                    channel_id=channel_id.strip() if channel_id else None,
                ),
            )
            self._save_scheduler_jobs(jobs)
            self._reload_scheduler_jobs()
            self.log_event("tool_call", tool="add_schedule", name=name)
            return f"Added schedule '{name}'."

        @tool("remove_schedule")
        def remove_schedule(name: str) -> str:
            """Remove a scheduler job by name."""
            before = self._load_scheduler_jobs()
            after = [job for job in before if job.name != name]
            self._save_scheduler_jobs(after)
            self._reload_scheduler_jobs()
            self.log_event("tool_call", tool="remove_schedule", name=name, removed=len(before) - len(after))
            return f"Removed {len(before) - len(after)} schedule(s) named '{name}'."

        return [
            send_message,
            react,
            list_messages,
            journal,
            list_memory_blocks,
            create_memory_block,
            update_memory_block,
            delete_memory_block,
            list_schedules,
            add_schedule,
            remove_schedule,
        ]

    def _load_scheduler_jobs(self) -> list[SchedulerJob]:
        if not self.layout.scheduler_file.exists():
            return []
        loaded = yaml.safe_load(self.layout.scheduler_file.read_text(encoding="utf-8"))
        if loaded is None:
            return []
        if isinstance(loaded, list):
            raw_jobs = loaded
        else:
            raw_jobs = loaded.get("jobs", [])
        jobs: list[SchedulerJob] = []
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            prompt = str(raw.get("prompt", "")).strip()
            if not name or not prompt:
                continue
            cron = str(raw.get("cron", "")).strip() or None
            time_of_day = str(raw.get("time_of_day", "")).strip() or None
            channel_id = str(raw.get("channel_id", "")).strip() or None
            jobs.append(
                SchedulerJob(
                    name=name,
                    prompt=prompt,
                    cron=cron,
                    time_of_day=time_of_day,
                    channel_id=channel_id,
                ),
            )
        return jobs

    def _save_scheduler_jobs(self, jobs: list[SchedulerJob]) -> None:
        data = {"jobs": [job.to_dict() for job in jobs]}
        self.layout.scheduler_file.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

    def _reload_scheduler_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("open_strix:"):
                self.scheduler.remove_job(job.id)

        for job in self._load_scheduler_jobs():
            if bool(job.cron) == bool(job.time_of_day):
                self.log_event("scheduler_invalid_job", name=job.name)
                continue

            trigger: CronTrigger
            if job.cron:
                try:
                    trigger = CronTrigger.from_crontab(job.cron, timezone=UTC)
                except ValueError as exc:
                    self.log_event("scheduler_invalid_cron", name=job.name, error=str(exc))
                    continue
            else:
                try:
                    hour_str, minute_str = str(job.time_of_day).split(":")
                    trigger = CronTrigger(
                        hour=int(hour_str),
                        minute=int(minute_str),
                        timezone=UTC,
                    )
                except (TypeError, ValueError) as exc:
                    self.log_event("scheduler_invalid_time", name=job.name, error=str(exc))
                    continue

            self.scheduler.add_job(
                self._on_scheduler_fire,
                trigger=trigger,
                kwargs={
                    "name": job.name,
                    "prompt": job.prompt,
                    "channel_id": job.channel_id,
                },
                id=f"open_strix:{job.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        self.log_event("scheduler_reloaded", jobs=len(self._load_scheduler_jobs()))

    def _on_scheduler_fire(self, name: str, prompt: str, channel_id: str | None = None) -> None:
        asyncio.create_task(
            self.enqueue_event(
                AgentEvent(
                    event_type="scheduler",
                    prompt=prompt,
                    channel_id=channel_id,
                    scheduler_name=name,
                    dedupe_key=f"scheduler:{name}",
                ),
            ),
        )

    async def enqueue_event(self, event: AgentEvent) -> None:
        if event.dedupe_key:
            if event.dedupe_key in self.pending_scheduler_keys:
                self.log_event("event_deduped", key=event.dedupe_key)
                return
            self.pending_scheduler_keys.add(event.dedupe_key)

        await self.queue.put(event)
        self.log_event(
            "event_queued",
            source_event_type=event.event_type,
            channel_id=event.channel_id,
            scheduler_name=event.scheduler_name,
            queue_size=self.queue.qsize(),
            source_id=event.source_id,
        )

    async def handle_discord_message(self, message: discord.Message) -> None:
        await self._refresh_channel_history_from_discord(
            channel_id=str(message.channel.id),
            before_message_id=str(message.id),
        )
        attachment_names = await self._save_attachments(message)
        prompt = (message.content or "").strip()
        if not prompt:
            prompt = "User sent a message with no text."
        author_id = str(getattr(message.author, "id", "")).strip() or None
        author_is_bot = bool(getattr(message.author, "bot", False))
        force_reply = author_is_bot and self.should_respond_to_bot(author_id)

        self._remember_message(
            channel_id=str(message.channel.id),
            author=str(message.author),
            content=message.content or "",
            attachment_names=attachment_names,
            message_id=str(message.id),
            is_bot=author_is_bot,
            source="discord",
        )
        self.log_event(
            "discord_message",
            channel_id=str(message.channel.id),
            author=str(message.author),
            author_id=author_id,
            author_is_bot=author_is_bot,
            force_reply=force_reply,
            attachment_names=attachment_names,
            source_id=str(message.id),
        )
        await self.enqueue_event(
            AgentEvent(
                event_type="discord_message",
                prompt=prompt,
                channel_id=str(message.channel.id),
                author=str(message.author),
                author_id=author_id,
                attachment_names=attachment_names,
                source_id=str(message.id),
                force_reply=force_reply,
            ),
        )

    async def _refresh_channel_history_from_discord(
        self,
        *,
        channel_id: str,
        before_message_id: str | None = None,
    ) -> int:
        if self.discord_client is None or not self.discord_client.is_ready():
            return 0

        try:
            channel_int = int(channel_id)
        except ValueError:
            return 0

        channel = self.discord_client.get_channel(channel_int)
        if channel is None:
            channel = await self.discord_client.fetch_channel(channel_int)

        history_method = getattr(channel, "history", None)
        if history_method is None:
            return 0

        limit = max(DISCORD_HISTORY_REFRESH_LIMIT, self.config.discord_messages_in_prompt * 3)
        history_before: discord.Object | None = None
        if before_message_id is not None:
            try:
                history_before = discord.Object(id=int(before_message_id))
            except ValueError:
                history_before = None

        added = 0
        try:
            async for historic_message in history_method(
                limit=limit,
                oldest_first=True,
                before=history_before,
            ):
                attachment_names = [
                    str(Path(attachment.filename).name)
                    for attachment in getattr(historic_message, "attachments", [])
                ]
                created_at = getattr(historic_message, "created_at", None)
                created_at_iso: str | None = None
                if isinstance(created_at, datetime):
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=UTC)
                    created_at_iso = created_at.astimezone(UTC).isoformat()

                remember_added = self._remember_message(
                    channel_id=channel_id,
                    author=str(getattr(historic_message, "author", "unknown")),
                    content=str(getattr(historic_message, "content", "")),
                    attachment_names=attachment_names,
                    message_id=str(getattr(historic_message, "id", "")),
                    is_bot=bool(getattr(getattr(historic_message, "author", None), "bot", False)),
                    source="discord",
                    timestamp=created_at_iso,
                )
                if remember_added:
                    added += 1
        except Exception as exc:
            self.log_event(
                "discord_history_refresh_error",
                channel_id=channel_id,
                before_message_id=before_message_id,
                error=str(exc),
            )
            return 0

        self.log_event(
            "discord_history_refreshed",
            channel_id=channel_id,
            before_message_id=before_message_id,
            limit=limit,
            added=added,
        )
        return added

    async def _save_attachments(self, message: discord.Message) -> list[str]:
        if not message.attachments:
            return []
        attachments_dir = self.layout.state_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []
        for attachment in message.attachments:
            file_name = Path(attachment.filename).name
            target = attachments_dir / f"{message.id}-{file_name}"
            await attachment.save(target)
            saved.append(str(target.relative_to(self.home)))
        return saved

    def _remember_message(
        self,
        channel_id: str,
        author: str,
        content: str,
        attachment_names: list[str],
        message_id: str | None = None,
        is_bot: bool = False,
        source: str = "discord",
        timestamp: str | None = None,
    ) -> bool:
        normalized_message_id = str(message_id).strip() if message_id not in (None, "") else None
        if normalized_message_id is not None:
            for existing in self.message_history_by_channel.get(channel_id, []):
                if existing.get("message_id") == normalized_message_id:
                    return False

        item = {
            "timestamp": timestamp if timestamp is not None else utc_now_iso(),
            "channel_id": channel_id,
            "message_id": normalized_message_id,
            "author": author,
            "is_bot": is_bot,
            "source": source,
            "content": content,
            "attachments": attachment_names,
        }
        self.message_history_all.append(item)
        self.message_history_by_channel[channel_id].append(item)
        return True

    def _latest_message_reference(
        self,
        channel_id: str | None = None,
        include_bot: bool = True,
    ) -> tuple[str | None, str | None]:
        if channel_id:
            for item in reversed(self.message_history_by_channel.get(channel_id, [])):
                if not include_bot and bool(item.get("is_bot")):
                    continue
                message_id = item.get("message_id")
                if message_id:
                    return str(message_id), channel_id
            return None, channel_id

        for item in reversed(self.message_history_all):
            if not include_bot and bool(item.get("is_bot")):
                continue
            message_id = item.get("message_id")
            item_channel_id = item.get("channel_id")
            if message_id and item_channel_id:
                return str(message_id), str(item_channel_id)
        return None, None

    async def _react_to_message(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> bool:
        if self.discord_client is None or not self.discord_client.is_ready():
            return False
        try:
            channel_int = int(channel_id)
            message_int = int(message_id)
        except ValueError:
            return False

        channel = self.discord_client.get_channel(channel_int)
        if channel is None:
            channel = await self.discord_client.fetch_channel(channel_int)
        if not hasattr(channel, "fetch_message"):
            return False

        message = await channel.fetch_message(message_int)
        await message.add_reaction(emoji)
        self.log_event(
            "reaction_added",
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )
        return True

    async def _react_to_latest_message(
        self,
        channel_id: str | None,
        emoji: str,
        include_bot: bool = True,
    ) -> bool:
        message_id, inferred_channel_id = self._latest_message_reference(
            channel_id=channel_id,
            include_bot=include_bot,
        )
        target_channel_id = channel_id or inferred_channel_id
        if target_channel_id is None or message_id is None:
            return False
        return await self._react_to_message(
            channel_id=target_channel_id,
            message_id=message_id,
            emoji=emoji,
        )

    @asynccontextmanager
    async def _typing_indicator(self, event: AgentEvent):
        channel_id = event.channel_id
        if channel_id is None:
            yield
            return

        if self.discord_client is None or not self.discord_client.is_ready():
            yield
            return

        try:
            channel_int = int(channel_id)
        except ValueError:
            yield
            return

        channel = self.discord_client.get_channel(channel_int)
        if channel is None:
            try:
                channel = await self.discord_client.fetch_channel(channel_int)
            except Exception as exc:
                self.log_event(
                    "typing_indicator_error",
                    source_event_type=event.event_type,
                    channel_id=channel_id,
                    source_id=event.source_id,
                    error=str(exc),
                )
                yield
                return

        typing_method = getattr(channel, "typing", None)
        if typing_method is None:
            yield
            return

        typing_context = typing_method()
        if not hasattr(typing_context, "__aenter__") or not hasattr(typing_context, "__aexit__"):
            yield
            return

        self.log_event(
            "typing_indicator_start",
            source_event_type=event.event_type,
            channel_id=channel_id,
            source_id=event.source_id,
        )
        async with typing_context:
            try:
                yield
            finally:
                self.log_event(
                    "typing_indicator_stop",
                    source_event_type=event.event_type,
                    channel_id=channel_id,
                    source_id=event.source_id,
                )

    async def _run_post_turn_git_sync(self, event: AgentEvent) -> str:
        if not self.config.git_sync_after_turn:
            return "disabled"

        git_result = await asyncio.to_thread(_git_sync, self.home)
        self.log_event(
            "git_sync_after_turn",
            source_event_type=event.event_type,
            channel_id=event.channel_id,
            git_sync=git_result,
        )

        if "failed:" not in git_result:
            return git_result

        sent_messages = self._current_turn_sent_messages or []
        if not sent_messages:
            return git_result

        channel_id, message_id = sent_messages[-1]
        self.log_event(
            "warning",
            where="post_turn_git_sync",
            warning_type="git_sync_failed",
            git_sync=git_result,
            channel_id=channel_id,
            message_id=message_id,
        )
        await self._react_to_message(
            channel_id=channel_id,
            message_id=message_id,
            emoji=WARNING_REACTION_EMOJI,
        )
        return git_result

    def _render_prompt(self, event: AgentEvent) -> str:
        journal_entries = _tail_jsonl(
            self.layout.journal_log,
            self.config.journal_entries_in_prompt,
        )
        journals = self._render_journal_entries(journal_entries)

        blocks = self._load_blocks_for_prompt()
        blocks_text = self._render_memory_blocks(blocks)

        discord_messages = [
            item
            for item in self.message_history_all
            if item.get("source") == "discord"
        ]
        recent_messages = discord_messages[-self.config.discord_messages_in_prompt :]
        messages_text = self._render_discord_messages(recent_messages)

        current_event_text = self._render_current_event(event)

        return textwrap.dedent(
            f"""\
            Context for this turn:

            1) Last journal entries:
            {journals}

            2) Memory blocks:
            {blocks_text}

            3) Last Discord messages:
            {messages_text}

            4) Current message + reply channel:
            {current_event_text}

            If you need to message the user, call send_message.
            """
        )

    def _render_journal_entries(self, entries: list[dict[str, Any]]) -> str:
        if not entries:
            return "(none)"

        now = datetime.now(tz=UTC)
        rendered: list[str] = []
        for entry in entries:
            lines = [
                f"timestamp: {_format_timestamp(entry.get('timestamp'), now=now)}",
            ]
            channel_id = entry.get("channel_id")
            if channel_id not in (None, ""):
                lines.append(f"channel_id: {channel_id}")
            lines.append(f"user_wanted: {entry.get('user_wanted', '')}")
            lines.append(f"agent_did: {entry.get('agent_did', '')}")

            predictions = _normalize_predictions(entry.get("predictions"))
            if predictions:
                lines.append("predictions:")
                lines.extend(f"- {prediction}" for prediction in predictions)

            rendered.append("\n".join(lines))
        return "\n\n".join(rendered)

    def _render_memory_blocks(self, blocks: list[dict[str, Any]]) -> str:
        if not blocks:
            return "(none)"

        rendered: list[str] = []
        for block in blocks:
            name = str(block.get("name", "")).strip() or str(block.get("id", "")).strip() or "unnamed"
            value = str(block.get("text", "")).strip()
            rendered.append(f"memory block: {name}\n{value}")
        return "\n\n".join(rendered)

    def _render_discord_messages(self, messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "(none)"

        now = datetime.now(tz=UTC)
        rendered: list[str] = []
        for message in messages:
            timestamp = _format_timestamp(message.get("timestamp"), now=now)
            author = str(message.get("author", "unknown"))
            message_id = str(message.get("message_id", "unknown"))
            content = str(message.get("content", "")).strip() or "(no text)"

            lines = [f"{timestamp} | {author} | message_id={message_id}", content]
            attachments = message.get("attachments")
            if isinstance(attachments, list) and attachments:
                lines.append("attachments:")
                lines.extend(f"  - {item}" for item in attachments)
            rendered.append("\n".join(lines))

        return "\n\n".join(rendered)

    def _render_current_event(self, event: AgentEvent) -> str:
        now = datetime.now(tz=UTC)
        timestamp = _format_timestamp(now, now=now)
        author = event.author if event.author else "system"
        message_id = event.source_id if event.source_id else "unknown"
        content = event.prompt.strip() if event.prompt.strip() else "(no text)"

        lines = [
            f"channel_id: {event.channel_id if event.channel_id else 'unknown'}",
            f"event_type: {event.event_type}",
            f"{timestamp} | {author} | message_id={message_id}",
            content,
        ]
        if event.attachment_names:
            lines.append("attachments:")
            lines.extend(f"  - {item}" for item in event.attachment_names)
        if event.scheduler_name:
            lines.append(f"scheduler_name: {event.scheduler_name}")
        return "\n".join(lines)

    def _load_blocks_for_prompt(self) -> list[dict[str, Any]]:
        blocks = self._load_memory_blocks()
        return [
            {
                "id": block["id"],
                "name": block["name"],
                "sort_order": block["sort_order"],
                "text": block["text"],
            }
            for block in blocks
        ]

    async def _event_worker(self) -> None:
        while True:
            event = await self.queue.get()
            self.current_channel_id = event.channel_id
            try:
                await self._process_event(event)
            except Exception as exc:
                reacted = await self._react_to_latest_message(
                    channel_id=event.channel_id,
                    emoji=ERROR_REACTION_EMOJI,
                    include_bot=False,
                )
                self.log_event(
                    "error",
                    where="event_worker",
                    source_event_type=event.event_type,
                    error=str(exc),
                    reacted_to_last_user_message=reacted,
                )
            finally:
                if event.dedupe_key:
                    self.pending_scheduler_keys.discard(event.dedupe_key)
                self.current_channel_id = None
                self.queue.task_done()

    async def _process_event(self, event: AgentEvent) -> None:
        self._current_turn_sent_messages = []
        prompt = self._render_prompt(event)
        self.log_event(
            "agent_invoke_start",
            source_event_type=event.event_type,
            channel_id=event.channel_id,
            scheduler_name=event.scheduler_name,
        )
        try:
            async with self._typing_indicator(event):
                result = await self.agent.ainvoke({"messages": [HumanMessage(content=prompt)]})
            self._log_agent_trace(result)

            final_text = self._extract_final_text(result)
            self.log_event(
                "agent_final_message_discarded",
                source_event_type=event.event_type,
                channel_id=event.channel_id,
                final_text=final_text,
            )
            if event.force_reply and not (self._current_turn_sent_messages or []):
                fallback_text = final_text.strip() if final_text.strip() else "Acknowledged."
                if event.channel_id is None:
                    sent, sent_message_id, sent_chunks = False, None, 0
                else:
                    sent, sent_message_id, sent_chunks = await self._send_discord_message(
                        channel_id=event.channel_id,
                        text=fallback_text,
                    )
                self.log_event(
                    "forced_bot_reply",
                    source_event_type=event.event_type,
                    channel_id=event.channel_id,
                    source_id=event.source_id,
                    author_id=event.author_id,
                    sent=sent,
                    chunks=sent_chunks,
                    message_id=sent_message_id,
                    used_final_text=bool(final_text.strip()),
                    text_preview=fallback_text[:300],
                )
            await self._run_post_turn_git_sync(event)
        finally:
            self._current_turn_sent_messages = None

    def _log_agent_trace(self, result: dict[str, Any]) -> None:
        messages = result.get("messages")
        if not isinstance(messages, list):
            return
        for message in messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    self.log_event(
                        "tool_call",
                        tool=call.get("name"),
                        args=call.get("args"),
                    )

    def _extract_final_text(self, result: dict[str, Any]) -> str:
        messages = result.get("messages")
        if not isinstance(messages, list):
            return ""
        for message in reversed(messages):
            if not isinstance(message, AIMessage):
                continue
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(str(part.get("text", "")))
                return "\n".join(text_parts).strip()
        return ""

    async def _stdin_mode(self) -> None:
        self.log_event("stdin_mode_start")
        print("No Discord token configured. Running in stdin mode.")
        while True:
            try:
                line = await asyncio.to_thread(input, "open-strix> ")
            except EOFError:
                self.log_event("stdin_mode_eof")
                return
            prompt = line.strip()
            if not prompt:
                continue
            self._remember_message(
                channel_id="stdin",
                author="local_user",
                content=prompt,
                attachment_names=[],
                message_id=None,
                source="stdin",
            )
            await self.enqueue_event(
                AgentEvent(
                    event_type="stdin_message",
                    prompt=prompt,
                    channel_id="stdin",
                    author="local_user",
                ),
            )

    async def run(self) -> None:
        self.worker_task = asyncio.create_task(self._event_worker())
        self.scheduler.start()
        self._reload_scheduler_jobs()
        self.log_event("app_started", home=str(self.home))

        token = os.getenv(self.config.discord_token_env, "")
        if token:
            self.discord_client = DiscordBridge(self)
            self.log_event("discord_connecting")
            await self.discord_client.start(token)
            return

        await self._stdin_mode()

    async def shutdown(self) -> None:
        self.log_event("app_shutdown_start")
        if self.discord_client is not None and not self.discord_client.is_closed():
            await self.discord_client.close()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.worker_task is not None:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        self.log_event("app_shutdown_complete")


def load_config(layout: RepoLayout) -> AppConfig:
    loaded = yaml.safe_load(layout.config_file.read_text(encoding="utf-8")) or {}
    model_raw = loaded.get("model", DEFAULT_MODEL)
    model = str(model_raw).strip() if model_raw is not None else ""
    if not model:
        model = DEFAULT_MODEL
    return AppConfig(
        model=model,
        journal_entries_in_prompt=int(loaded.get("journal_entries_in_prompt", 90)),
        discord_messages_in_prompt=int(loaded.get("discord_messages_in_prompt", 10)),
        discord_token_env=str(loaded.get("discord_token_env", "DISCORD_TOKEN")),
        always_respond_bot_ids=_normalize_id_list(loaded.get("always_respond_bot_ids")),
        git_sync_after_turn=_safe_bool(loaded.get("git_sync_after_turn"), True),
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

    if changed:
        config_file.write_text(yaml.safe_dump(loaded, sort_keys=False), encoding="utf-8")


def bootstrap_home_repo(layout: RepoLayout) -> None:
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    layout.blocks_dir.mkdir(parents=True, exist_ok=True)
    layout.skills_dir.mkdir(parents=True, exist_ok=True)
    layout.scripts_dir.mkdir(parents=True, exist_ok=True)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    (layout.state_dir / ".gitkeep").touch(exist_ok=True)
    (layout.blocks_dir / ".gitkeep").touch(exist_ok=True)
    (layout.skills_dir / ".gitkeep").touch(exist_ok=True)
    (layout.scripts_dir / ".gitkeep").touch(exist_ok=True)
    _write_if_missing(layout.config_file, DEFAULT_CONFIG)
    _ensure_config_defaults(layout.config_file)
    _write_if_missing(layout.scheduler_file, DEFAULT_SCHEDULER)
    _write_if_missing(layout.checkpoint_file, DEFAULT_CHECKPOINT)
    _write_if_missing(layout.scripts_dir / "pre_commit.py", DEFAULT_PRE_COMMIT_SCRIPT)
    layout.events_log.touch(exist_ok=True)
    layout.journal_log.touch(exist_ok=True)
    _install_git_hook(layout.home)
    _ensure_logs_ignored(layout.home)


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
    required_entries = ["logs/", ".env"]
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


def run_open_strix(home: Path | None = None) -> None:
    app = OpenStrixApp(home=home or Path.cwd())

    async def _runner() -> None:
        try:
            await app.run()
        finally:
            await app.shutdown()

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        pass
