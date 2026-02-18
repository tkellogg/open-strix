from __future__ import annotations

from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult

from .builtin_skills import materialize_builtin_skills

BUILTIN_SKILLS_ROUTE = "/.open_strix_builtin_skills/"


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


def build_builtin_skills_backend() -> ReadOnlyFilesystemBackend:
    return ReadOnlyFilesystemBackend(root_dir=materialize_builtin_skills())
