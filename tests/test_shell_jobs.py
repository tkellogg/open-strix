"""Tests for open_strix.shell_jobs — the async shell job registry."""

from __future__ import annotations

import time
from pathlib import Path

from open_strix.shell_jobs import (
    POST_EXIT_GRACE_SECONDS,
    UI_VISIBILITY_THRESHOLD_SECONDS,
    ShellJobRegistry,
)


def _wait_until(pred, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _finished(job) -> bool:
    return job.status in ("exited_ok", "exited_error")


def test_spawn_captures_stdout_to_file(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = 'echo hello-async && sleep 0.05'
    job = reg.spawn(cmd, argv=['bash', '-lc', cmd])

    assert job.pid > 0
    assert job.job_id.startswith("j_")
    assert job.stdout_path.exists()

    assert _wait_until(lambda: _finished(reg.get(job.job_id)))
    assert reg.get(job.job_id).exit_code == 0

    data = reg.read_output(job.job_id, tail_lines=10, stream="both")
    assert "hello-async" in data["stdout_tail"]
    assert data["status"] == "exited_ok"


def test_spawn_streams_python_stdout_before_exit(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = """uv run python - <<'PY'
import time
print("hello-python")
time.sleep(0.4)
PY"""
    job = reg.spawn(cmd, argv=["bash", "-lc", cmd])

    assert _wait_until(
        lambda: "hello-python" in reg.read_output(job.job_id, tail_lines=10, stream="stdout")["stdout_tail"],
        timeout=2.0,
    ), "python stdout should stream before process exit"

    snap = reg.get(job.job_id).snapshot()
    assert snap["status"] == "running"


def test_running_jobs_are_visible_immediately(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = 'sleep 0.8'
    job = reg.spawn(cmd, argv=['bash', '-lc', cmd])

    visible_now = reg.visible_jobs()
    assert any(j.job_id == job.job_id for j in visible_now)


def test_visibility_threshold(tmp_path: Path) -> None:
    """Short finished jobs should not be UI-visible until they age into the grace window."""
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = 'echo fast'
    job = reg.spawn(cmd, argv=['bash', '-lc', cmd])
    assert _wait_until(lambda: _finished(reg.get(job.job_id)))

    visible_now = reg.visible_jobs()
    assert all(j.job_id != job.job_id for j in visible_now), \
        "short finished job must not be UI-visible immediately"

    # Simulate time passing.
    j = reg.get(job.job_id)
    j.started_at -= UI_VISIBILITY_THRESHOLD_SECONDS + 1

    # During grace window it should still be visible.
    visible = reg.visible_jobs()
    assert any(jj.job_id == job.job_id for jj in visible)

    # After the grace window, it should drop off.
    j.finished_at = time.time() - (POST_EXIT_GRACE_SECONDS + 5)
    visible_after = reg.visible_jobs()
    assert all(jj.job_id != job.job_id for jj in visible_after)


def test_all_jobs_persists_after_exit(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = 'echo persist'
    job = reg.spawn(cmd, argv=['bash', '-lc', cmd])
    assert _wait_until(lambda: _finished(reg.get(job.job_id)))
    # all_jobs must still include the finished job (no cleanup).
    assert any(j.job_id == job.job_id for j in reg.all_jobs())


def test_snapshot_shape(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = 'echo snap'
    job = reg.spawn(cmd, argv=['bash', '-lc', cmd])
    snap = job.snapshot()
    for key in (
        "job_id", "pid", "command", "started_at", "elapsed_seconds",
        "last_live_signal", "seconds_since_last_signal", "status", "exit_code",
    ):
        assert key in snap, f"snapshot missing {key}"


def test_read_output_unknown_job(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    result = reg.read_output("j_does_not_exist", tail_lines=10, stream="both")
    assert "error" in result


def test_spawn_records_channel_id(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    cmd = "echo hi"
    job = reg.spawn(
        cmd,
        argv=["bash", "-lc", cmd],
        channel_id="chan-123",
        channel_name="test-channel",
    )
    assert job.channel_id == "chan-123"
    assert job.channel_name == "test-channel"
    snap = job.snapshot()
    assert snap["channel_id"] == "chan-123"
    assert snap["channel_name"] == "test-channel"

    assert _wait_until(lambda: _finished(reg.get(job.job_id)))


def test_spawn_fires_on_complete_callback(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    calls: list = []

    def on_complete(job) -> None:
        calls.append(job)

    cmd = "echo done && exit 0"
    job = reg.spawn(
        cmd,
        argv=["bash", "-lc", cmd],
        channel_id="chan-xyz",
        on_complete=on_complete,
    )

    assert _wait_until(lambda: len(calls) == 1, timeout=5.0)
    assert calls[0].job_id == job.job_id
    # Callback fires AFTER exit_code/finished_at are set.
    assert calls[0].exit_code == 0
    assert calls[0].finished_at is not None
    assert calls[0].channel_id == "chan-xyz"


def test_spawn_on_complete_callback_error_does_not_break_registry(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")

    def bad_callback(job) -> None:
        raise RuntimeError("boom")

    cmd = "echo fine"
    job = reg.spawn(
        cmd,
        argv=["bash", "-lc", cmd],
        on_complete=bad_callback,
    )

    # Job still finishes cleanly even though callback raised.
    assert _wait_until(lambda: _finished(reg.get(job.job_id)))
    assert reg.get(job.job_id).exit_code == 0


def test_spawn_fires_callback_on_nonzero_exit(tmp_path: Path) -> None:
    reg = ShellJobRegistry(jobs_dir=tmp_path / "jobs")
    calls: list = []

    cmd = "exit 3"
    job = reg.spawn(
        cmd,
        argv=["bash", "-lc", cmd],
        on_complete=lambda j: calls.append(j),
    )

    assert _wait_until(lambda: len(calls) == 1, timeout=5.0)
    assert calls[0].exit_code == 3
    assert calls[0].status == "exited_error"
