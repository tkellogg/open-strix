from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import discord
import yaml
from langchain_core.tools import ToolException, tool

from .discord import ERROR_REACTION_EMOJI, WARNING_REACTION_EMOJI
from .scheduler import SchedulerJob, _SCHEDULER_LOCK

UTC = timezone.utc
FETCH_CHUNK_SIZE_BYTES = 64 * 1024
DEFAULT_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
SHELL_OUTPUT_LIMIT_CHARS = 12_000
SEND_MESSAGE_LOOP_SOFT_LIMIT = 3
SEND_MESSAGE_LOOP_WARN_LIMIT = 7
SEND_MESSAGE_LOOP_HARD_LIMIT = 10
SEND_MESSAGE_LOOP_SIMILARITY_THRESHOLD = 0.98


class SendMessageCircuitBreakerStop(RuntimeError):
    """Raised when send_message loop detection hard-stops the current turn."""


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "block"


def _virtual_path(path: Path, *, root: Path) -> str:
    return "/" + path.relative_to(root).as_posix()


def _sanitize_download_name(value: str) -> str:
    # Keep names shell-safe and grep-friendly.
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        return "download.bin"
    if len(cleaned) <= 120:
        return cleaned

    suffix = Path(cleaned).suffix
    stem = Path(cleaned).stem[: max(1, 120 - len(suffix))]
    return f"{stem}{suffix}"


def _name_from_url(url: str) -> str:
    parsed = urlparse(url)
    raw_name = Path(unquote(parsed.path)).name
    if not raw_name:
        raw_name = "index.html" if parsed.path in {"", "/"} else "download.bin"

    name = _sanitize_download_name(raw_name)
    if "." not in name:
        return f"{name}.bin"
    return name


def _download_url_bytes(
    *,
    url: str,
    target_path: Path,
    timeout_seconds: int,
    max_bytes: int,
) -> dict[str, Any]:
    request = Request(
        url=url,
        headers={"User-Agent": "open-strix/fetch_url"},
    )

    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        status = int(response.getcode() or 0)
        final_url = str(response.geturl())
        content_type = str(response.headers.get("Content-Type", ""))

        total_bytes = 0
        hasher = hashlib.sha256()
        with target_path.open("wb") as f:
            while True:
                chunk = response.read(FETCH_CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ValueError(
                        f"download exceeded max_bytes={max_bytes} for url={url}",
                    )
                hasher.update(chunk)
                f.write(chunk)

    return {
        "status": status,
        "final_url": final_url,
        "content_type": content_type,
        "bytes": total_bytes,
        "sha256": hasher.hexdigest(),
    }


def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    request_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "open-strix/web_search",
        **headers,
    }
    request = Request(
        url=url,
        data=request_bytes,
        headers=request_headers,
        method="POST",
    )

    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        status = int(response.getcode() or 0)
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeded max_bytes={max_bytes} for url={url}")
        decoded = body.decode("utf-8", errors="replace")
        parsed = json.loads(decoded)
        return {
            "status": status,
            "json": parsed,
            "response_bytes": len(body),
            "final_url": str(response.geturl()),
        }


def _shell_tool_name() -> str:
    if os.name == "nt":
        return "powershell"
    return "bash"


# ---------------------------------------------------------------------------
# Bash command classification: detect file-read operations in shell commands
# ---------------------------------------------------------------------------

# Patterns that indicate a file read operation.  Each regex captures the
# first file-path-like argument so we can log *what* was read.
_READ_CMD_PATTERNS: list[re.Pattern[str]] = [
    # cat, head, tail, less, more  (with optional flags like -n 50)
    re.compile(r"\b(?:cat|head|tail|less|more)\b[\s]+(?:-[^\s]+\s+)*([^\s|>;]+)"),
    # sed reading a file (sed '...' /path/to/file)
    re.compile(r"\bsed\b\s+(?:'[^']*'|\"[^\"]*\")\s+([^\s|>;]+)"),
    # awk reading a file (awk '...' /path/to/file)
    re.compile(r"\bawk\b\s+(?:'[^']*'|\"[^\"]*\")\s+([^\s|>;]+)"),
]


def _extract_read_paths(command: str) -> list[str]:
    """Return file paths that *look like* read targets in a shell command."""
    paths: list[str] = []
    for pattern in _READ_CMD_PATTERNS:
        for m in pattern.finditer(command):
            candidate = m.group(1)
            # Skip things that are clearly not file paths
            if candidate.startswith("-") or candidate in ("", "/dev/null"):
                continue
            paths.append(candidate)
    return paths


def _shell_command_for_platform(command: str) -> list[str]:
    if os.name == "nt":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return ["bash", "-lc", command]


def _run_shell(command: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _shell_command_for_platform(command),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
    )


class ToolsMixin:
    def _reset_send_message_circuit_breaker(self) -> None:
        self._send_message_last_text_normalized = None
        self._send_message_similarity_streak = 0
        self._send_message_circuit_breaker_active = False
        self._send_message_warning_reaction_sent = False

    def _latest_agent_message_reference(
        self,
        channel_id: str | None,
    ) -> tuple[str | None, str | None]:
        if self._current_turn_sent_messages:
            for sent_channel_id, sent_message_id in reversed(self._current_turn_sent_messages):
                if sent_message_id:
                    return sent_message_id, sent_channel_id

        if channel_id is not None:
            for item in reversed(self.message_history_by_channel.get(channel_id, [])):
                if not bool(item.get("is_bot")):
                    continue
                message_id = str(item.get("message_id", "")).strip()
                if message_id:
                    return message_id, channel_id
            return None, channel_id

        for item in reversed(self.message_history_all):
            if not bool(item.get("is_bot")):
                continue
            message_id = str(item.get("message_id", "")).strip()
            row_channel_id = str(item.get("channel_id", "")).strip()
            if message_id and row_channel_id:
                return message_id, row_channel_id
        return None, None

    async def _react_to_last_agent_message(self, channel_id: str | None, emoji: str) -> bool:
        message_id, target_channel_id = self._latest_agent_message_reference(channel_id)
        if message_id is None or target_channel_id is None:
            return False
        return await self._react_to_message(
            channel_id=target_channel_id,
            message_id=message_id,
            emoji=emoji,
        )

    def _update_send_message_similarity_streak(self, text: str) -> tuple[int, float]:
        normalized_text = re.sub(r"\s+", " ", text.strip()).lower()
        previous = self._send_message_last_text_normalized
        similarity_ratio = 0.0

        if previous is None:
            streak = 1
        else:
            similarity_ratio = SequenceMatcher(a=previous, b=normalized_text).ratio()
            if similarity_ratio >= self.send_message_loop_similarity_threshold:
                streak = self._send_message_similarity_streak + 1
            else:
                streak = 1

        self._send_message_last_text_normalized = normalized_text
        self._send_message_similarity_streak = streak
        return streak, similarity_ratio

    def _resolve_send_message_attachments(
        self,
        attachment_paths: list[str] | str | None,
    ) -> tuple[list[Path], list[str]]:
        raw_items: list[str]
        if attachment_paths is None:
            raw_items = []
        elif isinstance(attachment_paths, str):
            raw_items = [attachment_paths]
        else:
            raw_items = [str(item) for item in attachment_paths]

        resolved_paths: list[Path] = []
        attachment_names: list[str] = []
        seen_paths: set[Path] = set()
        for raw in raw_items:
            raw_path = raw.strip()
            if not raw_path:
                continue

            raw_candidate = Path(raw_path).expanduser()
            if raw_candidate.is_absolute():
                absolute_candidate = raw_candidate.resolve()
                if self.home in absolute_candidate.parents:
                    candidate = absolute_candidate
                else:
                    candidate = (self.home / raw_path.lstrip("/\\")).resolve()
            else:
                candidate = (self.home / raw_candidate).resolve()

            if self.home not in candidate.parents:
                raise ToolException(
                    "send_message failed: attachment path must be inside the agent home directory.",
                )
            if not candidate.exists():
                raise ToolException(
                    f"send_message failed: attachment file does not exist: {candidate}",
                )
            if not candidate.is_file():
                raise ToolException(
                    f"send_message failed: attachment path is not a file: {candidate}",
                )
            if candidate in seen_paths:
                continue

            seen_paths.add(candidate)
            resolved_paths.append(candidate)
            attachment_names.append(_virtual_path(candidate, root=self.home))

        return resolved_paths, attachment_names

    def _build_tools(self) -> list[Any]:
        shell_tool_name = _shell_tool_name()

        @tool("send_message")
        async def send_message(
            text: str,
            channel_id: str | None = None,
            attachment_paths: list[str] | None = None,
        ) -> str:
            """Send a message to the current conversation or a specific channel with optional file attachments."""
            resolved_attachment_paths, attachment_names = self._resolve_send_message_attachments(
                attachment_paths,
            )

            if not text.strip() and not resolved_attachment_paths:
                self.log_event(
                    "tool_call_error",
                    tool="send_message",
                    error_type="empty_message",
                )
                raise ToolException(
                    "send_message failed: message text was empty and no attachments were provided.",
                )

            target_channel_id = channel_id or self.current_channel_id
            if target_channel_id is None:
                return "No channel_id provided and no current event channel is available."

            similarity_basis = text
            if attachment_names:
                similarity_basis = (
                    f"{text}\nattachments:{'|'.join(sorted(attachment_names))}"
                )

            streak, similarity_ratio = self._update_send_message_similarity_streak(similarity_basis)
            if streak >= self.send_message_loop_soft_limit:
                self._send_message_circuit_breaker_active = True

            if self._send_message_circuit_breaker_active:

                warning_reacted = False
                if not self._send_message_warning_reaction_sent:
                    warning_reacted = await self._react_to_last_agent_message(
                        channel_id=target_channel_id,
                        emoji=WARNING_REACTION_EMOJI,
                    )
                    if warning_reacted:
                        self._send_message_warning_reaction_sent = True

                if streak >= self.send_message_loop_hard_limit:
                    hard_stop_reacted = await self._react_to_last_agent_message(
                        channel_id=target_channel_id,
                        emoji=ERROR_REACTION_EMOJI,
                    )
                    self.log_event(
                        "send_message_loop_hard_stop",
                        tool="send_message",
                        channel_id=target_channel_id,
                        streak=streak,
                        similarity_ratio=round(similarity_ratio, 6),
                        reacted=hard_stop_reacted,
                    )
                    raise SendMessageCircuitBreakerStop(
                        "send_message hard stop: repeated near-duplicate loop at streak=10. "
                        "This turn is terminated for safety. Next turn: reflect on what went "
                        "wrong before resuming — consider using 5 Whys or a completely "
                        "different approach instead of retrying.",
                    )

                if streak >= self.send_message_loop_warn_limit:
                    self.log_event(
                        "send_message_loop_warning",
                        tool="send_message",
                        channel_id=target_channel_id,
                        streak=streak,
                        similarity_ratio=round(similarity_ratio, 6),
                        reacted_warning=warning_reacted,
                    )
                    return (
                        f"WARNING: You have sent {streak} near-duplicate messages. "
                        f"Hard stop at {self.send_message_loop_hard_limit} — after that, "
                        "this turn will be terminated.\n\n"
                        "Before that happens, stop and reflect:\n"
                        "1. What were you trying to accomplish? Did the approach work?\n"
                        "2. What is a COMPLETELY DIFFERENT way to achieve this?\n"
                        "3. If you have a 5 Whys or root-cause analysis skill, use it on why "
                        "this approach failed before trying again.\n\n"
                        "Do not retry the same action. Think creatively about an alternative, "
                        "or finish the turn safely."
                    )

                self.log_event(
                    "send_message_loop_detected",
                    tool="send_message",
                    channel_id=target_channel_id,
                    streak=streak,
                    similarity_ratio=round(similarity_ratio, 6),
                    reacted_warning=warning_reacted,
                )
                return (
                    "Loop detected in send_message calls. Message delivery is paused for this turn "
                    "to prevent an infinite output loop."
                )

            sent, sent_message_id, sent_chunks = await self._send_channel_message(
                channel_id=target_channel_id,
                text=text,
                attachment_paths=resolved_attachment_paths,
                attachment_names=attachment_names,
            )

            self.log_event(
                "tool_call",
                tool="send_message",
                channel_id=target_channel_id,
                sent=sent,
                chunks=sent_chunks,
                attachment_names=attachment_names,
                git_sync="deferred",
                message_id=sent_message_id,
                text=text,
            )
            return "send_message complete (sent={sent}, chunks={chunks}, attachments={attachments}, git_sync=deferred)".format(
                sent=sent,
                chunks=sent_chunks,
                attachments=len(attachment_names),
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
                                        timestamp = datetime.now(tz=UTC).isoformat()
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

        @tool(shell_tool_name)
        async def run_shell_tool(
            command: str,
            timeout_seconds: int = 120,
            max_output_chars: int = SHELL_OUTPUT_LIMIT_CHARS,
            async_mode: bool = False,
        ) -> str:
            """Run an arbitrary shell command on this machine.

            By default this blocks until the command finishes, subject to
            timeout_seconds. For long-running commands (builds, deployments,
            data processing, agent jobs like acpx/codex exec), set
            async_mode=True. The harness then spawns the command in the
            background and returns immediately with a job_id and PID.
            While the job runs you can keep working on other things.

            Check on async jobs with:
              - shell_jobs_list() — all registered jobs (running + recently finished)
              - shell_job_output(job_id) — tail stdout/stderr + current status

            Kill an async job by running `kill <PID>` (or `kill -9 <PID>`) in
            another shell call. Pick the signal level yourself.
            """
            normalized_command = command.strip()
            if not normalized_command:
                return "command is required."
            if timeout_seconds <= 0:
                return "timeout_seconds must be > 0."
            if max_output_chars <= 0:
                return "max_output_chars must be > 0."

            if async_mode:
                argv = _shell_command_for_platform(normalized_command)
                # Capture the channel at spawn time so the completion event can
                # resume the same conversation, even if current_channel_id has
                # rotated to a different event by the time the job exits.
                spawn_channel_id = self.current_channel_id
                spawn_channel_name = None
                try:
                    phone_book = getattr(self, "phone_book", None)
                    if phone_book is not None and spawn_channel_id:
                        entry = phone_book.entries.get(spawn_channel_id)
                        if entry is not None:
                            spawn_channel_name = entry.name
                except Exception:
                    spawn_channel_name = None
                on_complete = getattr(self, "_handle_shell_job_complete", None)
                try:
                    job = await asyncio.to_thread(
                        self.shell_jobs.spawn,
                        normalized_command,
                        argv=argv,
                        channel_id=spawn_channel_id,
                        channel_name=spawn_channel_name,
                        on_complete=on_complete,
                    )
                except FileNotFoundError:
                    self.log_event(
                        "tool_call_error",
                        tool=shell_tool_name,
                        error_type="missing_shell_binary",
                        async_mode=True,
                    )
                    return f"{shell_tool_name} is not available on this machine."
                self.log_event(
                    "tool_call",
                    tool=shell_tool_name,
                    async_mode=True,
                    job_id=job.job_id,
                    pid=job.pid,
                    command=normalized_command,
                )
                return (
                    f"Spawned async job {job.job_id} (pid {job.pid}).\n"
                    f"Command: {normalized_command}\n"
                    f"Status: running. Use shell_jobs_list to see all jobs, "
                    f"shell_job_output(\"{job.job_id}\") to tail output, "
                    f"or kill {job.pid} to stop it."
                )

            try:
                completed = await asyncio.to_thread(
                    _run_shell,
                    command=normalized_command,
                    timeout_seconds=timeout_seconds,
                )
            except FileNotFoundError:
                self.log_event(
                    "tool_call_error",
                    tool=shell_tool_name,
                    error_type="missing_shell_binary",
                )
                return f"{shell_tool_name} is not available on this machine."
            except subprocess.TimeoutExpired as exc:
                partial_stdout = str(exc.stdout or "")
                partial_stderr = str(exc.stderr or "")
                partial = "\n".join([part for part in (partial_stdout, partial_stderr) if part]).strip()
                if len(partial) > max_output_chars:
                    partial = partial[:max_output_chars] + "\n[output truncated]"
                self.log_event(
                    "tool_call_error",
                    tool=shell_tool_name,
                    error_type="timeout",
                    timeout_seconds=timeout_seconds,
                    command=normalized_command,
                )
                if partial:
                    return f"{shell_tool_name} timed out after {timeout_seconds}s.\n{partial}"
                return f"{shell_tool_name} timed out after {timeout_seconds}s."

            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            combined = "\n".join([part for part in (stdout_text, stderr_text) if part]).strip()
            if not combined:
                combined = "(no output)"
            if len(combined) > max_output_chars:
                combined = combined[:max_output_chars] + "\n[output truncated]"

            self.log_event(
                "tool_call",
                tool=shell_tool_name,
                exit_code=completed.returncode,
                timeout_seconds=timeout_seconds,
                command=normalized_command,
            )

            # Classify bash commands that read files so DAG traversal
            # analysis can detect read patterns even without dedicated
            # read_file tool usage.
            if completed.returncode == 0:
                for rpath in _extract_read_paths(normalized_command):
                    self.log_event(
                        "file_read",
                        tool=shell_tool_name,
                        file_path=rpath,
                        via="bash_classify",
                    )

            return f"[exit_code={completed.returncode}]\n{combined}"

        @tool("read_file")
        async def read_file(
            file_path: str,
            offset: int = 0,
            limit: int = 2000,
        ) -> str:
            """Read a file and return its contents with line numbers.

            Args:
                file_path: Absolute or relative path to the file.
                offset: Line number to start reading from (0-based).
                limit: Maximum number of lines to return.
            """
            resolved = Path(file_path).expanduser().resolve()
            if not resolved.is_file():
                self.log_event(
                    "tool_call_error",
                    tool="read_file",
                    error_type="not_found",
                    file_path=str(resolved),
                )
                return f"File not found: {resolved}"

            try:
                text = await asyncio.to_thread(resolved.read_text, encoding="utf-8", errors="replace")
            except OSError as exc:
                self.log_event(
                    "tool_call_error",
                    tool="read_file",
                    error_type="os_error",
                    file_path=str(resolved),
                )
                return f"Error reading {resolved}: {exc}"

            lines = text.splitlines()
            selected = lines[offset : offset + limit]
            numbered = [f"{i + offset + 1:6d}\t{line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            if not result:
                result = "(empty file)"

            self.log_event(
                "file_read",
                tool="read_file",
                file_path=str(resolved),
                offset=offset,
                limit=limit,
                lines_returned=len(selected),
                total_lines=len(lines),
            )
            return result

        @tool("glob")
        async def glob_files(
            pattern: str,
            path: str = ".",
        ) -> str:
            """Find files matching a glob pattern.

            Args:
                pattern: Glob pattern (e.g. '**/*.py', 'state/*.md').
                path: Directory to search in. Defaults to current directory.
            """
            base = Path(path).expanduser().resolve()
            if not base.is_dir():
                return f"Not a directory: {base}"

            try:
                matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError as exc:
                return f"Error during glob: {exc}"

            # Cap output
            capped = matches[:200]
            result_lines = [str(m) for m in capped]
            suffix = f"\n... and {len(matches) - 200} more" if len(matches) > 200 else ""

            self.log_event(
                "tool_call",
                tool="glob",
                pattern=pattern,
                path=str(base),
                matches=len(matches),
            )
            for match_path in capped:
                if match_path.is_file():
                    self.log_event(
                        "file_discovery",
                        tool="glob",
                        file_path=str(match_path),
                    )
            return "\n".join(result_lines) + suffix if result_lines else "No matches."

        @tool("edit_file")
        async def edit_file(
            file_path: str,
            old_string: str,
            new_string: str,
        ) -> str:
            """Replace an exact string in a file.

            Args:
                file_path: Path to the file to modify.
                old_string: The exact text to find and replace (must be unique in the file).
                new_string: The replacement text.
            """
            resolved = Path(file_path).expanduser().resolve()
            if not resolved.is_file():
                return f"File not found: {resolved}"

            try:
                content = await asyncio.to_thread(resolved.read_text, encoding="utf-8")
            except OSError as exc:
                return f"Error reading {resolved}: {exc}"

            count = content.count(old_string)
            if count == 0:
                return "old_string not found in file."
            if count > 1:
                return f"old_string appears {count} times — must be unique. Provide more context."

            new_content = content.replace(old_string, new_string, 1)
            try:
                await asyncio.to_thread(resolved.write_text, new_content, encoding="utf-8")
            except OSError as exc:
                return f"Error writing {resolved}: {exc}"

            self.log_event(
                "file_read",
                tool="edit_file",
                file_path=str(resolved),
            )
            self.log_event(
                "file_write",
                tool="edit_file",
                file_path=str(resolved),
            )
            return f"Edited {resolved}"

        @tool("write_file")
        async def write_file(
            file_path: str,
            content: str,
        ) -> str:
            """Write content to a file, creating it if needed.

            Args:
                file_path: Path to the file to write.
                content: The full content to write.
            """
            resolved = Path(file_path).expanduser().resolve()

            resolved.parent.mkdir(parents=True, exist_ok=True)

            try:
                await asyncio.to_thread(resolved.write_text, content, encoding="utf-8")
            except OSError as exc:
                return f"Error writing {resolved}: {exc}"

            self.log_event(
                "file_write",
                tool="write_file",
                file_path=str(resolved),
                bytes_written=len(content.encode("utf-8")),
            )
            return f"Wrote {resolved} ({len(content)} chars)"

        @tool("fetch_url")
        async def fetch_url(
            url: str,
            timeout_seconds: int = 20,
            max_bytes: int = 2_000_000,
        ) -> str:
            """Download a URL to a session cache file and return its path + metadata."""
            normalized_url = url.strip()
            if not normalized_url:
                return "url is required."
            if timeout_seconds <= 0:
                return "timeout_seconds must be > 0."
            if max_bytes <= 0:
                return "max_bytes must be > 0."

            parsed = urlparse(normalized_url)
            if parsed.scheme not in {"http", "https"}:
                return "Only http:// and https:// URLs are supported."

            cache_dir = self.fetch_cache_dir
            cache_dir.mkdir(parents=True, exist_ok=True)

            base_name = _name_from_url(normalized_url)
            digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:12]
            stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
            body_path = cache_dir / f"{stamp}-{digest}-{base_name}"
            meta_path = cache_dir / f"{body_path.name}.meta.json"

            try:
                fetched = await asyncio.to_thread(
                    _download_url_bytes,
                    url=normalized_url,
                    target_path=body_path,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )
            except HTTPError as exc:
                self.log_event(
                    "tool_call_error",
                    tool="fetch_url",
                    url=normalized_url,
                    error_type="http_error",
                    status=getattr(exc, "code", None),
                )
                return f"fetch_url failed: HTTP {exc.code} ({exc.reason})"
            except URLError as exc:
                self.log_event(
                    "tool_call_error",
                    tool="fetch_url",
                    url=normalized_url,
                    error_type="url_error",
                    reason=str(getattr(exc, "reason", exc)),
                )
                return f"fetch_url failed: {getattr(exc, 'reason', exc)}"
            except ValueError as exc:
                body_path.unlink(missing_ok=True)
                self.log_event(
                    "tool_call_error",
                    tool="fetch_url",
                    url=normalized_url,
                    error_type="validation_error",
                    error=str(exc),
                )
                return f"fetch_url failed: {exc}"
            except OSError as exc:
                body_path.unlink(missing_ok=True)
                self.log_event(
                    "tool_call_error",
                    tool="fetch_url",
                    url=normalized_url,
                    error_type="filesystem_error",
                    error_type_detail=type(exc).__name__,
                )
                return "fetch_url failed: could not write downloaded content."

            body_virtual_path = _virtual_path(body_path, root=self.home)
            meta_virtual_path = _virtual_path(meta_path, root=self.home)
            payload = {
                "url": normalized_url,
                "final_url": fetched["final_url"],
                "status": fetched["status"],
                "content_type": fetched["content_type"],
                "bytes": fetched["bytes"],
                "sha256": fetched["sha256"],
                "file_path": body_virtual_path,
                "metadata_path": meta_virtual_path,
            }
            meta_path.write_text(
                json.dumps(payload, ensure_ascii=True, sort_keys=False, indent=2) + "\n",
                encoding="utf-8",
            )

            self.log_event(
                "tool_call",
                tool="fetch_url",
                url=normalized_url,
                final_url=fetched["final_url"],
                status=fetched["status"],
                bytes=fetched["bytes"],
                file_path=body_virtual_path,
            )
            self.log_event(
                "file_write",
                tool="fetch_url",
                file_path=str(body_path),
            )
            return yaml.safe_dump(payload, sort_keys=False)

        @tool("web_search")
        async def web_search(
            query: str,
            limit: int = 5,
            topic: str = "general",
            time_range: str | None = None,
            timeout_seconds: int = 20,
        ) -> str:
            """Search the web via Tavily and return compact results."""
            normalized_query = query.strip()
            if not normalized_query:
                return "query is required."
            if limit <= 0:
                return "limit must be > 0."
            if limit > 10:
                limit = 10

            normalized_topic = topic.strip().lower()
            if normalized_topic not in {"general", "news", "finance"}:
                return "topic must be one of: general, news, finance."

            normalized_time_range = time_range.strip().lower() if time_range else None
            if normalized_time_range and normalized_time_range not in {"day", "week", "month", "year"}:
                return "time_range must be one of: day, week, month, year."
            if timeout_seconds <= 0:
                return "timeout_seconds must be > 0."

            if not self.web_search_enabled:
                return "web_search is disabled."

            api_key = self.tavily_api_key
            if not api_key:
                return "web_search is disabled."

            search_url = self.tavily_search_url or DEFAULT_TAVILY_SEARCH_URL
            if not search_url:
                return "TAVILY_SEARCH_URL is empty."

            payload: dict[str, Any] = {
                "query": normalized_query,
                "topic": normalized_topic,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            }
            if normalized_time_range:
                payload["time_range"] = normalized_time_range

            try:
                response = await asyncio.to_thread(
                    _post_json,
                    url=search_url,
                    payload=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout_seconds=timeout_seconds,
                )
            except HTTPError as exc:
                self.log_event(
                    "tool_call_error",
                    tool="web_search",
                    query=normalized_query,
                    error_type="http_error",
                    status=getattr(exc, "code", None),
                )
                return f"web_search failed: HTTP {exc.code} ({exc.reason})"
            except URLError as exc:
                self.log_event(
                    "tool_call_error",
                    tool="web_search",
                    query=normalized_query,
                    error_type="url_error",
                    reason=str(getattr(exc, "reason", exc)),
                )
                return f"web_search failed: {getattr(exc, 'reason', exc)}"
            except (ValueError, json.JSONDecodeError) as exc:
                self.log_event(
                    "tool_call_error",
                    tool="web_search",
                    query=normalized_query,
                    error_type="decode_error",
                    error=str(exc),
                )
                return f"web_search failed: {exc}"

            raw = response["json"]
            rows = raw.get("results")
            if not isinstance(rows, list):
                rows = []

            compact_results: list[dict[str, Any]] = []
            for idx, item in enumerate(rows[:limit], start=1):
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                url = str(item.get("url", "")).strip()
                snippet = str(item.get("content", "")).strip()
                if len(snippet) > 800:
                    snippet = snippet[:800].rstrip() + "..."
                compact_results.append(
                    {
                        "rank": idx,
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "score": item.get("score"),
                    },
                )

            result_payload = {
                "query": normalized_query,
                "topic": normalized_topic,
                "time_range": normalized_time_range,
                "count": len(compact_results),
                "results": compact_results,
                "response_time": raw.get("response_time"),
            }
            self.log_event(
                "tool_call",
                tool="web_search",
                query=normalized_query,
                count=len(compact_results),
            )
            return yaml.safe_dump(result_payload, sort_keys=False)

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
            self.log_event(
                "file_write",
                tool="journal",
                file_path=str(self.layout.journal_log),
            )
            self.log_event(
                "file_read",
                tool="journal",
                file_path=str(self.layout.checkpoint_file),
            )
            return checkpoint

        @tool("react")
        async def react(
            emoji: str,
            message_id: str | None = None,
            channel_id: str | None = None,
        ) -> str:
            """React to a message in the current conversation. Defaults to the latest known message."""
            if not emoji.strip():
                return "emoji is required."

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

            if self.is_local_web_channel(target_channel_id):
                reacted = await self._react_to_message(
                    channel_id=target_channel_id,
                    message_id=str(target_message_id),
                    emoji=emoji,
                )
                if not reacted:
                    return f"Message {target_message_id} was not found in channel {target_channel_id}."
                self.log_event(
                    "tool_call",
                    tool="react",
                    emoji=emoji,
                    channel_id=target_channel_id,
                    message_id=str(target_message_id),
                )
                return f"Reacted to message {target_message_id} in channel {target_channel_id}."

            if self.discord_client is None or not self.discord_client.is_ready():
                return "Discord is not connected."

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
                        "text_preview": str(block["text"])[:80],
                    }
                    for block in blocks
                ],
            }
            self.log_event("tool_call", tool="list_memory_blocks", count=len(payload["blocks"]))
            for block in blocks:
                block_path = self._find_memory_block_path(block["id"])
                if block_path is not None:
                    self.log_event(
                        "file_read",
                        tool="list_memory_blocks",
                        file_path=str(block_path),
                    )
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
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(block, sort_keys=False), encoding="utf-8")
            self.log_event(
                "file_write",
                tool="create_memory_block",
                file_path=str(target),
                block_id=chosen_id,
            )
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
            self.log_event(
                "file_read",
                tool="update_memory_block",
                file_path=str(path),
                block_id=normalized_id,
            )
            self.log_event(
                "file_write",
                tool="update_memory_block",
                file_path=str(path),
                block_id=normalized_id,
            )
            return f"Updated memory block '{normalized_id}'."

        @tool("delete_memory_block")
        def delete_memory_block(block_id: str) -> str:
            """Delete a memory block from blocks/."""
            normalized_id = _slugify(block_id)
            path = self._find_memory_block_path(normalized_id)
            if path is None:
                return f"memory block '{normalized_id}' not found."
            path.unlink()
            self.log_event(
                "file_write",
                tool="delete_memory_block",
                file_path=str(path),
                block_id=normalized_id,
                action="delete",
            )
            return f"Deleted memory block '{normalized_id}'."

        @tool("list_schedules")
        def list_schedules() -> str:
            """List scheduler jobs from scheduler.yaml."""
            jobs = [job.to_dict() for job in self._load_scheduler_jobs()]
            self.log_event("tool_call", tool="list_schedules", count=len(jobs))
            self.log_event(
                "file_read",
                tool="list_schedules",
                file_path=str(self.layout.scheduler_file),
            )
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

            with _SCHEDULER_LOCK:
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
            self.log_event(
                "file_write",
                tool="add_schedule",
                file_path=str(self.layout.scheduler_file),
                name=name,
            )
            return f"Added schedule '{name}'."

        @tool("remove_schedule")
        def remove_schedule(name: str) -> str:
            """Remove a scheduler job by name."""
            with _SCHEDULER_LOCK:
                before = self._load_scheduler_jobs()
                after = [job for job in before if job.name != name]
                self._save_scheduler_jobs(after)
                self._reload_scheduler_jobs()
            self.log_event(
                "file_write",
                tool="remove_schedule",
                file_path=str(self.layout.scheduler_file),
                name=name,
                removed=len(before) - len(after),
            )
            return f"Removed {len(before) - len(after)} schedule(s) named '{name}'."

        @tool("reload_pollers")
        def reload_pollers() -> str:
            """Reload all pollers from skills/*/pollers.json files. Call this after installing or updating a skill that includes pollers."""
            self._reload_scheduler_jobs()
            pollers = self._discover_pollers()
            self.log_event("tool_call", tool="reload_pollers", count=len(pollers))
            if not pollers:
                return "Reloaded. No pollers found."
            names = [p.name for p in pollers]
            return f"Reloaded. {len(pollers)} poller(s) registered: {', '.join(names)}"

        @tool("lookup")
        def lookup(query: str) -> str:
            """Look up a Discord user or channel by name or ID.  Returns matching entries with their IDs, mention format, and type.  Use this when you need to find a channel_id or user mention format."""
            results = self.phone_book.lookup(query)
            if not results:
                return f"No matches for '{query}'.  The phone book updates as new users and channels are discovered."
            lines: list[str] = []
            for entry in results:
                if entry.kind == "user":
                    bot_tag = " [bot]" if entry.is_bot else ""
                    lines.append(
                        f"User: {entry.name} | ID: {entry.id} | Mention: <@{entry.id}>{bot_tag}",
                    )
                else:
                    lines.append(
                        f"Channel: {entry.name} | ID: {entry.id} | Type: {entry.extra}",
                    )
            self.log_event("tool_call", tool="lookup", query=query, results=len(results))
            return "\n".join(lines)

        @tool("climb_register")
        def climb_register(
            climb_id: str,
            climb_dir: str,
            model: str | None = None,
        ) -> str:
            """Register and start a new mountaineering climb.

            Args:
                climb_id: Unique identifier for this climb.
                climb_dir: Path to the climb directory (must contain program.md,
                    config.json, eval/, workspace/).
                model: Optional LangGraph model string (e.g. "anthropic:claude-sonnet-4-6").
            """
            from .supervisor import preflight_check

            issues = preflight_check(climb_dir)
            if issues:
                self.log_event(
                    "tool_call_error",
                    tool="climb_register",
                    climb_id=climb_id,
                    error_type="preflight_failed",
                    issues=issues,
                )
                formatted = "\n".join(f"  - {i}" for i in issues)
                return f"Pre-flight failed for {climb_id}:\n{formatted}"

            # Collect skill dirs so climbers inherit parent agent's skills
            skill_dirs: list[str] = []
            if self.layout.skills_dir.exists():
                skill_dirs.append(str(self.layout.skills_dir))

            self.supervisor.register(
                climb_id,
                climb_dir,
                model=model,
                skills=skill_dirs or None,
            )
            self.log_event("tool_call", tool="climb_register", climb_id=climb_id, climb_dir=climb_dir)
            return f"Registered and started climb '{climb_id}'."

        @tool("climb_unregister")
        def climb_unregister(climb_id: str) -> str:
            """Stop a running climb and remove it from the manifest."""
            self.supervisor.unregister(climb_id)
            self.log_event("tool_call", tool="climb_unregister", climb_id=climb_id)
            return f"Unregistered climb '{climb_id}'."

        @tool("climb_status")
        def climb_status() -> str:
            """Get the status of all registered mountaineering climbs."""
            block = self.supervisor.format_monitoring_block()
            self.log_event("tool_call", tool="climb_status")
            return block

        @tool("shell_jobs_list")
        async def shell_jobs_list() -> str:
            """List all async shell jobs (running + recently finished).

            Use this to check on commands you spawned earlier with
            run_shell_tool(async_mode=True). Shows job_id, pid, status,
            elapsed time, and seconds since the last live output signal.
            """
            jobs = await asyncio.to_thread(self.shell_jobs.all_jobs)
            if not jobs:
                self.log_event("tool_call", tool="shell_jobs_list", count=0)
                return "No async shell jobs registered."
            lines = [
                f"{len(jobs)} async shell job(s):",
                "",
            ]
            for job in jobs:
                snap = job.snapshot()
                cmd_preview = snap["command"]
                if len(cmd_preview) > 80:
                    cmd_preview = cmd_preview[:77] + "..."
                lines.append(
                    f"  {snap['job_id']}  pid={snap['pid']}  {snap['status']}  "
                    f"elapsed={snap['elapsed_seconds']:.1f}s  "
                    f"last_signal={snap['seconds_since_last_signal']:.1f}s ago"
                )
                lines.append(f"    cmd: {cmd_preview}")
                if snap["exit_code"] is not None:
                    lines.append(f"    exit_code: {snap['exit_code']}")
            self.log_event(
                "tool_call",
                tool="shell_jobs_list",
                count=len(jobs),
            )
            return "\n".join(lines)

        @tool("shell_job_output")
        async def shell_job_output(
            job_id: str,
            tail_lines: int = 200,
            stream: str = "both",
        ) -> str:
            """Tail stdout/stderr for an async shell job.

            Args:
                job_id: The j_abc123 identifier returned by run_shell_tool(async_mode=True).
                tail_lines: Max lines per stream to return (default 200).
                stream: "stdout", "stderr", or "both".
            """
            if stream not in ("stdout", "stderr", "both"):
                return 'stream must be one of: "stdout", "stderr", "both"'
            if tail_lines < 0:
                return "tail_lines must be >= 0."

            result = await asyncio.to_thread(
                self.shell_jobs.read_output,
                job_id,
                tail_lines=tail_lines,
                stream=stream,
            )
            if "error" in result:
                self.log_event(
                    "tool_call_error",
                    tool="shell_job_output",
                    error_type="unknown_job_id",
                    job_id=job_id,
                )
                return result["error"]
            self.log_event(
                "tool_call",
                tool="shell_job_output",
                job_id=job_id,
                status=result["status"],
            )
            parts = [
                f"job_id: {result['job_id']}",
                f"pid: {result['pid']}",
                f"status: {result['status']}",
                f"elapsed: {result['elapsed_seconds']:.1f}s",
                f"last_signal: {result['seconds_since_last_signal']:.1f}s ago",
            ]
            if result["exit_code"] is not None:
                parts.append(f"exit_code: {result['exit_code']}")
            parts.append(f"command: {result['command']}")
            parts.append("")
            if stream in ("stdout", "both"):
                parts.append("--- stdout tail ---")
                parts.append(result["stdout_tail"] or "(empty)")
            if stream in ("stderr", "both"):
                parts.append("--- stderr tail ---")
                parts.append(result["stderr_tail"] or "(empty)")
            return "\n".join(parts)

        send_message.handle_tool_error = True
        list_messages.handle_tool_error = True
        run_shell_tool.handle_tool_error = True
        shell_jobs_list.handle_tool_error = True
        shell_job_output.handle_tool_error = True
        fetch_url.handle_tool_error = True

        tools: list[Any] = [
            send_message,
            react,
            list_messages,
            lookup,
            run_shell_tool,
            shell_jobs_list,
            shell_job_output,
            fetch_url,
            journal,
            list_memory_blocks,
            create_memory_block,
            update_memory_block,
            delete_memory_block,
            list_schedules,
            add_schedule,
            remove_schedule,
            reload_pollers,
            climb_register,
            climb_unregister,
            climb_status,
        ]
        if self.web_search_enabled:
            web_search.handle_tool_error = True
            tools.insert(4, web_search)
        return tools
