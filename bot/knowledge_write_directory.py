"""Keep knowledge reads and writes inside an opened directory."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import BinaryIO

    from bot.knowledge_windows_files import WindowsFiles


def _regular_file(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


@contextmanager
def open_knowledge_directory(path: Path, *, create: bool = False) -> Iterator[KnowledgeDirectory]:
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        with _open_windows_directory(path, create=create) as directory:
            yield directory
    else:
        with ExitStack() as stack:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root = os.open(path.anchor, flags)
            stack.callback(os.close, root)
            for name in path.parts[1:]:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(name, dir_fd=root)
                root = os.open(name, flags, dir_fd=root)
                stack.callback(os.close, root)
            yield _PosixDirectory(path, root)


class _PosixDirectory:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd

    def names(self) -> list[str]:
        return os.listdir(self.fd)

    def stat(self, name: str) -> os.stat_result:
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def exists(self, name: str) -> bool:
        try:
            self.stat(name)
        except FileNotFoundError:
            return False
        return True

    def open_read(self, name: str) -> int:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)

    def create_empty(self, name: str) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        os.close(os.open(name, flags, 0o600, dir_fd=self.fd))

    def open_lock(self, name: str) -> int:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
        try:
            if not _regular_file(os.fstat(fd)):
                raise OSError("학습 자료 잠금 파일은 일반 파일이어야 합니다.")
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def stage(self, contents: bytes):
        name = f".staged-{uuid4().hex}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)

        def publish(destination: str, *, replace: bool = False) -> None:
            if not os.path.samestat(os.fstat(fd), self.stat(name)):
                raise OSError("게시하기 전에 임시 학습 자료가 변경되었습니다.")
            if replace:
                os.replace(name, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.link(name, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)

        try:
            with os.fdopen(fd, "wb") as output:
                _write_stage(output, contents)
                yield publish
        finally:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=self.fd)


def _write_stage(output: BinaryIO, contents: bytes) -> None:
    output.write(contents)
    output.flush()
    os.fsync(output.fileno())


@contextmanager
def _open_windows_directory(path: Path, *, create: bool) -> Iterator[_WindowsDirectory]:
    # Import Win32 bindings only on Windows.
    from bot.knowledge_windows_files import WindowsFiles

    api = WindowsFiles()
    with ExitStack() as stack:
        root = api.open_anchor(path.anchor, stack)
        for name in path.parts[1:]:
            root = api.open_directory(root, name, stack, create=create)
        yield _WindowsDirectory(path, root, api)


class _WindowsDirectory:
    def __init__(self, path: Path, handle: int, api: WindowsFiles) -> None:
        self.path = path
        self.handle = handle
        self.api = api

    def names(self) -> list[str]:
        return self.api.names(self.handle)

    def stat(self, name: str) -> os.stat_result:
        fd = self.api.open_metadata(self.handle, name)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)

    def exists(self, name: str) -> bool:
        return self.api.exists(self.handle, name)

    def open_read(self, name: str) -> int:
        return self.api.open_read(self.handle, name)

    def create_empty(self, name: str) -> None:
        self.api.create_empty(self.handle, name)

    def open_lock(self, name: str) -> int:
        return self.api.open_lock(self.handle, name)

    @contextmanager
    def stage(self, contents: bytes):
        name = f".staged-{uuid4().hex}.tmp"
        fd = self.api.create_stage(self.handle, name)
        published = False

        def publish(destination: str, *, replace: bool = False) -> None:
            nonlocal published
            self.api.rename_stage(fd, destination, replace=replace)
            published = True

        with os.fdopen(fd, "wb") as output:
            try:
                _write_stage(output, contents)
                yield publish
            finally:
                if not published:
                    self.api.remove_stage(fd)


KnowledgeDirectory = _PosixDirectory | _WindowsDirectory
