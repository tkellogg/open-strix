from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import io
import json
from pathlib import Path
import time

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from aiohttp.web_request import FileField
from langchain_core.messages import HumanMessage
from multidict import CIMultiDict, CIMultiDictProxy, MultiDict
import pytest

from open_strix.config import AppConfig, RepoLayout
from open_strix.discord import DiscordMixin
from open_strix.models import AgentEvent
from open_strix.shell_jobs import ShellJobRegistry
from open_strix.web_ui import (
    WebChatMixin,
    _build_web_ui_app,
    _is_inline_image,
    _render_web_ui_page,
    _web_agent_name,
)


class DummyStrix(DiscordMixin, WebChatMixin):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig(
            web_ui_port=8084,
            web_ui_host="127.0.0.1",
            web_ui_channel_id="local-web",
        )
        self.message_history_all = deque(maxlen=500)
        self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
        self._current_turn_sent_messages: list[tuple[str, str]] | None = []
        self.current_channel_id: str | None = None
        self.current_event_label: str | None = None
        self.current_turn_start: float | None = None
        self.discord_client = None
        self.shell_jobs = ShellJobRegistry(self.layout.logs_dir / "shell-jobs")
        self.logged: list[dict[str, object]] = []
        self.enqueued: list[AgentEvent] = []

    def log_event(self, event_type: str, **payload: object) -> None:
        self.logged.append({"type": event_type, **payload})

    async def enqueue_event(self, event: AgentEvent) -> None:
        self.enqueued.append(event)


def _get_route_handler(app, path: str, method: str):
    for route in app.router.routes():
        if route.method == method and getattr(route.resource, "canonical", None) == path:
            return route.handler
    raise AssertionError(f"missing {method} route for {path}")


def test_web_ui_page_includes_markdown_assets_and_styles(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert '<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>' in page
    assert (
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" '
        'rel="stylesheet">'
    ) in page
    assert 'font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;' in page
    assert 'marked.parse(text)' in page
    assert "replace(/&/g, '&amp;')" not in page
    assert 's = s.replace(/```(\\\\w*)\\\\n?([\\\\s\\\\S]*?)```/g' not in page
    assert ".body table" in page
    assert ".body th" in page
    assert ".body td" in page
    assert ':root[data-theme="dark"]' in page
    assert 'id="theme-toggle"' in page
    assert 'localStorage.setItem("open-strix-theme", nextTheme)' in page
    assert 'prefers-color-scheme: dark' in page


def test_web_ui_page_refresh_updates_existing_message_reactions_without_replacing_nodes(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert "function updateReactions(existingEl, newReactions)" in page
    assert "function upsertMessageElement(message, append = true)" in page
    assert "const existing = knownIds.get(message.message_id);" in page
    assert "updateReactions(existing, message.reactions);" in page
    assert "existing.replaceWith(el);" not in page


def test_web_ui_page_escapes_shell_job_output_newlines_in_inline_js(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert "const out = (data.stdout_tail || '') + (data.stderr_tail ? '\\n--- stderr ---\\n' + data.stderr_tail : '');" in page


def test_web_ui_page_places_shell_jobs_widget_in_status_row(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert 'class="status-row"' in page
    assert 'class="shell-jobs-widget" id="shell-jobs-widget"' in page
    assert 'class="shell-jobs-pill" id="shell-jobs-pill" type="button"' in page
    assert "let shellJobOutputState = new Map();" in page
    assert "function refreshShellJobOutput(jobId, status)" in page
    assert "function updateShellJobOutputElement(jobId)" in page
    assert "function escapeHtml(text)" in page
    assert "shellJobsWidgetEl.contains(event.target)" in page


def test_web_attachment_payload_strips_leading_slashes_from_urls(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    payload = strix._web_attachment_payload("/state/research-findings/report.md")

    assert payload["path"] == "/state/research-findings/report.md"
    assert payload["url"] == "/files/state/research-findings/report.md"


def test_is_inline_image_detects_svg() -> None:
    # Issue: web UI only rendered raster image attachments; SVGs were shown
    # as a plain "attachment" link instead of inlined. Including .svg in
    # IMAGE_EXTENSIONS lets the existing <img>-rendering branch handle them
    # (browsers render image/svg+xml inline, and aiohttp.web.FileResponse
    # already serves the correct content-type via mimetypes.guess_type).
    assert _is_inline_image("diagram.svg") is True
    assert _is_inline_image("DIAGRAM.SVG") is True
    assert _is_inline_image("/state/visuals/agent.svg") is True
    # Sanity: still rejects non-image extensions.
    assert _is_inline_image("notes.txt") is False
    assert _is_inline_image("script.js") is False


def test_web_attachment_payload_marks_svg_as_inline_image(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    payload = strix._web_attachment_payload("/state/visuals/diagram.svg")

    assert payload["name"] == "diagram.svg"
    assert payload["url"] == "/files/state/visuals/diagram.svg"
    assert payload["is_image"] is True


def test_web_ui_page_includes_status_css_classes(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert "status-slow" in page
    assert "status-stuck" in page
    assert "elapsed" in page


def test_web_ui_html_message_bubble_uses_normal_message_width(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)
    selector = ".message:has(> .body > .html-body)"
    rule_start = page.index(selector)
    rule_end = page.index("}", rule_start)
    rule = page[rule_start:rule_end]

    assert ":has(> .body > .html-body)" in rule
    assert "width: min(42rem, 92%)" in rule


@pytest.mark.asyncio
async def test_web_ui_message_flow_and_attachment_serving(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)
    app = _build_web_ui_app(strix)
    post_handler = _get_route_handler(app, "/api/messages", "POST")
    upload = FileField(
        name="files",
        filename="photo.png",
        file=io.BytesIO(b"png-bytes"),
        content_type="image/png",
        headers=CIMultiDictProxy(CIMultiDict()),
    )

    class DummyUploadRequest:
        content_type = "multipart/form-data"

        async def post(self) -> MultiDict[str | FileField]:
            return MultiDict([("text", "hello from the browser"), ("files", upload)])

    response = await post_handler(DummyUploadRequest())
    assert response.status == 200
    body = json.loads(response.text)
    assert body["status"] == "queued"
    assert body["channel_id"] == "local-web"

    assert len(strix.enqueued) == 1
    event = strix.enqueued[0]
    assert event.event_type == "web_message"
    assert event.channel_id == "local-web"
    assert event.prompt == "hello from the browser"
    assert len(event.attachment_names) == 1
    saved_path = tmp_path / event.attachment_names[0]
    assert saved_path.read_bytes() == b"png-bytes"

    messages_handler = _get_route_handler(app, "/api/messages", "GET")
    messages_request = make_mocked_request("GET", "/api/messages", app=app)
    messages_response = await messages_handler(messages_request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["channel_id"] == "local-web"
    assert messages_body["is_processing"] is False
    assert len(messages_body["messages"]) == 1
    message = messages_body["messages"][0]
    assert message["content"] == "hello from the browser"
    assert message["attachments"][0]["name"] == saved_path.name
    assert message["attachments"][0]["is_image"] is True

    file_handler = next(
        route.handler
        for route in app.router.routes()
        if route.method == "GET" and getattr(route.resource, "canonical", "").startswith("/files/")
    )

    class DummyFileRequest:
        match_info = {"path": message["attachments"][0]["path"]}

    file_response = await file_handler(DummyFileRequest())
    assert file_response.status == 200
    assert isinstance(file_response, web.FileResponse)
    assert Path(file_response._path) == saved_path.resolve()


@pytest.mark.asyncio
async def test_web_ui_continue_uses_cached_agent_context(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    previous_messages = [HumanMessage(content="original agent turn prompt")]
    strix.cache_web_continuation_context(
        message_ids=["web-agent-reply"],
        messages=previous_messages,
    )
    app = _build_web_ui_app(strix)
    continue_handler = _get_route_handler(app, "/api/messages/continue", "POST")

    request = make_mocked_request(
        "POST",
        "/api/messages/continue",
        headers={"Content-Type": "application/json"},
        payload=None,
        app=app,
    )

    async def _json() -> dict[str, str]:
        return {
            "source_message_id": "web-agent-reply",
            "text": "continue from here",
        }

    request.json = _json  # type: ignore[method-assign]

    response = await continue_handler(request)
    assert response.status == 200
    body = json.loads(response.text)
    assert body["status"] == "queued"
    assert body["continuation_source_id"] == "web-agent-reply"

    assert len(strix.enqueued) == 1
    event = strix.enqueued[0]
    assert event.event_type == "web_continue"
    assert event.prompt == "continue from here"
    assert event.continuation_messages == previous_messages


@pytest.mark.asyncio
async def test_web_ui_continue_loads_persisted_agent_context(tmp_path: Path) -> None:
    home = tmp_path / "atlas"
    original_strix = DummyStrix(home)
    previous_messages = [HumanMessage(content="agent context before web restart")]
    original_strix.cache_web_continuation_context(
        message_ids=["web-agent-reply"],
        messages=previous_messages,
    )
    assert original_strix._web_continuation_path("web-agent-reply").exists()

    restarted_strix = DummyStrix(home)
    app = _build_web_ui_app(restarted_strix)
    continue_handler = _get_route_handler(app, "/api/messages/continue", "POST")

    request = make_mocked_request(
        "POST",
        "/api/messages/continue",
        headers={"Content-Type": "application/json"},
        payload=None,
        app=app,
    )

    async def _json() -> dict[str, str]:
        return {
            "source_message_id": "web-agent-reply",
            "text": "continue after restart",
        }

    request.json = _json  # type: ignore[method-assign]

    response = await continue_handler(request)
    assert response.status == 200
    event = restarted_strix.enqueued[0]
    assert event.event_type == "web_continue"
    assert event.prompt == "continue after restart"
    assert event.continuation_messages == previous_messages


@pytest.mark.asyncio
async def test_web_ui_continue_waits_for_active_turn_context(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    previous_messages = [HumanMessage(content="agent context after turn finishes")]
    strix._current_turn_sent_messages = [("local-web", "web-agent-reply")]

    continue_task = asyncio.create_task(
        strix.handle_web_continue(
            source_message_id="web-agent-reply",
            text="continue after the send_message tool returns",
        ),
    )
    await asyncio.sleep(0)
    assert not continue_task.done()

    strix.cache_web_continuation_context(
        message_ids=["web-agent-reply"],
        messages=previous_messages,
    )

    message_id = await continue_task
    assert message_id.startswith("web-")
    assert strix.logged[0]["type"] == "web_continue_context_wait"
    event = strix.enqueued[0]
    assert event.event_type == "web_continue"
    assert event.prompt == "continue after the send_message tool returns"
    assert event.continuation_messages == previous_messages


@pytest.mark.asyncio
async def test_web_ui_continue_requires_cached_context(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    app = _build_web_ui_app(strix)
    continue_handler = _get_route_handler(app, "/api/messages/continue", "POST")

    request = make_mocked_request(
        "POST",
        "/api/messages/continue",
        headers={"Content-Type": "application/json"},
        payload=None,
        app=app,
    )

    async def _json() -> dict[str, str]:
        return {
            "source_message_id": "missing",
            "text": "continue from here",
        }

    request.json = _json  # type: ignore[method-assign]

    response = await continue_handler(request)
    assert response.status == 400
    body = json.loads(response.text)
    assert "continuation context is not available" in body["error"]
    assert "regenerate the HTML control" in body["error"]


@pytest.mark.asyncio
async def test_local_web_send_and_react_round_trip(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)
    shared_file = tmp_path / "state" / "summary.txt"
    shared_file.parent.mkdir(parents=True, exist_ok=True)
    shared_file.write_text("hello", encoding="utf-8")

    sent, message_id, chunks = await strix._send_channel_message(
        channel_id="local-web",
        text="agent reply",
        attachment_paths=[shared_file],
        attachment_names=["/state/summary.txt"],
    )

    assert sent is True
    assert chunks == 1
    assert message_id is not None
    assert strix._current_turn_sent_messages == [("local-web", message_id)]

    reacted = await strix._react_to_message(
        channel_id="local-web",
        message_id=message_id,
        emoji="👍",
    )
    assert reacted is True

    messages, _has_more = strix.serialize_web_messages()
    assert len(messages) == 1
    assert messages[0]["content"] == "agent reply"
    assert messages[0]["attachments"][0]["path"] == "/state/summary.txt"
    assert messages[0]["attachments"][0]["url"] == "/files/state/summary.txt"
    assert messages[0]["reactions"] == ["👍"]

    resolved = strix.resolve_web_shared_file("state/summary.txt")
    assert resolved == shared_file.resolve()
    resolved_with_leading_slash = strix.resolve_web_shared_file("/state/summary.txt")
    assert resolved_with_leading_slash == shared_file.resolve()


@pytest.mark.asyncio
async def test_web_ui_message_record_includes_format_field(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)

    sent, message_id, chunks = await strix._send_channel_message(
        channel_id="local-web",
        text="<strong>agent card</strong>",
        format="html",
    )

    assert sent is True
    assert chunks == 1
    assert message_id is not None

    history_lines = [
        json.loads(line)
        for line in (tmp_path / "logs" / "chat-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert history_lines[-1]["format"] == "html"

    app = _build_web_ui_app(strix)
    request = make_mocked_request("GET", "/api/messages", app=app)
    handler = _get_route_handler(app, "/api/messages", "GET")
    response = await handler(request)
    assert response.status == 200
    body = json.loads(response.text)
    assert body["messages"][-1]["format"] == "html"


def test_web_ui_renders_html_in_iframe(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert 'if (message.format === "html")' in page
    assert 'document.createElement("iframe")' in page
    assert 'frame.setAttribute("sandbox", "allow-scripts allow-forms");' in page
    assert "function htmlWithBridge(" in page
    assert "function htmlMessageBridgeSource(" in page
    assert 'frame.setAttribute("srcdoc", htmlWithBridge(message.content || "", message.message_id || ""));' in page
    html_message_start = page.index('if (message.format === "html")')
    html_message_end = page.index("body.appendChild(frame);", html_message_start)
    html_message_block = page[html_message_start:html_message_end]
    assert "allow-same-origin" not in html_message_block


def test_web_ui_page_includes_ui_plugin_shell(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert '<div class="ui-strip" id="ui-strip"' in page
    assert 'class="ui-hamburger" id="ui-hamburger"' in page
    assert 'fetch("/api/uis"' in page


def test_ui_strip_grows_to_fill_available_width(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    # Old fixed-width rule must be gone.
    assert "flex: 0 0 320px;" not in page
    # New growable rule must be present (min-width keeps it readable, max-width caps it).
    assert "min-width: 320px;" in page
    assert "max-width: 600px;" in page


def test_minimize_collapses_card_via_is_minimized_class(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    # CSS rule that actually hides the body when minimized.
    assert ".ui-card.is-minimized .ui-body" in page
    # JS toggles the class on the card element.
    assert 'card.classList.toggle("is-minimized", widget.minimized)' in page


def test_plugin_card_has_fixed_frame_and_hides_placeholder_when_running(tmp_path: Path) -> None:
    """Open plugin cards must have a fixed iframe area and the placeholder must
    actually disappear when the iframe is showing.

    History:
    - 2026-05-12a — `.ui-frame-slot { height: 20rem }` left huge empty space.
    - 2026-05-12b — switched card to `flex: 1 1 0` so it stretched the whole
      strip (still wrong; iframe stayed at 150px because its height: 100% chain
      didn't resolve through unbounded flex parents).
    - 2026-05-12c — pinned the slot at 600px. Card sized correctly but the
      `.ui-placeholder` sibling stayed visible (its `display: grid` rule
      overrides the `[hidden]` attribute) and ate ~128px below the iframe.
    - 2026-05-12d (this) — slot pinned at 260px (Tim asked for ~half of 600).
      Added explicit `[hidden]` overrides on both `.ui-frame-slot` and
      `.ui-placeholder` so the JS `widget.placeholder.hidden = running` /
      `widget.frameSlot.hidden = !running` actually collapse them.
    """
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    # The frame slot has a fixed height so the iframe can resolve 100%.
    assert "height: 260px;" in page
    # The old 600px and 20rem rules must be gone.
    assert "height: 600px;" not in page
    assert "height: 20rem" not in page
    # The card does NOT grow to fill the strip — it takes its natural height.
    assert "flex: 1 1 0;" not in page
    # The strip content still stretches to fill the strip vertically.
    assert "min-height: 100%;" in page
    # Card uses flex: 0 0 auto so it sizes to content (titlebar + slot).
    assert "flex: 0 0 auto;" in page
    # The `hidden` attribute on placeholder/slot must actually hide them —
    # without these rules the `display: grid` on `.ui-placeholder` wins.
    assert ".ui-placeholder[hidden]" in page
    assert ".ui-frame-slot[hidden]" in page


def test_plugin_titlebar_has_reload_button(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    # A reload button is created alongside minimize and maximize.
    assert 'reload.title = "Reload"' in page
    # The reload click handler re-assigns iframe.src to force a reload.
    assert "widget.iframe.src = widget.iframe.src" in page
    # The reload button is in the actions row (now alongside back/forward).
    assert "actions.append(back, forward, reload, minimize, maximize)" in page


def test_plugin_titlebar_has_back_forward_buttons(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    # Back and forward buttons are created.
    assert 'back.title = "Back"' in page
    assert 'forward.title = "Forward"' in page
    # They navigate the iframe's session history (works through error pages).
    assert "widget.iframe.contentWindow.history.back()" in page
    assert "widget.iframe.contentWindow.history.forward()" in page
    # Back has a fallback: if contentWindow is cross-origin or detached,
    # reload the root plugin URL so the user always has an escape hatch.
    assert 'widget.iframe.src = "/ui/" + encodeURIComponent(widget.name) + "/";' in page


def test_iframe_height_uses_max_of_html_and_body_scroll_height(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert "Math.max(" in page
    assert "documentElement" in page
    assert "body.scrollHeight" in page


def test_iframe_uses_resize_observer_for_async_layout(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert "ResizeObserver" in page
    assert ".observe(" in page


def test_iframe_resize_observer_is_optional(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert 'typeof ResizeObserver !== "undefined"' in page


@pytest.mark.asyncio
async def test_web_ui_uses_configured_display_name(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    strix.config.name = "Keel"

    page = _render_web_ui_page(strix)
    assert _web_agent_name(strix) == "Keel"
    assert "<title>Keel Chat</title>" in page
    assert "No messages yet. Say something and Keel will respond here." in page
    assert 'placeholder="Message Keel..."' in page

    app = _build_web_ui_app(strix)
    request = make_mocked_request("GET", "/api/messages", app=app)
    handler = _get_route_handler(app, "/api/messages", "GET")
    messages_response = await handler(request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["agent_name"] == "Keel"


@pytest.mark.asyncio
async def test_web_ui_falls_back_to_home_name_when_display_name_missing(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)
    assert _web_agent_name(strix) == "atlas"
    assert "<title>atlas Chat</title>" in page
    assert "No messages yet. Say something and atlas will respond here." in page
    assert 'placeholder="Message atlas..."' in page

    app = _build_web_ui_app(strix)
    request = make_mocked_request("GET", "/api/messages", app=app)
    handler = _get_route_handler(app, "/api/messages", "GET")
    messages_response = await handler(request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["agent_name"] == "atlas"


@pytest.mark.asyncio
async def test_list_messages_includes_turn_elapsed_when_idle(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/api/messages", "GET")

    request = make_mocked_request("GET", "/api/messages", app=app)
    response = await handler(request)

    body = json.loads(response.text)
    assert body["turn_elapsed_seconds"] is None
    assert body["is_processing"] is False


@pytest.mark.asyncio
async def test_list_messages_includes_turn_elapsed_when_processing(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    strix.current_event_label = "web_message"
    strix.current_turn_start = time.monotonic() - 45.0
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/api/messages", "GET")

    request = make_mocked_request("GET", "/api/messages", app=app)
    response = await handler(request)

    body = json.loads(response.text)
    assert body["is_processing"] is True
    assert body["turn_elapsed_seconds"] is not None
    assert body["turn_elapsed_seconds"] >= 44.0


@pytest.mark.asyncio
async def test_list_messages_includes_running_shell_jobs_immediately(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    job = strix.shell_jobs.spawn("sleep 0.8", argv=["bash", "-lc", "sleep 0.8"])
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/api/messages", "GET")

    request = make_mocked_request("GET", "/api/messages", app=app)
    response = await handler(request)

    body = json.loads(response.text)
    assert [item["job_id"] for item in body["shell_jobs"]] == [job.job_id]
    assert body["shell_jobs"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_web_ui_shell_jobs_routes_return_running_jobs_and_output(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    app = _build_web_ui_app(strix)

    running = strix.shell_jobs.spawn("sleep 0.8", argv=["bash", "-lc", "sleep 0.8"])
    completed_cmd = "printf 'stdout\\n'; printf 'stderr\\n' >&2"
    completed = strix.shell_jobs.spawn(completed_cmd, argv=["bash", "-lc", completed_cmd])
    deadline = time.monotonic() + 2.0
    while completed.exit_code is None:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)

    list_handler = _get_route_handler(app, "/api/shell-jobs", "GET")
    list_request = make_mocked_request("GET", "/api/shell-jobs", app=app)
    list_response = await list_handler(list_request)
    list_body = json.loads(list_response.text)
    assert list_body["scope"] == "running"
    assert [item["job_id"] for item in list_body["jobs"]] == [running.job_id]

    detail_handler = _get_route_handler(app, "/api/shell-jobs/{job_id}", "GET")
    detail_request = make_mocked_request(
        "GET",
        f"/api/shell-jobs/{completed.job_id}",
        app=app,
        match_info={"job_id": completed.job_id},
    )
    detail_response = await detail_handler(detail_request)
    detail_body = json.loads(detail_response.text)
    assert detail_body["job_id"] == completed.job_id
    assert "stdout" in detail_body["stdout_tail"]
    assert "stderr" in detail_body["stderr_tail"]


@pytest.mark.asyncio
async def test_health_includes_turn_state(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    app = _build_web_ui_app(strix)
    handler = _get_route_handler(app, "/api/health", "GET")

    request = make_mocked_request("GET", "/api/health", app=app)
    response = await handler(request)

    body = json.loads(response.text)
    assert "is_processing" in body
    assert "turn_elapsed_seconds" in body


def test_render_page_includes_ui_plugin_link_navigation(tmp_path: Path) -> None:
    """Markdown click delegation, HTML iframe actions, JS bridge, and hash
    routing must all be wired up in the rendered page. This is the regression
    guard for the link-to-plugin nav feature (2026-05-12) plus the explicit
    HTML action API."""
    strix = DummyStrix(tmp_path / "atlas")
    html = _render_web_ui_page(strix)

    # Core helpers exist.
    assert "function parseUiPluginHref(" in html
    assert "function routeUiPluginNav(" in html
    assert "function attachStrixHtmlActions(" in html
    assert "function runStrixAction(" in html
    assert "function postWebChatMessage(" in html
    assert "function postWebConversationContinue(" in html

    # Two action paths: parent chat container and injected HTML-message bridge.
    assert "attachStrixHtmlActions(messagesEl)" in html
    assert "registerHtmlMessageFrame(frame)" in html
    assert "htmlMessageBridgeSource" in html

    # Explicit HTML and JS action APIs.
    assert "data-strix-action" in html
    assert 'action === "widget.navigate"' in html
    assert 'action === "chat.send"' in html
    assert 'action === "conversation.continue"' in html
    assert 'frame.dataset.messageId = message.message_id || "";' in html
    assert "sourceMessageId" in html
    assert "clickedIsSubmitter" in html
    assert 'root.addEventListener("keydown"' in html
    assert 'window.addEventListener("message"' in html
    assert 'payload.strix !== "v1"' in html

    # Hash-routed deep links remain as a compatibility escape hatch.
    assert "hashchange" in html
    assert '"#/ui/"' in html

    # parseUiPluginHref must unwrap a leading "#/ui/" BEFORE URL normalization,
    # otherwise `new URL("#/ui/...", origin)` collapses pathname to "/" and
    # the recombined path becomes "/#/ui/..." which fails every prefix check.
    # Regression guard for the 2026-05-12 hash-form bug Tim reported at 14:14 UTC.
    assert 'path.startsWith("#/ui/")' in html
    assert 'path.indexOf("#/ui/")' in html

    # Markdown link rewriting must explicitly skip /ui/ links so they stay
    # in-tab and get claimed by the click delegate.
    assert "parseUiPluginHref(href)" in html
