"""Prepare recorded audio and own asynchronous speech recognition requests."""

from array import array
from dataclasses import dataclass, field
from sys import byteorder
from threading import Event, Lock, Thread
from typing import Protocol

from bot.recording import MAX_SECONDS, SAMPLE_RATE, MicrophoneCapture, Recording


SILENCE_AMPLITUDE = 327  # Approximately -40 dBFS peak.
SILENCE_FRAMES = 5 * SAMPLE_RATE


class TranscriptionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SpeechRecognizer(Protocol):
    def transcribe(self, wav: bytes, cancelled: Event) -> str:
        """Recognize PCM16 mono 16 kHz WAV, observing cancellation."""
        ...


def trim_silence(pcm: bytes, cancelled: Event) -> bytes:
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 2 * SAMPLE_RATE * MAX_SECONDS:
        raise TranscriptionError("invalid_audio")
    pcm_view = memoryview(pcm)
    samples = pcm_view.cast("h")
    if byteorder != "little":
        samples = array("h", pcm)
        samples.byteswap()
        samples = memoryview(samples)
    kept = []
    kept_from = silent_from = 0
    for block_start in range(0, len(samples), SAMPLE_RATE):
        if cancelled.is_set():
            raise TranscriptionError("cancelled")
        block = samples[block_start:block_start + SAMPLE_RATE]
        for index, sample in enumerate(block, block_start):
            if abs(sample) > SILENCE_AMPLITUDE:
                if index - silent_from >= SILENCE_FRAMES:
                    kept.append(pcm_view[kept_from * 2:silent_from * 2])
                    kept_from = index
                silent_from = index + 1
    if not silent_from:
        return b""
    end = silent_from if len(samples) - silent_from >= SILENCE_FRAMES else len(samples)
    if kept_from == 0 and end == len(samples):
        return pcm
    kept.append(pcm_view[kept_from * 2:end * 2])
    return b"".join(kept)


@dataclass
class _Request:
    id: int
    recording_id: int
    cancelled: Event = field(default_factory=Event)
    status: str = "transcribing"
    error: str | None = None
    text: str | None = None

    def snapshot(self) -> dict:
        return {
            "ok": True, "request_id": self.id, "recording_id": self.recording_id,
            "status": self.status, "error": self.error, "has_result": self.text is not None,
        }


class TranscriptionService:
    def __init__(self, microphone: MicrophoneCapture, recognizer: SpeechRecognizer) -> None:
        self._microphone = microphone
        self._recognizer = recognizer
        self._lock = Lock()
        self._current: _Request | None = None
        self._worker: Thread | None = None
        self._closed = False

    def start(self, recording_id: int) -> dict:
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "closed"}
            if self._worker is not None:
                return {"ok": False, "error": "busy"}
            recording = self._microphone.take_recording(recording_id)
            if recording is None:
                return {"ok": False, "error": "recording_unavailable"}
            request = _Request(self._current.id + 1 if self._current else 1, recording_id)
            self._current = request
            worker = Thread(target=self._run, args=(request, recording), daemon=True, name="speech-recognition")
            self._worker = worker
            try:
                worker.start()
            except RuntimeError:
                self._worker = None
                request.status = "failed"
                request.error = "transcription_failed"
            return request.snapshot()

    def status(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            return request.snapshot() if request else {"ok": False, "error": "stale_request"}

    def take_result(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            if request is None:
                return {"ok": False, "error": "stale_request"}
            if request.text is None:
                return {"ok": False, "error": "result_unavailable"}
            text, request.text = request.text, None
            return {"ok": True, "request_id": request.id, "recording_id": request.recording_id, "text": text}

    def cancel(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            if request is None:
                return {"ok": False, "error": "stale_request"}
            self._cancel(request)
            return request.snapshot()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._current is not None:
                self._cancel(self._current)
            worker = self._worker
        if worker is not None:
            worker.join()

    def _find(self, request_id: int) -> _Request | None:
        if type(request_id) is int and self._current and self._current.id == request_id:
            return self._current
        return None

    @staticmethod
    def _cancel(request: _Request) -> None:
        request.cancelled.set()
        request.status = "cancelled"
        request.error = None
        request.text = None

    def _run(self, request: _Request, recording: Recording) -> None:
        text = ""
        error_code = None
        try:
            pcm = trim_silence(recording.pcm, request.cancelled)
            del recording
            if pcm and not request.cancelled.is_set():
                wav = Recording(pcm).to_wav()
                del pcm
                text = self._recognizer.transcribe(wav, request.cancelled).strip()
        except TranscriptionError as error:
            error_code = error.code
        except Exception:
            error_code = "transcription_failed"
        finally:
            with self._lock:
                if not request.cancelled.is_set():
                    request.error = error_code
                    request.text = (text or None) if error_code is None else None
                    request.status = "failed" if error_code else ("completed" if text else "empty")
                self._worker = None
