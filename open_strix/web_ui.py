from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import uuid4

from aiohttp import web
from aiohttp.web_request import FileField

from .models import AgentEvent

if TYPE_CHECKING:
    from .app import OpenStrixApp

WEB_UI_CHANNEL_NAME = "Local Web"
WEB_UI_AUTHOR = "local_user"
WEB_UI_AUTHOR_ID = "local-web-user"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".heic"}


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
        return {
            "path": virtual_path,
            "name": Path(virtual_path).name,
            "url": f"/files/{quote(virtual_path, safe='/')}",
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
            str(path)
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


def _render_web_ui_page(strix: OpenStrixApp) -> str:
    agent_name = _web_agent_name(strix)
    agent_name_json = json.dumps(agent_name)
    channel_id_json = json.dumps(strix.config.web_ui_channel_id)
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
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
      }}

      .title {{
        margin: 0;
        font-size: clamp(1.2rem, 2vw, 1.65rem);
        line-height: 1.1;
      }}

      .typing-indicator {{
        font-size: 0.75rem;
        color: var(--muted);
        min-height: 1.2em;
        padding-left: 0.5rem;
        margin-bottom: 0.15rem;
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

      .body {{
        line-height: 1.45;
        overflow-wrap: anywhere;
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
        min-height: 6.5rem;
        resize: vertical;
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
    </style>
  </head>
  <body>
    <main class="shell">
      <header class="header">
        <h1 class="title">{agent_name}</h1>
      </header>

      <section class="messages" id="messages" aria-live="polite">
        <div class="empty">No messages yet. Say something and {agent_name} will respond here.</div>
      </section>

      <section class="composer">
        <form class="composer-form" id="composer">
          <div class="typing-indicator" id="typing-indicator"></div>
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

      let pastedFiles = [];
      let knownIds = new Map();
      let oldestLoadedId = null;
      let hasMoreHistory = true;
      let loadingHistory = false;
      let isNearBottom = true;

      function formatTime(value) {{
        if (!value) return "";
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return value;
        return dt.toLocaleTimeString([], {{ hour: "numeric", minute: "2-digit" }});
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
          link.target = "_blank";
          link.rel = "noreferrer";
        }});
        return wrapper.innerHTML;
      }}

      function createMessageElement(message) {{
        const article = document.createElement("article");
        article.className = `message ${{message.is_bot ? "agent" : "user"}}`;
        article.dataset.messageId = message.message_id;

        const meta = document.createElement("div");
        meta.className = "meta";
        const author = document.createElement("strong");
        author.textContent = message.is_bot ? AGENT_NAME : "You";
        const time = document.createElement("span");
        time.textContent = formatTime(message.timestamp);
        meta.append(author, time);
        article.appendChild(meta);

        if (message.content) {{
          const body = document.createElement("div");
          body.className = "body";
          body.innerHTML = simpleMarkdown(message.content);
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

        if (message.reactions && message.reactions.length) {{
          const reactions = document.createElement("div");
          reactions.className = "reactions";
          message.reactions.forEach((emoji) => {{
            const chip = document.createElement("span");
            chip.className = "reaction";
            chip.textContent = emoji;
            reactions.appendChild(chip);
          }});
          article.appendChild(reactions);
        }}

        return article;
      }}

      function renderMessages(payload) {{
        if (payload.is_processing) {{
          typingEl.innerHTML = '<span class="typing-dot"></span>' + AGENT_NAME + ' is thinking…';
        }} else {{
          typingEl.innerHTML = '';
        }}

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
          return;
        }}
        if (emptyEl) emptyEl.remove();

        payload.messages.forEach((message) => {{
          if (!knownIds.has(message.message_id)) {{
            const el = createMessageElement(message);
            knownIds.set(message.message_id, el);
            messagesEl.appendChild(el);
          }}
        }});

        if (payload.messages.length && !oldestLoadedId) {{
          oldestLoadedId = payload.messages[0].message_id;
        }}

        if (isNearBottom) {{
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }}
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
          filesEl.value = "";
          pastedFiles = [];
          updateFileList();
          await refresh();
        }} catch (error) {{
          console.error(error);
          alert(error instanceof Error ? error.message : "Failed to send message");
        }} finally {{
          sendEl.disabled = false;
          textEl.focus();
        }}
      }}

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

      messagesEl.addEventListener("scroll", () => {{
        isNearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 100;
        if (messagesEl.scrollTop < 150 && hasMoreHistory && !loadingHistory) {{
          loadHistory();
        }}
      }});

      messagesEl.addEventListener("scroll", () => {{
        isNearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 100;
        if (messagesEl.scrollTop < 150 && hasMoreHistory && !loadingHistory) {{
          loadHistory();
        }}
      }});

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
        channel_id=strix.config.web_ui_channel_id,
        channel_id_json=channel_id_json,
    )


def _build_web_ui_app(strix: OpenStrixApp) -> web.Application:
    app = web.Application(client_max_size=25 * 1024**2)

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=_render_web_ui_page(strix), content_type="text/html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "channel_id": strix.config.web_ui_channel_id})

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
                "is_processing": strix.current_channel_id == strix.config.web_ui_channel_id,
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

    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/messages", list_messages)
    app.router.add_post("/api/messages", post_message)
    app.router.add_get("/files/{path:.*}", serve_file)
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
