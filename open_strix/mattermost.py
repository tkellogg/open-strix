from __future__ import annotations

import asyncio
import contextlib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from .models import AgentEvent

UTC = timezone.utc
MATTERMOST_MESSAGE_CHAR_LIMIT = 16000


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _chunk_mattermost_message(text: str, limit: int = MATTERMOST_MESSAGE_CHAR_LIMIT) -> list[str]:
    if limit <= 0:
        limit = MATTERMOST_MESSAGE_CHAR_LIMIT
    if len(text) <= limit:
        return [text]

    def _split_oversized_block(block: str) -> list[str]:
        if len(block) <= limit:
            return [block]
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


def _parse_mattermost_url(url: str) -> tuple[str, str, int]:
    """Return (scheme, host, port) from a Mattermost URL."""
    raw = url.rstrip("/")
    if raw.startswith("http://"):
        scheme = "http"
        rest = raw[len("http://"):]
        default_port = 80
    elif raw.startswith("https://"):
        scheme = "https"
        rest = raw[len("https://"):]
        default_port = 443
    else:
        scheme = "https"
        rest = raw
        default_port = 443

    if ":" in rest:
        host, port_str = rest.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            host = rest
            port = default_port
    else:
        host = rest
        port = default_port

    return scheme, host, port


class MattermostBridge:
    """WebSocket bridge to a Mattermost server using mattermostdriver."""

    def __init__(self, app: Any, url: str, token: str, bot_user_id: str = "") -> None:
        from mattermostdriver import Driver  # type: ignore[import]

        scheme, host, port = _parse_mattermost_url(url)
        self._driver = Driver(
            {
                "url": host,
                "token": token,
                "scheme": scheme,
                "port": port,
            }
        )
        self._app = app
        self._bot_user_id = bot_user_id
        self._closed = False

    def login(self) -> None:
        self._driver.login()

    async def start(self) -> None:
        self.login()
        print(
            "Open-Strix is operational and listening on Mattermost.",
            flush=True,
        )
        self._app.log_event("mattermost_ready")
        await self._driver.init_websocket(self._handle_event)

    async def _handle_event(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return
        if event.get("event") != "posted":
            return
        data = event.get("data", {})
        post_raw = data.get("post", "{}")
        try:
            post = json.loads(post_raw)
        except Exception:
            return
        user_id = post.get("user_id", "")
        if self._bot_user_id and user_id == self._bot_user_id:
            return
        await self._app.handle_mattermost_message(post)

    def post_message(self, channel_id: str, message: str) -> dict[str, Any]:
        return self._driver.api["posts"].create_post(  # type: ignore[no-any-return]
            options={"channel_id": channel_id, "message": message}
        )

    def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> None:
        self._driver.api["reactions"].create_reaction(
            options={"user_id": user_id, "post_id": post_id, "emoji_name": emoji_name}
        )

    def post_typing(self, channel_id: str) -> None:
        try:
            self._driver.client.make_request(  # type: ignore[attr-defined]
                "post",
                "/users/me/typing",
                options={"channel_id": channel_id},
            )
        except Exception:
            pass

    def close(self) -> None:
        self._closed = True
        try:
            self._driver.disconnect()
        except Exception:
            pass

    def is_closed(self) -> bool:
        return self._closed


class MattermostMixin:
    async def _send_mattermost_message(
        self,
        channel_id: str,
        text: str,
    ) -> tuple[bool, str | None, int]:
        chunks = [chunk for chunk in _chunk_mattermost_message(text) if chunk.strip()]
        sent = False
        sent_message_id: str | None = None
        sent_chunks = 0

        if self.mattermost_client is not None and not self.mattermost_client.is_closed():
            try:
                for chunk in chunks:
                    result = self.mattermost_client.post_message(channel_id, chunk)
                    post_id = result.get("id") if isinstance(result, dict) else None
                    sent_message_id = str(post_id) if post_id else None
                    self._remember_message(
                        channel_id=channel_id,
                        author="open_strix",
                        content=chunk,
                        attachment_names=[],
                        message_id=sent_message_id,
                        is_bot=True,
                        source="mattermost",
                    )
                    if self._current_turn_sent_messages is not None:
                        self._current_turn_sent_messages.append(
                            (channel_id, sent_message_id),
                        )
                    sent = True
                    sent_chunks += 1
            except Exception as exc:
                self.log_event(
                    "mattermost_send_error",
                    channel_id=channel_id,
                    error=str(exc),
                )

        if not sent:
            for chunk in chunks:
                if not chunk.strip():
                    continue
                print(f"[open-strix mattermost send_message channel={channel_id}] {chunk}")
            sent_chunks = len(chunks)

        return sent, sent_message_id, sent_chunks

    async def _react_to_mattermost_message(
        self,
        channel_id: str,
        post_id: str,
        emoji_name: str,
    ) -> bool:
        if self.mattermost_client is None or self.mattermost_client.is_closed():
            return False
        bot_user_id = self.config.mattermost_bot_user_id
        if not bot_user_id:
            return False
        try:
            self.mattermost_client.add_reaction(bot_user_id, post_id, emoji_name)
            self.log_event(
                "mattermost_reaction_added",
                channel_id=channel_id,
                post_id=post_id,
                emoji_name=emoji_name,
            )
            return True
        except Exception as exc:
            self.log_event(
                "mattermost_reaction_error",
                channel_id=channel_id,
                post_id=post_id,
                emoji_name=emoji_name,
                error=str(exc),
            )
            return False

    @asynccontextmanager
    async def _mattermost_typing_indicator(self, event: AgentEvent):
        channel_id = event.channel_id
        if channel_id is None or self.mattermost_client is None or self.mattermost_client.is_closed():
            yield
            return

        async def _keep_typing() -> None:
            while True:
                try:
                    self.mattermost_client.post_typing(channel_id)
                except Exception:
                    pass
                await asyncio.sleep(5)

        task = asyncio.create_task(_keep_typing())
        self.log_event(
            "mattermost_typing_start",
            source_event_type=event.event_type,
            channel_id=channel_id,
            source_id=event.source_id,
        )
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self.log_event(
                "mattermost_typing_stop",
                source_event_type=event.event_type,
                channel_id=channel_id,
                source_id=event.source_id,
            )

    async def handle_mattermost_message(self, post_data: dict[str, Any]) -> None:
        channel_id = str(post_data.get("channel_id", ""))
        post_id = str(post_data.get("id", ""))
        user_id = str(post_data.get("user_id", ""))
        message = str(post_data.get("message", "")).strip()

        timestamp: str | None = None
        create_at = post_data.get("create_at")
        if isinstance(create_at, (int, float)):
            dt = datetime.fromtimestamp(create_at / 1000, tz=UTC)
            timestamp = dt.isoformat()

        prompt = message or "User sent a message with no text."

        self._remember_message(
            channel_id=channel_id,
            author=user_id,
            content=message,
            attachment_names=[],
            message_id=post_id,
            is_bot=False,
            source="mattermost",
            timestamp=timestamp,
        )
        self.log_event(
            "mattermost_message",
            channel_id=channel_id,
            author=user_id,
            source_id=post_id,
            content=prompt,
        )
        await self.enqueue_event(
            AgentEvent(
                event_type="mattermost_message",
                prompt=prompt,
                channel_id=channel_id,
                channel_name=None,
                channel_conversation_type="multi_user",
                channel_visibility="unknown",
                author=user_id,
                author_id=user_id,
                attachment_names=[],
                source_id=post_id,
            ),
        )
