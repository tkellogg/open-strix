from __future__ import annotations

from pathlib import Path

import open_strix.release as release_mod


def test_read_token_from_pypirc_reads_api_token(tmp_path: Path) -> None:
    pypirc = tmp_path / ".pypirc"
    pypirc.write_text(
        "[pypi]\n"
        "username = __token__\n"
        "password = pypi-test-token\n",
        encoding="utf-8",
    )
    assert release_mod._read_token_from_pypirc(pypirc) == "pypi-test-token"


def test_resolve_publish_token_prefers_env(monkeypatch, tmp_path: Path) -> None:
    pypirc = tmp_path / ".pypirc"
    pypirc.write_text(
        "[pypi]\n"
        "username = __token__\n"
        "password = pypi-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "pypi-from-env")

    token, source = release_mod._resolve_publish_token(pypirc)
    assert token == "pypi-from-env"
    assert source == "UV_PUBLISH_TOKEN"


def test_main_dry_run_uses_pypirc_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    pypirc = tmp_path / ".pypirc"
    pypirc.write_text(
        "[pypi]\n"
        "username = __token__\n"
        "password = pypi-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)

    rc = release_mod.main(["--dry-run", "--pypirc", str(pypirc), "--check-url", "https://example.com"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Using UV_PUBLISH_TOKEN from" in captured.out
    assert "+ rm -rf dist" in captured.out
    assert "+ uv build" in captured.out
    assert "+ uv publish --check-url https://example.com" in captured.out
