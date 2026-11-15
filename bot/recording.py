"""Own microphone requests and their in-memory PCM until claimed or discarded."""

from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from threading import Event, Lock, Thread
from time import monotonic
from typing import Protocol
import wave


SAMPLE_RATE = 16_000
MAX_SECONDS = 180


class CaptureError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AudioInput(Protocol):
    def devices(self) -> list[dict[str, str]]: ...

    def capture(
        self, device_id: str | None, started: Callable[[], None],
        receive: Callable[[bytes], None], stopped: Event, deadline: float,
    ) -> None:
        """Deliver mono signed 16-bit little-endian PCM at SAMPLE_RATE."""
        ...


@dataclass(frozen=True)
class Recording:
    pcm: bytes

    def to_wav(self) -> bytes:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            output.writeframes(self.pcm)
        return buffer.getvalue()


@dataclass
class _Request:
    id: int
    started_at: float
    deadline: float
    meter_only: bool
    stopped: Event = field(default_factory=Event)
    pcm: bytearray = field(default_factory=bytearray)
    status: str = "starting"
    error: str | None = None
    reason: str | None = None
    ended_at: float | None = None
    level: float = 0.0


class MicrophoneCapture:
    def __init__(self, audio: AudioInput, *, max_seconds: float = MAX_SECONDS) -> None:
        self._audio = audio
        self._max_seconds = max_seconds
        self._lock = Lock()
        self._request: _Request | None = None
        self._worker: Thread | None = None
        self._next_id = 0
        self._closed = False

    def devices(self) -> dict:
        try:
            return {"ok": True, "devices": self._audio.devices()}
        except CaptureError as error:
            return {"ok": False, "error": error.code}

    def start(self, device_id: str | None = None, meter_only: bool = False) -> dict:
        if (device_id is not None and (not isinstance(device_id, str) or not device_id.strip())
                or not isinstance(meter_only, bool)):
            return {"ok": False, "error": "invalid_request"}
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "closed"}
            if self._worker is not None:
                return {"ok": False, "error": "busy"}
            self._next_id += 1
            now = monotonic()
            request = _Request(self._next_id, now, now + self._max_seconds, meter_only)
            if self._request is not None:
                self._request.pcm.clear()
            self._request = request
            worker = Thread(target=self._capture, args=(request, device_id), name="microphone-capture", daemon=True)
            self._worker = worker
            try:
                worker.start()
            except RuntimeError:
                self._worker = None
                request.status = "failed"
                request.error = "capture_failed"
                request.ended_at = now
            return self._snapshot(request)

    def status(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            return self._snapshot(request) if request else {"ok": False, "error": "stale_request"}

    def stop(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            if request is None:
                return {"ok": False, "error": "stale_request"}
            if request.status in ("starting", "recording"):
                now = monotonic()
                request.status = "stopping"
                request.reason = "limit" if now >= request.deadline else "manual"
                request.ended_at = min(now, request.deadline)
                request.stopped.set()
            return self._snapshot(request)

    def cancel(self, request_id: int) -> dict:
        with self._lock:
            request = self._find(request_id)
            if request is None:
                return {"ok": False, "error": "stale_request"}
            self._cancel(request)
            return self._snapshot(request)

    def take_recording(self, request_id: int) -> Recording | None:
        """Transfer the completed recording once to its Python consumer."""
        with self._lock:
            request = self._find(request_id)
            if request is None or request.status != "completed" or not request.pcm:
                return None
            recording = Recording(bytes(request.pcm))
            request.pcm.clear()
            return recording

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._request is not None:
                self._cancel(self._request)
            worker = self._worker
        if worker is not None:
            worker.join()

    def _find(self, request_id: int) -> _Request | None:
        request = self._request
        if type(request_id) is int and request is not None and request.id == request_id:
            return request
        return None

    @staticmethod
    def _cancel(request: _Request) -> None:
        request.status = "cancelled"
        request.error = None
        request.reason = None
        request.level = 0.0
        if request.ended_at is None:
            request.ended_at = min(monotonic(), request.deadline)
        request.pcm.clear()
        request.stopped.set()

    @staticmethod
    def _snapshot(request: _Request) -> dict:
        end = request.ended_at if request.ended_at is not None else min(monotonic(), request.deadline)
        return {
            "ok": True, "request_id": request.id, "status": request.status, "error": request.error,
            "reason": request.reason, "elapsed_ms": round((end - request.started_at) * 1000),
            "level": request.level, "meter_only": request.meter_only,
            "has_recording": request.status == "completed" and bool(request.pcm),
        }

    def _capture(self, request: _Request, device_id: str | None) -> None:
        def started() -> None:
            with self._lock:
                if request.status == "starting":
                    request.status = "recording"

        def receive(pcm: bytes) -> None:
            with self._lock:
                if request.stopped.is_set() or monotonic() >= request.deadline:
                    return
                if request.meter_only:
                    request.level = max(map(abs, memoryview(pcm).cast("h")), default=0) / 32768
                else:
                    request.pcm.extend(pcm[:capacity - len(request.pcm)])

        error_code = None
        try:
            capacity = 0 if request.meter_only else int(self._max_seconds * SAMPLE_RATE) * 2
            self._audio.capture(device_id, started, receive, request.stopped, request.deadline)
        except CaptureError as error:
            error_code = error.code
        except Exception:
            error_code = "capture_failed"
        finally:
            with self._lock:
                if request.status != "cancelled":
                    now = monotonic()
                    if request.ended_at is None:
                        request.ended_at = min(now, request.deadline)
                    if error_code is None and request.reason is None:
                        if now >= request.deadline:
                            request.reason = "limit"
                        else:
                            error_code = "capture_failed"
                    if error_code is None and not request.meter_only and not request.pcm:
                        error_code = "empty_recording"
                    request.level = 0.0
                    request.error = error_code
                    request.status = "failed" if error_code else "completed"
                    if error_code:
                        request.pcm.clear()
                self._worker = None
