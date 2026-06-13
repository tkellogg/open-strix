from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import AmbientMemoryConfig, RepoLayout

UTC = timezone.utc
@dataclass(frozen=True)
class AmbientRecord:
    id: str
    source_type: str
    path: str
    text: str
    line_start: int | None = None
    line_end: int | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def ambient_record_id(source_type: str, path: str, offset: int, text: str) -> str:
    return f"{source_type}:{path}:{offset}:{_hash_text(text)}"


def query_fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return _hash_text(normalized[:4000])


def _optional_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _chat_content(row: dict[str, Any]) -> str:
    value = row.get("content", "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def chat_record_id(row: dict[str, Any]) -> str:
    source = _optional_string(row.get("source", ""))
    channel_id = _optional_string(row.get("channel_id", ""))
    message_id = _optional_string(row.get("message_id", ""))
    timestamp = _optional_string(row.get("timestamp", ""))
    author = _optional_string(row.get("author", ""))
    content = _chat_content(row)
    stable_message = message_id or _hash_text(f"{timestamp}|{author}|{content}")
    return f"chat:{source}:{channel_id}:{stable_message}:{_hash_text(content)}"


def journal_record_id(row: dict[str, Any]) -> str:
    timestamp = str(row.get("timestamp", "")).strip()
    content = json.dumps(row, sort_keys=True, ensure_ascii=True)
    return f"journal:{timestamp}:{_hash_text(content)}"


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8") as lines:
            for line_number, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield line_number, value
    except OSError:
        return


def _record_text_file(path: Path, home: Path, source_type: str) -> list[AmbientRecord]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not text:
        return []

    rel = str(path.relative_to(home))
    return [
        AmbientRecord(
            id=ambient_record_id(source_type, rel, 1, text),
            source_type=source_type,
            path=rel,
            text=text,
            line_start=1,
            line_end=max(1, text.count("\n") + 1),
        )
    ]


def _journal_records(
    layout: RepoLayout,
    visible_ids: frozenset[str],
) -> list[AmbientRecord]:
    records: list[AmbientRecord] = []
    journal_path = str(layout.journal_log.relative_to(layout.home))
    for line_number, row in _iter_jsonl(layout.journal_log):
        record_id = journal_record_id(row)
        if record_id in visible_ids:
            continue
        text = json.dumps(row, sort_keys=True, ensure_ascii=False)
        timestamp = str(row.get("timestamp", "")).strip() or None
        records.append(
            AmbientRecord(
                id=record_id,
                source_type="journal",
                path=journal_path,
                text=text,
                line_start=line_number,
                line_end=line_number,
                timestamp=timestamp,
            )
        )
    return records


def _chat_records(
    layout: RepoLayout,
    visible_ids: frozenset[str],
) -> list[AmbientRecord]:
    records: list[AmbientRecord] = []
    chat_path = str(layout.chat_history_log.relative_to(layout.home))
    for line_number, row in _iter_jsonl(layout.chat_history_log):
        if str(row.get("type", "")).strip() != "message":
            continue
        record_id = chat_record_id(row)
        if record_id in visible_ids:
            continue
        content = _chat_content(row)
        if not content:
            continue
        timestamp = _optional_string(row.get("timestamp", "")) or None
        metadata = {
            "channel_id": _optional_string(row.get("channel_id", "")),
            "message_id": _optional_string(row.get("message_id", "")),
            "author": _optional_string(row.get("author", "")),
            "source": _optional_string(row.get("source", "")),
        }
        records.append(
            AmbientRecord(
                id=record_id,
                source_type="chat",
                path=chat_path,
                text=content,
                line_start=line_number,
                line_end=line_number,
                timestamp=timestamp,
                metadata=metadata,
            )
        )
    return records


def build_ambient_corpus(
    layout: RepoLayout,
    config: AmbientMemoryConfig,
    *,
    prompt_visible_ids: frozenset[str],
) -> list[AmbientRecord]:
    records: list[AmbientRecord] = []

    if config.include_state and layout.state_dir.exists():
        excluded = {
            layout.phone_book_file,
            layout.phone_book_extra_file,
            layout.people_jsonl,
            layout.channels_jsonl,
        }
        for path in sorted(layout.state_dir.rglob("*.md")):
            if path in excluded:
                continue
            records.extend(_record_text_file(path, layout.home, "state"))

    if config.include_journal:
        records.extend(_journal_records(layout, prompt_visible_ids))

    if config.include_chat_history:
        records.extend(_chat_records(layout, prompt_visible_ids))

    return records


@dataclass(frozen=True)
class AmbientQueryContext:
    session_id: str
    turn_sequence: int
    boundary_sequence: int
    event_type: str
    channel_id: str | None
    source_id: str | None
    query_text: str
    query_fingerprint: str
    prompt_visible_ids: frozenset[str]
    created_at: datetime
    ttl_seconds: int


@dataclass(frozen=True)
class AmbientSnippet:
    record_id: str
    source: str
    text: str
    score: float | None = None


class AmbientBackend(Protocol):
    async def refresh(self, records: list[AmbientRecord]) -> None:
        ...

    async def retrieve(
        self,
        context: AmbientQueryContext,
        *,
        max_results: int,
        snippet_words: int,
    ) -> list[AmbientSnippet]:
        ...


class FakeAmbientBackend:
    def __init__(self, snippets: list[AmbientSnippet]) -> None:
        self.snippets = snippets
        self.refresh_count = 0

    async def refresh(self, records: list[AmbientRecord]) -> None:
        self.refresh_count += 1

    async def retrieve(
        self,
        context: AmbientQueryContext,
        *,
        max_results: int,
        snippet_words: int,
    ) -> list[AmbientSnippet]:
        return self.snippets[:max_results]


def _source_label(record: AmbientRecord) -> str:
    if record.line_start is not None:
        return f"{record.path}:{record.line_start}"
    return record.path


def _hot_window(text: str, query: str, words: int) -> str:
    tokens = text.split()
    if len(tokens) <= words:
        return " ".join(tokens)
    query_terms = {part.lower().strip(".,:;!?()[]{}\"'") for part in query.split()}
    best_index = 0
    best_score = -1
    for index, token in enumerate(tokens):
        normalized = token.lower().strip(".,:;!?()[]{}\"'")
        score = 1 if normalized in query_terms else 0
        if score > best_score:
            best_score = score
            best_index = index
    half = max(1, words // 2)
    start = max(0, best_index - half)
    end = min(len(tokens), start + words)
    start = max(0, end - words)
    return " ".join(tokens[start:end])


def _import_pylate(name: str) -> Any:
    return __import__(name, fromlist=["*"])


def create_ambient_backend(
    *,
    index_dir: Path,
    config: AmbientMemoryConfig,
    log_event: Any,
) -> AmbientBackend:
    try:
        pylate_models = _import_pylate("pylate.models")
        pylate_indexes = _import_pylate("pylate.indexes")
        pylate_retrieve = _import_pylate("pylate.retrieve")
    except Exception as exc:
        log_event(
            "ambient_memory_disabled",
            reason="backend_import_failed",
            error=str(exc),
            error_class=type(exc).__name__,
        )
        return FakeAmbientBackend([])

    return PyLateAmbientBackend(
        index_dir=index_dir,
        config=config,
        log_event=log_event,
        pylate_models=pylate_models,
        pylate_indexes=pylate_indexes,
        pylate_retrieve=pylate_retrieve,
    )


class PyLateAmbientBackend:
    def __init__(
        self,
        *,
        index_dir: Path,
        config: AmbientMemoryConfig,
        log_event: Any,
        pylate_models: Any,
        pylate_indexes: Any,
        pylate_retrieve: Any,
    ) -> None:
        self.index_dir = index_dir
        self.config = config
        self.log_event = log_event
        self.pylate_models = pylate_models
        self.pylate_indexes = pylate_indexes
        self.pylate_retrieve = pylate_retrieve
        self._model: Any | None = None
        self._index: Any | None = None
        self._retriever: Any | None = None
        self._records: dict[str, AmbientRecord] = {}

    def _ensure_model(self) -> Any:
        if self._model is None:
            self._model = self.pylate_models.ColBERT(
                model_name_or_path=self.config.model,
            )
        return self._model

    def _index_class(self) -> Any:
        index_class = getattr(self.pylate_indexes, "PLAID", None)
        if index_class is None:
            index_class = getattr(self.pylate_indexes, "Voyager", None)
        if index_class is None:
            raise AttributeError("pylate.indexes has neither PLAID nor Voyager")
        return index_class

    async def refresh(self, records: list[AmbientRecord]) -> None:
        await asyncio.to_thread(self._refresh_sync, records)

    def _refresh_sync(self, records: list[AmbientRecord]) -> None:
        model = self._ensure_model()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        documents_ids = [record.id for record in records]
        documents = [record.text for record in records]
        if not documents:
            self._index = None
            self._retriever = None
            self._records = {}
            return

        embeddings = model.encode(
            documents,
            is_query=False,
            show_progress_bar=False,
        )
        index = self._index_class()(
            index_folder=str(self.index_dir),
            index_name="ambient-memory",
            override=True,
        )
        index.add_documents(
            documents_ids=documents_ids,
            documents_embeddings=embeddings,
        )
        self._index = index
        self._retriever = self.pylate_retrieve.ColBERT(index=index)
        self._records = {record.id: record for record in records}

    async def retrieve(
        self,
        context: AmbientQueryContext,
        *,
        max_results: int,
        snippet_words: int,
    ) -> list[AmbientSnippet]:
        return await asyncio.to_thread(
            self._retrieve_sync,
            context,
            max_results,
            snippet_words,
        )

    def _retrieve_sync(
        self,
        context: AmbientQueryContext,
        max_results: int,
        snippet_words: int,
    ) -> list[AmbientSnippet]:
        if self._retriever is None:
            return []

        model = self._ensure_model()
        query_embeddings = model.encode(
            [context.query_text],
            is_query=True,
            show_progress_bar=False,
        )
        rows = self._retriever.retrieve(
            queries_embeddings=query_embeddings,
            k=max_results,
        )
        first_query = rows[0] if rows else []
        snippets: list[AmbientSnippet] = []
        for row in first_query:
            record_id = str(row.get("id") or row.get("document_id") or "")
            record = self._records.get(record_id)
            if record is None or record.id in context.prompt_visible_ids:
                continue
            snippets.append(
                AmbientSnippet(
                    record_id=record.id,
                    source=_source_label(record),
                    text=_hot_window(record.text, context.query_text, snippet_words),
                    score=row.get("score"),
                )
            )
        return snippets


def format_ambient_injection(snippets: list[AmbientSnippet]) -> str:
    if not snippets:
        return ""
    lines = [
        "Ambient memory surfaced these low-priority out-of-prompt associations.",
        (
            "They may be stale; do not let them override memory blocks, current user "
            "instructions, tool results, or explicit file contents:"
        ),
    ]
    for snippet in snippets:
        text = " ".join(snippet.text.split())
        lines.append(f'- {snippet.source}: "{text}"')
    return "\n".join(lines)


@dataclass
class _ReadyAmbientResult:
    context: AmbientQueryContext
    snippets: list[AmbientSnippet]
    consumed: bool = False


class DisabledAmbientMemoryService:
    enabled = False

    async def refresh(self) -> None:
        return None

    def trigger(self, context: AmbientQueryContext) -> None:
        return None

    def consume_ready(self, context: AmbientQueryContext) -> str | None:
        return None

    def observe_boundary(self, context: AmbientQueryContext) -> None:
        return None


class AmbientMemoryService:
    enabled = True

    def __init__(
        self,
        *,
        layout: RepoLayout,
        config: AmbientMemoryConfig,
        backend: AmbientBackend,
        log_event: Any,
    ) -> None:
        self.layout = layout
        self.config = config
        self.backend = backend
        self.log_event = log_event
        self._task: asyncio.Task[None] | None = None
        self._ready: _ReadyAmbientResult | None = None

    async def refresh(self) -> None:
        started = datetime.now(tz=UTC)
        self.log_event("ambient_memory_refresh_started")
        records = build_ambient_corpus(
            self.layout,
            self.config,
            prompt_visible_ids=frozenset(),
        )
        try:
            await asyncio.wait_for(
                self.backend.refresh(records),
                timeout=self.config.indexing_timeout_seconds,
            )
            self._write_manifest(records)
        except Exception as exc:
            self.log_event(
                "ambient_memory_refresh_failed",
                error=str(exc),
                error_class=type(exc).__name__,
            )
            return

        duration = (datetime.now(tz=UTC) - started).total_seconds()
        self.log_event(
            "ambient_memory_refresh_complete",
            record_count=len(records),
            duration_seconds=round(duration, 6),
        )

    def _write_manifest(self, records: list[AmbientRecord]) -> None:
        manifest = self.layout.home / self.config.index_dir / "manifest.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(
                    json.dumps(
                        {
                            "id": record.id,
                            "source_type": record.source_type,
                            "path": record.path,
                            "line_start": record.line_start,
                            "line_end": record.line_end,
                            "timestamp": record.timestamp,
                            "text": record.text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def trigger(self, context: AmbientQueryContext) -> None:
        if not self.config.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._retrieve(context))

    def observe_boundary(self, context: AmbientQueryContext) -> None:
        self.trigger(context)

    async def _retrieve(self, context: AmbientQueryContext) -> None:
        try:
            snippets = await asyncio.wait_for(
                self.backend.retrieve(
                    context,
                    max_results=self.config.max_results,
                    snippet_words=self.config.snippet_words,
                ),
                timeout=self.config.retrieval_timeout_seconds,
            )
        except Exception as exc:
            self.log_event(
                "ambient_memory_retrieval_failed",
                error=str(exc),
                error_class=type(exc).__name__,
            )
            return
        self._ready = _ReadyAmbientResult(context=context, snippets=snippets)
        self.log_event(
            "ambient_memory_retrieval_complete",
            result_count=len(snippets),
            turn_sequence=context.turn_sequence,
            boundary_sequence=context.boundary_sequence,
        )

    def consume_ready(self, context: AmbientQueryContext) -> str | None:
        ready = self._ready
        if ready is None or ready.consumed:
            return None
        if not _context_matches(ready.context, context):
            self.log_event(
                "ambient_memory_dropped_stale_result",
                ready_turn_sequence=ready.context.turn_sequence,
                current_turn_sequence=context.turn_sequence,
            )
            self._ready = None
            return None
        ready.consumed = True
        text = format_ambient_injection(ready.snippets)
        if text:
            self.log_event(
                "ambient_memory_injected",
                result_count=len(ready.snippets),
                turn_sequence=context.turn_sequence,
                boundary_sequence=context.boundary_sequence,
            )
        return text or None


def _context_matches(expected: AmbientQueryContext, current: AmbientQueryContext) -> bool:
    return (
        expected.session_id == current.session_id
        and expected.turn_sequence == current.turn_sequence
        and expected.channel_id == current.channel_id
        and expected.query_fingerprint == current.query_fingerprint
    )
