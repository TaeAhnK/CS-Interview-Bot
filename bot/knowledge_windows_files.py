"""Windows file operations relative to an already-open directory handle."""

from __future__ import annotations

import ctypes
import msvcrt
import ntpath
import os
import stat
from contextlib import ExitStack
from ctypes import wintypes as w


class _UnicodeString(ctypes.Structure):
    _fields_ = [("length", w.USHORT), ("maximum_length", w.USHORT), ("buffer", w.LPVOID)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", w.ULONG), ("root", w.HANDLE), ("name", ctypes.POINTER(_UnicodeString)),
        ("attributes", w.ULONG), ("security_descriptor", w.LPVOID), ("security_qos", w.LPVOID),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("status_or_pointer", w.LPVOID), ("information", ctypes.c_size_t)]


class _RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", w.DWORD), ("root", w.HANDLE), ("name_length", w.DWORD), ("name", w.WCHAR * 1),
    ]


class _DirectoryEntry(ctypes.Structure):
    _fields_ = [
        ("next_offset", w.ULONG), ("index", w.ULONG), ("times_and_sizes", ctypes.c_int64 * 6),
        ("attributes", w.ULONG), ("name_length", w.ULONG), ("ea_size", w.ULONG), ("name", w.WCHAR * 1),
    ]


_READ = 0x80000000  # GENERIC_READ
_READ_ATTRIBUTES = 0x80  # FILE_READ_ATTRIBUTES
_READ_DIRECTORY = 0xA1  # FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES
_WRITE = 0x40000000  # GENERIC_WRITE
_DELETE = 0x00010000
_SHARE_READ_WRITE = 0x3
_SHARE_ALL = 0x7
_OPEN = 1
_CREATE = 2
_OPEN_IF = 3
_DIRECTORY = 0x1
_NON_DIRECTORY = 0x40
_OPEN_REPARSE_POINT = 0x00200000
_VOLUME_NAME_NONE = 0x4


def _bind(library, name, result, *args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = args
    return function


class WindowsFiles:
    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        self._open_anchor = _bind(kernel, "CreateFileW", w.HANDLE,
                                  w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
        self._close = _bind(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self._get_info = _bind(kernel, "GetFileInformationByHandleEx", w.BOOL,
                               w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
        self._final_path = _bind(kernel, "GetFinalPathNameByHandleW", w.DWORD,
                                 w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
        self._set_info = _bind(kernel, "SetFileInformationByHandle", w.BOOL,
                               w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
        self._create = _bind(nt, "NtCreateFile", ctypes.c_long,
                             ctypes.POINTER(w.HANDLE), w.ULONG, ctypes.POINTER(_ObjectAttributes),
                             ctypes.POINTER(_IoStatus), w.LPVOID, w.ULONG, w.ULONG, w.ULONG,
                             w.ULONG, w.LPVOID, w.ULONG)
        self._dos_error = _bind(nt, "RtlNtStatusToDosError", w.ULONG, ctypes.c_long)
        self._nt_set_info = _bind(nt, "NtSetInformationFile", ctypes.c_long, w.HANDLE,
                                  ctypes.POINTER(_IoStatus), w.LPVOID, w.ULONG, ctypes.c_int)

    def _open_child(self, root: int, name: str, access: int, share: int, disposition: int, options: int) -> int:
        if not name or name in (".", "..") or any(char in name for char in "/\\:\0"):
            raise ValueError("Expected one filename component")
        encoded = name.encode("utf-16-le", errors="surrogatepass")
        buffer = ctypes.create_string_buffer(encoded + b"\0\0")
        string = _UnicodeString(len(encoded), len(encoded) + 2, ctypes.addressof(buffer))
        # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE. No child lookup can restart
        # through a reparse point. For existing files also inspect the opened leaf.
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), root, ctypes.pointer(string), 0x1040)
        handle = w.HANDLE()
        status = self._create(ctypes.byref(handle), access | 0x00100000, ctypes.byref(attributes),
                              ctypes.byref(_IoStatus()), None, 0, share, disposition, options | 0x20, None, 0)
        if status < 0:
            raise ctypes.WinError(self._dos_error(status))
        return handle.value

    def _attributes(self, handle: int) -> int:
        info = (w.DWORD * 2)()
        if not self._get_info(handle, 9, info, ctypes.sizeof(info)):  # FileAttributeTagInfo
            raise ctypes.WinError(ctypes.get_last_error())
        return info[0]

    def _check_directory(self, handle: int, stack: ExitStack) -> int:
        stack.callback(self._close, handle)
        attributes = self._attributes(handle)
        if not attributes & stat.FILE_ATTRIBUTE_DIRECTORY or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("학습 자료 경로는 실제 디렉터리여야 합니다.")
        return handle

    def open_anchor(self, anchor: str, stack: ExitStack) -> int:
        flags = 0x02000000 | _OPEN_REPARSE_POINT  # FILE_FLAG_BACKUP_SEMANTICS
        handle = self._open_anchor(anchor, _READ_DIRECTORY, _SHARE_READ_WRITE, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return self._check_directory(handle, stack)

    def open_directory(self, root: int, name: str, stack: ExitStack, *, create: bool) -> int:
        try:
            handle = self._open_child(root, name, _READ_DIRECTORY, _SHARE_READ_WRITE, _OPEN, _OPEN_REPARSE_POINT)
        except FileNotFoundError:
            if not create:
                raise
            try:
                handle = self._open_child(root, name, _READ_DIRECTORY, _SHARE_READ_WRITE, _CREATE, _DIRECTORY)
            except FileExistsError:
                handle = self._open_child(root, name, _READ_DIRECTORY, _SHARE_READ_WRITE, _OPEN, _OPEN_REPARSE_POINT)
        return self._check_directory(handle, stack)

    def names(self, root: int) -> list[str]:
        buffer = (ctypes.c_int64 * 8192)()
        size = ctypes.sizeof(buffer)
        base = ctypes.addressof(buffer)
        info_class = 15  # FileFullDirectoryRestartInfo
        names = []
        while self._get_info(root, info_class, buffer, size):
            info_class = 14  # FileFullDirectoryInfo
            offset = 0
            while True:
                entry = _DirectoryEntry.from_address(base + offset)
                if not entry.attributes & (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    name = ctypes.string_at(base + offset + _DirectoryEntry.name.offset, entry.name_length)
                    names.append(name.decode("utf-16-le", errors="surrogatepass"))
                if not entry.next_offset:
                    break
                offset += entry.next_offset
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(error)
        return names

    def exists(self, root: int, name: str) -> bool:
        try:
            handle = self._open_child(root, name, _READ_ATTRIBUTES, _SHARE_ALL, _OPEN, _OPEN_REPARSE_POINT)
        except FileNotFoundError:
            return False
        self._close(handle)
        return True

    def _open_fd(
        self, root: int, name: str, access: int, share: int, disposition: int, flags: int,
        *, exact_name: bool = False, metadata_only: bool = False,
    ) -> int:
        options = _OPEN_REPARSE_POINT if metadata_only else _NON_DIRECTORY | _OPEN_REPARSE_POINT
        handle = self._open_child(root, name, access, share, disposition, options)
        try:
            if self._attributes(handle) & (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise FileNotFoundError("학습 자료는 일반 파일이어야 합니다.")
            if exact_name:
                # Keep the editor's canonical filename check for case and 8.3 aliases.
                # The directory boundary is enforced by the relative open above.
                buffer = ctypes.create_unicode_buffer(32768)
                length = self._final_path(handle, buffer, len(buffer), _VOLUME_NAME_NONE)
                if not length:
                    raise ctypes.WinError(ctypes.get_last_error())
                if length >= len(buffer) or ntpath.basename(buffer.value) != name:
                    raise FileNotFoundError("학습 자료의 실제 파일명을 사용해 주세요.")
            return msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)
        except BaseException:
            self._close(handle)
            raise

    def open_read(self, root: int, name: str) -> int:
        return self._open_fd(root, name, _READ, _SHARE_ALL, _OPEN, os.O_RDONLY, exact_name=True)

    def open_metadata(self, root: int, name: str) -> int:
        return self._open_fd(root, name, _READ_ATTRIBUTES, _SHARE_ALL, _OPEN, os.O_RDONLY, metadata_only=True)

    def open_lock(self, root: int, name: str) -> int:
        fd = self._open_fd(root, name, _READ | _WRITE, _SHARE_READ_WRITE, _OPEN_IF, os.O_RDWR)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError("학습 자료 잠금 파일은 일반 파일이어야 합니다.")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def create_stage(self, root: int, name: str) -> int:
        return self._open_fd(root, name, _WRITE | _DELETE | _READ_ATTRIBUTES, 0, _CREATE, os.O_WRONLY)

    def create_empty(self, root: int, name: str) -> None:
        os.close(self._open_fd(root, name, _WRITE | _READ_ATTRIBUTES, _SHARE_ALL, _CREATE, os.O_WRONLY))

    def rename_stage(self, fd: int, name: str, *, replace: bool) -> None:
        # A simple name with RootDirectory == NULL renames within the opened
        # source file's directory. The kernel does not reopen the source by path.
        if not name or name in (".", "..") or any(char in name for char in "/\\:\0"):
            raise ValueError("Expected one filename component")
        encoded = name.encode("utf-16-le", errors="surrogatepass")
        offset = _RenameInfo.name.offset
        size = max(ctypes.sizeof(_RenameInfo), offset + len(encoded) + 2)
        buffer = ctypes.create_string_buffer(size)
        info = _RenameInfo.from_buffer(buffer)
        info.replace = replace
        info.name_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        status = self._nt_set_info(msvcrt.get_osfhandle(fd), ctypes.byref(_IoStatus()), buffer, size, 10)
        if status < 0:  # FileRenameInformation
            raise ctypes.WinError(self._dos_error(status))

    def remove_stage(self, fd: int) -> None:
        delete_file = w.BYTE(1)
        if not self._set_info(msvcrt.get_osfhandle(fd), 4, ctypes.byref(delete_file), ctypes.sizeof(delete_file)):
            raise ctypes.WinError(ctypes.get_last_error())
