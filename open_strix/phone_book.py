"""Phone book for resolving Discord user IDs and channel IDs.

Auto-populates from guild data on startup, incrementally updates as new
users are seen in messages.  Persists as ``state/phone-book.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PhoneBookEntry:
    id: str
    name: str
    kind: str  # "user" or "channel"
    is_bot: bool = False
    extra: str = ""  # e.g. channel type, roles


@dataclass
class PhoneBook:
    entries: dict[str, PhoneBookEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, entry: PhoneBookEntry) -> bool:
        """Add or update an entry.  Returns True if the book changed."""
        existing = self.entries.get(entry.id)
        if existing is not None:
            changed = False
            if existing.name != entry.name:
                existing.name = entry.name
                changed = True
            if existing.extra != entry.extra and entry.extra:
                existing.extra = entry.extra
                changed = True
            if existing.is_bot != entry.is_bot:
                existing.is_bot = entry.is_bot
                changed = True
            return changed
        self.entries[entry.id] = entry
        return True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, query: str) -> list[PhoneBookEntry]:
        """Search by name (substring, case-insensitive) or exact ID."""
        query_lower = query.lower().strip()
        # Strip mention formatting if present
        id_match = re.match(r"<[@#]!?(\d+)>", query)
        if id_match:
            query_lower = id_match.group(1)

        results: list[PhoneBookEntry] = []
        for entry in self.entries.values():
            if entry.id == query_lower:
                results.append(entry)
            elif query_lower in entry.name.lower():
                results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def render_markdown(self) -> str:
        users = sorted(
            (e for e in self.entries.values() if e.kind == "user"),
            key=lambda e: e.name.lower(),
        )
        channels = sorted(
            (e for e in self.entries.values() if e.kind == "channel"),
            key=lambda e: e.name.lower(),
        )

        lines: list[str] = ["# Phone Book", "", "Auto-generated. Updated as new users and channels are discovered.", ""]

        if users:
            lines.append("## Users")
            lines.append("")
            lines.append("| Name | ID | Mention | Bot |")
            lines.append("|------|-----|---------|-----|")
            for u in users:
                mention = f"`<@{u.id}>`"
                bot_label = "yes" if u.is_bot else ""
                lines.append(f"| {u.name} | {u.id} | {mention} | {bot_label} |")
            lines.append("")

        if channels:
            lines.append("## Channels")
            lines.append("")
            lines.append("| Name | ID | Type |")
            lines.append("|------|-----|------|")
            for c in channels:
                lines.append(f"| {c.name} | {c.id} | {c.extra} |")
            lines.append("")

        lines.append("## Usage")
        lines.append("")
        lines.append("- To mention a user in send_message: use `<@USER_ID>` (e.g. `<@123456>`)")
        lines.append("- To send to a channel: use the channel ID as the `channel_id` parameter")
        lines.append("- To look up a user or channel: use the `lookup` tool")
        lines.append("")

        return "\n".join(lines)

    @classmethod
    def parse_markdown(cls, text: str) -> PhoneBook:
        """Parse a phone-book.md back into a PhoneBook.  Best-effort."""
        book = cls()
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## Users"):
                section = "users"
                continue
            if stripped.startswith("## Channels"):
                section = "channels"
                continue
            if stripped.startswith("## "):
                section = ""
                continue
            if not stripped.startswith("|") or stripped.startswith("|--") or stripped.startswith("| Name"):
                continue

            cells = [c.strip() for c in stripped.split("|")]
            # cells[0] is empty (before first |), cells[-1] may be empty too
            cells = [c for c in cells if c]

            if section == "users" and len(cells) >= 3:
                name = cells[0]
                id_ = cells[1]
                is_bot = len(cells) >= 4 and cells[3].lower() == "yes"
                book.add(PhoneBookEntry(id=id_, name=name, kind="user", is_bot=is_bot))
            elif section == "channels" and len(cells) >= 2:
                name = cells[0]
                id_ = cells[1]
                extra = cells[2] if len(cells) >= 3 else ""
                book.add(PhoneBookEntry(id=id_, name=name, kind="channel", extra=extra))

        return book


# ------------------------------------------------------------------
# Discord integration helpers
# ------------------------------------------------------------------


def populate_from_guilds(book: PhoneBook, guilds: list[Any]) -> bool:
    """Add channels from all guilds the bot can see.  Returns True if anything changed."""
    changed = False
    for guild in guilds:
        # Channels (no special intent needed)
        for channel in getattr(guild, "channels", []):
            channel_type = str(getattr(channel, "type", "")).replace("ChannelType.", "")
            if channel_type in ("category",):
                continue
            entry = PhoneBookEntry(
                id=str(channel.id),
                name=getattr(channel, "name", str(channel.id)),
                kind="channel",
                extra=channel_type,
            )
            if book.add(entry):
                changed = True

        # Members — only cached members (requires members intent for full list)
        for member in getattr(guild, "members", []):
            entry = PhoneBookEntry(
                id=str(member.id),
                name=str(getattr(member, "display_name", getattr(member, "name", str(member.id)))),
                kind="user",
                is_bot=bool(getattr(member, "bot", False)),
            )
            if book.add(entry):
                changed = True

    return changed


def update_from_message(book: PhoneBook, author: Any) -> bool:
    """Add or update a user entry from a message author.  Returns True if changed."""
    if author is None:
        return False
    author_id = str(getattr(author, "id", "")).strip()
    if not author_id:
        return False
    name = str(getattr(author, "display_name", getattr(author, "name", author_id)))
    is_bot = bool(getattr(author, "bot", False))
    return book.add(PhoneBookEntry(id=author_id, name=name, kind="user", is_bot=is_bot))


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def load_phone_book(path: Path) -> PhoneBook:
    """Load phone book from a markdown file."""
    if not path.exists():
        return PhoneBook()
    text = path.read_text(encoding="utf-8")
    return PhoneBook.parse_markdown(text)


def save_phone_book(book: PhoneBook, path: Path) -> None:
    """Save phone book as a markdown file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(book.render_markdown(), encoding="utf-8")
