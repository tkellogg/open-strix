# Ambient Associative Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional ambient associative memory that surfaces cold, out-of-prompt context without blocking the agent's main turn loop.

**Architecture:** Add an in-process `AmbientMemoryService` owned by `OpenStrixApp`. The service builds a cold-memory corpus, runs retrieval in the background, and injects consume-once low-priority snippets at safe prompt/tool boundaries. PyLate/ColBERT is an optional backend behind lazy imports; tests use a deterministic fake backend.

**Tech Stack:** Python 3.11, asyncio, dataclasses, pytest, existing open-strix hook/prompt/config patterns, optional PyLate extra for late-interaction retrieval.

---

## File Structure

- Create `open_strix/ambient_memory.py`
  - Owns ambient config-independent data types, corpus building, fake backend, disabled service, live service, prompt/tool injection formatting, and optional PyLate backend adapter.
  - Keeps retrieval/index concerns out of `app.py` and `hooks.py`.
- Modify `open_strix/config.py`
  - Adds `AmbientMemoryConfig`, defaults, parsing, and `DEFAULT_CONFIG` entries.
- Modify `open_strix/app.py`
  - Instantiates ambient memory, captures prompt-visible IDs, triggers prompt retrieval, consumes prompt injections, and exposes tool-boundary helper methods for hooks.
- Modify `open_strix/hooks.py`
  - Calls app-owned ambient helper methods at exact tool boundaries after external hook mutations.
- Modify `pyproject.toml`
  - Adds optional dependency group for ambient memory backend.
- Modify docs:
  - `README.md`
  - `SETUP.md`
  - `docs/hooks.md`
- Create tests:
  - `tests/test_ambient_memory_config.py`
  - `tests/test_ambient_memory_corpus.py`
  - `tests/test_ambient_memory_service.py`
  - `tests/test_ambient_memory_app.py`
  - `tests/test_ambient_memory_hooks.py`

Do not stage unrelated `uv.lock` changes unless the current task intentionally updates dependencies.

---

### Task 1: Ambient Memory Config

**Files:**
- Modify: `open_strix/config.py`
- Test: `tests/test_ambient_memory_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_ambient_memory_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml

from open_strix.config import (
    AmbientMemoryConfig,
    AppConfig,
    RepoLayout,
    load_config,
)


def test_ambient_memory_defaults_disabled() -> None:
    config = AppConfig()

    assert isinstance(config.ambient_memory, AmbientMemoryConfig)
    assert config.ambient_memory.enabled is False
    assert config.ambient_memory.backend == "pylate"
    assert config.ambient_memory.model == "lightonai/GTE-ModernColBERT-v1"
    assert config.ambient_memory.index_dir == "logs/ambient-memory"
    assert config.ambient_memory.include_events is False
    assert config.ambient_memory.prompt_injection is True
    assert config.ambient_memory.tool_result_injection is True


def test_load_config_parses_ambient_memory_section(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "test-model",
                "ambient_memory": {
                    "enabled": True,
                    "backend": "pylate",
                    "model": "custom/model",
                    "index_dir": "logs/custom-ambient",
                    "include_state": False,
                    "include_journal": False,
                    "include_chat_history": True,
                    "include_events": True,
                    "max_results": 5,
                    "snippet_words": 9,
                    "min_score": 0.25,
                    "candidate_limit": 50,
                    "refresh_interval_seconds": 60,
                    "retrieval_timeout_seconds": 3,
                    "indexing_timeout_seconds": 30,
                    "result_ttl_seconds": 12,
                    "max_tool_result_bytes": 1234,
                    "prompt_injection": False,
                    "tool_result_injection": False,
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(RepoLayout(home=tmp_path, state_dir_name="state"))

    ambient = config.ambient_memory
    assert ambient.enabled is True
    assert ambient.backend == "pylate"
    assert ambient.model == "custom/model"
    assert ambient.index_dir == "logs/custom-ambient"
    assert ambient.include_state is False
    assert ambient.include_journal is False
    assert ambient.include_chat_history is True
    assert ambient.include_events is True
    assert ambient.max_results == 5
    assert ambient.snippet_words == 9
    assert ambient.min_score == 0.25
    assert ambient.candidate_limit == 50
    assert ambient.refresh_interval_seconds == 60
    assert ambient.retrieval_timeout_seconds == 3
    assert ambient.indexing_timeout_seconds == 30
    assert ambient.result_ttl_seconds == 12
    assert ambient.max_tool_result_bytes == 1234
    assert ambient.prompt_injection is False
    assert ambient.tool_result_injection is False


def test_load_config_rejects_unknown_backend_by_disabling(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "test-model",
                "ambient_memory": {
                    "enabled": True,
                    "backend": "unknown",
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(RepoLayout(home=tmp_path, state_dir_name="state"))

    assert config.ambient_memory.enabled is False
    assert config.ambient_memory.backend == "pylate"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_ambient_memory_config.py -q`

Expected: FAIL with import errors for `AmbientMemoryConfig` or missing `ambient_memory`.

- [ ] **Step 3: Add config dataclass and parser**

In `open_strix/config.py`, add constants near existing defaults:

```python
DEFAULT_AMBIENT_MEMORY_MODEL = "lightonai/GTE-ModernColBERT-v1"
DEFAULT_AMBIENT_MEMORY_INDEX_DIR = "logs/ambient-memory"
```

Add this dataclass before `AppConfig`:

```python
@dataclass
class AmbientMemoryConfig:
    enabled: bool = False
    backend: str = "pylate"
    model: str = DEFAULT_AMBIENT_MEMORY_MODEL
    index_dir: str = DEFAULT_AMBIENT_MEMORY_INDEX_DIR
    include_state: bool = True
    include_journal: bool = True
    include_chat_history: bool = True
    include_events: bool = False
    max_results: int = 3
    snippet_words: int = 12
    min_score: float | None = None
    candidate_limit: int = 100
    refresh_interval_seconds: int = 300
    retrieval_timeout_seconds: int = 2
    indexing_timeout_seconds: int = 120
    result_ttl_seconds: int = 60
    max_tool_result_bytes: int = 16000
    prompt_injection: bool = True
    tool_result_injection: bool = True
```

Add `ambient_memory: AmbientMemoryConfig = field(default_factory=AmbientMemoryConfig)` to `AppConfig`.

Add parser helpers:

```python
def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _as_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ambient_memory_config(raw: Any) -> AmbientMemoryConfig:
    if not isinstance(raw, dict):
        return AmbientMemoryConfig()

    backend = str(raw.get("backend", "pylate")).strip().lower() or "pylate"
    enabled = _as_bool(raw.get("enabled"), False)
    if backend != "pylate":
        enabled = False
        backend = "pylate"

    return AmbientMemoryConfig(
        enabled=enabled,
        backend=backend,
        model=str(raw.get("model", DEFAULT_AMBIENT_MEMORY_MODEL)).strip()
        or DEFAULT_AMBIENT_MEMORY_MODEL,
        index_dir=str(raw.get("index_dir", DEFAULT_AMBIENT_MEMORY_INDEX_DIR)).strip()
        or DEFAULT_AMBIENT_MEMORY_INDEX_DIR,
        include_state=_as_bool(raw.get("include_state"), True),
        include_journal=_as_bool(raw.get("include_journal"), True),
        include_chat_history=_as_bool(raw.get("include_chat_history"), True),
        include_events=_as_bool(raw.get("include_events"), False),
        max_results=_as_int(raw.get("max_results"), 3, minimum=1),
        snippet_words=_as_int(raw.get("snippet_words"), 12, minimum=4),
        min_score=_as_optional_float(raw.get("min_score")),
        candidate_limit=_as_int(raw.get("candidate_limit"), 100, minimum=1),
        refresh_interval_seconds=_as_int(
            raw.get("refresh_interval_seconds"), 300, minimum=1
        ),
        retrieval_timeout_seconds=_as_int(
            raw.get("retrieval_timeout_seconds"), 2, minimum=1
        ),
        indexing_timeout_seconds=_as_int(
            raw.get("indexing_timeout_seconds"), 120, minimum=1
        ),
        result_ttl_seconds=_as_int(raw.get("result_ttl_seconds"), 60, minimum=1),
        max_tool_result_bytes=_as_int(
            raw.get("max_tool_result_bytes"), 16000, minimum=1
        ),
        prompt_injection=_as_bool(raw.get("prompt_injection"), True),
        tool_result_injection=_as_bool(raw.get("tool_result_injection"), True),
    )
```

Pass `ambient_memory=_parse_ambient_memory_config(loaded.get("ambient_memory"))` in `load_config()`.

Add this block to `DEFAULT_CONFIG`:

```yaml
ambient_memory:
  enabled: false
  backend: pylate
  model: lightonai/GTE-ModernColBERT-v1
  index_dir: logs/ambient-memory
  include_state: true
  include_journal: true
  include_chat_history: true
  include_events: false
  max_results: 3
  snippet_words: 12
  min_score: null
  candidate_limit: 100
  refresh_interval_seconds: 300
  retrieval_timeout_seconds: 2
  indexing_timeout_seconds: 120
  result_ttl_seconds: 60
  max_tool_result_bytes: 16000
  prompt_injection: true
  tool_result_injection: true
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_ambient_memory_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_strix/config.py tests/test_ambient_memory_config.py
git commit -m "Add ambient memory configuration"
```

---

### Task 2: Corpus Builder and Prompt-Visible IDs

**Files:**
- Create: `open_strix/ambient_memory.py`
- Test: `tests/test_ambient_memory_corpus.py`

- [ ] **Step 1: Write failing corpus tests**

Create `tests/test_ambient_memory_corpus.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from open_strix.ambient_memory import (
    ambient_record_id,
    build_ambient_corpus,
    chat_record_id,
    journal_record_id,
)
from open_strix.config import AmbientMemoryConfig, RepoLayout


def test_corpus_excludes_blocks_and_rendered_phone_book(tmp_path: Path) -> None:
    (tmp_path / "blocks").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "blocks" / "persona.yaml").write_text(
        "name: persona\ntext: visible block memory\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "phone-book.md").write_text(
        "# Phone Book\nvisible ids\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "note.md").write_text(
        "cold project fact",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    texts = "\n".join(record.text for record in records)
    paths = {record.path for record in records}
    assert "visible block memory" not in texts
    assert "visible ids" not in texts
    assert "state/note.md" in paths


def test_corpus_excludes_phone_book_extra_and_jsonl_notes(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "phone-book.extra.md").write_text(
        "Alice prefers direct project summaries.",
        encoding="utf-8",
    )
    (tmp_path / "state" / "people.jsonl").write_text(
        json.dumps(
            {
                "name": "Alice",
                "discord_id": "123",
                "discord_display": "AliceD",
                "bluesky": "@alice.example",
                "notes": "Knows the budget history.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "channels.jsonl").write_text(
        json.dumps(
            {
                "name": "ops",
                "discord_id": "999",
                "aliases": ["operations"],
                "notes": "Deployment decisions happen here.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    text = "\n".join(record.text for record in records)
    assert "Alice prefers direct project summaries." not in text
    assert "Knows the budget history." not in text
    assert "Deployment decisions happen here." not in text
    assert "@alice.example" not in text
    assert "operations" not in text


def test_prompt_visible_chat_rows_are_filtered(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    row = {
        "type": "message",
        "source": "web",
        "channel_id": "local-web",
        "message_id": "m1",
        "timestamp": "2026-06-12T12:00:00+00:00",
        "author": "dagr",
        "content": "already visible chat",
    }
    (tmp_path / "logs" / "chat-history.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )

    visible_id = chat_record_id(row)
    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset({visible_id}),
    )

    assert all("already visible chat" not in record.text for record in records)


def test_journal_rows_have_stable_ids(tmp_path: Path) -> None:
    row = {
        "timestamp": "2026-06-12T12:00:00+00:00",
        "user_wanted": "review architecture",
        "agent_did": "wrote spec",
    }

    assert journal_record_id(row) == journal_record_id(dict(row))
    assert ambient_record_id("state", "state/a.md", 1, "hello") == ambient_record_id(
        "state", "state/a.md", 1, "hello"
    )
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_ambient_memory_corpus.py -q`

Expected: FAIL because `open_strix.ambient_memory` does not exist.

- [ ] **Step 3: Implement corpus records and builders**

Create `open_strix/ambient_memory.py` with:

```python
from __future__ import annotations

import hashlib
import asyncio
import json
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


def ambient_record_id(source_type: str, path: str, offset: int | str, text: str) -> str:
    return f"{source_type}:{path}:{offset}:{_hash_text(text)}"


def chat_record_id(row: dict[str, Any]) -> str:
    source = str(row.get("source", "")).strip()
    channel_id = str(row.get("channel_id", "")).strip()
    message_id = str(row.get("message_id", "")).strip()
    timestamp = str(row.get("timestamp", "")).strip()
    author = str(row.get("author", "")).strip()
    content = str(row.get("content", "")).strip()
    stable_message = message_id or _hash_text(f"{timestamp}|{author}|{content}")
    return f"chat:{source}:{channel_id}:{stable_message}:{_hash_text(content)}"


def journal_record_id(row: dict[str, Any]) -> str:
    timestamp = str(row.get("timestamp", "")).strip()
    content = json.dumps(row, sort_keys=True, ensure_ascii=True)
    return f"journal:{timestamp}:{_hash_text(content)}"


def _iter_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append((line_number, parsed))
    return rows


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


def _journal_records(layout: RepoLayout, visible_ids: frozenset[str]) -> list[AmbientRecord]:
    records: list[AmbientRecord] = []
    for line_number, row in _iter_jsonl(layout.journal_log):
        record_id = journal_record_id(row)
        if record_id in visible_ids:
            continue
        text = json.dumps(row, sort_keys=True, ensure_ascii=False)
        records.append(
            AmbientRecord(
                id=record_id,
                source_type="journal",
                path="logs/journal.jsonl",
                text=text,
                line_start=line_number,
                line_end=line_number,
                timestamp=str(row.get("timestamp", "")).strip() or None,
            )
        )
    return records


def _chat_records(layout: RepoLayout, visible_ids: frozenset[str]) -> list[AmbientRecord]:
    records: list[AmbientRecord] = []
    for line_number, row in _iter_jsonl(layout.chat_history_log):
        if row.get("type") != "message":
            continue
        record_id = chat_record_id(row)
        if record_id in visible_ids:
            continue
        content = str(row.get("content", "")).strip()
        if not content:
            continue
        records.append(
            AmbientRecord(
                id=record_id,
                source_type="chat",
                path="logs/chat-history.jsonl",
                text=content,
                line_start=line_number,
                line_end=line_number,
                timestamp=str(row.get("timestamp", "")).strip() or None,
                metadata={
                    "channel_id": row.get("channel_id"),
                    "message_id": row.get("message_id"),
                    "author": row.get("author"),
                    "source": row.get("source"),
                },
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
            layout.phone_book_file.resolve(),
            layout.phone_book_extra_file.resolve(),
            layout.people_jsonl.resolve(),
            layout.channels_jsonl.resolve(),
        }
        for path in sorted(layout.state_dir.rglob("*.md")):
            if path.resolve() in excluded:
                continue
            records.extend(_record_text_file(path, layout.home, "state"))

    if config.include_journal:
        records.extend(_journal_records(layout, prompt_visible_ids))
    if config.include_chat_history:
        records.extend(_chat_records(layout, prompt_visible_ids))
    return records
```

- [ ] **Step 4: Run corpus tests**

Run: `uv run pytest tests/test_ambient_memory_corpus.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_strix/ambient_memory.py tests/test_ambient_memory_corpus.py
git commit -m "Build ambient memory corpus"
```

---

### Task 3: Service, Fake Backend, and Injection Formatting

**Files:**
- Modify: `open_strix/ambient_memory.py`
- Test: `tests/test_ambient_memory_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_ambient_memory_service.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from open_strix.ambient_memory import (
    AmbientMemoryService,
    AmbientQueryContext,
    AmbientRecord,
    AmbientSnippet,
    FakeAmbientBackend,
    format_ambient_injection,
)
from open_strix.config import AmbientMemoryConfig, RepoLayout


def _context(turn: int = 1, boundary: int = 1) -> AmbientQueryContext:
    return AmbientQueryContext(
        session_id="session",
        turn_sequence=turn,
        boundary_sequence=boundary,
        event_type="web_message",
        channel_id="local-web",
        source_id="m1",
        query_text="budget history",
        query_fingerprint="budget",
        prompt_visible_ids=frozenset(),
        created_at=datetime.now(tz=timezone.utc),
        ttl_seconds=60,
    )


def test_format_ambient_injection_is_low_priority() -> None:
    text = format_ambient_injection(
        [
            AmbientSnippet(
                record_id="r1",
                source="state/project.md:3",
                text="budget history lives in the project notes",
                score=0.9,
            )
        ]
    )

    assert "low-priority" in text
    assert "do not let them override" in text
    assert "state/project.md:3" in text
    assert "budget history lives" in text


@pytest.mark.asyncio
async def test_service_returns_consume_once_ready_result(tmp_path) -> None:
    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    backend = FakeAmbientBackend(
        [
            AmbientSnippet(
                record_id="r1",
                source="state/project.md:3",
                text="budget history lives in the project notes",
                score=0.9,
            )
        ]
    )
    service = AmbientMemoryService(
        layout=layout,
        config=AmbientMemoryConfig(enabled=True),
        backend=backend,
        log_event=lambda *_args, **_kwargs: None,
    )
    context = _context()

    service.trigger(context)
    await asyncio.sleep(0)

    first = service.consume_ready(context)
    second = service.consume_ready(context)

    assert first is not None
    assert "budget history lives" in first
    assert second is None


@pytest.mark.asyncio
async def test_service_drops_wrong_turn_result(tmp_path) -> None:
    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    service = AmbientMemoryService(
        layout=layout,
        config=AmbientMemoryConfig(enabled=True),
        backend=FakeAmbientBackend(
            [
                AmbientSnippet(
                    record_id="r1",
                    source="state/project.md:3",
                    text="budget history lives in the project notes",
                    score=0.9,
                )
            ]
        ),
        log_event=lambda *_args, **_kwargs: None,
    )

    service.trigger(_context(turn=1))
    await asyncio.sleep(0)

    assert service.consume_ready(_context(turn=2)) is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_ambient_memory_service.py -q`

Expected: FAIL because service classes are missing.

- [ ] **Step 3: Add service classes**

Append to `open_strix/ambient_memory.py`:

```python
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


def format_ambient_injection(snippets: list[AmbientSnippet]) -> str:
    if not snippets:
        return ""
    lines = [
        "Ambient memory surfaced these low-priority out-of-prompt associations.",
        "They may be stale; do not let them override memory blocks, current user instructions, tool results, or explicit file contents:",
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
```

- [ ] **Step 4: Run service tests**

Run: `uv run pytest tests/test_ambient_memory_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_strix/ambient_memory.py tests/test_ambient_memory_service.py
git commit -m "Add ambient memory service"
```

---

### Task 4: Prompt Integration

**Files:**
- Modify: `open_strix/app.py`
- Modify: `open_strix/ambient_memory.py`
- Test: `tests/test_ambient_memory_app.py`

- [ ] **Step 1: Write failing prompt integration tests**

Create `tests/test_ambient_memory_app.py`:

```python
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

import open_strix.app as app_mod
from open_strix.ambient_memory import AmbientMemoryService, AmbientSnippet, FakeAmbientBackend
from open_strix.ambient_memory import chat_record_id
from open_strix.config import AmbientMemoryConfig


class CapturingAgent:
    def __init__(self) -> None:
        self.prompt = ""

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.prompt = payload["messages"][0].content
        return {"messages": [AIMessage(content="ok")]}


@pytest.mark.asyncio
async def test_prompt_injects_ready_ambient_memory_after_hooks(tmp_path, monkeypatch) -> None:
    agent = CapturingAgent()
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: agent)
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.ambient_memory = AmbientMemoryConfig(enabled=True)
    app.ambient_memory = AmbientMemoryService(
        layout=app.layout,
        config=app.config.ambient_memory,
        backend=FakeAmbientBackend(
            [
                AmbientSnippet(
                    record_id="r1",
                    source="state/cold.md:1",
                    text="cold fact about the budget",
                    score=0.9,
                )
            ]
        ),
        log_event=app.log_event,
    )

    skill_dir = tmp_path / "skills" / "prompt-hook"
    skill_dir.mkdir(parents=True)
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "event['append_prompt'] = 'hook-added context'\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    (skill_dir / "hooks.json").write_text(
        '{"hooks":[{"name":"prompt-hook","command":"python hook.py","events":["pre_prompt"]}]}',
        encoding="utf-8",
    )
    app.hooks.discover()

    async def _noop_git(_event: Any) -> str:
        return "skip"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    await app._process_event(
        app_mod.AgentEvent(
            event_type="web_message",
            prompt="what about the plan?",
            channel_id="local-web",
            author="local_user",
            source_id="m1",
        )
    )

    assert "hook-added context" in agent.prompt
    assert "Ambient memory surfaced" in agent.prompt
    assert agent.prompt.index("hook-added context") < agent.prompt.index("Ambient memory")


def test_render_prompt_reports_prompt_visible_chat_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: CapturingAgent())
    app = app_mod.OpenStrixApp(tmp_path)
    app._remember_message(
        channel_id="local-web",
        message_id="visible-1",
        author="dagr",
        content="already visible chat row",
        attachment_names=[],
        source="web",
    )

    _prompt, visible_ids = app._render_prompt_with_ambient_metadata(
        app_mod.AgentEvent(
            event_type="web_message",
            prompt="current",
            channel_id="local-web",
            author="local_user",
        )
    )

    assert chat_record_id(app.message_history_all[-1]) in visible_ids
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_ambient_memory_app.py::test_prompt_injects_ready_ambient_memory_after_hooks -q`

Expected: FAIL because app has no `ambient_memory` integration.

- [ ] **Step 3: Add app-owned context helpers**

In `open_strix/ambient_memory.py`, add:

```python
def query_fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return _hash_text(normalized[:4000])
```

In `open_strix/app.py`, import:

```python
from .ambient_memory import (
    AmbientMemoryService,
    AmbientQueryContext,
    DisabledAmbientMemoryService,
    chat_record_id,
    journal_record_id,
    query_fingerprint,
)
```

Add app attributes in `OpenStrixApp.__init__` after `self.hooks.discover()`:

```python
self._ambient_turn_sequence = 0
self._ambient_boundary_sequence = 0
self.ambient_memory = DisabledAmbientMemoryService()
if self.config.ambient_memory.enabled:
    self.ambient_memory = AmbientMemoryService(
        layout=self.layout,
        config=self.config.ambient_memory,
        backend=self._build_ambient_memory_backend(),
        log_event=self.log_event,
    )
```

Add `_build_ambient_memory_backend()` returning a disabled fake until Task 6:

```python
def _build_ambient_memory_backend(self) -> Any:
    from .ambient_memory import FakeAmbientBackend

    self.log_event("ambient_memory_started", backend=self.config.ambient_memory.backend)
    return FakeAmbientBackend([])
```

Add a render helper that preserves the existing `_render_prompt()` string API while exposing prompt-visible IDs to `_process_event()`:

```python
def _render_prompt_with_ambient_metadata(
    self,
    event: AgentEvent,
) -> tuple[str, frozenset[str]]:
    journal_entries = _tail_jsonl(
        self.layout.journal_log,
        self.config.journal_entries_in_prompt,
    )
    blocks = self._load_blocks_for_prompt()
    recent_candidates = [
        item
        for item in self.message_history_all
        if item.get("source") in {"discord", "web", "stdin"}
    ]
    if event.channel_id:
        channel_recent = [
            item
            for item in recent_candidates
            if item.get("channel_id") == event.channel_id
        ]
        if channel_recent:
            recent_candidates = channel_recent
    recent_messages = recent_candidates[-self.config.discord_messages_in_prompt :]
    prompt = render_turn_prompt(
        journal_entries=journal_entries,
        memory_blocks=blocks,
        recent_messages=recent_messages,
        current_event={
            "event_type": event.event_type,
            "prompt": event.prompt,
            "channel_id": event.channel_id,
            "channel_name": event.channel_name,
            "channel_conversation_type": event.channel_conversation_type,
            "channel_visibility": event.channel_visibility,
            "author": event.author,
            "attachment_names": event.attachment_names,
            "scheduler_name": event.scheduler_name,
            "source_id": event.source_id,
            "source_platform": event.source_platform,
        },
        last_turn_failure=self._last_turn_failure,
        aliases_block=render_aliases_block(self.phone_book),
    )
    visible_ids = {journal_record_id(row) for row in journal_entries}
    visible_ids.update(chat_record_id(row) for row in recent_messages)
    return prompt, frozenset(visible_ids)


def _render_prompt(self, event: AgentEvent) -> str:
    prompt, _visible_ids = self._render_prompt_with_ambient_metadata(event)
    return prompt
```

Replace the existing `_render_prompt()` implementation with this pair rather than duplicating prompt rendering logic.

Add context method:

```python
def _ambient_context(
    self,
    *,
    event: AgentEvent,
    query_text: str,
    prompt_visible_ids: frozenset[str] = frozenset(),
) -> AmbientQueryContext:
    return AmbientQueryContext(
        session_id=self.session_id,
        turn_sequence=self._ambient_turn_sequence,
        boundary_sequence=self._ambient_boundary_sequence,
        event_type=event.event_type,
        channel_id=event.channel_id,
        source_id=event.source_id,
        query_text=query_text,
        query_fingerprint=query_fingerprint(query_text),
        prompt_visible_ids=prompt_visible_ids,
        created_at=datetime.now(tz=UTC),
        ttl_seconds=self.config.ambient_memory.result_ttl_seconds,
    )
```

- [ ] **Step 4: Trigger and consume prompt injection**

In `_process_event()`, increment turn and boundary counters at the start:

```python
self._ambient_turn_sequence += 1
self._ambient_boundary_sequence = 0
```

When rendering the turn prompt in `_process_event()`, capture prompt-visible IDs:

```python
prompt_visible_ids = frozenset()
if event.continuation_messages:
    prompt = event.prompt
else:
    prompt, prompt_visible_ids = self._render_prompt_with_ambient_metadata(event)
```

After `prompt` is rendered and before `self.hooks.run_event("pre_prompt", ...)`:

```python
ambient_context = self._ambient_context(
    event=event,
    query_text=prompt,
    prompt_visible_ids=prompt_visible_ids,
)
self.ambient_memory.trigger(ambient_context)
await asyncio.sleep(0)
```

After hook prompt mutation and `append_prompt` handling:

```python
ambient_append = self.ambient_memory.consume_ready(ambient_context)
if self.config.ambient_memory.prompt_injection and ambient_append:
    prompt = f"{prompt}\n\n{ambient_append}"
```

This uses `await asyncio.sleep(0)` only to let fake/test backends complete; real retrieval still remains bounded and non-blocking.

- [ ] **Step 5: Run prompt integration test**

Run: `uv run pytest tests/test_ambient_memory_app.py::test_prompt_injects_ready_ambient_memory_after_hooks -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add open_strix/app.py open_strix/ambient_memory.py tests/test_ambient_memory_app.py
git commit -m "Inject ambient memory into prompts"
```

---

### Task 5: Tool Boundary Integration

**Files:**
- Modify: `open_strix/app.py`
- Modify: `open_strix/hooks.py`
- Test: `tests/test_ambient_memory_hooks.py`

- [ ] **Step 1: Write failing tool-boundary tests**

Create `tests/test_ambient_memory_hooks.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import tool

from open_strix.ambient_memory import AmbientMemoryService, AmbientSnippet, FakeAmbientBackend
from open_strix.config import AmbientMemoryConfig, RepoLayout
from open_strix.hooks import HookManager


class FakeApp:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.config = type(
            "Config",
            (),
            {"ambient_memory": AmbientMemoryConfig(enabled=True)},
        )()
        self.session_id = "session"
        self.current_channel_id = "local-web"
        self.current_event_label = "unit-test"
        self.events: list[dict[str, Any]] = []
        self._ambient_turn_sequence = 1
        self._ambient_boundary_sequence = 0
        self.ambient_memory = AmbientMemoryService(
            layout=self.layout,
            config=self.config.ambient_memory,
            backend=FakeAmbientBackend(
                [
                    AmbientSnippet(
                        record_id="r1",
                        source="state/cold.md:1",
                        text="cold fact from tool boundary",
                        score=0.9,
                    )
                ]
            ),
            log_event=self.log_event,
        )

    def log_event(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, **payload})

    def ambient_pre_tool_call(self, tool_name: str, args: dict[str, Any]) -> Any:
        from open_strix.app import OpenStrixApp

        return OpenStrixApp.ambient_pre_tool_call(self, tool_name, args)

    def ambient_post_tool_success(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        *,
        ambient_context: Any,
        return_direct: bool,
    ) -> Any:
        from open_strix.app import OpenStrixApp

        return OpenStrixApp.ambient_post_tool_success(
            self,
            tool_name,
            args,
            result,
            ambient_context=ambient_context,
            return_direct=return_direct,
        )


@pytest.mark.asyncio
async def test_ambient_tool_injection_after_post_hooks(tmp_path: Path) -> None:
    (tmp_path / "skills" / "mutator").mkdir(parents=True)
    skill_dir = tmp_path / "skills" / "mutator"
    (skill_dir / "hook.py").write_text(
        "import json, sys\n"
        "event = json.loads(sys.stdin.readline())\n"
        "if event['type'] == 'post_tool_call':\n"
        "    event['result'] = event['result'] + ' post-hook'\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    (skill_dir / "hooks.json").write_text(
        '{"hooks":[{"name":"mutator","command":"python hook.py","events":["post_tool_call"]}]}',
        encoding="utf-8",
    )

    @tool("echo")
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    app = FakeApp(tmp_path)
    manager = HookManager(app)
    manager.discover()
    hooked = manager.wrap_tool(echo)

    result = await hooked.ainvoke({"text": "base"})

    assert "base post-hook" in result
    assert "Ambient memory surfaced" in result
    assert result.index("base post-hook") < result.index("Ambient memory")


@pytest.mark.asyncio
async def test_ambient_tool_injection_skips_send_message(tmp_path: Path) -> None:
    @tool("send_message")
    async def send_message(text: str) -> str:
        """Send message."""
        return "sent"

    app = FakeApp(tmp_path)
    manager = HookManager(app)
    hooked = manager.wrap_tool(send_message)

    result = await hooked.ainvoke({"text": "hello"})

    assert result == "sent"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_ambient_memory_hooks.py -q`

Expected: FAIL because app helper methods and hook integration do not exist.

- [ ] **Step 3: Add app helper methods**

In `open_strix/app.py`, add methods on `OpenStrixApp`:

```python
def ambient_pre_tool_call(
    self,
    tool_name: str,
    args: dict[str, Any],
) -> AmbientQueryContext | None:
    if not self.config.ambient_memory.enabled:
        return None
    self._ambient_boundary_sequence += 1
    query_text = f"tool={tool_name}\nargs={json.dumps(args, sort_keys=True, default=str)}"
    context = AmbientQueryContext(
        session_id=self.session_id,
        turn_sequence=self._ambient_turn_sequence,
        boundary_sequence=self._ambient_boundary_sequence,
        event_type=str(self.current_event_label or ""),
        channel_id=self.current_channel_id,
        source_id=None,
        query_text=query_text,
        query_fingerprint=query_fingerprint(query_text),
        prompt_visible_ids=frozenset(),
        created_at=datetime.now(tz=UTC),
        ttl_seconds=self.config.ambient_memory.result_ttl_seconds,
    )
    self.ambient_memory.observe_boundary(context)
    return context


def ambient_post_tool_success(
    self,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    ambient_context: AmbientQueryContext | None,
    return_direct: bool,
) -> Any:
    if not self.config.ambient_memory.enabled:
        return result
    if not self.config.ambient_memory.tool_result_injection:
        return result
    if return_direct or tool_name in {"send_message", "react"}:
        return result
    if not isinstance(result, str):
        return result
    if len(result.encode("utf-8")) > self.config.ambient_memory.max_tool_result_bytes:
        return result
    if ambient_context is None:
        return result
    append = self.ambient_memory.consume_ready(ambient_context)
    if not append:
        return result
    return f"{result}\n\n{append}"
```

- [ ] **Step 4: Wire `HookManager.wrap_tool()`**

In `open_strix/hooks.py`, after final `next_args` validation and before `wrapped.ainvoke(next_args)`:

```python
ambient_context = None
ambient_pre = getattr(hooks.strix, "ambient_pre_tool_call", None)
if callable(ambient_pre):
    ambient_context = ambient_pre(tool_name, next_args)
```

After `post_event = await hooks.run_event("post_tool_call", ...)`, replace the return with:

```python
final_result = post_event.get("result", result)
ambient_post = getattr(hooks.strix, "ambient_post_tool_success", None)
if callable(ambient_post):
    final_result = ambient_post(
        tool_name,
        next_args,
        final_result,
        ambient_context=ambient_context,
        return_direct=bool(getattr(wrapped, "return_direct", False)),
    )
return final_result
```

Do not call `ambient_post_tool_success()` in the blocked-call or exception branches.

- [ ] **Step 5: Run hook integration tests**

Run: `uv run pytest tests/test_ambient_memory_hooks.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add open_strix/app.py open_strix/hooks.py tests/test_ambient_memory_hooks.py
git commit -m "Wire ambient memory tool boundaries"
```

---

### Task 6: Optional PyLate Backend

**Files:**
- Modify: `open_strix/ambient_memory.py`
- Modify: `pyproject.toml`
- Test: `tests/test_ambient_memory_service.py`

- [ ] **Step 1: Add failing optional-backend tests**

Append to `tests/test_ambient_memory_service.py`:

```python
def test_create_backend_fails_open_when_pylate_missing(monkeypatch, tmp_path) -> None:
    from open_strix.ambient_memory import create_ambient_backend

    def fake_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr("open_strix.ambient_memory._import_pylate", fake_import)

    events: list[dict[str, object]] = []
    backend = create_ambient_backend(
        index_dir=tmp_path / "logs" / "ambient-memory",
        config=AmbientMemoryConfig(enabled=True),
        log_event=lambda event_type, **payload: events.append({"type": event_type, **payload}),
    )

    assert isinstance(backend, FakeAmbientBackend)
    assert any(event["type"] == "ambient_memory_disabled" for event in events)
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_ambient_memory_service.py::test_create_backend_fails_open_when_pylate_missing -q`

Expected: FAIL because `create_ambient_backend` is missing.

- [ ] **Step 3: Add optional dependency extra**

In `pyproject.toml`, add:

```toml
[project.optional-dependencies]
ambient-memory = [
    "pylate>=1.0.0",
]
```

If the package name or minimum version fails resolution during implementation, inspect PyPI with `uv pip index versions pylate` and use the newest stable version available. Commit the exact resolved `pyproject.toml` and `uv.lock` changes together in this task.

- [ ] **Step 4: Add backend factory and adapter**

Append to `open_strix/ambient_memory.py`:

```python
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
                model_name_or_path=self.config.model
            )
        return self._model

    async def refresh(self, records: list[AmbientRecord]) -> None:
        await asyncio.to_thread(self._refresh_sync, records)

    def _refresh_sync(self, records: list[AmbientRecord]) -> None:
        model = self._ensure_model()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        documents_ids = [record.id for record in records]
        documents = [record.text for record in records]
        if not documents:
            self._records = {}
            return
        embeddings = model.encode(
            documents,
            is_query=False,
            show_progress_bar=False,
        )
        index = self.pylate_indexes.Voyager(
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
```

Also add:

```python
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
```

- [ ] **Step 5: Wire app backend factory**

In `OpenStrixApp._build_ambient_memory_backend()`, replace the fake backend with:

```python
from .ambient_memory import create_ambient_backend

index_dir = self.home / self.config.ambient_memory.index_dir
self.log_event("ambient_memory_started", backend=self.config.ambient_memory.backend)
return create_ambient_backend(
    index_dir=index_dir,
    config=self.config.ambient_memory,
    log_event=self.log_event,
)
```

- [ ] **Step 6: Run optional-backend tests**

Run: `uv run pytest tests/test_ambient_memory_service.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add open_strix/ambient_memory.py open_strix/app.py pyproject.toml uv.lock tests/test_ambient_memory_service.py
git commit -m "Add optional PyLate ambient memory backend"
```

---

### Task 7: Refresh Lifecycle and Manifest

**Files:**
- Modify: `open_strix/ambient_memory.py`
- Modify: `open_strix/app.py`
- Test: `tests/test_ambient_memory_service.py`

- [ ] **Step 1: Add failing refresh tests**

Append to `tests/test_ambient_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_refresh_writes_manifest_and_keeps_last_good_generation(tmp_path) -> None:
    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "cold.md").write_text("cold note", encoding="utf-8")
    backend = FakeAmbientBackend([])
    service = AmbientMemoryService(
        layout=layout,
        config=AmbientMemoryConfig(enabled=True),
        backend=backend,
        log_event=lambda *_args, **_kwargs: None,
    )

    await service.refresh()

    manifest = tmp_path / "logs" / "ambient-memory" / "manifest.jsonl"
    assert manifest.exists()
    assert "cold note" in manifest.read_text(encoding="utf-8")
    assert backend.refresh_count == 1
```

- [ ] **Step 2: Run refresh test to verify failure**

Run: `uv run pytest tests/test_ambient_memory_service.py::test_refresh_writes_manifest_and_keeps_last_good_generation -q`

Expected: FAIL because `refresh()` is missing.

- [ ] **Step 3: Implement refresh and manifest**

In `AmbientMemoryService`, add:

```python
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
    except Exception as exc:
        self.log_event(
            "ambient_memory_refresh_failed",
            error=str(exc),
            error_class=type(exc).__name__,
        )
        return
    self._write_manifest(records)
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
```

In `OpenStrixApp.run()` or the existing startup path after post-startup hooks, schedule refresh without awaiting it:

```python
if self.config.ambient_memory.enabled:
    asyncio.create_task(self.ambient_memory.refresh())
```

If `DisabledAmbientMemoryService` needs `refresh()`, add:

```python
async def refresh(self) -> None:
    return None
```

- [ ] **Step 4: Run refresh tests**

Run: `uv run pytest tests/test_ambient_memory_service.py::test_refresh_writes_manifest_and_keeps_last_good_generation -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add open_strix/ambient_memory.py open_strix/app.py tests/test_ambient_memory_service.py
git commit -m "Refresh ambient memory index"
```

---

### Task 8: Documentation and Focused Regression Suite

**Files:**
- Modify: `README.md`
- Modify: `SETUP.md`
- Modify: `docs/hooks.md`
- Test: existing and new ambient tests

- [ ] **Step 1: Document config and philosophy**

In `README.md`, update the Memory section after the existing "No embeddings, no vector search" line:

```markdown
Ambient associative memory is available as an opt-in extension. When enabled, it indexes cold memory surfaces that are not already in the prompt, such as `state/` notes, older journal rows, and older chat history. It does not index memory blocks, because blocks are already high-priority prompt context.
```

In `SETUP.md`, add the `ambient_memory` config block from the spec and explain:

```markdown
`ambient_memory.enabled` defaults to `false`. Enable it only after installing the optional ambient-memory extra. Retrieval failures fail open: the agent continues normally and logs why ambient memory was disabled or skipped.
```

In `docs/hooks.md`, add a design note:

```markdown
Ambient memory, when enabled, runs inside open-strix rather than as a command hook. Prompt injection happens after `pre_prompt` hook mutations. Tool-result injection happens after successful `post_tool_call` hook mutations and never applies to blocked or error calls.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_ambient_memory_config.py \
  tests/test_ambient_memory_corpus.py \
  tests/test_ambient_memory_service.py \
  tests/test_ambient_memory_app.py \
  tests/test_ambient_memory_hooks.py \
  tests/test_hooks.py \
  tests/test_prompt_context.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run broader suite**

Run: `uv run pytest -q`

Expected: PASS. If live/provider tests require unavailable credentials, record the exact skipped or failing tests and run the largest local subset that does not require network/API credentials.

- [ ] **Step 4: Commit**

```bash
git add README.md SETUP.md docs/hooks.md
git commit -m "Document ambient associative memory"
```

---

## Self-Review Checklist

- Spec coverage:
  - Cold surfaces: Task 2.
  - Prompt-visible subtraction: Task 2 and Task 4.
  - Background service and consume-once results: Task 3.
  - Prompt boundary ordering: Task 4.
  - Tool boundary ordering and skip list: Task 5.
  - Optional PyLate backend: Task 6.
  - Refresh, manifest, and last-good behavior: Task 7.
  - Docs and tests: Task 8.
- Completeness scan:
  - No marker tokens or unbounded edge-case steps.
  - Each task has exact files, commands, and expected outcomes.
- Type consistency:
  - `AmbientMemoryConfig`, `AmbientRecord`, `AmbientQueryContext`, `AmbientSnippet`, `AmbientMemoryService`, `FakeAmbientBackend`, and `DisabledAmbientMemoryService` are introduced before use in integration tasks.
