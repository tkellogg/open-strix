"""A completed shell job must expose its final output to its callback."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from open_strix.shell_jobs import ShellJob, ShellJobRegistry


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_completion_waits_for_output_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    flushing = threading.Event()
    release_flush = threading.Event()
    closed = threading.Event()
    completed = threading.Event()
    outputs: list[bytes] = []
    original_open = Path.open
    suffix = ".out" if stream == "stdout" else ".err"

    class DelayedFlush:
        def __init__(self, file) -> None:
            self.file = file

        def write(self, data: bytes) -> int:
            return self.file.write(data)

        def flush(self) -> None:
            flushing.set()
            release_flush.wait(timeout=5)
            self.file.flush()

        def close(self) -> None:
            self.file.close()
            closed.set()

    def open_output(path: Path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        if args == ("wb",) and path.suffix == suffix:
            return DelayedFlush(file)
        return file

    def on_complete(job: ShellJob) -> None:
        path = job.stdout_path if stream == "stdout" else job.stderr_path
        outputs.append(path.read_bytes())
        completed.set()

    monkeypatch.setattr(Path, "open", open_output)
    registry = ShellJobRegistry(tmp_path / "jobs")
    code = f"import sys; sys.{stream}.write('final output\\n')"
    job = registry.spawn(
        "write final output",
        argv=[sys.executable, "-c", code],
        on_complete=on_complete,
    )

    try:
        assert flushing.wait(timeout=5)
        # The real child has exited while the final file flush is paused.
        assert job._process.wait(timeout=5) == 0
        assert not completed.wait(timeout=0.1)
    finally:
        release_flush.set()
        assert closed.wait(timeout=5)

    assert completed.wait(timeout=5)
    assert outputs == [b"final output\n"]
    assert job.status == "exited_ok"
