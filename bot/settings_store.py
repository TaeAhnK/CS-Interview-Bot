"""User settings and Windows DPAPI protected API-key storage."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol


PROVIDERS = ("gemini", "openai")
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "openai": "gpt-5-codex"}
APP_DATA_DIRECTORY_NAME = "CS Interview Bot"
KEY_FILE_RELATIVE_PATH = Path("secrets") / "api-keys.dpapi"
SETTINGS_FILE_NAME = "settings.json"


class SettingsError(Exception):
    """Safe, user-displayable settings error with no secret-bearing detail."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Protector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...
    def unprotect(self, ciphertext: bytes) -> bytes: ...


class WindowsDPAPIProtector:
    """Protect bytes with CryptProtectData for the current Windows user."""

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self) -> None:
        if os.name != "nt":
            raise SettingsError("dpapi_unavailable")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(self._Blob), ctypes.c_wchar_p, ctypes.POINTER(self._Blob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(self._Blob),
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_bool
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(self._Blob), ctypes.c_void_p, ctypes.POINTER(self._Blob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(self._Blob),
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_bool
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @classmethod
    def _input_blob(cls, data: bytes):
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return cls._Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    def _crypt(self, data: bytes, *, decrypt: bool) -> bytes:
        source, keepalive = self._input_blob(data)
        output = self._Blob()
        if decrypt:
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
            )
        else:
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(source), "CS Interview Bot API keys", None, None, None, 0,
                ctypes.byref(output),
            )
        _ = keepalive
        if not ok:
            raise SettingsError("decrypt_failed" if decrypt else "encrypt_failed")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def protect(self, plaintext: bytes) -> bytes:
        return self._crypt(plaintext, decrypt=False)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._crypt(ciphertext, decrypt=True)


@dataclass(frozen=True)
class PublicSettings:
    provider: str
    models: dict[str, str]
    key_registered: dict[str, bool]


def user_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_DATA_DIRECTORY_NAME


class SettingsStore:
    """Persist non-secret preferences separately from encrypted API keys."""

    def __init__(
        self, root: Path | None = None, protector: Protector | None = None, *,
        knowledge_directory: Path | None = None,
        key_relative_path: Path = KEY_FILE_RELATIVE_PATH,
    ) -> None:
        self.root = root or user_data_directory()
        self.key_path = self.root / key_relative_path
        self.knowledge_directory = knowledge_directory or self.root / "knowledge"
        self.settings_path = self.root / SETTINGS_FILE_NAME
        self.protector = protector
        self._lock = Lock()

    def _get_protector(self) -> Protector:
        if self.protector is None:
            self.protector = WindowsDPAPIProtector()
        return self.protector

    def _read_preferences(self) -> tuple[str, dict[str, str]]:
        defaults = ("gemini", dict(DEFAULT_MODELS))
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defaults
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SettingsError("settings_unavailable") from error
        provider = data.get("provider") if isinstance(data, dict) else None
        models = data.get("models") if isinstance(data, dict) else None
        if provider not in PROVIDERS or not isinstance(models, dict):
            raise SettingsError("settings_unavailable")
        clean_models = {}
        for name in PROVIDERS:
            model = models.get(name, DEFAULT_MODELS[name])
            if not isinstance(model, str) or not model.strip() or len(model) > 120:
                raise SettingsError("settings_unavailable")
            clean_models[name] = model.strip()
        return provider, clean_models

    def _read_keys(self) -> dict[str, str]:
        try:
            encrypted = self.key_path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise SettingsError("key_file_unavailable") from error
        try:
            raw = self._get_protector().unprotect(encrypted)
            data = json.loads(raw.decode("utf-8"))
        except SettingsError:
            raise
        except Exception as error:
            raise SettingsError("decrypt_failed") from error
        if not isinstance(data, dict) or any(
            key not in PROVIDERS or not isinstance(value, str) for key, value in data.items()
        ):
            raise SettingsError("decrypt_failed")
        return data

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
                temp_path = Path(file.name)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as error:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SettingsError("write_failed") from error

    def public_settings(self) -> PublicSettings:
        with self._lock:
            provider, models = self._read_preferences()
            return self._public_settings(provider, models, self._read_keys())

    @staticmethod
    def _public_settings(provider: str, models: dict[str, str], keys: dict[str, str]) -> PublicSettings:
        return PublicSettings(provider, models, {name: name in keys for name in PROVIDERS})

    def save_preferences(self, provider: str, models: dict[str, str]) -> PublicSettings:
        if provider not in PROVIDERS or not isinstance(models, dict):
            raise SettingsError("invalid_settings")
        normalized: dict[str, str] = {}
        for name in PROVIDERS:
            value = models.get(name, DEFAULT_MODELS[name])
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
                raise SettingsError("invalid_settings")
            normalized[name] = value.strip()
        payload = json.dumps({"provider": provider, "models": normalized}, ensure_ascii=False, indent=2).encode("utf-8")
        with self._lock:
            keys = self._read_keys()
            self._atomic_write(self.settings_path, payload)
            return self._public_settings(provider, normalized, keys)

    def set_key(self, provider: str, value: str) -> PublicSettings:
        if provider not in PROVIDERS or not isinstance(value, str) or not value.strip():
            raise SettingsError("invalid_key")
        with self._lock:
            selected_provider, models = self._read_preferences()
            keys = self._read_keys()
            keys[provider] = value.strip()
            self._write_keys(keys)
            return self._public_settings(selected_provider, models, keys)

    def _write_keys(self, keys: dict[str, str]) -> None:
        if not keys:
            try:
                self.key_path.unlink(missing_ok=True)
            except OSError as error:
                raise SettingsError("write_failed") from error
            return
        try:
            encrypted = self._get_protector().protect(
                json.dumps(keys, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        except SettingsError:
            raise
        except Exception as error:
            raise SettingsError("encrypt_failed") from error
        self._atomic_write(self.key_path, encrypted)

    def delete_key(self, provider: str) -> PublicSettings:
        if provider not in PROVIDERS:
            raise SettingsError("invalid_provider")
        with self._lock:
            selected_provider, models = self._read_preferences()
            keys = self._read_keys()
            keys.pop(provider, None)
            self._write_keys(keys)
            return self._public_settings(selected_provider, models, keys)

    def get_key_for_provider(self, provider: str) -> str | None:
        if provider not in PROVIDERS:
            raise SettingsError("invalid_provider")
        with self._lock:
            return self._read_keys().get(provider)

    def open_knowledge_folder(self) -> None:
        path = self.knowledge_directory
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys_platform_is_macos():
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as error:
            raise SettingsError("folder_open_failed") from error


def sys_platform_is_macos() -> bool:
    import sys

    return sys.platform == "darwin"
