from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

UTC = timezone.utc
HOOK_TIMEOUT_SECONDS = 10
VALID_HOOK_EVENTS = frozenset(
    {
        "pre_tool_call",
        "post_tool_call",
        "pre_prompt",
        "pre_startup",
        "post_startup",
        "pre_shutdown",
        "post_shutdown",
    },
)


@dataclass(frozen=True)
class HookConfig:
    """A command hook declared in a skill's hooks.json."""

    name: str
    command: str
    events: frozenset[str]
    env: dict[str, str]
    skill_dir: Path
    timeout_seconds: float = HOOK_TIMEOUT_SECONDS
    include_conversation: bool = False


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _parse_hook_events(entry: dict[str, Any]) -> frozenset[str]:
    raw_events: Any
    if "events" in entry:
        raw_events = entry["events"]
    elif "event" in entry:
        raw_events = [entry["event"]]
    else:
        return frozenset()

    if not isinstance(raw_events, list):
        return frozenset()

    events = frozenset(str(item).strip() for item in raw_events if str(item).strip())
    if not events.issubset(VALID_HOOK_EVENTS):
        return frozenset()
    return events


class HookManager:
    def __init__(self, strix: Any) -> None:
        self.strix = strix
        self.hooks: list[HookConfig] = []

    def discover(self) -> list[HookConfig]:
        """Scan skill directories for hooks.json files."""
        hooks: list[HookConfig] = []
        skills_dir = self.strix.layout.skills_dir
        if not skills_dir.exists():
            self.hooks = []
            return hooks

        for hooks_file in sorted(skills_dir.rglob("hooks.json")):
            skill_dir = hooks_file.parent
            try:
                raw = json.loads(hooks_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.strix.log_event(
                    "hook_invalid_json",
                    path=str(hooks_file),
                    error=str(exc),
                )
                continue

            if not isinstance(raw, dict):
                self.strix.log_event(
                    "hook_invalid_format",
                    path=str(hooks_file),
                    error="expected a JSON object with 'hooks' key",
                )
                continue

            entries = raw.get("hooks")
            if not isinstance(entries, list):
                self.strix.log_event(
                    "hook_invalid_format",
                    path=str(hooks_file),
                    error="'hooks' key must be an array",
                )
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                name = str(entry.get("name", "")).strip()
                command = str(entry.get("command", "")).strip()
                events = _parse_hook_events(entry)
                if not name or not command or not events:
                    self.strix.log_event(
                        "hook_missing_fields",
                        path=str(hooks_file),
                        entry=entry,
                    )
                    continue

                env = entry.get("env", {})
                if not isinstance(env, dict):
                    env = {}

                timeout_seconds = entry.get("timeout_seconds", HOOK_TIMEOUT_SECONDS)
                try:
                    timeout = float(timeout_seconds)
                except (TypeError, ValueError):
                    timeout = HOOK_TIMEOUT_SECONDS
                if timeout <= 0:
                    timeout = HOOK_TIMEOUT_SECONDS

                include_conversation = bool(entry.get("include_conversation", False))

                hooks.append(
                    HookConfig(
                        name=name,
                        command=command,
                        events=events,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                        timeout_seconds=timeout,
                        include_conversation=include_conversation,
                    ),
                )

        self.hooks = hooks
        return hooks

    async def run_event(self, event_type: str, event: dict[str, Any]) -> dict[str, Any]:
        """Run all hooks registered for event_type, piping mutations forward."""
        if event_type not in VALID_HOOK_EVENTS:
            return event

        current = {
            "type": event_type,
            "timestamp": event.get("timestamp") or _utc_now_iso(),
            "session_id": getattr(self.strix, "session_id", "unknown"),
            **event,
        }
        for hook in self.hooks:
            if event_type not in hook.events:
                continue
            current = await self._run_hook(hook, self._payload_for_hook(hook, current))
            current.pop("conversation", None)
        return current

    def _payload_for_hook(self, hook: HookConfig, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        if hook.include_conversation:
            payload["conversation"] = self._conversation_payload()
        else:
            payload.pop("conversation", None)
        return payload

    def _conversation_payload(self) -> dict[str, Any]:
        all_messages = self._persisted_conversation_messages()
        source = "chat_history_log"
        if not all_messages:
            all_messages = [
                self._message_payload(item)
                for item in getattr(self.strix, "message_history_all", [])
            ]
            source = "memory"
        channel_id = getattr(self.strix, "current_channel_id", None)
        channel_messages = [
            item
            for item in all_messages
            if channel_id is not None and item.get("channel_id") == channel_id
        ]
        return {
            "current_channel_id": channel_id,
            "source": source,
            "all_messages": all_messages,
            "channel_messages": channel_messages,
        }

    def _persisted_conversation_messages(self) -> list[dict[str, Any]]:
        layout = getattr(self.strix, "layout", None)
        path = getattr(layout, "chat_history_log", None)
        if path is None or not Path(path).exists():
            return []

        messages: list[dict[str, Any]] = []
        try:
            handle = Path(path).open("r", encoding="utf-8")
        except OSError:
            return []
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict) or parsed.get("type") != "message":
                    continue
                messages.append(self._message_payload(parsed))
        return messages

    @staticmethod
    def _message_payload(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {"content": str(item)}
        return {
            key: value
            for key, value in item.items()
            if key
            in {
                "timestamp",
                "channel_id",
                "channel_name",
                "author",
                "author_id",
                "content",
                "attachments",
                "attachment_names",
                "message_id",
                "is_bot",
                "source",
                "format",
                "reactions",
            }
        }

    async def _run_hook(self, hook: HookConfig, event: dict[str, Any]) -> dict[str, Any]:
        env = {
            **os.environ,
            **hook.env,
            "STATE_DIR": str(hook.skill_dir),
            "HOOK_NAME": hook.name,
            "HOOK_EVENT": str(event.get("type", "")),
        }
        payload = json.dumps(event, ensure_ascii=True, default=str) + "\n"

        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(hook.skill_dir),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(payload.encode("utf-8")),
                timeout=hook.timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.strix.log_event(
                "hook_timeout",
                name=hook.name,
                hook_event_type=event.get("type"),
                timeout_seconds=hook.timeout_seconds,
            )
            try:
                proc.kill()
            except Exception:
                pass
            return event
        except Exception as exc:
            self.strix.log_event(
                "hook_exec_error",
                name=hook.name,
                hook_event_type=event.get("type"),
                error=str(exc),
            )
            return event

        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr_text:
            self.strix.log_event(
                "hook_stderr",
                name=hook.name,
                hook_event_type=event.get("type"),
                stderr=stderr_text[:2000],
            )

        if proc.returncode != 0:
            self.strix.log_event(
                "hook_nonzero_exit",
                name=hook.name,
                hook_event_type=event.get("type"),
                returncode=proc.returncode,
            )
            return event

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            return event

        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            self.strix.log_event(
                "hook_invalid_output",
                name=hook.name,
                hook_event_type=event.get("type"),
                error=str(exc),
                output=stdout_text[:500],
            )
            return event

        if not isinstance(parsed, dict):
            self.strix.log_event(
                "hook_invalid_output",
                name=hook.name,
                hook_event_type=event.get("type"),
                error="hook stdout must be a JSON object",
                output=stdout_text[:500],
            )
            return event

        return parsed

    def wrap_tools(self, tools: list[Any]) -> list[Any]:
        return [self.wrap_tool(tool) for tool in tools]

    def wrap_tool(self, wrapped: Any) -> Any:
        """Return a tool with hook execution around ainvoke."""
        hooks = self
        tool_name = str(getattr(wrapped, "name", "unknown_tool"))

        async def _hooked_tool(**kwargs: Any) -> Any:
            pre_event = await hooks.run_event(
                "pre_tool_call",
                {
                    "tool": tool_name,
                    "args": kwargs,
                    "channel_id": getattr(hooks.strix, "current_channel_id", None),
                    "current_event": getattr(hooks.strix, "current_event_label", None),
                },
            )
            # Pre-tool-call BLOCK verb: a hook may return `_block` to short-circuit
            # the tool call. The wrapped tool is NOT invoked. Instead, the agent
            # sees `_block.result` as the tool's return value (just like a normal
            # tool result), and a `hook_blocked_tool_call` event is logged.
            #
            # Shape:
            #   {"_block": {"reason": "<short id>", "result": "<text to agent>"}}
            #
            # Both `reason` and `result` are required strings. Invalid specs are
            # logged via `hook_invalid_block` and the call proceeds normally.
            #
            # Why a new verb: the prior contract only allowed args mutation, so a
            # hook that wanted to reject a bad call had to mutate args into a
            # diagnostic payload — which then actually executed (e.g. a
            # `send_message` diagnostic still shipped to the channel). With
            # `_block`, the call is suppressed and the diagnostic goes back to
            # the agent as the tool result instead. Born 2026-05-19 from Tim's
            # ask after the send-message-shell-leak-block hook's mutate-to-
            # diagnostic shape leaked into chat.
            block_spec = pre_event.get("_block")
            if isinstance(block_spec, dict):
                reason = block_spec.get("reason")
                synthetic_result = block_spec.get("result")
                if isinstance(reason, str) and isinstance(synthetic_result, str):
                    hooks.strix.log_event(
                        "hook_blocked_tool_call",
                        tool=tool_name,
                        reason=reason,
                        channel_id=getattr(hooks.strix, "current_channel_id", None),
                    )
                    await hooks.run_event(
                        "post_tool_call",
                        {
                            "tool": tool_name,
                            "args": pre_event.get("args", kwargs),
                            "status": "blocked",
                            "block_reason": reason,
                            "result": synthetic_result,
                            "channel_id": getattr(hooks.strix, "current_channel_id", None),
                            "current_event": getattr(hooks.strix, "current_event_label", None),
                        },
                    )
                    return synthetic_result
                hooks.strix.log_event(
                    "hook_invalid_block",
                    tool=tool_name,
                    error="'_block' must be an object with string 'reason' and 'result' fields",
                )

            next_args = pre_event.get("args", kwargs)
            if not isinstance(next_args, dict):
                hooks.strix.log_event(
                    "hook_invalid_mutation",
                    hook_event_type="pre_tool_call",
                    tool=tool_name,
                    error="'args' must remain a JSON object",
                )
                next_args = kwargs

            started_at = datetime.now(tz=UTC)
            try:
                result = await wrapped.ainvoke(next_args)
            except Exception as exc:
                await hooks.run_event(
                    "post_tool_call",
                    {
                        "tool": tool_name,
                        "args": next_args,
                        "status": "error",
                        "error": str(exc),
                        "error_class": type(exc).__name__,
                        "channel_id": getattr(hooks.strix, "current_channel_id", None),
                        "current_event": getattr(hooks.strix, "current_event_label", None),
                    },
                )
                raise

            duration_seconds = (
                datetime.now(tz=UTC) - started_at
            ).total_seconds()
            post_event = await hooks.run_event(
                "post_tool_call",
                {
                    "tool": tool_name,
                    "args": next_args,
                    "status": "success",
                    "result": result,
                    "duration_seconds": round(duration_seconds, 6),
                    "channel_id": getattr(hooks.strix, "current_channel_id", None),
                    "current_event": getattr(hooks.strix, "current_event_label", None),
                },
            )
            return post_event.get("result", result)

        hooked = StructuredTool.from_function(
            coroutine=_hooked_tool,
            name=tool_name,
            description=str(getattr(wrapped, "description", "")),
            return_direct=bool(getattr(wrapped, "return_direct", False)),
            args_schema=getattr(wrapped, "args_schema", None),
            response_format=getattr(wrapped, "response_format", "content"),
        )
        hooked.handle_tool_error = getattr(wrapped, "handle_tool_error", False)
        hooked.handle_validation_error = getattr(wrapped, "handle_validation_error", False)
        hooked.metadata = getattr(wrapped, "metadata", None)
        hooked.tags = getattr(wrapped, "tags", None)
        return hooked
