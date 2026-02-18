from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from typing import Any, Mapping

UTC = timezone.utc

DEFAULT_CHECKPOINT = """\
When you write a journal entry, think through:
- What did the user want, exactly?
- What did you do?
- What prediction do you have about how the user will react?
- What should you do differently next time?
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


def render_journal_entries(entries: list[dict[str, Any]]) -> str:
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


def render_memory_blocks(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return "(none)"

    rendered: list[str] = []
    for block in blocks:
        name = str(block.get("name", "")).strip() or str(block.get("id", "")).strip() or "unnamed"
        value = str(block.get("text", "")).strip()
        rendered.append(f"memory block: {name}\n{value}")
    return "\n\n".join(rendered)


def render_discord_messages(messages: list[dict[str, Any]]) -> str:
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


def render_current_event(event: Mapping[str, Any]) -> str:
    now = datetime.now(tz=UTC)
    timestamp = _format_timestamp(now, now=now)
    author = str(event.get("author") or "system")
    message_id = str(event.get("source_id") or "unknown")
    prompt = str(event.get("prompt", ""))
    content = prompt.strip() if prompt.strip() else "(no text)"

    lines = [
        f"channel_id: {event.get('channel_id') if event.get('channel_id') else 'unknown'}",
        f"event_type: {event.get('event_type')}",
        f"{timestamp} | {author} | message_id={message_id}",
        content,
    ]
    attachment_names = event.get("attachment_names")
    if isinstance(attachment_names, list) and attachment_names:
        lines.append("attachments:")
        lines.extend(f"  - {item}" for item in attachment_names)
    scheduler_name = event.get("scheduler_name")
    if scheduler_name:
        lines.append(f"scheduler_name: {scheduler_name}")
    return "\n".join(lines)


def render_turn_prompt(
    *,
    journal_entries: list[dict[str, Any]],
    memory_blocks: list[dict[str, Any]],
    discord_messages: list[dict[str, Any]],
    current_event: Mapping[str, Any],
) -> str:
    journals = render_journal_entries(journal_entries)
    blocks_text = render_memory_blocks(memory_blocks)
    messages_text = render_discord_messages(discord_messages)
    current_event_text = render_current_event(current_event)

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
