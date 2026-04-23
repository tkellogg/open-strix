"""In-flight shell job registry for async long-running shell commands.

When the shell tool is invoked with async_mode=True, the command is spawned
via subprocess.Popen, registered here, and its stdout/stderr are captured
to files on disk via background drainer threads. The LLM can then use
shell_jobs_list / shell_job_output tools to check on progress, and the
UI can surface running jobs by polling a field in the web UI payload.

Design notes:
- No lifecycle cleanup. Kills happen via bash/powershell (the agent or user
  decides what signal to use). Finished jobs linger in the registry so the
  LLM can still retrieve their final output.
- No algedonic signals. The tool description is the only nudge toward async.
- Running jobs are visible to the UI immediately. Finished jobs only linger in
  the UI if they ran long enough to be worth surfacing.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


UI_VISIBILITY_THRESHOLD_SECONDS = 10
POST_EXIT_GRACE_SECONDS = 15
SHELL_JOB_OUTPUT_DEFAULT_TAIL_LINES = 1000
SHELL_JOB_OUTPUT_MAX_TAIL_LINES = 2000
DEFAULT_SHELL_JOB_SCOPE = "running"
VALID_SHELL_JOB_SCOPES = frozenset({"running", "visible", "all"})
DEFAULT_SHELL_JOB_STREAM = "both"
VALID_SHELL_JOB_STREAMS = frozenset({"stdout", "stderr", "both"})


@dataclass
class ShellJob:
    """A spawned async shell command with file-backed stdout/stderr."""

    job_id: str
    command: str
    pid: int
    started_at: float
    stdout_path: Path
    stderr_path: Path
    last_live_signal: float  # epoch seconds; updated by drainer threads
    exit_code: Optional[int] = None  # None while running
    finished_at: Optional[float] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def status(self) -> str:
        if self.exit_code is None:
            return "running"
        if self.exit_code == 0:
            return "exited_ok"
        return "exited_error"

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def seconds_since_last_signal(self) -> float:
        return max(0.0, time.time() - self.last_live_signal)

    def touch(self) -> None:
        with self._lock:
            self.last_live_signal = time.time()

    def snapshot(self) -> dict:
        """JSON-serializable view for tools and API responses."""
        return {
            "job_id": self.job_id,
            "pid": self.pid,
            "command": self.command,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "last_live_signal": self.last_live_signal,
            "seconds_since_last_signal": round(self.seconds_since_last_signal, 2),
            "status": self.status,
            "exit_code": self.exit_code,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
        }


class ShellJobRegistry:
    """In-memory registry of spawned shell jobs.

    Instances are stored on the OpenStrixApp. Thread-safe because drainer
    threads update jobs while the main asyncio loop reads them.
    """

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ShellJob] = {}
        self._lock = threading.Lock()

    def _make_job_id(self) -> str:
        return "j_" + uuid.uuid4().hex[:10]

    def spawn(
        self,
        command: str,
        *,
        argv: list[str],
        channel_id: Optional[str] = None,
        channel_name: Optional[str] = None,
        on_complete: Optional[Callable[["ShellJob"], None]] = None,
    ) -> ShellJob:
        """Spawn argv as a subprocess and register it.

        argv is the platform-specific wrapped command
        (e.g. ['bash', '-lc', cmd]).

        channel_id / channel_name optionally record which conversation spawned
        the job so the completion callback can resume it in-place.

        on_complete, if provided, is invoked from the waiter thread once the
        subprocess exits and exit_code/finished_at have been set. Exceptions
        raised by the callback are caught and dropped so the registry stays
        intact.
        """
        job_id = self._make_job_id()
        stdout_path = self.jobs_dir / f"{job_id}.out"
        stderr_path = self.jobs_dir / f"{job_id}.err"

        stdout_f = stdout_path.open("wb")
        stderr_f = stderr_path.open("wb")

        try:
            env = os.environ.copy()
            # Python is a common job runner here; force unbuffered output so
            # "print then sleep" jobs stream into the UI while still running.
            env.setdefault("PYTHONUNBUFFERED", "1")
            popen_kwargs: dict = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "bufsize": 0,
                "env": env,
            }
            # Put child in its own process group so the agent can send signals
            # to it (via bash) without affecting the harness.
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(argv, **popen_kwargs)
        except Exception:
            stdout_f.close()
            stderr_f.close()
            raise

        started_at = time.time()
        job = ShellJob(
            job_id=job_id,
            command=command,
            pid=proc.pid,
            started_at=started_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            last_live_signal=started_at,
            channel_id=channel_id,
            channel_name=channel_name,
            _process=proc,
        )

        def _drain(stream, outfile, on_signal):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    outfile.write(chunk)
                    outfile.flush()
                    on_signal()
            finally:
                try:
                    outfile.close()
                except Exception:
                    pass

        def _waiter():
            try:
                rc = proc.wait()
            except Exception:
                rc = -1
            with job._lock:
                job.exit_code = rc
                job.finished_at = time.time()
            if on_complete is not None:
                try:
                    on_complete(job)
                except Exception:
                    # Never let a callback error break the registry.
                    pass

        threading.Thread(
            target=_drain,
            args=(proc.stdout, stdout_f, job.touch),
            daemon=True,
            name=f"shelljob-out-{job_id}",
        ).start()
        threading.Thread(
            target=_drain,
            args=(proc.stderr, stderr_f, job.touch),
            daemon=True,
            name=f"shelljob-err-{job_id}",
        ).start()
        threading.Thread(
            target=_waiter,
            daemon=True,
            name=f"shelljob-wait-{job_id}",
        ).start()

        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[ShellJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def all_jobs(self) -> list[ShellJob]:
        with self._lock:
            return list(self._jobs.values())

    def _sorted_jobs(self, jobs: list[ShellJob]) -> list[ShellJob]:
        return sorted(
            jobs,
            key=lambda job: (job.exit_code is not None, -job.started_at),
        )

    def running_jobs(self) -> list[ShellJob]:
        return self._sorted_jobs([job for job in self.all_jobs() if job.exit_code is None])

    def visible_jobs(self, *, now: Optional[float] = None) -> list[ShellJob]:
        """Jobs the UI should surface.

        Running jobs are visible immediately so the user can inspect them as
        soon as they start. Finished jobs stay visible for
        POST_EXIT_GRACE_SECONDS only if they ran long enough to cross the
        visibility threshold, so the UI does not flicker for tiny async tasks.
        """
        now = now if now is not None else time.time()
        visible: list[ShellJob] = []
        for job in self.all_jobs():
            if job.exit_code is None:
                visible.append(job)
                continue
            elapsed = (job.finished_at or now) - job.started_at
            if elapsed < UI_VISIBILITY_THRESHOLD_SECONDS:
                continue
            if (
                job.finished_at is not None
                and (now - job.finished_at) > POST_EXIT_GRACE_SECONDS
            ):
                continue
            visible.append(job)
        return self._sorted_jobs(visible)

    def read_output(
        self,
        job_id: str,
        *,
        tail_lines: int = 200,
        stream: str = "both",
    ) -> dict:
        """Return tail of stdout/stderr for job_id.

        stream in {"stdout", "stderr", "both"}.
        """
        job = self.get(job_id)
        if job is None:
            return {"error": f"unknown job_id: {job_id}"}

        def _tail(path: Path, n: int) -> str:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                return ""
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if n <= 0 or len(lines) <= n:
                return text
            return "\n".join(lines[-n:])

        out = _tail(job.stdout_path, tail_lines) if stream in ("stdout", "both") else ""
        err = _tail(job.stderr_path, tail_lines) if stream in ("stderr", "both") else ""
        result = job.snapshot()
        result["stdout_tail"] = out
        result["stderr_tail"] = err
        result["stdout_path"] = str(job.stdout_path)
        result["stderr_path"] = str(job.stderr_path)
        return result


def normalize_shell_job_scope(
    scope: str | None,
    *,
    default: str = DEFAULT_SHELL_JOB_SCOPE,
) -> str:
    resolved = (scope or default).strip().lower() or default
    if resolved not in VALID_SHELL_JOB_SCOPES:
        allowed = ", ".join(f'"{item}"' for item in sorted(VALID_SHELL_JOB_SCOPES))
        raise ValueError(f"scope must be one of: {allowed}")
    return resolved


def normalize_shell_job_stream(
    stream: str | None,
    *,
    default: str = DEFAULT_SHELL_JOB_STREAM,
) -> str:
    resolved = (stream or default).strip().lower() or default
    if resolved not in VALID_SHELL_JOB_STREAMS:
        allowed = ", ".join(f'"{item}"' for item in sorted(VALID_SHELL_JOB_STREAMS))
        raise ValueError(f"stream must be one of: {allowed}")
    return resolved


def parse_shell_job_tail_lines(
    raw_value: str | None,
    *,
    default: int = SHELL_JOB_OUTPUT_DEFAULT_TAIL_LINES,
) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        tail_lines = int(raw_value)
    except ValueError as exc:
        raise ValueError("tail must be an integer") from exc
    if tail_lines <= 0:
        raise ValueError("tail must be > 0")
    return min(tail_lines, SHELL_JOB_OUTPUT_MAX_TAIL_LINES)


def shell_job_snapshots(
    registry: ShellJobRegistry | None,
    *,
    scope: str = DEFAULT_SHELL_JOB_SCOPE,
) -> list[dict]:
    if registry is None:
        return []

    resolved_scope = normalize_shell_job_scope(scope)
    if resolved_scope == "running":
        jobs = registry.running_jobs()
    elif resolved_scope == "visible":
        jobs = registry.visible_jobs()
    else:
        jobs = registry._sorted_jobs(registry.all_jobs())
    return [job.snapshot() for job in jobs]
