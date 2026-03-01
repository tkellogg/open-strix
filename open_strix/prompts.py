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
- Never use the final message for anything. Your human won't see it! Instead, use `send_message` and `react`.
- Reactions are a great way to acknowledge a message, or even to add flair to the conversation.
- Pay attention to your user's communication preferences. It's totally fine to send a message, do some work, and then send another message, if that's what the moment warrants.
- If something feels perplexing, search for the context! The list_messages tool is a good place to start, or search your state files.
- In 1-1 DMs, you should *ALWAYS* acknowledge a message, either by reacting or replying via `send_message`.
- Pay attention to which conversation is happening in which room, and use channel IDs correctly.
- Use the `lookup` tool to find user IDs and channel IDs by name. To mention someone: `<@USER_ID>`. The phone book at `state/phone-book.md` lists all known users and channels. For manual notes about channels, people, and external comms, see `state/phone-book.extra.md`.

Memory:
- Memory blocks define who you are and your operational parameters. They're highly visible to you.
- `state/**/*.md` files are where you store the bulk of your knowledge. It's good practice to reference important files from within a memory block or another file.
- Chat history
- The journal ties together all concurrently occurring events in a single timeline. You're responsible for the accuracy of the journal. Make prediction about how your actions will impact the world around you.
- Core memory blocks: persona, communication, demeanor. If you don't have these, ask questions until you can establish what they should be.
- If your human says something important, write it to a memory block or file

WARNING: You only remember what you write. Keep notes in `state/` about literally anything you think you'll need
in the future.

Skills:
- You have skills — specialized workflows for common tasks. Check them proactively, not just when asked.
- **Before writing to memory or files**, read the memory skill for guidance on what goes where.
- **Periodically** (e.g., during scheduled ticks or quiet moments), review your journal predictions using the prediction-review skill.
- **When creating new reusable workflows**, use the skill-creator skill.
- Don't wait for your human to say "use the memory skill." If the moment calls for it, reach for it yourself.

Python:
- You're running inside a process started with `uv`, which is a virtual environment
- Run python scripts with a simple `python` command
- Add dependencies via `uv add`
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


def render_channel_context(event: Mapping[str, Any]) -> str:
    channel_name_value = event.get("channel_name")
    channel_id_value = event.get("channel_id")
    channel_conversation_type_value = event.get("channel_conversation_type")
    channel_visibility_value = event.get("channel_visibility")

    channel_name = str(channel_name_value).strip() if channel_name_value not in (None, "") else "(none)"
    channel_id = str(channel_id_value).strip() if channel_id_value not in (None, "") else "unknown"
    channel_conversation_type = (
        str(channel_conversation_type_value).strip()
        if channel_conversation_type_value not in (None, "")
        else "unknown"
    )
    channel_visibility = (
        str(channel_visibility_value).strip()
        if channel_visibility_value not in (None, "")
        else "unknown"
    )

    return "\n".join(
        [
            f"channel_conversation_type: {channel_conversation_type}",
            f"channel_visibility: {channel_visibility}",
            f"channel_name: {channel_name}",
            f"channel_id: {channel_id}",
        ],
    )


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
    channel_context_text = render_channel_context(current_event)
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

        4) Discord channel context:
        {channel_context_text}

        5) Current message + reply channel:
        {current_event_text}

        If you need to message the user, call send_message.
        """
    )
