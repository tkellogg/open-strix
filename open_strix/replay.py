"""Startup hook: replay un-processed events from logs/events.jsonl.

Why this exists
---------------
The runtime keeps an in-memory `asyncio.Queue` of events (web/Discord messages,
shell-job completions, scheduler ticks). If the process crashes or is killed
while the queue is non-empty, those events are gone — they were never durably
queued anywhere, only logged to `logs/events.jsonl` *as they arrived*.

Symptom from the field: the user sends 6 messages over an evening, the agent
processes 2 and stalls. On restart the agent only sees the *next* new message
and has no idea what the user said in between. The user has to ask:
"check events.jsonl, did you get my messages?"

This module reads the recent tail of `events.jsonl` and re-enqueues every
event that does *not* appear to have triggered an `agent_invoke_start` before
the current process started.

Scope
-----
- Only events from the last ``window_seconds`` (default 600 = 10 min) are
  considered. Anything older is treated as "expired" — we are not a durable
  message broker, we are an undo for short outages.
- Only the event types the runtime can actually re-process are replayed:
  ``web_message``, ``discord_message``, ``shell_job_complete``,
  ``scheduler_tick``. Internal events (``event_queued``, ``tool_call``,
  ``file_read``, ``app_started`` …) are ignored.
- The current process's own ``app_started`` is the cutoff. Any event before
  that timestamp without a downstream ``agent_invoke_start`` in the same
  session window is a candidate for replay.
- Dedupe keys are preserved when re-enqueuing, so a replayed scheduler tick
  won't double-fire if the scheduler also re-emits it post-startup.

This is intentionally a thin, best-effort recovery layer. It is NOT a
guarantee. The right long-term fix is a durable queue (SQLite, Redis,
filesystem-backed), but that's a much bigger change.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import AgentEvent

_log = logging.getLogger(__name__)

# Event types that represent external stimuli we know how to re-process.
# Everything else in events.jsonl is internal telemetry (tool_call,
# file_read, event_queued, agent_invoke_start, app_started, etc.) and is
# either redundant or dangerous to replay.
REPLAYABLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "web_message",
        "discord_message",
        "shell_job_complete",
        "scheduler_tick",
    }
)


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string into an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_jsonl_tail(path: Path, *, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Read the tail of a JSONL file. Defaults to last ~2MB.

    Returns parsed records in file order (oldest first within the window).
    Malformed lines are silently skipped — we never want startup to abort
    because some other writer wrote a partial line.
    """
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []

    offset = max(0, size - max_bytes)
    records: list[dict[str, Any]] = []
    try:
        with path.open("rb") as fh:
            if offset > 0:
                fh.seek(offset)
                # Discard the (likely partial) first line.
                fh.readline()
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError as exc:
        _log.warning("replay: failed to read %s: %s", path, exc)
        return []
    return records


def _build_agent_event(record: dict[str, Any]) -> AgentEvent | None:
    """Reconstruct an AgentEvent from a logged event record.

    Returns None if the record cannot be reconstructed (missing required
    fields, unrecognized shape, etc.) — caller logs and skips.
    """
    event_type = record.get("type")
    if event_type not in REPLAYABLE_EVENT_TYPES:
        return None

    if event_type in {"web_message", "discord_message"}:
        content = record.get("content")
        if not isinstance(content, str) or not content:
            return None
        return AgentEvent(
            event_type=event_type,
            prompt=content,
            channel_id=record.get("channel_id"),
            channel_name=record.get("channel_name"),
            channel_conversation_type=record.get("channel_conversation_type"),
            channel_visibility=record.get("channel_visibility"),
            author=record.get("author"),
            author_id=record.get("author_id"),
            attachment_names=list(record.get("attachment_names") or []),
            source_id=record.get("source_id"),
            source_platform=record.get("source_platform"),
            dedupe_key=record.get("dedupe_key"),
        )

    if event_type == "shell_job_complete":
        job_id = record.get("job_id") or str(record.get("source_id", "")).replace(
            "shell_job:", ""
        )
        if not job_id:
            return None
        prompt = (
            f"Shell job {job_id} completed while the agent was offline "
            f"(replayed from events.jsonl). Use shell_job_output({job_id!r}) "
            f"to read stdout/stderr."
        )
        return AgentEvent(
            event_type="shell_job_complete",
            prompt=prompt,
            channel_id=record.get("channel_id"),
            channel_name=record.get("channel_name"),
            source_id=f"shell_job:{job_id}",
            dedupe_key=f"shell_job_complete:{job_id}",
        )

    if event_type == "scheduler_tick":
        # Scheduler ticks are time-bound; replaying a stale tick is almost
        # always wrong (the scheduler will fire its next regularly-scheduled
        # tick on its own). Skip by default. Left here as a marker so future
        # callers know we considered it.
        return None

    return None


def _candidate_events(
    records: list[dict[str, Any]],
    *,
    current_session_id: str,
    window_start: datetime,
) -> list[dict[str, Any]]:
    """Find replayable events in the window that were not acknowledged.

    A record is a candidate iff:
      * it has type in REPLAYABLE_EVENT_TYPES
      * its timestamp is >= window_start
      * its session_id != current_session_id (current session events are
        either already-processed or about to be processed normally)
      * no agent_invoke_start in the same session, with matching
        source_event_type and channel_id, occurs at-or-after the candidate.

    The last condition is conservative: we re-enqueue only if we can't find
    *any* downstream invoke that plausibly handled the event. Duplicates are
    annoying but tractable; missed messages are not.
    """
    candidates: list[dict[str, Any]] = []
    invokes: dict[tuple[str, str, str], list[datetime]] = {}
    for rec in records:
        if rec.get("type") != "agent_invoke_start":
            continue
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        key = (
            str(rec.get("session_id") or ""),
            str(rec.get("source_event_type") or ""),
            str(rec.get("channel_id") or ""),
        )
        invokes.setdefault(key, []).append(ts)

    for rec in records:
        evt_type = rec.get("type")
        if evt_type not in REPLAYABLE_EVENT_TYPES:
            continue
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None or ts < window_start:
            continue
        session_id = str(rec.get("session_id") or "")
        if not session_id or session_id == current_session_id:
            continue
        key = (session_id, str(evt_type), str(rec.get("channel_id") or ""))
        invoke_ts_list = invokes.get(key, [])
        if any(t >= ts for t in invoke_ts_list):
            continue
        candidates.append(rec)
    return candidates


async def replay_unprocessed_events(
    *,
    events_log_path: Path,
    current_session_id: str,
    enqueue_event,
    window_seconds: int = 600,
    now: datetime | None = None,
    log_event=None,
) -> int:
    """Replay un-acknowledged events from ``events_log_path``.

    Parameters
    ----------
    events_log_path
        Path to ``logs/events.jsonl``.
    current_session_id
        The session id of the *current* (post-restart) process. Events with
        this session id are skipped (they belong to the live process).
    enqueue_event
        Async callable ``enqueue_event(AgentEvent) -> None`` — typically
        ``Strix.enqueue_event``.
    window_seconds
        Look-back window. Events older than ``now - window_seconds`` are
        treated as expired and not replayed. Default 600 (10 minutes).
    now
        Override for the current time (testing).
    log_event
        Optional structured logger (e.g. ``Strix.log_event``). Called with
        event_type and **kwargs.

    Returns
    -------
    int
        Number of events re-enqueued.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)

    records = _read_jsonl_tail(Path(events_log_path))
    candidates = _candidate_events(
        records,
        current_session_id=current_session_id,
        window_start=window_start,
    )

    if log_event is not None:
        log_event(
            "replay_scan",
            records_scanned=len(records),
            candidates_found=len(candidates),
            window_seconds=window_seconds,
        )

    replayed = 0
    for rec in candidates:
        event = _build_agent_event(rec)
        if event is None:
            if log_event is not None:
                log_event(
                    "replay_skipped",
                    reason="unbuildable",
                    source_event_type=rec.get("type"),
                    source_id=rec.get("source_id"),
                )
            continue
        try:
            await enqueue_event(event)
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("replay: failed to enqueue %s: %s", rec.get("source_id"), exc)
            if log_event is not None:
                log_event(
                    "replay_enqueue_failed",
                    source_event_type=rec.get("type"),
                    source_id=rec.get("source_id"),
                    error=str(exc),
                )
            continue
        replayed += 1
        if log_event is not None:
            log_event(
                "replay_enqueued",
                source_event_type=event.event_type,
                channel_id=event.channel_id,
                source_id=event.source_id,
                original_timestamp=rec.get("timestamp"),
            )
    return replayed
