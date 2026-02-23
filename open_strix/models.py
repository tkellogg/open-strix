from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentEvent:
    event_type: str
    prompt: str
    channel_id: str | None = None
    channel_name: str | None = None
    channel_conversation_type: str | None = None
    channel_visibility: str | None = None
    author: str | None = None
    author_id: str | None = None
    attachment_names: list[str] = field(default_factory=list)
    scheduler_name: str | None = None
    dedupe_key: str | None = None
    source_id: str | None = None
