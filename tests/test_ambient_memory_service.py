from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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


async def _wait_for_retrieval(service: AmbientMemoryService) -> None:
    task = service._task
    assert task is not None
    await task


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


def test_create_backend_fails_open_when_pylate_missing(monkeypatch, tmp_path) -> None:
    from open_strix.ambient_memory import create_ambient_backend

    def fake_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr("open_strix.ambient_memory._import_pylate", fake_import)

    events: list[dict[str, object]] = []
    backend = create_ambient_backend(
        index_dir=tmp_path / "logs" / "ambient-memory",
        config=AmbientMemoryConfig(enabled=True),
        log_event=lambda event_type, **payload: events.append(
            {"type": event_type, **payload}
        ),
    )

    assert isinstance(backend, FakeAmbientBackend)
    assert any(event["type"] == "ambient_memory_disabled" for event in events)


@pytest.mark.asyncio
async def test_pylate_backend_refreshes_and_retrieves(monkeypatch, tmp_path) -> None:
    from open_strix.ambient_memory import create_ambient_backend

    class FakeColBERTModel:
        def __init__(self, model_name_or_path: str) -> None:
            self.model_name_or_path = model_name_or_path

        def encode(
            self,
            values: list[str],
            *,
            is_query: bool,
            show_progress_bar: bool,
        ) -> list[str]:
            assert show_progress_bar is False
            prefix = "query" if is_query else "doc"
            return [f"{prefix}:{value}" for value in values]

    class FakePlaidIndex:
        def __init__(
            self,
            *,
            index_folder: str,
            index_name: str,
            override: bool,
        ) -> None:
            self.index_folder = index_folder
            self.index_name = index_name
            self.override = override
            self.document_ids: list[str] = []

        def add_documents(
            self,
            *,
            documents_ids: list[str],
            documents_embeddings: list[str],
        ) -> None:
            assert len(documents_ids) == len(documents_embeddings)
            self.document_ids = documents_ids

    class FakeRetriever:
        def __init__(self, *, index: FakePlaidIndex) -> None:
            self.index = index

        def retrieve(self, *, queries_embeddings: list[str], k: int) -> list[list[dict[str, object]]]:
            assert queries_embeddings == ["query:budget history"]
            return [[{"id": self.index.document_ids[0], "score": 0.73}]]

    def fake_import(name: str):
        modules = {
            "pylate.models": SimpleNamespace(ColBERT=FakeColBERTModel),
            "pylate.indexes": SimpleNamespace(PLAID=FakePlaidIndex),
            "pylate.retrieve": SimpleNamespace(ColBERT=FakeRetriever),
        }
        return modules[name]

    monkeypatch.setattr("open_strix.ambient_memory._import_pylate", fake_import)
    backend = create_ambient_backend(
        index_dir=tmp_path / "logs" / "ambient-memory",
        config=AmbientMemoryConfig(enabled=True, snippet_words=3),
        log_event=lambda *_args, **_kwargs: None,
    )
    record = AmbientRecord(
        id="state:note",
        source_type="state",
        path="state/cold.md",
        text="one two budget three four",
        line_start=7,
        line_end=7,
    )

    await backend.refresh([record])
    snippets = await backend.retrieve(
        _context(),
        max_results=1,
        snippet_words=3,
    )

    assert [snippet.record_id for snippet in snippets] == ["state:note"]
    assert snippets[0].source == "state/cold.md:7"
    assert snippets[0].text == "two budget three"
    assert snippets[0].score == 0.73


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


@pytest.mark.asyncio
async def test_refresh_failure_preserves_existing_manifest(tmp_path) -> None:
    class FailingBackend(FakeAmbientBackend):
        async def refresh(self, records: list[AmbientRecord]) -> None:
            raise RuntimeError("index failed")

    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "cold.md").write_text("first note", encoding="utf-8")
    service = AmbientMemoryService(
        layout=layout,
        config=AmbientMemoryConfig(enabled=True),
        backend=FakeAmbientBackend([]),
        log_event=lambda *_args, **_kwargs: None,
    )
    await service.refresh()
    manifest = tmp_path / "logs" / "ambient-memory" / "manifest.jsonl"
    first_manifest = manifest.read_text(encoding="utf-8")
    events: list[dict[str, object]] = []
    failing_service = AmbientMemoryService(
        layout=layout,
        config=AmbientMemoryConfig(enabled=True),
        backend=FailingBackend([]),
        log_event=lambda event_type, **payload: events.append(
            {"type": event_type, **payload}
        ),
    )

    (tmp_path / "state" / "cold.md").write_text("second note", encoding="utf-8")
    await failing_service.refresh()

    assert manifest.read_text(encoding="utf-8") == first_manifest
    assert any(event["type"] == "ambient_memory_refresh_failed" for event in events)


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
    await _wait_for_retrieval(service)

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
    await _wait_for_retrieval(service)

    assert service.consume_ready(_context(turn=2)) is None
