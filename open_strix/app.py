from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import discord
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage

from .config import (
    DEFAULT_CONFIG,
    DEFAULT_MODEL,
    DEFAULT_MODEL_PROVIDER,
    DEFAULT_PRE_COMMIT_SCRIPT,
    DEFAULT_SCHEDULER,
    STATE_DIR_NAME,
    AppConfig,
    RepoLayout,
    bootstrap_home_repo,
    load_config,
)
from .discord import (
    DISCORD_HISTORY_REFRESH_LIMIT,
    DISCORD_MESSAGE_CHAR_LIMIT,
    ERROR_REACTION_EMOJI,
    WARNING_REACTION_EMOJI,
    DiscordBridge,
    DiscordMixin,
    _chunk_discord_message,
)
from .models import AgentEvent
from .prompts import DEFAULT_CHECKPOINT, SYSTEM_PROMPT, render_turn_prompt
from .scheduler import SchedulerJob, SchedulerMixin
from .tools import ToolsMixin

UTC = timezone.utc
LOG_ROLL_BYTES = 1_000_000


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "block"


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


class OpenStrixApp(DiscordMixin, SchedulerMixin, ToolsMixin):
    def __init__(self, home: Path) -> None:
        self.home = home.resolve()
        self.layout = RepoLayout(home=self.home, state_dir_name=STATE_DIR_NAME)
        bootstrap_home_repo(self.layout, checkpoint_text=DEFAULT_CHECKPOINT)
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
        blocks = self._load_blocks_for_prompt()
        recent_messages = [
            item
            for item in self.message_history_all
            if item.get("source") == "discord"
        ][-self.config.discord_messages_in_prompt :]

        return render_turn_prompt(
            journal_entries=journal_entries,
            memory_blocks=blocks,
            discord_messages=recent_messages,
            current_event={
                "event_type": event.event_type,
                "prompt": event.prompt,
                "channel_id": event.channel_id,
                "author": event.author,
                "attachment_names": event.attachment_names,
                "scheduler_name": event.scheduler_name,
                "source_id": event.source_id,
            },
        )

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
