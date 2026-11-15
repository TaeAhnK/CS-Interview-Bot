"""Local whisper.cpp CLI adapter for Windows CPU transcription."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic

from bot.transcription import TranscriptionError


@dataclass(frozen=True)
class WhisperCppConfig:
    executable: Path
    model_path: Path
    language: str = "ko"
    threads: int = 4
    timeout_seconds: float = 240

    @classmethod
    def from_app_root(cls, root: Path) -> "WhisperCppConfig":
        return cls(
            root / "runtime" / "whisper" / "whisper-cli.exe",
            root / "models" / "stt" / "ggml-small-q5_1.bin",
        )


class WhisperCppRecognizer:
    def __init__(self, config: WhisperCppConfig) -> None:
        self._config = config

    def transcribe(self, wav: bytes, cancelled: Event) -> str:
        config = self._config
        executable = config.executable.absolute()
        model_path = config.model_path.absolute()
        if cancelled.is_set():
            raise TranscriptionError("cancelled")
        if not executable.is_file():
            raise TranscriptionError("engine_unavailable")
        if not model_path.is_file():
            raise TranscriptionError("model_missing")

        with TemporaryDirectory(prefix="cs-interview-stt-") as directory:
            audio = Path(directory) / "input.wav"
            output = Path(directory) / "transcript"
            audio.write_bytes(wav)
            if cancelled.is_set():
                raise TranscriptionError("cancelled")
            command = [
                str(executable), "-m", str(model_path), "-f", str(audio),
                "-l", config.language, "-t", str(config.threads),
                "-ng", "-nt", "-otxt", "-of", str(output),
            ]
            try:
                process = subprocess.Popen(
                    command, cwd=executable.parent,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as error:
                raise TranscriptionError("engine_unavailable") from error
            deadline = monotonic() + config.timeout_seconds
            try:
                while (code := process.poll()) is None:
                    remaining = deadline - monotonic()
                    if cancelled.is_set():
                        raise TranscriptionError("cancelled")
                    if remaining <= 0:
                        raise TranscriptionError("timeout")
                    cancelled.wait(min(remaining, 0.05))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
            if cancelled.is_set():
                raise TranscriptionError("cancelled")
            if code & 0xFFFFFFFF == 0xC0000135:  # STATUS_DLL_NOT_FOUND
                raise TranscriptionError("engine_unavailable")
            if code:
                raise TranscriptionError("transcription_failed")
            try:
                return output.with_suffix(".txt").read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError) as error:
                raise TranscriptionError("transcription_failed") from error
