from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult

from .builtin_skills import materialize_builtin_skills

BUILTIN_SKILLS_ROUTE = "/.open_strix_builtin_skills/"
UTC = timezone.utc

# Thread-local flag: set when we're inside a dedicated tool call that already logs.
# When set, LoggingWriteGuardBackend skips its own logging to avoid duplicates.
_IN_TOOL_CALL = threading.local()

def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


@contextmanager
def _inside_tool_call():
    """Context manager: marks the current thread as being inside a dedicated tool call.

    Dedicated tools wrap their backend calls with this so that
    LoggingWriteGuardBackend knows not to emit a duplicate event.

    Usage:
        with _inside_tool_call():
            result = backend.read(file_path)
    """
    prev = getattr(_IN_TOOL_CALL, "active", False)
    _IN_TOOL_CALL.active = True
    try:
        yield
    finally:
        _IN_TOOL_CALL.active = prev


def in_tool_call() -> bool:
    """True if we're currently inside a dedicated tool that handles its own logging."""
    return getattr(_IN_TOOL_CALL, "active", False)


class LoggingWriteGuardBackend:
    """WriteGuardBackend that logs read-adjacent tool calls to events.jsonl.

    Filesystem read tools (read_file, ls, glob, grep, execute) are handled by
    the FilesystemMiddleware directly and don't appear in AIMessage.tool_calls.
    This wrapper instruments the underlying backend to emit equivalent events.
    """

    def __init__(
        self,
        root_dir: Path,
        writable_dirs: list[str],
        events_log_path: str | None = None,
        session_id: str = "unknown",
    ) -> None:
        self._inner = WriteGuardBackend(root_dir=root_dir, writable_dirs=writable_dirs)
        self._events_log_path = events_log_path
        self._session_id = session_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _log_read_tool(self, tool_name: str, **params: Any) -> None:
        # Skip if we're inside a dedicated tool call that handles its own logging.
        # This prevents double-logging when a dedicated tool calls a backend method.
        if not self._events_log_path or in_tool_call():
            return
        record = {
            "timestamp": _utc_now_iso(),
            "type": "tool_call",
            "session_id": self._session_id,
            "tool": tool_name,
            "args": params,
        }
        _append_jsonl(self._events_log_path, record)

    def read(self, file_path: str, **kwargs) -> str:
        self._log_read_tool("read_file", file_path=file_path, **kwargs)
        return self._inner.read(file_path, **kwargs)

    async def aread(self, file_path: str, **kwargs) -> str:
        self._log_read_tool("read_file", file_path=file_path, **kwargs)
        return await self._inner.aread(file_path, **kwargs)

    def ls_info(self, path: str) -> list:
        self._log_read_tool("ls", path=path)
        return self._inner.ls_info(path)

    async def als_info(self, path: str) -> list:
        self._log_read_tool("ls", path=path)
        return await self._inner.als_info(path)

    def grep_raw(self, pattern: str, **kwargs) -> dict:
        self._log_read_tool("grep", pattern=pattern, **kwargs)
        return self._inner.grep_raw(pattern, **kwargs)

    async def agrep_raw(self, pattern: str, **kwargs) -> dict:
        self._log_read_tool("grep", pattern=pattern, **kwargs)
        return await self._inner.agrep_raw(pattern, **kwargs)

    def glob_info(self, pattern: str, path: str = "/") -> list:
        self._log_read_tool("glob", pattern=pattern, path=path)
        return self._inner.glob_info(pattern, path=path)

    async def aglob_info(self, pattern: str, path: str = "/") -> list:
        self._log_read_tool("glob", pattern=pattern, path=path)
        return await self._inner.aglob_info(pattern, path=path)

    def execute(self, command: str, **kwargs) -> dict:
        self._log_read_tool("bash", command=command, **kwargs)
        return self._inner.execute(command, **kwargs)

    async def aexecute(self, command: str, **kwargs) -> dict:
        self._log_read_tool("bash", command=command, **kwargs)
        return await self._inner.aexecute(command, **kwargs)

    def download_files(self, paths: list[str]) -> list:
        self._log_read_tool("read_file", paths=paths)
        return self._inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list:
        self._log_read_tool("read_file", paths=paths)
        return await self._inner.adownload_files(paths)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._inner.write(file_path=file_path, content=content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._inner.awrite(file_path=file_path, content=content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._inner.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._inner.aedit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._inner.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._inner.aupload_files(files)


class WriteGuardBackend:
    """A filesystem backend that restricts writes to specific directories.

    Used by the main agent (writable_dirs from config) and by climber
    subprocesses (writable_dirs=["workspace"] for Law 4 enforcement).
    """

    def __init__(self, root_dir: Path, writable_dirs: list[str]) -> None:
        self._fs = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
        self._writable_roots = [
            PurePosixPath("/" + d.strip("/")) for d in writable_dirs
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fs, name)

    def _is_write_allowed(self, file_path: str) -> bool:
        path = PurePosixPath("/" + file_path.lstrip("/"))
        return any(
            path == root or root in path.parents
            for root in self._writable_roots
        )

    def _allowed_dirs_label(self) -> str:
        return ", ".join(f"{r}/" for r in self._writable_roots)

    def write(self, file_path: str, content: str) -> WriteResult:
        if not self._is_write_allowed(file_path):
            return WriteResult(
                error=f"Write blocked. Writable directories: {self._allowed_dirs_label()}",
            )
        return self._fs.write(file_path=file_path, content=content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path=file_path, content=content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if not self._is_write_allowed(file_path):
            return EditResult(
                error=f"Edit blocked. Writable directories: {self._allowed_dirs_label()}",
            )
        return self._fs.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        blocked = [path for path, _ in files if not self._is_write_allowed(path)]
        if blocked:
            return [FileUploadResponse(path=p, error="permission_denied") for p in blocked]
        return self._fs.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


class ReadOnlyFilesystemBackend:
    def __init__(self, root_dir: Path) -> None:
        self._fs = FilesystemBackend(root_dir=root_dir, virtual_mode=True)

    def __getattr__(self, name: str):
        return getattr(self._fs, name)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=f"Write blocked. '{file_path}' is read-only.")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path=file_path, content=content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=f"Edit blocked. '{file_path}' is read-only.")

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


def build_builtin_skills_backend(root_dir: Path | None = None) -> ReadOnlyFilesystemBackend:
    target_root = root_dir if root_dir is not None else materialize_builtin_skills()
    return ReadOnlyFilesystemBackend(root_dir=target_root)
