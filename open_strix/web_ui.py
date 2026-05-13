from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import uuid4

from aiohttp import ClientError, ClientSession, web
from aiohttp.web_request import FileField

from .models import AgentEvent
from .ops_dashboard import (
    build_dashboard_payload,
    parse_days_param,
    render_dashboard_html,
)
from .shell_jobs import (
    normalize_shell_job_scope,
    normalize_shell_job_stream,
    parse_shell_job_tail_lines,
    shell_job_snapshots,
)

if TYPE_CHECKING:
    from .app import OpenStrixApp

WEB_UI_CHANNEL_NAME = "Local Web"
WEB_UI_AUTHOR = "local_user"
WEB_UI_AUTHOR_ID = "local-web-user"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".heic", ".svg"}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
}


def _is_inline_image(path_text: str) -> bool:
    return Path(path_text).suffix.lower() in IMAGE_EXTENSIONS


class WebChatMixin:
    def is_local_web_channel(self, channel_id: str | None) -> bool:
        if channel_id in (None, ""):
            return False
        return str(channel_id).strip() == self.config.web_ui_channel_id

    def _new_web_message_id(self) -> str:
        return f"web-{uuid4().hex[:12]}"

    async def _store_web_uploads(
        self,
        uploads: list[FileField],
        *,
        message_id: str,
    ) -> list[str]:
        if not uploads:
            return []

        attachments_dir = self.layout.state_dir / "attachments" / "web"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[str] = []
        for idx, upload in enumerate(uploads, start=1):
            file_name = Path(upload.filename or "upload.bin").name or "upload.bin"
            target = attachments_dir / f"{message_id}-{idx}-{file_name}"
            target.write_bytes(upload.file.read())
            saved_paths.append(str(target.relative_to(self.home)))

        return saved_paths

    async def handle_web_message(
        self,
        *,
        text: str,
        uploads: list[FileField] | None = None,
    ) -> str:
        message_id = self._new_web_message_id()
        normalized_text = text.strip()
        attachment_names = await self._store_web_uploads(uploads or [], message_id=message_id)
        if not normalized_text and not attachment_names:
            raise ValueError("message text or at least one attachment is required")

        prompt = normalized_text or "User sent a message with no text."
        self._remember_message(
            channel_id=self.config.web_ui_channel_id,
            author=WEB_UI_AUTHOR,
            content=normalized_text,
            attachment_names=attachment_names,
            message_id=message_id,
            is_bot=False,
            source="web",
        )
        self.log_event(
            "web_message",
            channel_id=self.config.web_ui_channel_id,
            author=WEB_UI_AUTHOR,
            author_id=WEB_UI_AUTHOR_ID,
            channel_name=WEB_UI_CHANNEL_NAME,
            channel_conversation_type="dm",
            channel_visibility="private",
            attachment_names=attachment_names,
            source_id=message_id,
            content=prompt,
        )
        await self.enqueue_event(
            AgentEvent(
                event_type="web_message",
                prompt=prompt,
                channel_id=self.config.web_ui_channel_id,
                channel_name=WEB_UI_CHANNEL_NAME,
                channel_conversation_type="dm",
                channel_visibility="private",
                author=WEB_UI_AUTHOR,
                author_id=WEB_UI_AUTHOR_ID,
                attachment_names=attachment_names,
                source_id=message_id,
            ),
        )
        return message_id

    async def _send_web_message(
        self,
        *,
        channel_id: str,
        text: str,
        attachment_names: list[str] | None = None,
        format: str = "markdown",
    ) -> tuple[bool, str | None, int]:
        message_id = self._new_web_message_id()
        outbound_attachment_names = attachment_names or []
        self._remember_message(
            channel_id=channel_id,
            author="open_strix",
            content=text,
            attachment_names=outbound_attachment_names,
            message_id=message_id,
            is_bot=True,
            source="web",
            format=format,
        )
        if self._current_turn_sent_messages is not None:
            self._current_turn_sent_messages.append((channel_id, message_id))
        return True, message_id, 1

    async def _react_to_web_message(
        self,
        *,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> bool:
        return self._apply_reaction_to_memory(
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )

    def _web_attachment_payload(self, virtual_path: str) -> dict[str, Any]:
        normalized_path = virtual_path.lstrip("/")
        return {
            "path": virtual_path,
            "name": Path(virtual_path).name,
            "url": f"/files/{quote(normalized_path, safe='/')}",
            "is_image": _is_inline_image(virtual_path),
        }

    def serialize_web_messages(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        rows = list(self.message_history_by_channel.get(self.config.web_ui_channel_id, []))
        if before is not None:
            before_index = next(
                (idx for idx, row in enumerate(rows) if row.get("message_id") == before),
                len(rows),
            )
            rows = rows[:before_index]

        limit = max(limit, 0)
        start_index = max(len(rows) - limit, 0)
        has_more = start_index > 0
        rows = rows[start_index:]

        serialized: list[dict[str, Any]] = []
        for row in rows:
            attachments = row.get("attachments")
            serialized.append(
                {
                    "timestamp": row.get("timestamp"),
                    "channel_id": row.get("channel_id"),
                    "message_id": row.get("message_id"),
                    "author": row.get("author"),
                    "is_bot": bool(row.get("is_bot")),
                    "source": row.get("source"),
                    "content": row.get("content", ""),
                    "format": row.get("format", "markdown"),
                    "attachments": [
                        self._web_attachment_payload(str(path))
                        for path in attachments
                        if str(path).strip()
                    ]
                    if isinstance(attachments, list)
                    else [],
                    "reactions": list(row.get("reactions", [])),
                },
            )
        return serialized, has_more

    def resolve_web_shared_file(self, virtual_path: str) -> Path | None:
        normalized = virtual_path.lstrip("/").strip()
        if not normalized:
            return None

        allowed_paths = {
            str(path).lstrip("/").strip()
            for item in self.message_history_by_channel.get(self.config.web_ui_channel_id, [])
            for path in item.get("attachments", [])
            if str(path).strip()
        }
        if normalized not in allowed_paths:
            return None

        candidate = (self.home / normalized).resolve()
        if candidate != self.home and self.home not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate


def _web_agent_name(strix: OpenStrixApp) -> str:
    return strix.config.name or strix.home.name


def _turn_elapsed_seconds(strix: OpenStrixApp) -> float | None:
    turn_start = strix.current_turn_start
    if turn_start is None:
        return None
    return round(time.monotonic() - turn_start, 1)


def _shell_jobs_payload(strix: OpenStrixApp) -> list[dict]:
    """Snapshot of currently visible shell jobs for the web UI."""
    registry = getattr(strix, "shell_jobs", None)
    if registry is None:
        return []
    try:
        return shell_job_snapshots(registry, scope="visible")
    except Exception:
        return []


def _render_web_ui_page(strix: OpenStrixApp) -> str:
    agent_name = _web_agent_name(strix)
    agent_name_json = json.dumps(agent_name)
    channel_id_json = json.dumps(strix.config.web_ui_channel_id)
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2016%2016%27%3E%3Cpath%20d%3D%27M11.5%204.5C11.5%202.01%209.99.5%207.5.5%205.01.5%203.5%202.01%203.5%204.5c0%202%201.5%203%203.5%204s2.5%202%202.5%203.5c0%201.38-1.12%202-2%202s-2-.62-2-2H3.5c0%202.49%202.01%204%204%204s4-1.51%204-4c0-2-1.5-3-3.5-4S5.5%206.5%205.5%204.5c0-1.38.62-2%202-2s2%201.12%202%202z%27%20fill%3D%27%230d766e%27%2F%3E%3C%2Fsvg%3E" />
    <title>{agent_name} Chat</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>
    <style>
      :root {{
        --paper: #f5efe3;
        --paper-strong: #fffaf1;
        --ink: #1e2430;
        --muted: #5f6b76;
        --line: rgba(30, 36, 48, 0.12);
        --accent: #0d766e;
        --accent-soft: rgba(13, 118, 110, 0.12);
        --agent: #d9ece8;
        --user: #fffdf8;
        --shadow: 0 22px 60px rgba(44, 54, 64, 0.12);
      }}

      * {{
        box-sizing: border-box;
      }}

      html, body {{
        margin: 0;
        height: 100%;
        overflow: hidden;
        background:
          radial-gradient(circle at top left, rgba(13, 118, 110, 0.08), transparent 32rem),
          linear-gradient(180deg, #efe4cf 0%, #f7f2e7 36%, #f5efe3 100%);
        color: var(--ink);
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }}

      body {{
        padding: 1rem;
      }}

      .app-frame {{
        height: 100%;
      }}

      .app-frame.has-uis {{
        display: flex;
        align-items: stretch;
        justify-content: center;
        gap: 1rem;
      }}

      .app-frame.has-uis .shell {{
        flex: 1 1 880px;
        margin: 0;
      }}

      .ui-strip {{
        flex: 1 1 360px;
        width: auto;
        min-width: 320px;
        max-width: 600px;
        height: calc(100vh - 2rem);
        overflow-y: auto;
        padding-right: 0.1rem;
      }}

      .ui-strip[hidden],
      .ui-hamburger[hidden],
      .ui-overlay[hidden],
      .ui-modal[hidden] {{
        display: none;
      }}

      .ui-strip-content {{
        display: flex;
        flex-direction: column;
        gap: 0.8rem;
        min-height: 100%;
      }}

      .ui-mobile-list {{
        display: flex;
        flex-direction: column;
        gap: 0.8rem;
      }}

      .ui-card {{
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 0.7rem;
        background: rgba(255, 250, 241, 0.9);
        box-shadow: 0 12px 28px rgba(44, 54, 64, 0.08);
        display: flex;
        flex-direction: column;
        flex: 0 0 auto;
      }}

      .ui-card.is-minimized {{
        flex: 0 0 auto;
        min-height: 0;
      }}

      .ui-card.is-minimized .ui-body {{
        display: none;
      }}

      .ui-titlebar {{
        min-height: 2.4rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.45rem 0.55rem 0.45rem 0.75rem;
        background: #ecdfc6;
        border-bottom: 1px solid var(--line);
      }}

      .ui-title {{
        flex: 1 1 auto;
        min-width: 0;
        font-size: 0.86rem;
        font-weight: 700;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}

      .ui-actions {{
        display: inline-flex;
        gap: 0.35rem;
      }}

      .ui-tool-button,
      .ui-hamburger,
      .ui-close {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.8rem;
        height: 1.8rem;
        padding: 0;
        border: 1px solid rgba(30, 36, 48, 0.14);
        border-radius: 0.45rem;
        background: rgba(255, 250, 241, 0.76);
        color: var(--ink);
        line-height: 1;
      }}

      .ui-tool-button:hover,
      .ui-tool-button:focus-visible,
      .ui-hamburger:hover,
      .ui-hamburger:focus-visible,
      .ui-close:hover,
      .ui-close:focus-visible,
      .ui-tool-button.active {{
        background: var(--accent);
        border-color: var(--accent);
        color: white;
        outline: none;
      }}

      .ui-body {{
        background: var(--paper-strong);
        display: flex;
        flex-direction: column;
      }}

      .ui-frame-slot {{
        width: 100%;
        height: 260px;
      }}

      .ui-frame-slot[hidden] {{
        display: none;
      }}

      .ui-frame {{
        display: block;
        width: 100%;
        height: 100%;
        border: 0;
        background: white;
      }}

      .ui-placeholder {{
        min-height: 8rem;
        display: grid;
        place-items: center;
        padding: 1rem;
        color: var(--muted);
        font-size: 0.88rem;
        text-align: center;
      }}

      .ui-placeholder[hidden] {{
        display: none;
      }}

      .ui-hamburger {{
        flex: 0 0 auto;
      }}

      .ui-overlay,
      .ui-modal {{
        position: fixed;
        inset: 0;
        z-index: 100;
        background: rgba(30, 36, 48, 0.42);
        backdrop-filter: blur(5px);
      }}

      .ui-overlay-panel {{
        width: 100vw;
        height: 100vh;
        overflow-y: auto;
        padding: 1rem;
        background: var(--paper);
      }}

      .ui-overlay-head,
      .ui-modal-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 0.8rem;
      }}

      .ui-overlay-title,
      .ui-modal-title {{
        margin: 0;
        font-size: 1rem;
      }}

      .ui-modal-panel {{
        width: 90vw;
        height: 90vh;
        margin: 5vh auto;
        display: grid;
        grid-template-rows: auto 1fr;
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 0.85rem;
        background: var(--paper-strong);
        box-shadow: var(--shadow);
      }}

      .ui-modal-head {{
        margin: 0;
        padding: 0.6rem 0.7rem 0.6rem 1rem;
        background: #ecdfc6;
        border-bottom: 1px solid var(--line);
      }}

      .ui-modal-body {{
        min-height: 0;
      }}

      .shell {{
        max-width: 880px;
        height: calc(100vh - 2rem);
        margin: 0 auto;
        display: grid;
        grid-template-rows: auto 1fr auto;
        background: rgba(255, 250, 241, 0.84);
        border: 1px solid rgba(255, 255, 255, 0.5);
        border-radius: 1.5rem;
        box-shadow: var(--shadow);
        backdrop-filter: blur(10px);
        overflow: hidden;
      }}

      .header {{
        padding: 1rem 1.25rem 0.9rem;
        border-bottom: 1px solid var(--line);
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 1rem;
      }}

      .title {{
        margin: 0;
        font-size: clamp(1.2rem, 2vw, 1.65rem);
        line-height: 1.1;
      }}

      .header-links {{
        display: flex;
        gap: 0.9rem;
        font-size: 0.85rem;
      }}

      .header-links a {{
        color: var(--accent);
        text-decoration: none;
      }}

      .header-links a:hover {{
        text-decoration: underline;
      }}

      .status-row {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        min-height: 1.3rem;
      }}

      .typing-indicator {{
        flex: 1 1 auto;
        font-size: 0.75rem;
        color: var(--muted);
        min-height: 1.2em;
        margin: 0;
        overflow-wrap: anywhere;
      }}

      .typing-indicator.status-slow {{
        color: #e6a700;
      }}

      .typing-indicator.status-stuck {{
        color: #e63946;
        font-weight: bold;
      }}

      .shell-jobs-widget {{
        position: relative;
        flex: 0 0 auto;
        margin-left: auto;
      }}

      .shell-jobs-pill {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 0.35rem;
        font: inherit;
        font-size: 0.72rem;
        line-height: 1.2;
        color: var(--accent);
        background: rgba(13, 118, 110, 0.08);
        border: 1px solid rgba(13, 118, 110, 0.24);
        border-radius: 999px;
        padding: 0.2rem 0.65rem;
        cursor: pointer;
        user-select: none;
        appearance: none;
        box-shadow: none;
      }}

      .shell-jobs-pill:hover,
      .shell-jobs-pill:focus-visible {{
        background: rgba(13, 118, 110, 0.14);
        outline: none;
      }}
      .shell-jobs-pill[hidden] {{ display: none; }}

      .shell-jobs-panel {{
        position: absolute;
        right: 0;
        bottom: calc(100% + 0.55rem);
        width: min(520px, calc(100vw - 2rem));
        max-height: 60vh;
        background: rgba(26, 32, 40, 0.98);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 0.85rem;
        padding: 0.6rem;
        font-size: 0.8rem;
        box-shadow: 0 18px 40px rgba(24, 32, 40, 0.28);
        overflow: auto;
        z-index: 50;
      }}
      .shell-jobs-panel[hidden] {{ display: none; }}

      .shell-job-row {{
        padding: 0.35rem 0.4rem;
        border-bottom: 1px solid #2a2a2a;
        cursor: pointer;
      }}
      .shell-job-row:last-child {{ border-bottom: none; }}
      .shell-job-row:hover {{ background: rgba(255,255,255,0.04); }}
      .shell-job-row .jid {{ font-family: monospace; color: #7ee4d0; }}
      .shell-job-row .cmd {{ color: #ccc; font-family: monospace; }}
      .shell-job-row .meta {{ color: #9caab5; font-size: 0.72rem; }}

      .shell-job-output {{
        background: #0d0d0d;
        color: #ddd;
        padding: 0.5rem;
        font-family: monospace;
        font-size: 0.72rem;
        white-space: pre-wrap;
        max-height: 40vh;
        overflow: auto;
        border-radius: 4px;
        margin-top: 0.35rem;
      }}

      .elapsed {{
        font-size: 0.85em;
        opacity: 0.8;
      }}

      .typing-dot {{
        display: inline-block;
        width: 0.45em;
        height: 0.45em;
        border-radius: 50%;
        background: var(--accent);
        margin-right: 0.35em;
        vertical-align: middle;
        animation: typingPulse 1.2s infinite;
      }}

      @keyframes typingPulse {{
        0% {{ opacity: 0.3; }}
        50% {{ opacity: 1; }}
        100% {{ opacity: 0.3; }}
      }}

      .messages {{
        overflow-y: auto;
        padding: 1.1rem;
        display: flex;
        flex-direction: column;
        gap: 0.9rem;
      }}

      .empty {{
        margin: auto;
        max-width: 28rem;
        padding: 1.2rem;
        text-align: center;
        color: var(--muted);
        background: rgba(255, 255, 255, 0.45);
        border: 1px dashed var(--line);
        border-radius: 1rem;
      }}

      .message {{
        max-width: min(42rem, 92%);
        padding: 0.9rem 1rem;
        border-radius: 1.05rem;
        border: 1px solid var(--line);
        background: var(--user);
        align-self: flex-end;
      }}

      .message:has(> .body > .html-body) {{
        width: min(42rem, 92%);
      }}

      .message.agent {{
        align-self: flex-start;
        background: var(--agent);
      }}

      .meta {{
        display: flex;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 0.45rem;
        color: var(--muted);
        font-size: 0.82rem;
      }}

      .meta-actions {{
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        margin-left: auto;
      }}

      .copy-raw {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 1.8rem;
        width: 1.8rem;
        height: 1.8rem;
        padding: 0;
        border: 1px solid transparent;
        border-radius: 0.55rem;
        appearance: none;
        background: transparent;
        color: var(--muted);
        cursor: pointer;
        line-height: 0;
        transition:
          color 140ms ease,
          background-color 140ms ease,
          border-color 140ms ease;
      }}

      .copy-raw svg {{
        width: 0.95rem;
        height: 0.95rem;
        display: block;
        flex: 0 0 auto;
      }}

      .copy-raw:hover,
      .copy-raw:focus-visible,
      .copy-raw.copied {{
        background: var(--accent-soft);
        color: var(--accent);
        border-color: rgba(13, 118, 110, 0.22);
        outline: none;
      }}

      .body {{
        line-height: 1.45;
        overflow-wrap: anywhere;
      }}

      .html-body {{
        display: block;
      }}

      .body code {{ background: rgba(30,36,48,0.07); padding: 0.15em 0.4em; border-radius: 0.3em; font-family: ui-monospace, 'SF Mono', Monaco, monospace; font-size: 0.9em; }}
      .body pre {{ background: rgba(30,36,48,0.06); padding: 0.8rem 1rem; border-radius: 0.6rem; overflow-x: auto; font-family: ui-monospace, 'SF Mono', Monaco, monospace; font-size: 0.88em; line-height: 1.5; margin: 0.5rem 0; }}
      .body pre code {{ background: none; padding: 0; font-size: inherit; }}
      .body a {{ color: var(--accent); }}
      .body p {{ margin: 0.4em 0; }}
      .body p:first-child {{ margin-top: 0; }}
      .body p:last-child {{ margin-bottom: 0; }}
      .body > :first-child {{ margin-top: 0; }}
      .body > :last-child {{ margin-bottom: 0; }}
      .body h1,
      .body h2,
      .body h3 {{
        line-height: 1.25;
        margin: 0.9rem 0 0.55rem;
      }}
      .body h1 {{ font-size: 1.3em; padding-bottom: 0.25rem; border-bottom: 1px solid var(--line); }}
      .body h2 {{ font-size: 1.15em; padding-bottom: 0.22rem; border-bottom: 1px solid var(--line); }}
      .body h3 {{ font-size: 1.05em; }}
      .body ul,
      .body ol {{
        margin: 0.5rem 0;
        padding-left: 1.4rem;
      }}
      .body li + li {{ margin-top: 0.25rem; }}
      .body blockquote {{
        margin: 0.75rem 0;
        padding: 0.55rem 0.8rem;
        border-left: 0.24rem solid var(--accent);
        background: rgba(255, 255, 255, 0.48);
        color: var(--muted);
        border-radius: 0 0.7rem 0.7rem 0;
      }}
      .body hr {{
        margin: 0.9rem 0;
        border: 0;
        border-top: 1px solid var(--line);
      }}
      .body table {{
        width: 100%;
        max-width: 100%;
        display: block;
        overflow-x: auto;
        border-collapse: collapse;
        margin: 0.75rem 0;
        border: 1px solid var(--line);
        border-radius: 0.75rem;
        background: rgba(255, 255, 255, 0.5);
      }}
      .body th,
      .body td {{
        min-width: 7rem;
        padding: 0.55rem 0.7rem;
        border: 1px solid var(--line);
        text-align: left;
        vertical-align: top;
      }}
      .body th {{
        background: rgba(13, 118, 110, 0.12);
        font-weight: 600;
      }}
      .body tr:nth-child(even) td {{
        background: rgba(255, 255, 255, 0.32);
      }}

      .attachments {{
        display: grid;
        gap: 0.6rem;
        margin-top: 0.7rem;
      }}

      .attachment-link {{
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        color: var(--accent);
        text-decoration: none;
        font-size: 0.95rem;
      }}

      .attachment-link:hover {{
        text-decoration: underline;
      }}

      .image {{
        width: min(100%, 25rem);
        border-radius: 0.9rem;
        border: 1px solid rgba(30, 36, 48, 0.08);
        display: block;
        background: rgba(255, 255, 255, 0.75);
      }}

      .reactions {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        margin-top: 0.6rem;
      }}

      .reaction {{
        padding: 0.2rem 0.45rem;
        border-radius: 999px;
        background: var(--accent-soft);
        font-size: 0.92rem;
      }}

      .composer {{
        padding: 1rem;
        border-top: 1px solid var(--line);
        background: rgba(255, 251, 244, 0.95);
      }}

      .composer-form {{
        display: grid;
        gap: 0.8rem;
      }}

      textarea {{
        width: 100%;
        min-height: 3.2rem;
        max-height: 25vh;
        resize: none;
        overflow-y: auto;
        border-radius: 1rem;
        border: 1px solid rgba(30, 36, 48, 0.16);
        padding: 0.9rem 1rem;
        font: inherit;
        line-height: 1.4;
        background: var(--paper-strong);
        color: var(--ink);
      }}

      textarea:focus,
      .file-label:focus-within {{
        outline: none;
        border-color: rgba(13, 118, 110, 0.55);
        box-shadow: 0 0 0 0.22rem rgba(13, 118, 110, 0.14);
      }}

      .composer-actions {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.8rem;
        flex-wrap: wrap;
      }}

      .file-label {{
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        padding: 0.75rem 0.95rem;
        border: 1px dashed rgba(30, 36, 48, 0.24);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.56);
        cursor: pointer;
      }}

      .file-label input {{
        display: none;
      }}

      .file-list {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.45rem;
        color: var(--muted);
        font-size: 0.9rem;
      }}

      .file-chip {{
        padding: 0.35rem 0.6rem;
        border-radius: 999px;
        background: rgba(13, 118, 110, 0.1);
      }}

      button {{
        border: 0;
        border-radius: 999px;
        background: var(--accent);
        color: white;
        padding: 0.78rem 1.2rem;
        font: inherit;
        cursor: pointer;
      }}

      button[disabled] {{
        opacity: 0.7;
        cursor: wait;
      }}

      .footer-note {{
        color: var(--muted);
        font-size: 0.84rem;
      }}

      @media (max-width: 720px) {{
        body {{
          padding: 0;
        }}

        .app-frame {{
          height: 100vh;
        }}

        .shell {{
          height: 100vh;
          border-radius: 0;
        }}

        .header,
        .composer {{
          padding-inline: 0.9rem;
        }}

        .messages {{
          padding-inline: 0.8rem;
        }}

        .message {{
          max-width: 100%;
        }}
      }}

      @media (max-width: 899px) {{
        .app-frame.has-uis {{
          display: block;
        }}

        .ui-strip {{
          display: none;
        }}

        .ui-hamburger:not([hidden]) {{
          display: inline-flex;
        }}
      }}

      @media (min-width: 900px) {{
        .ui-hamburger {{
          display: none;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="app-frame" id="app-frame">
      <div class="ui-strip" id="ui-strip" aria-label="UI plugins" hidden>
        <div class="ui-strip-content" id="ui-strip-content"></div>
      </div>
      <main class="shell">
      <header class="header">
        <h1 class="title">{agent_name}</h1>
        <nav class="header-links">
          <a href="/ops" title="Live ops dashboard">Ops</a>
          <button class="ui-hamburger" id="ui-hamburger" type="button" aria-label="Open UI plugins" title="Open UI plugins" hidden>☰</button>
        </nav>
      </header>

      <section class="messages" id="messages" aria-live="polite">
        <div class="empty">No messages yet. Say something and {agent_name} will respond here.</div>
      </section>

      <section class="composer">
        <form class="composer-form" id="composer">
          <div class="status-row">
            <div class="typing-indicator" id="typing-indicator"></div>
            <div class="shell-jobs-widget" id="shell-jobs-widget">
              <button class="shell-jobs-pill" id="shell-jobs-pill" type="button" hidden></button>
              <div class="shell-jobs-panel" id="shell-jobs-panel" hidden></div>
            </div>
          </div>
          <textarea id="text" name="text" placeholder="Message {agent_name}..."></textarea>
          <div class="composer-actions">
            <label class="file-label">
              <input id="files" type="file" name="files" multiple />
              <span>Attach files</span>
            </label>
            <button id="send" type="submit">Send</button>
          </div>
          <div class="file-list" id="file-list"></div>
          <div class="footer-note">Uploads stay inside the agent home and are shared only when attached in this chat.</div>
        </form>
      </section>
      </main>
    </div>

    <div class="ui-overlay" id="ui-mobile-overlay" hidden>
      <div class="ui-overlay-panel">
        <div class="ui-overlay-head">
          <h2 class="ui-overlay-title">UI plugins</h2>
          <button class="ui-close" id="ui-mobile-close" type="button" aria-label="Close UI plugins" title="Close">×</button>
        </div>
        <div class="ui-mobile-list" id="ui-mobile-list"></div>
      </div>
    </div>

    <div class="ui-modal" id="ui-modal" hidden>
      <div class="ui-modal-panel">
        <div class="ui-modal-head">
          <h2 class="ui-modal-title" id="ui-modal-title"></h2>
          <button class="ui-close" id="ui-modal-close" type="button" aria-label="Close plugin" title="Close">×</button>
        </div>
        <div class="ui-modal-body" id="ui-modal-body"></div>
      </div>
    </div>

    <script>
      const AGENT_NAME = {agent_name_json};
      const CHANNEL_ID = {channel_id_json};
      const messagesEl = document.getElementById("messages");
      const composerEl = document.getElementById("composer");
      const textEl = document.getElementById("text");
      const filesEl = document.getElementById("files");
      const fileListEl = document.getElementById("file-list");
      const sendEl = document.getElementById("send");
      const typingEl = document.getElementById("typing-indicator");
      const shellJobsWidgetEl = document.getElementById("shell-jobs-widget");
      const shellJobsPillEl = document.getElementById("shell-jobs-pill");
      const shellJobsPanelEl = document.getElementById("shell-jobs-panel");
      const appFrameEl = document.getElementById("app-frame");
      const uiStripEl = document.getElementById("ui-strip");
      const uiStripContentEl = document.getElementById("ui-strip-content");
      const uiHamburgerEl = document.getElementById("ui-hamburger");
      const uiMobileOverlayEl = document.getElementById("ui-mobile-overlay");
      const uiMobileListEl = document.getElementById("ui-mobile-list");
      const uiMobileCloseEl = document.getElementById("ui-mobile-close");
      const uiModalEl = document.getElementById("ui-modal");
      const uiModalBodyEl = document.getElementById("ui-modal-body");
      const uiModalTitleEl = document.getElementById("ui-modal-title");
      const uiModalCloseEl = document.getElementById("ui-modal-close");

      let shellJobsPanelOpen = false;
      let shellJobsExpandedId = null;
      let currentShellJobs = [];
      let shellJobOutputState = new Map();
      let pastedFiles = [];
      let uiWidgets = new Map();
      // Route /ui/<plugin>/ links from chat messages into the matching widget.
      // The click handler closes over uiWidgets dynamically, so it sees
      // plugins as they register.
      window.addEventListener("DOMContentLoaded", () => {{
        if (messagesEl) attachUiPluginLinkInterceptor(messagesEl);
      }});
      if (document.readyState !== "loading" && messagesEl) {{
        attachUiPluginLinkInterceptor(messagesEl);
      }}
      // Hash-routed deep links (works from sandboxed HTML iframes that use
      // <a href="#/ui/<plugin>/..." target="_top">): listen on hashchange.
      window.addEventListener("hashchange", () => {{
        const h = window.location.hash;
        if (h && h.startsWith("#/ui/")) {{
          if (routeUiPluginNav(h)) {{
            try {{
              history.replaceState(null, "", window.location.pathname + window.location.search);
            }} catch (e) {{ /* ignore */ }}
          }}
        }}
      }});
      let maximizedWidget = null;

      function formatElapsed(sec) {{
        if (sec == null) return '';
        if (sec < 60) return sec.toFixed(0) + 's';
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return m + 'm' + s + 's';
      }}

      function escapeHtml(text) {{
        const escapes = {{
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        }};
        return String(text ?? '').replace(/[&<>"']/g, (char) => escapes[char]);
      }}

      function uiStatusText(plugin) {{
        return plugin.status === "starting"
          ? plugin.name + " is starting"
          : plugin.name + " is not available";
      }}

      function createUiWidget(plugin) {{
        const card = document.createElement("section");
        card.className = "ui-card";
        card.dataset.uiName = plugin.name;

        const titlebar = document.createElement("div");
        titlebar.className = "ui-titlebar";
        const title = document.createElement("div");
        title.className = "ui-title";
        title.textContent = plugin.name;
        const actions = document.createElement("div");
        actions.className = "ui-actions";
        const back = document.createElement("button");
        back.className = "ui-tool-button";
        back.type = "button";
        back.title = "Back";
        back.setAttribute("aria-label", "Back in " + plugin.name);
        back.textContent = "◀";
        const forward = document.createElement("button");
        forward.className = "ui-tool-button";
        forward.type = "button";
        forward.title = "Forward";
        forward.setAttribute("aria-label", "Forward in " + plugin.name);
        forward.textContent = "▶";
        const reload = document.createElement("button");
        reload.className = "ui-tool-button";
        reload.type = "button";
        reload.title = "Reload";
        reload.setAttribute("aria-label", "Reload " + plugin.name);
        reload.textContent = "⟳";
        const minimize = document.createElement("button");
        minimize.className = "ui-tool-button";
        minimize.type = "button";
        minimize.title = "Minimize";
        minimize.setAttribute("aria-label", "Minimize " + plugin.name);
        minimize.textContent = "−";
        const maximize = document.createElement("button");
        maximize.className = "ui-tool-button";
        maximize.type = "button";
        maximize.title = "Maximize";
        maximize.setAttribute("aria-label", "Maximize " + plugin.name);
        maximize.textContent = "⛶";
        actions.append(back, forward, reload, minimize, maximize);
        titlebar.append(title, actions);

        const body = document.createElement("div");
        body.className = "ui-body";
        const frameSlot = document.createElement("div");
        frameSlot.className = "ui-frame-slot";
        const placeholder = document.createElement("div");
        placeholder.className = "ui-placeholder";
        body.append(frameSlot, placeholder);
        card.append(titlebar, body);

        const widget = {{
          name: plugin.name,
          status: plugin.status,
          card,
          body,
          frameSlot,
          placeholder,
          iframe: null,
          minimized: false,
          back,
          forward,
          reload,
          minimize,
          maximize,
        }};

        reload.addEventListener("click", () => {{
          if (!widget.iframe) return;
          // Re-assigning src forces a reload even if the URL is unchanged.
          widget.iframe.src = widget.iframe.src;
        }});
        back.addEventListener("click", () => {{
          // Navigate the iframe's session history back one entry. Works
          // across error pages since the iframe retains its own history
          // regardless of HTTP status. Sandbox includes allow-same-origin,
          // so contentWindow.history is reachable. A no-op silently if
          // we're at the start of history.
          if (!widget.iframe) return;
          try {{
            widget.iframe.contentWindow.history.back();
          }} catch (e) {{
            // Cross-origin or detached frame — fall back to reloading root.
            widget.iframe.src = "/ui/" + encodeURIComponent(widget.name) + "/";
          }}
        }});
        forward.addEventListener("click", () => {{
          if (!widget.iframe) return;
          try {{
            widget.iframe.contentWindow.history.forward();
          }} catch (e) {{
            // No-op; can't restore forward history if cross-origin.
          }}
        }});
        minimize.addEventListener("click", () => {{
          widget.minimized = !widget.minimized;
          minimize.classList.toggle("active", widget.minimized);
          minimize.setAttribute("aria-pressed", widget.minimized ? "true" : "false");
          card.classList.toggle("is-minimized", widget.minimized);
          applyUiWidgetState(widget);
        }});
        maximize.addEventListener("click", () => openUiModal(widget));
        return widget;
      }}

      function ensureUiIframe(widget) {{
        if (widget.iframe) return;
        const frame = document.createElement("iframe");
        frame.className = "ui-frame";
        frame.src = "/ui/" + encodeURIComponent(widget.name) + "/";
        frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
        frame.title = widget.name;
        widget.iframe = frame;
        widget.frameSlot.appendChild(frame);
      }}

      function applyUiWidgetState(widget) {{
        const running = widget.status === "running";
        widget.placeholder.hidden = running;
        widget.frameSlot.hidden = !running;
        widget.placeholder.textContent = running ? "" : uiStatusText(widget);
        if (running) {{
          ensureUiIframe(widget);
          if (maximizedWidget === widget) {{
            widget.iframe.style.display = "block";
          }} else {{
            widget.iframe.style.display = widget.minimized ? "none" : "block";
            if (widget.iframe.parentElement !== widget.frameSlot) {{
              widget.frameSlot.appendChild(widget.iframe);
            }}
          }}
        }}
      }}

      function parseUiPluginHref(href) {{
        // Accept absolute URLs, root-relative paths, and hash-routed forms.
        // Returns {{ name, path }} if href targets a UI plugin, else null.
        // Forms:
        //   /ui/<name>/<rest>          (markdown link inside chat DOM)
        //   #/ui/<name>/<rest>         (hash-routed; usable from sandboxed HTML iframes)
        //   http(s)://host/ui/<name>/<rest>
        //   http(s)://host/#/ui/<name>/<rest>
        if (!href) return null;
        let path = String(href);
        // Hash-only forms must be unwrapped BEFORE URL parsing — otherwise
        // `new URL("#/ui/...", origin)` collapses to pathname="/" + hash, and
        // the recombined path becomes "/#/ui/..." which no prefix below catches.
        if (path.startsWith("#/ui/")) {{
          path = path.slice(1);
        }} else {{
          try {{
            const u = new URL(path, window.location.origin);
            if (u.origin !== window.location.origin) return null;
            path = u.pathname + u.search + u.hash;
          }} catch (e) {{
            // not a parseable URL — fall through and try direct prefix match
          }}
          // After URL normalization, a hash-routed absolute URL looks like
          // "/path#/ui/..." — claim it via the canonical hash form.
          if (!path.startsWith("/ui/")) {{
            const hashIdx = path.indexOf("#/ui/");
            if (hashIdx !== -1) {{
              path = path.slice(hashIdx + 1);
            }}
          }}
        }}
        if (!path.startsWith("/ui/")) return null;
        const rest = path.slice(4);
        const slash = rest.indexOf("/");
        const name = decodeURIComponent(slash === -1 ? rest : rest.slice(0, slash));
        if (!name) return null;
        return {{ name, path }};
      }}

      function routeUiPluginNav(href) {{
        // Try to navigate a running plugin widget to `href`. Returns true if
        // the link was claimed by a plugin (caller should preventDefault),
        // false otherwise (caller should let the browser handle the link).
        const parsed = parseUiPluginHref(href);
        if (!parsed) return false;
        const widget = uiWidgets.get(parsed.name);
        if (!widget) return false;
        if (widget.status !== "running") {{
          // Known plugin but not booted yet — claim the click so we don't
          // open a broken tab. The user can re-click after it starts.
          return true;
        }}
        ensureUiIframe(widget);
        if (widget.minimized) {{
          widget.minimized = false;
          widget.minimize.classList.remove("active");
          widget.minimize.setAttribute("aria-pressed", "false");
          widget.card.classList.remove("is-minimized");
        }}
        applyUiWidgetState(widget);
        widget.iframe.src = parsed.path;
        try {{
          widget.card.scrollIntoView({{ behavior: "smooth", block: "nearest", inline: "nearest" }});
        }} catch (e) {{
          widget.card.scrollIntoView();
        }}
        return true;
      }}

      function attachUiPluginLinkInterceptor(root) {{
        // Delegated click handler for <a> elements whose href targets a
        // /ui/<name>/ path. Works for both the parent chat DOM (markdown
        // messages) and the contentDocument of sandboxed HTML message
        // iframes (allow-same-origin lets us listen even without
        // allow-scripts inside the iframe).
        if (!root || root.__uiPluginLinkInterceptorAttached) return;
        root.__uiPluginLinkInterceptorAttached = true;
        root.addEventListener("click", (event) => {{
          if (event.defaultPrevented || event.button !== 0) return;
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          const anchor = event.target.closest && event.target.closest("a[href]");
          if (!anchor) return;
          const href = anchor.getAttribute("href");
          if (!href) return;
          if (routeUiPluginNav(href)) {{
            event.preventDefault();
            event.stopPropagation();
          }}
        }}, true);
      }}

      function renderUiPlugins(plugins) {{
        const names = new Set((plugins || []).map((plugin) => plugin.name));
        Array.from(uiWidgets.keys()).forEach((name) => {{
          if (!names.has(name)) {{
            const widget = uiWidgets.get(name);
            if (maximizedWidget === widget) closeUiModal();
            widget.card.remove();
            uiWidgets.delete(name);
          }}
        }});

        (plugins || []).forEach((plugin) => {{
          let widget = uiWidgets.get(plugin.name);
          if (!widget) {{
            widget = createUiWidget(plugin);
            uiWidgets.set(plugin.name, widget);
            uiStripContentEl.appendChild(widget.card);
          }}
          widget.status = plugin.status;
          if (!uiMobileOverlayEl.hidden && widget.card.parentElement !== uiMobileListEl) {{
            uiMobileListEl.appendChild(widget.card);
          }} else if (uiMobileOverlayEl.hidden && widget.card.parentElement !== uiStripContentEl) {{
            uiStripContentEl.appendChild(widget.card);
          }}
          applyUiWidgetState(widget);
        }});

        const hasPlugins = uiWidgets.size > 0;
        appFrameEl.classList.toggle("has-uis", hasPlugins);
        uiStripEl.hidden = !hasPlugins;
        uiHamburgerEl.hidden = !hasPlugins;
        if (!hasPlugins) {{
          closeMobileUiOverlay();
        }}
      }}

      async function refreshUiPlugins() {{
        try {{
          const response = await fetch("/api/uis", {{ cache: "no-store" }});
          if (!response.ok) throw new Error("UI plugin fetch failed");
          renderUiPlugins(await response.json());
        }} catch (error) {{
          console.error("UI plugin refresh failed:", error);
        }}
      }}

      function openUiModal(widget) {{
        if (widget.status !== "running") return;
        ensureUiIframe(widget);
        maximizedWidget = widget;
        uiModalTitleEl.textContent = widget.name;
        uiModalEl.hidden = false;
        widget.iframe.style.display = "block";
        uiModalBodyEl.appendChild(widget.iframe);
        widget.maximize.classList.add("active");
        widget.maximize.setAttribute("aria-pressed", "true");
      }}

      function closeUiModal() {{
        if (!maximizedWidget) {{
          uiModalEl.hidden = true;
          return;
        }}
        const widget = maximizedWidget;
        maximizedWidget = null;
        uiModalEl.hidden = true;
        widget.maximize.classList.remove("active");
        widget.maximize.setAttribute("aria-pressed", "false");
        if (widget.iframe) {{
          widget.frameSlot.appendChild(widget.iframe);
        }}
        applyUiWidgetState(widget);
      }}

      function openMobileUiOverlay() {{
        uiMobileOverlayEl.hidden = false;
        uiWidgets.forEach((widget) => uiMobileListEl.appendChild(widget.card));
      }}

      function closeMobileUiOverlay() {{
        uiMobileOverlayEl.hidden = true;
        uiWidgets.forEach((widget) => uiStripContentEl.appendChild(widget.card));
      }}

      uiHamburgerEl.addEventListener("click", openMobileUiOverlay);
      uiMobileCloseEl.addEventListener("click", closeMobileUiOverlay);
      uiModalCloseEl.addEventListener("click", closeUiModal);

      function pruneShellJobOutputState() {{
        const currentIds = new Set(currentShellJobs.map((job) => job.job_id));
        Array.from(shellJobOutputState.keys()).forEach((jobId) => {{
          if (!currentIds.has(jobId)) {{
            shellJobOutputState.delete(jobId);
          }}
        }});
      }}

      function getShellJobOutputText(jobId) {{
        const state = shellJobOutputState.get(jobId);
        if (!state) return 'loading…';
        if (typeof state.text === 'string') return state.text;
        if (state.error) return state.error;
        if (state.loading) return 'loading…';
        return '(no output yet)';
      }}

      function updateShellJobOutputElement(jobId) {{
        const el = shellJobsPanelEl.querySelector('[data-output="' + jobId + '"]');
        if (!el) return;
        el.textContent = getShellJobOutputText(jobId);
      }}

      function refreshShellJobOutput(jobId, status) {{
        const state = shellJobOutputState.get(jobId);
        if (state && state.loading) {{
          updateShellJobOutputElement(jobId);
          return;
        }}
        shellJobOutputState.set(jobId, {{
          ...state,
          loading: true,
          error: null,
        }});
        updateShellJobOutputElement(jobId);
        fetch('/api/shell-jobs/' + encodeURIComponent(jobId) + '?tail=1000&stream=both')
          .then(async (r) => {{
            const data = await r.json();
            if (!r.ok) {{
              throw new Error(data.error || 'failed to load shell job output');
            }}
            return data;
          }})
          .then((data) => {{
            const out = (data.stdout_tail || '') + (data.stderr_tail ? '\\n--- stderr ---\\n' + data.stderr_tail : '');
            shellJobOutputState.set(jobId, {{
              text: out || '(no output yet)',
              error: null,
              loading: false,
              lastFetchedStatus: status,
            }});
            updateShellJobOutputElement(jobId);
          }})
          .catch((error) => {{
            shellJobOutputState.set(jobId, {{
              text: null,
              error: error instanceof Error ? error.message : 'failed to load shell job output',
              loading: false,
              lastFetchedStatus: status,
            }});
            updateShellJobOutputElement(jobId);
          }});
      }}

      function renderShellJobs(jobs) {{
        currentShellJobs = [...(jobs || [])].sort((a, b) => {{
          const aRunning = a.status === 'running';
          const bRunning = b.status === 'running';
          if (aRunning !== bRunning) {{
            return aRunning ? -1 : 1;
          }}
          return (b.started_at || 0) - (a.started_at || 0);
        }});
        pruneShellJobOutputState();
        const running = currentShellJobs.filter(j => j.status === 'running');
        if (shellJobsExpandedId && !currentShellJobs.some(j => j.job_id === shellJobsExpandedId)) {{
          shellJobsExpandedId = null;
        }}
        if (currentShellJobs.length === 0) {{
          shellJobsPillEl.hidden = true;
          shellJobsPanelEl.hidden = true;
          shellJobsPanelOpen = false;
          shellJobsExpandedId = null;
          shellJobsPillEl.setAttribute('aria-expanded', 'false');
          return;
        }}
        const label = running.length > 0
          ? running.length + ' running'
          : currentShellJobs.length + ' recent';
        shellJobsPillEl.textContent = label;
        const labelCount = running.length > 0 ? running.length : currentShellJobs.length;
        shellJobsPillEl.setAttribute('aria-label', label + ' shell job' + (labelCount === 1 ? '' : 's'));
        shellJobsPillEl.setAttribute('aria-expanded', shellJobsPanelOpen ? 'true' : 'false');
        shellJobsPillEl.hidden = false;
        if (shellJobsPanelOpen) {{
          renderShellJobsPanel();
        }}
      }}

      function renderShellJobsPanel() {{
        shellJobsPillEl.setAttribute('aria-expanded', shellJobsPanelOpen ? 'true' : 'false');
        if (!shellJobsPanelOpen) {{ shellJobsPanelEl.hidden = true; return; }}
        shellJobsPanelEl.hidden = false;
        const rows = currentShellJobs.map(j => {{
          const cmd = escapeHtml((j.command || '').slice(0, 160));
          const statusColor = j.status === 'running' ? '#6fdc8c' : (j.exit_code === 0 ? '#888' : '#e63946');
          const expanded = shellJobsExpandedId === j.job_id;
          const outputHtml = expanded
            ? '<div class="shell-job-output" data-output="' + j.job_id + '"></div>'
            : '';
          return (
            '<div class="shell-job-row" data-job="' + j.job_id + '">' +
              '<div><span class="jid">' + escapeHtml(j.job_id) + '</span> ' +
              '<span style="color:' + statusColor + '">' + escapeHtml(j.status) + '</span> ' +
              '<span class="meta">pid=' + j.pid + ' · ' + formatElapsed(j.elapsed_seconds) +
              (j.exit_code != null ? ' · exit=' + j.exit_code : '') + '</span></div>' +
              '<div class="cmd">$ ' + cmd + '</div>' +
              outputHtml +
            '</div>'
          );
        }}).join('');
        shellJobsPanelEl.innerHTML = rows || '<div class="meta">No jobs.</div>';
        shellJobsPanelEl.querySelectorAll('.shell-job-row').forEach(row => {{
          row.addEventListener('click', () => {{
            const jid = row.getAttribute('data-job');
            shellJobsExpandedId = (shellJobsExpandedId === jid) ? null : jid;
            renderShellJobsPanel();
          }});
        }});
        if (shellJobsExpandedId) {{
          const expandedJob = currentShellJobs.find((job) => job.job_id === shellJobsExpandedId);
          if (expandedJob) {{
            updateShellJobOutputElement(expandedJob.job_id);
            const state = shellJobOutputState.get(expandedJob.job_id);
            if (!state || state.lastFetchedStatus !== expandedJob.status || expandedJob.status === 'running') {{
              refreshShellJobOutput(expandedJob.job_id, expandedJob.status);
            }}
          }}
        }}
      }}

      shellJobsPillEl.addEventListener('click', () => {{
        if (currentShellJobs.length === 0) return;
        shellJobsPanelOpen = !shellJobsPanelOpen;
        renderShellJobsPanel();
      }});

      document.addEventListener('click', (event) => {{
        if (!shellJobsPanelOpen) return;
        if (shellJobsWidgetEl.contains(event.target)) return;
        shellJobsPanelOpen = false;
        renderShellJobsPanel();
      }});

      let knownIds = new Map();
      let oldestLoadedId = null;
      let hasMoreHistory = true;
      let loadingHistory = false;
      let isNearBottom = true;
      let hasCompletedInitialRender = false;
      let hasUnreadMessages = false;
      let titleFlashTimer = 0;
      let titleFlashShowingUnread = false;
      let notificationPermissionRequested = false;
      const originalTitle = document.title;
      const unreadTitle = "💬 New message";
      const notificationSoundDataUri = createNotificationSoundDataUri();
      const COPY_ICON_SVG = `
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
          <rect x="5" y="3" width="8" height="10" rx="1.5"></rect>
          <path d="M3.75 11.5h-1A1.75 1.75 0 0 1 1 9.75v-7.5A1.75 1.75 0 0 1 2.75.5h5.5A1.75 1.75 0 0 1 10 2.25v.5"></path>
        </svg>`;
      const CHECK_ICON_SVG = `
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
          <path d="M3.5 8.5 6.5 11.5 12.5 4.5"></path>
        </svg>`;

      function formatTime(value) {{
        if (!value) return "";
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return value;
        return dt.toLocaleTimeString([], {{ hour: "numeric", minute: "2-digit" }});
      }}

      function setCopyButtonState(button, isCopied) {{
        button.innerHTML = isCopied ? CHECK_ICON_SVG : COPY_ICON_SVG;
        button.classList.toggle("copied", isCopied);
        const label = isCopied ? "Copied!" : "Copy raw markdown";
        button.setAttribute("aria-label", label);
        button.title = label;
      }}

      // Build a short inline WAV so the page stays self-contained.
      function createNotificationSoundDataUri() {{
        const sampleRate = 8000;
        const durationSeconds = 0.12;
        const frequency = 660;
        const amplitude = 0.18;
        const sampleCount = Math.floor(sampleRate * durationSeconds);
        const wavBytes = new Uint8Array(44 + sampleCount * 2);
        const view = new DataView(wavBytes.buffer);

        function writeAscii(offset, text) {{
          for (let index = 0; index < text.length; index += 1) {{
            view.setUint8(offset + index, text.charCodeAt(index));
          }}
        }}

        writeAscii(0, "RIFF");
        view.setUint32(4, 36 + sampleCount * 2, true);
        writeAscii(8, "WAVE");
        writeAscii(12, "fmt ");
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeAscii(36, "data");
        view.setUint32(40, sampleCount * 2, true);

        for (let index = 0; index < sampleCount; index += 1) {{
          const envelope = 1 - index / sampleCount;
          const sample =
            Math.sin((2 * Math.PI * frequency * index) / sampleRate) * amplitude * envelope;
          view.setInt16(44 + index * 2, Math.round(sample * 32767), true);
        }}

        const binary = Array.from(wavBytes, (value) => String.fromCharCode(value)).join("");
        return "data:audio/wav;base64," + btoa(binary);
      }}

      async function requestNotificationPermission() {{
        if (!("Notification" in window)) return;
        if (Notification.permission !== "default") return;
        if (notificationPermissionRequested) return;
        notificationPermissionRequested = true;
        try {{
          await Notification.requestPermission();
        }} catch (error) {{
          console.error("notification permission request failed:", error);
        }}
      }}

      function buildNotificationPreview(message) {{
        const previewText = (message.content || "").replace(/\\s+/g, " ").trim();
        if (previewText) {{
          return previewText.length > 100 ? previewText.slice(0, 97) + "..." : previewText;
        }}
        if (message.attachments && message.attachments.length) {{
          return message.attachments.length === 1
            ? "Sent an attachment."
            : "Sent " + message.attachments.length + " attachments.";
        }}
        return "New message";
      }}

      function startTitleFlash() {{
        if (!hasUnreadMessages || titleFlashTimer) return;
        titleFlashShowingUnread = true;
        document.title = unreadTitle;
        titleFlashTimer = window.setInterval(() => {{
          titleFlashShowingUnread = !titleFlashShowingUnread;
          document.title = titleFlashShowingUnread ? unreadTitle : originalTitle;
        }}, 1000);
      }}

      function clearUnreadState() {{
        hasUnreadMessages = false;
        titleFlashShowingUnread = false;
        if (titleFlashTimer) {{
          window.clearInterval(titleFlashTimer);
          titleFlashTimer = 0;
        }}
        document.title = originalTitle;
      }}

      function playNotificationSound() {{
        const sound = new Audio(notificationSoundDataUri);
        sound.volume = 0.18;
        void sound.play().catch((error) => {{
          console.error("notification sound failed:", error);
        }});
      }}

      function showHiddenTabNotification(message) {{
        if (!("Notification" in window)) return;
        if (Notification.permission !== "granted") return;
        const notification = new Notification(AGENT_NAME, {{
          body: buildNotificationPreview(message),
          tag: "open-strix-hidden-message",
          renotify: true,
          silent: true,
        }});
        notification.onclick = () => {{
          window.focus();
          notification.close();
        }};
      }}

      function updateFileList() {{
        const pickedNames = Array.from(filesEl.files || []).map((file) => file.name);
        const pastedNames = pastedFiles.map((file) => file.name);
        const allNames = [...pickedNames, ...pastedNames];
        fileListEl.innerHTML = "";
        allNames.forEach((name) => {{
          const chip = document.createElement("span");
          chip.className = "file-chip";
          chip.textContent = name;
          fileListEl.appendChild(chip);
        }});
      }}

      function simpleMarkdown(text) {{
        if (!text) return "";
        marked.setOptions({{ breaks: true, gfm: true }});
        text = text.replace(/<script\\b[^<]*(?:(?!<\\/script>)<[^<]*)*<\\/script>/gi, "");
        const wrapper = document.createElement("div");
        wrapper.innerHTML = marked.parse(text);
        wrapper.querySelectorAll("script").forEach((node) => node.remove());
        wrapper.querySelectorAll("a").forEach((link) => {{
          const href = link.getAttribute("href") || "";
          // Leave /ui/<plugin>/ and #/ui/<plugin>/ links in-tab so the
          // delegated click handler on the messages container can route them
          // into the matching plugin iframe instead of opening a new tab.
          if (parseUiPluginHref(href)) {{
            return;
          }}
          link.target = "_blank";
          link.rel = "noreferrer";
        }});
        return wrapper.innerHTML;
      }}

      function createReactionsElement(reactionList) {{
        if (!reactionList || !reactionList.length) {{
          return null;
        }}

        const reactions = document.createElement("div");
        reactions.className = "reactions";
        reactions.dataset.reactions = JSON.stringify(reactionList);
        reactionList.forEach((emoji) => {{
          const chip = document.createElement("span");
          chip.className = "reaction";
          chip.textContent = emoji;
          reactions.appendChild(chip);
        }});
        return reactions;
      }}

      function updateReactions(existingEl, newReactions) {{
        const existingReactions = existingEl.querySelector(".reactions");
        const nextValue = JSON.stringify(newReactions || []);
        const currentValue = existingReactions ? (existingReactions.dataset.reactions || "[]") : "[]";
        if (currentValue === nextValue) {{
          return;
        }}

        const nextReactions = createReactionsElement(newReactions);
        if (existingReactions && nextReactions) {{
          existingReactions.replaceWith(nextReactions);
          return;
        }}
        if (existingReactions) {{
          existingReactions.remove();
          return;
        }}
        if (nextReactions) {{
          existingEl.appendChild(nextReactions);
        }}
      }}

      function createMessageElement(message) {{
        const article = document.createElement("article");
        article.className = `message ${{message.is_bot ? "agent" : "user"}}`;
        article.dataset.messageId = message.message_id;
        article.dataset.rawContent = message.content || "";

        const meta = document.createElement("div");
        meta.className = "meta";
        const author = document.createElement("strong");
        author.textContent = message.is_bot ? AGENT_NAME : "You";
        const metaActions = document.createElement("div");
        metaActions.className = "meta-actions";
        const time = document.createElement("span");
        time.textContent = formatTime(message.timestamp);
        const copyButton = document.createElement("button");
        copyButton.type = "button";
        copyButton.className = "copy-raw";
        setCopyButtonState(copyButton, false);
        let copyResetTimer = 0;
        copyButton.addEventListener("click", async () => {{
          try {{
            await navigator.clipboard.writeText(article.dataset.rawContent || "");
          }} catch (error) {{
            console.error("copy raw failed:", error);
            return;
          }}
          setCopyButtonState(copyButton, true);
          window.clearTimeout(copyResetTimer);
          copyResetTimer = window.setTimeout(() => {{
            setCopyButtonState(copyButton, false);
          }}, 1500);
        }});
        metaActions.append(time, copyButton);
        meta.append(author, metaActions);
        article.appendChild(meta);

        if (message.content) {{
          const body = document.createElement("div");
          body.className = "body";
          if (message.format === "html") {{
            const frame = document.createElement("iframe");
            frame.className = "html-body";
            frame.setAttribute("sandbox", "allow-same-origin");
            frame.setAttribute("srcdoc", message.content || "");
            frame.style.border = "none";
            frame.style.width = "100%";
            frame.style.minHeight = "120px";
            const resize = () => {{
              try {{
                const doc = frame.contentDocument;
                if (!doc || !doc.body) return;
                const html = doc.documentElement;
                const next = Math.max(
                  html ? html.scrollHeight : 0,
                  doc.body.scrollHeight,
                );
                if (next > 0) {{
                  frame.style.height = next + "px";
                }}
              }} catch (e) {{
                // Cross-origin or sandbox lockdown: keep min height.
              }}
            }};
            frame.addEventListener("load", () => {{
              resize();
              try {{
                const doc = frame.contentDocument;
                if (doc && doc.documentElement && typeof ResizeObserver !== "undefined") {{
                  const ro = new ResizeObserver(() => resize());
                  ro.observe(doc.documentElement);
                  if (doc.body) {{
                    ro.observe(doc.body);
                  }}
                }}
                if (doc) {{
                  attachUiPluginLinkInterceptor(doc);
                }}
              }} catch (e) {{
                // Sandbox or older browser without ResizeObserver / accessor: keep single measurement.
              }}
            }});
            body.appendChild(frame);
          }} else {{
            body.innerHTML = simpleMarkdown(message.content);
          }}
          article.appendChild(body);
        }}

        if (message.attachments && message.attachments.length) {{
          const attachments = document.createElement("div");
          attachments.className = "attachments";
          message.attachments.forEach((attachment) => {{
            if (attachment.is_image) {{
              const link = document.createElement("a");
              link.href = attachment.url;
              link.target = "_blank";
              link.rel = "noreferrer";

              const image = document.createElement("img");
              image.className = "image";
              image.loading = "lazy";
              image.src = attachment.url;
              image.alt = attachment.name;
              link.appendChild(image);
              attachments.appendChild(link);
            }}

            const link = document.createElement("a");
            link.className = "attachment-link";
            link.href = attachment.url;
            link.target = "_blank";
            link.rel = "noreferrer";
            link.textContent = attachment.name;
            attachments.appendChild(link);
          }});
          article.appendChild(attachments);
        }}

        const reactions = createReactionsElement(message.reactions);
        if (reactions) {{
          article.appendChild(reactions);
        }}

        return article;
      }}

      function upsertMessageElement(message, append = true) {{
        const existing = knownIds.get(message.message_id);
        if (existing) {{
          updateReactions(existing, message.reactions);
          knownIds.set(message.message_id, existing);
          return;
        }}

        const el = createMessageElement(message);
        if (append) {{
          messagesEl.appendChild(el);
        }}
        knownIds.set(message.message_id, el);
      }}

      function renderMessages(payload) {{
        if (payload.is_processing) {{
          const label = payload.processing_label ? ' (' + payload.processing_label + ')' : '';
          const elapsed = payload.turn_elapsed_seconds;
          let statusClass = '';
          let statusText = '';

          if (elapsed !== null && elapsed > 120) {{
            statusClass = 'status-stuck';
            const mins = Math.floor(elapsed / 60);
            const secs = Math.floor(elapsed % 60);
            statusText =
              '<span class="typing-dot"></span>' +
              AGENT_NAME +
              ' may be stuck' +
              label +
              ' <span class="elapsed">' +
              mins +
              'm ' +
              secs +
              's</span>';
          }} else if (elapsed !== null && elapsed > 30) {{
            statusClass = 'status-slow';
            statusText =
              '<span class="typing-dot"></span>' +
              AGENT_NAME +
              ' is still working…' +
              label +
              ' <span class="elapsed">' +
              Math.floor(elapsed) +
              's</span>';
          }} else {{
            statusText = '<span class="typing-dot"></span>' + AGENT_NAME + ' is thinking…' + label;
          }}

          typingEl.className = statusClass ? 'typing-indicator ' + statusClass : 'typing-indicator';
          typingEl.innerHTML = statusText;
        }} else {{
          typingEl.className = 'typing-indicator';
          typingEl.innerHTML = '';
        }}
        renderShellJobs(payload.shell_jobs || []);

        if (typeof payload.has_more !== 'undefined') {{
          hasMoreHistory = payload.has_more;
        }}

        const emptyEl = messagesEl.querySelector(".empty");
        if (!payload.messages.length && knownIds.size === 0) {{
          if (!emptyEl) {{
            const empty = document.createElement("div");
            empty.className = "empty";
            empty.textContent = "No messages yet. Say something and " + AGENT_NAME + " will respond here.";
            messagesEl.appendChild(empty);
          }}
          hasCompletedInitialRender = true;
          return;
        }}
        if (emptyEl) emptyEl.remove();

        const newBotMessages = [];
        payload.messages.forEach((message) => {{
          if (hasCompletedInitialRender && !knownIds.has(message.message_id) && message.is_bot) {{
            newBotMessages.push(message);
          }}
          upsertMessageElement(message);
        }});

        if (document.hidden && newBotMessages.length) {{
          hasUnreadMessages = true;
          startTitleFlash();
          playNotificationSound();
          showHiddenTabNotification(newBotMessages[newBotMessages.length - 1]);
        }}

        if (payload.messages.length && !oldestLoadedId) {{
          oldestLoadedId = payload.messages[0].message_id;
        }}

        if (isNearBottom) {{
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }}
        hasCompletedInitialRender = true;
      }}

      async function loadHistory() {{
        if (!oldestLoadedId || loadingHistory || !hasMoreHistory) return;
        loadingHistory = true;
        try {{
          const prevHeight = messagesEl.scrollHeight;
          const response = await fetch("/api/messages?limit=50&before=" + encodeURIComponent(oldestLoadedId), {{ cache: "no-store" }});
          if (!response.ok) throw new Error("history fetch failed");
          const payload = await response.json();
          hasMoreHistory = payload.has_more;
          const fragment = document.createDocumentFragment();
          payload.messages.forEach((message) => {{
            if (!knownIds.has(message.message_id)) {{
              const el = createMessageElement(message);
              knownIds.set(message.message_id, el);
              fragment.appendChild(el);
            }}
          }});
          if (fragment.childNodes.length) {{
            messagesEl.insertBefore(fragment, messagesEl.firstChild);
            messagesEl.scrollTop = messagesEl.scrollHeight - prevHeight;
          }}
          if (payload.messages.length) {{
            oldestLoadedId = payload.messages[0].message_id;
          }}
        }} catch (error) {{
          console.error("loadHistory error:", error);
        }}
        loadingHistory = false;
      }}

      async function refresh() {{
        const response = await fetch("/api/messages", {{ cache: "no-store" }});
        if (!response.ok) {{
          throw new Error(`refresh failed: ${{response.status}}`);
        }}
        const payload = await response.json();
        renderMessages(payload);
      }}

      async function sendMessage(event) {{
        event.preventDefault();
        const text = textEl.value.trim();
        const files = [...Array.from(filesEl.files || []), ...pastedFiles];
        if (!text && files.length === 0) {{
          return;
        }}
        void requestNotificationPermission();

        sendEl.disabled = true;
        const body = new FormData();
        body.set("text", textEl.value);
        files.forEach((file) => body.append("files", file));
        try {{
          const response = await fetch("/api/messages", {{
            method: "POST",
            body,
          }});
          if (!response.ok) {{
            const payload = await response.json().catch(() => ({{ error: response.statusText }}));
            throw new Error(payload.error || "message send failed");
          }}
          textEl.value = "";
          textEl.style.height = "auto";
          filesEl.value = "";
          pastedFiles = [];
          updateFileList();
          isNearBottom = true;
          await refresh();
        }} catch (error) {{
          console.error(error);
          alert(error instanceof Error ? error.message : "Failed to send message");
        }} finally {{
          sendEl.disabled = false;
          textEl.focus();
        }}
      }}

      function autoResize() {{
        textEl.style.overflow = "hidden";
        textEl.style.height = "auto";
        textEl.style.height = textEl.scrollHeight + "px";
        textEl.style.overflow = "";
        if (isNearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
      }}
      textEl.addEventListener("input", autoResize);

      textEl.addEventListener("keydown", (event) => {{
        const isEnter = event.key === "Enter" || event.code === "Enter";
        const hasMod = event.metaKey || event.ctrlKey;
        if (!isEnter || !hasMod) return;
        event.preventDefault();
        event.stopPropagation();
        composerEl.requestSubmit();
      }});
      textEl.addEventListener("paste", (event) => {{
        const items = event.clipboardData && event.clipboardData.items;
        if (!items) return;
        let hasFiles = false;
        for (const item of items) {{
          if (item.kind === "file") {{
            const file = item.getAsFile();
            if (file) {{
              pastedFiles.push(file);
              hasFiles = true;
            }}
          }}
        }}
        if (hasFiles) {{
          updateFileList();
        }}
      }});
      filesEl.addEventListener("change", updateFileList);
      composerEl.addEventListener("submit", sendMessage);
      document.addEventListener("visibilitychange", () => {{
        if (!document.hidden) {{
          clearUnreadState();
        }}
      }});

      messagesEl.addEventListener("scroll", () => {{
        isNearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 100;
        if (messagesEl.scrollTop < 150 && hasMoreHistory && !loadingHistory) {{
          loadHistory();
        }}
      }});

      refreshUiPlugins();
      window.setInterval(refreshUiPlugins, 30000);
      refresh().then(() => {{ messagesEl.scrollTop = messagesEl.scrollHeight; }}).catch((error) => console.error(error));
      window.setInterval(() => {{
        refresh().catch((error) => console.error(error));
      }}, 1500);
    </script>
  </body>
</html>
""".format(
        agent_name=agent_name,
        agent_name_json=agent_name_json,
        channel_id_json=channel_id_json,
    )


def _build_web_ui_app(strix: OpenStrixApp) -> web.Application:
    app = web.Application(client_max_size=25 * 1024**2)

    async def ui_proxy_session(app: web.Application):
        app["ui_proxy_session"] = ClientSession()
        yield
        await app["ui_proxy_session"].close()

    app.cleanup_ctx.append(ui_proxy_session)

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=_render_web_ui_page(strix), content_type="text/html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "channel_id": strix.config.web_ui_channel_id,
                "is_processing": strix.current_event_label is not None,
                "processing_label": strix.current_event_label,
                "turn_elapsed_seconds": _turn_elapsed_seconds(strix),
                "shell_jobs": _shell_jobs_payload(strix),
            },
        )

    async def list_messages(request: web.Request) -> web.Response:
        limit_text = request.query.get("limit", "50")
        before = request.query.get("before")

        try:
            limit = int(limit_text)
        except ValueError:
            return web.json_response({"error": "limit must be an integer"}, status=400)

        messages, has_more = strix.serialize_web_messages(limit=limit, before=before)
        return web.json_response(
            {
                "agent_name": _web_agent_name(strix),
                "channel_id": strix.config.web_ui_channel_id,
                "is_processing": strix.current_event_label is not None,
                "processing_label": strix.current_event_label,
                "turn_elapsed_seconds": _turn_elapsed_seconds(strix),
                "shell_jobs": _shell_jobs_payload(strix),
                "messages": messages,
                "has_more": has_more,
            },
        )

    async def post_message(request: web.Request) -> web.Response:
        if request.content_type.startswith("application/json"):
            body = await request.json()
            text = str(body.get("text", ""))
            uploads: list[FileField] = []
        else:
            form = await request.post()
            text = str(form.get("text", ""))
            uploads = [
                value
                for value in form.values()
                if isinstance(value, FileField) and bool(getattr(value, "filename", ""))
            ]

        try:
            message_id = await strix.handle_web_message(text=text, uploads=uploads)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        return web.json_response(
            {
                "status": "queued",
                "channel_id": strix.config.web_ui_channel_id,
                "message_id": message_id,
            },
        )

    async def serve_file(request: web.Request) -> web.StreamResponse:
        virtual_path = request.match_info.get("path", "")
        target = strix.resolve_web_shared_file(virtual_path)
        if target is None:
            raise web.HTTPNotFound()
        return web.FileResponse(target)

    async def list_shell_jobs(request: web.Request) -> web.Response:
        try:
            scope = normalize_shell_job_scope(request.query.get("scope"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        registry = getattr(strix, "shell_jobs", None)
        return web.json_response(
            {
                "scope": scope,
                "jobs": shell_job_snapshots(registry, scope=scope),
            },
        )

    async def shell_job_detail(request: web.Request) -> web.Response:
        registry = getattr(strix, "shell_jobs", None)
        if registry is None:
            return web.json_response({"error": "shell jobs unavailable"}, status=404)
        try:
            tail_lines = parse_shell_job_tail_lines(request.query.get("tail"))
            stream = normalize_shell_job_stream(request.query.get("stream"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        data = registry.read_output(
            request.match_info["job_id"],
            tail_lines=tail_lines,
            stream=stream,
        )
        if "error" in data:
            return web.json_response(data, status=404)
        return web.json_response(data)

    async def ops_dashboard(request: web.Request) -> web.Response:
        try:
            days = parse_days_param(request.query.get("days"))
        except ValueError as exc:
            return web.Response(text=str(exc), status=400)
        stats = build_dashboard_payload(strix, days)
        return web.Response(text=render_dashboard_html(stats), content_type="text/html")

    async def ops_dashboard_json(request: web.Request) -> web.Response:
        try:
            days = parse_days_param(request.query.get("days"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(build_dashboard_payload(strix, days))

    async def list_uis(_: web.Request) -> web.Response:
        manager = getattr(strix, "ui_plugins", None)
        if manager is None:
            return web.json_response([])
        return web.json_response(manager.status())

    async def proxy_ui(request: web.Request) -> web.StreamResponse:
        # TODO(v2): websocket proxy support
        manager = getattr(strix, "ui_plugins", None)
        name = request.match_info["name"]
        plugin = manager.find(name) if manager is not None else None
        status = plugin.state if plugin is not None else "dead"
        if plugin is None or plugin.state != "running":
            return web.json_response(
                {"error": "ui not available", "name": name, "status": status},
                status=503,
            )

        path = request.match_info.get("path", "")
        target_url = f"http://127.0.0.1:{plugin.port}/{quote(path, safe='/')}"
        if request.query_string:
            target_url = f"{target_url}?{request.query_string}"

        request_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        session = request.app["ui_proxy_session"]
        request_body = request.content.iter_chunked(64 * 1024) if request.can_read_body else None
        try:
            async with session.request(
                request.method,
                target_url,
                headers=request_headers,
                data=request_body,
                allow_redirects=False,
            ) as proxied:
                response_headers = {
                    key: value
                    for key, value in proxied.headers.items()
                    if key.lower() not in HOP_BY_HOP_HEADERS
                }
                response = web.StreamResponse(
                    status=proxied.status,
                    reason=proxied.reason,
                    headers=response_headers,
                )
                await response.prepare(request)
                async for chunk in proxied.content.iter_chunked(64 * 1024):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except ClientError as exc:
            return web.json_response(
                {"error": "ui proxy failed", "name": name, "detail": str(exc)},
                status=502,
            )

    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/messages", list_messages)
    app.router.add_post("/api/messages", post_message)
    app.router.add_get("/api/uis", list_uis)
    app.router.add_route("*", "/ui/{name}/", proxy_ui)
    app.router.add_route("*", "/ui/{name}/{path:.*}", proxy_ui)
    app.router.add_get("/api/shell-jobs", list_shell_jobs)
    app.router.add_get("/api/shell-jobs/{job_id}", shell_job_detail)
    app.router.add_get("/files/{path:.*}", serve_file)
    app.router.add_get("/ops", ops_dashboard)
    app.router.add_get("/api/ops", ops_dashboard_json)
    return app


async def start_web_ui(strix: OpenStrixApp, host: str, port: int) -> web.AppRunner:
    app = _build_web_ui_app(strix)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    strix.log_event(
        "web_ui_started",
        host=host,
        port=port,
        channel_id=strix.config.web_ui_channel_id,
    )
    return runner
