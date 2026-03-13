"""Reflection — async post-send_message dissonance detection.

After each send_message, the agent's own model evaluates the outgoing message
against criteria defined in a user-editable markdown file. If dissonance is
detected, a 🪞 reaction is added to the sent message.

Design principles:
- Async side-effect (does not block message delivery)
- Not injected into conversation context (the agent doesn't see the result)
- Criteria owned by the user (editable markdown file)
- Off by default
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DISSONANCE_EMOJI = "🪞"

UTC = timezone.utc


def _read_questions_file(home: Path, questions_file: str) -> str | None:
    """Read the dissonance criteria from the questions file."""
    path = home / questions_file
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _build_reflection_prompt(message_text: str, criteria: str) -> str:
    return f"""{criteria}

---

## Message to evaluate

{message_text}

---

Call `is_dissonant` with your judgment."""


async def _run_reflection(
    message_text: str,
    criteria: str,
    model: str,
) -> dict[str, Any] | None:
    """Run a single-turn LLM evaluation of the message against criteria.

    Uses the same model the agent uses (via LangChain) to keep dependencies
    minimal. Returns the structured result or None on failure.
    """
    prompt = _build_reflection_prompt(message_text, criteria)

    tool_schema = {
        "name": "is_dissonant",
        "description": "Report whether the message shows dissonance with the agent's values",
        "input_schema": {
            "type": "object",
            "properties": {
                "yes": {
                    "type": "boolean",
                    "description": "True if a dissonance pattern was detected",
                },
                "pattern_type": {
                    "type": "string",
                    "description": "Type of pattern: service_wrapup, ball_handing, hollow_validation, stance_avoidance, other",
                },
                "suggestion": {
                    "type": "string",
                    "description": "Brief suggestion for how the message could better match the agent's values",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence 0-1 in the detection",
                },
            },
            "required": ["yes", "pattern_type", "suggestion", "confidence"],
        },
    }

    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=500,
            tools=[tool_schema],  # type: ignore[arg-type]
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "is_dissonant":
                result: dict[str, Any] = block.input  # type: ignore[assignment]
                return result

        return None
    except Exception:
        return None


class ReflectionHook:
    """Manages async reflection after send_message calls."""

    def __init__(
        self,
        *,
        home: Path,
        questions_file: str,
        model: str,
        log_fn: Callable[..., None],
        react_fn: Any,  # async callable(channel_id, message_id, emoji) -> bool
    ) -> None:
        self._home = home
        self._questions_file = questions_file
        self._model = model
        self._log_fn = log_fn
        self._react_fn = react_fn

    async def on_message_sent(
        self,
        text: str,
        channel_id: str,
        message_id: str | None,
    ) -> None:
        """Fire-and-forget reflection on a sent message."""
        if not text.strip() or not message_id:
            return

        criteria = _read_questions_file(self._home, self._questions_file)
        if not criteria:
            self._log_fn(
                "reflection_skip",
                reason="questions_file_missing_or_empty",
                questions_file=self._questions_file,
            )
            return

        asyncio.create_task(
            self._evaluate(text, channel_id, message_id, criteria),
        )

    async def _evaluate(
        self,
        text: str,
        channel_id: str,
        message_id: str,
        criteria: str,
    ) -> None:
        result = await asyncio.to_thread(
            _run_reflection_sync, text, criteria, self._model,
        )

        is_dissonant = bool(result and result.get("yes"))
        confidence = float(result.get("confidence", 0)) if result else 0.0
        pattern_type = str(result.get("pattern_type", "")) if result else ""

        self._log_fn(
            "reflection_check",
            is_dissonant=is_dissonant,
            confidence=round(confidence, 3),
            pattern_type=pattern_type,
            message_preview=text[:200],
        )

        if is_dissonant and confidence >= 0.7:
            reacted = await self._react_fn(
                channel_id=channel_id,
                message_id=message_id,
                emoji=DISSONANCE_EMOJI,
            )
            self._log_fn(
                "reflection_dissonance",
                pattern_type=pattern_type,
                confidence=round(confidence, 3),
                suggestion=str(result.get("suggestion", "")) if result else "",
                message_preview=text[:200],
                reacted=reacted,
            )


def _run_reflection_sync(
    message_text: str,
    criteria: str,
    model: str,
) -> dict[str, Any] | None:
    """Synchronous wrapper for _run_reflection (used via asyncio.to_thread)."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_reflection(message_text, criteria, model))
    finally:
        loop.close()
