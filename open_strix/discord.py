from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

import discord

from .models import AgentEvent

UTC = timezone.utc
DISCORD_MESSAGE_CHAR_LIMIT = 2000
DISCORD_HISTORY_REFRESH_LIMIT = 50
ERROR_REACTION_EMOJI = "❌"
WARNING_REACTION_EMOJI = "⚠️"


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _channel_conversation_type(channel: Any) -> str:
    channel_type = getattr(channel, "type", None)
    dm_type = getattr(discord.ChannelType, "private", None)
    if channel_type == dm_type:
        return "dm"
    if isinstance(channel, discord.DMChannel):
        return "dm"
    return "multi_user"


def _channel_visibility(channel: Any, conversation_type: str) -> str:
    if conversation_type == "dm":
        return "private"

    channel_type = getattr(channel, "type", None)
    group_type = getattr(discord.ChannelType, "group", None)
    private_thread_type = getattr(discord.ChannelType, "private_thread", None)
    if channel_type in {kind for kind in (group_type, private_thread_type) if kind is not None}:
        return "private"

    public_thread_type = getattr(discord.ChannelType, "public_thread", None)
    news_thread_type = getattr(discord.ChannelType, "news_thread", None)
    if channel_type in {
        kind for kind in (public_thread_type, news_thread_type) if kind is not None
    }:
        return "public"

    guild = getattr(channel, "guild", None)
    permissions_for = getattr(channel, "permissions_for", None)
    default_role = getattr(guild, "default_role", None)
    if guild is not None and callable(permissions_for) and default_role is not None:
        permissions = permissions_for(default_role)
        can_view = getattr(permissions, "view_channel", None)
        if can_view is None:
            can_view = getattr(permissions, "read_messages", None)
        if can_view is not None:
            return "public" if bool(can_view) else "private"

    return "unknown"


def _describe_channel_context(channel: Any) -> tuple[str, str, str | None]:
    conversation_type = _channel_conversation_type(channel)
    visibility = _channel_visibility(channel, conversation_type)
    channel_name = str(getattr(channel, "name", "")).strip() or None
    return conversation_type, visibility, channel_name


def _chunk_discord_message(text: str, limit: int = DISCORD_MESSAGE_CHAR_LIMIT) -> list[str]:
    if limit <= 0:
        limit = DISCORD_MESSAGE_CHAR_LIMIT
    if len(text) <= limit:
        return [text]

    def _split_oversized_block(block: str) -> list[str]:
        if len(block) <= limit:
            return [block]

        # Prefer line boundaries before falling back to hard slicing.
        lines = block.splitlines(keepends=True)
        if len(lines) <= 1:
            return [block[idx : idx + limit] for idx in range(0, len(block), limit)]

        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(line) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(
                    line[idx : idx + limit] for idx in range(0, len(line), limit)
                )
                continue
            if not current:
                current = line
                continue
            if len(current) + len(line) <= limit:
                current += line
                continue
            chunks.append(current)
            current = line

        if current:
            chunks.append(current)
        return chunks

    # Keep paragraph separators attached to the prior block so chunk joins are exact.
    paragraph_blocks: list[str] = []
    cursor = 0
    for match in re.finditer(r"\n\s*\n+", text):
        end = match.end()
        paragraph_blocks.append(text[cursor:end])
        cursor = end
    if cursor < len(text):
        paragraph_blocks.append(text[cursor:])
    if not paragraph_blocks:
        paragraph_blocks = [text]

    chunks: list[str] = []
    current = ""
    for block in paragraph_blocks:
        if not block:
            continue
        if len(block) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_oversized_block(block))
            continue
        if not current:
            current = block
            continue
        if len(current) + len(block) <= limit:
            current += block
            continue
        chunks.append(current)
        current = block

    if current:
        chunks.append(current)
    return chunks


class DiscordBridge(discord.Client):
    def __init__(self, app: Any) -> None:
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


class DiscordMixin:
    async def _send_discord_message(
        self,
        *,
        channel_id: str,
        text: str,
        attachment_paths: list[Path] | None = None,
        attachment_names: list[str] | None = None,
    ) -> tuple[bool, str | None, int]:
        chunks = [chunk for chunk in _chunk_discord_message(text) if chunk.strip()]
        files_to_send = attachment_paths or []
        outbound_attachment_names = attachment_names or []
        if files_to_send and not chunks:
            chunks = [""]

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
                    for chunk_idx, chunk in enumerate(chunks):
                        if chunk_idx == 0 and files_to_send:
                            discord_files = [discord.File(str(path)) for path in files_to_send]
                            if chunk:
                                sent_msg = await channel.send(chunk, files=discord_files)
                            else:
                                sent_msg = await channel.send(files=discord_files)
                        else:
                            sent_msg = await channel.send(chunk)
                        sent_message_id = str(getattr(sent_msg, "id", "")) or None
                        self._remember_message(
                            channel_id=channel_id,
                            author="open_strix",
                            content=chunk,
                            attachment_names=outbound_attachment_names if chunk_idx == 0 else [],
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
                if not chunk.strip():
                    continue
                print(f"[open-strix send_message channel={channel_id}] {chunk}")
            if outbound_attachment_names:
                print(
                    "[open-strix send_message attachments channel={channel_id}] {attachments}".format(
                        channel_id=channel_id,
                        attachments=", ".join(outbound_attachment_names),
                    ),
                )
            sent_chunks = len(chunks)
        return sent, sent_message_id, sent_chunks

    async def handle_discord_message(self, message: discord.Message) -> None:
        await self._refresh_channel_history_from_discord(
            channel_id=str(message.channel.id),
            before_message_id=str(message.id),
        )
        attachment_names = await self._save_attachments(message)
        channel_conversation_type, channel_visibility, channel_name = _describe_channel_context(
            message.channel,
        )
        prompt = (message.content or "").strip()
        if not prompt:
            prompt = "User sent a message with no text."
        author_id = str(getattr(message.author, "id", "")).strip() or None
        author_is_bot = bool(getattr(message.author, "bot", False))

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
            channel_name=channel_name,
            channel_conversation_type=channel_conversation_type,
            channel_visibility=channel_visibility,
            attachment_names=attachment_names,
            source_id=str(message.id),
            content=prompt,
        )
        await self.enqueue_event(
            AgentEvent(
                event_type="discord_message",
                prompt=prompt,
                channel_id=str(message.channel.id),
                channel_name=channel_name,
                channel_conversation_type=channel_conversation_type,
                channel_visibility=channel_visibility,
                author=str(message.author),
                author_id=author_id,
                attachment_names=attachment_names,
                source_id=str(message.id),
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
            "timestamp": timestamp if timestamp is not None else _utc_now_iso(),
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
