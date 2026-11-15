"""Engine-independent speech requests and playback lifecycle."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from functools import partial
from io import BytesIO
from math import pi, sin
from struct import pack
from threading import Event, Lock
from typing import Callable, Literal, Protocol
import wave


logger = logging.getLogger(__name__)


class SpeechError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Synthesizer(Protocol):
    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Return a complete PCM WAV file."""
        ...


@dataclass(frozen=True)
class OutputDevice:
    id: str
    name: str


class AudioOutput(Protocol):
    def devices(self) -> list[OutputDevice]: ...

    def play(self, wav: bytes, device_id: str | None, volume: float, stopped: Event) -> None: ...


@dataclass(frozen=True)
class SpeechStatus:
    request_id: int
    status: Literal["idle", "synthesizing", "playing", "completed", "stopped", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class _Request:
    id: int
    stopped: Event = field(default_factory=Event)
    synthesis_ready: Event = field(default_factory=Event)

    def cancel(self) -> None:
        self.stopped.set()
        self.synthesis_ready.set()


def spoken_text(text: str) -> str:
    # Collect matched ranges first so an unmatched opening parenthesis cannot
    # swallow the rest of the question.
    stack: list[int] = []
    ranges: dict[int, int] = {}
    for index, char in enumerate(text):
        if char == "(":
            stack.append(index)
        elif char == ")" and stack:
            ranges[stack.pop()] = index + 1
    parts = []
    index = 0
    while index < len(text):
        end = ranges.get(index)
        if end is None:
            parts.append(text[index])
            index += 1
        else:
            index = end
    return "".join(parts).strip()


def valid_output_preferences(device_id: str | None, volume: float) -> bool:
    return (
        (device_id is None or isinstance(device_id, str) and bool(device_id.strip()))
        and not isinstance(volume, bool)
        and isinstance(volume, (int, float))
        and 0 <= volume <= 1
    )


def _speaker_test_tone() -> bytes:
    rate = 44100
    frames = rate * 3 // 4
    fade = rate // 50
    samples = bytearray()
    for frame in range(frames):
        gain = min(frame, frames - frame - 1, fade) / fade
        samples.extend(pack("<h", round(8192 * gain * sin(2 * pi * 660 * frame / rate))))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples)
    return buffer.getvalue()


class SpeechPlayback:
    def __init__(self, synthesizer: Synthesizer, output: AudioOutput) -> None:
        self._synthesizer = synthesizer
        self._output = output
        self._lock = Lock()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._synthesis = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-synthesis")
        self._future: Future[None] | None = None
        self._request = _Request(0)
        self._closed = False
        self._status = SpeechStatus(0, "idle")

    def devices(self) -> dict:
        try:
            return {"ok": True, "devices": [asdict(device) for device in self._output.devices()]}
        except SpeechError as error:
            return {"ok": False, "error": error.code}

    def status(self) -> dict:
        with self._lock:
            return asdict(self._status)

    def start(self, text: str, device_id: str | None, volume: float) -> dict:
        if not isinstance(text, str) or not valid_output_preferences(device_id, volume):
            return self._start("failed", error="invalid_request")
        return self._start_question(spoken_text(text), device_id, volume)

    def start_test(self, device_id: str | None, volume: float) -> dict:
        if not valid_output_preferences(device_id, volume):
            return self._start("failed", error="invalid_request")
        return self._start("playing", partial(self._play_test, device_id, volume))

    def start_study(
        self, question: str, answer: str, device_id: str | None, volume: float,
        answer_output: Callable[[], tuple[str | None, float]],
    ) -> dict:
        if not isinstance(question, str) or not isinstance(answer, str) or not valid_output_preferences(device_id, volume):
            return self._start("failed", error="invalid_request")
        question, answer = spoken_text(question), spoken_text(answer)
        if not answer:
            return self._start_question(question, device_id, volume)
        return self._start("synthesizing", partial(self._read_study, question, answer, device_id, volume, answer_output))

    def _start_question(self, text: str, device_id: str | None, volume: float) -> dict:
        if not text:
            return self._start("completed")
        return self._start("synthesizing", partial(self._read_question, text, device_id, volume))

    def _start(
        self, status: Literal["failed", "completed", "playing", "synthesizing"],
        run: Callable[[_Request], None] | None = None, *, error: str | None = None,
    ) -> dict:
        with self._lock:
            if self._closed:
                return asdict(self._status)
            self._cancel_current()
            request_id = self._status.request_id + 1
            self._status = SpeechStatus(request_id, status, error)
            if run is not None:
                self._request = _Request(request_id)
                self._future = self._worker.submit(self._run, self._request, run)
            return asdict(self._status)

    def stop(self, request_id: int | None = None) -> dict:
        with self._lock:
            if (request_id is None or request_id == self._status.request_id) and self._status.status in (
                "synthesizing", "playing"
            ):
                self._cancel_current()
                self._status = SpeechStatus(self._status.request_id, "stopped")
            return asdict(self._status)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel_current()
            if self._status.status in ("synthesizing", "playing"):
                self._status = SpeechStatus(self._status.request_id, "stopped")
        self._worker.shutdown(wait=False, cancel_futures=True)
        self._synthesis.shutdown(wait=False, cancel_futures=True)

    def _cancel_current(self) -> None:
        self._request.cancel()
        if self._future is not None:
            self._future.cancel()
            self._future = None

    def _publish(
        self, request: _Request, status: Literal["playing", "synthesizing", "completed", "failed"],
        error: str | None = None,
    ) -> bool:
        with self._lock:
            if request.stopped.is_set():
                return False
            self._status = SpeechStatus(request.id, status, error)
            return True

    def _synthesize(self, text: str, voice: str | None, stopped: Event) -> bytes | None:
        if stopped.is_set():
            return None
        try:
            wav = self._synthesizer.synthesize(text) if voice is None else self._synthesizer.synthesize(text, voice)
        except SpeechError:
            raise
        except Exception as error:
            logger.exception("TTS request failed during synthesis_failed")
            raise SpeechError("synthesis_failed") from error
        return None if stopped.is_set() else wav

    def _queue_synthesis(self, text: str, voice: str | None, request: _Request) -> Future[bytes | None] | None:
        with self._lock:
            if request.stopped.is_set():
                return None
            request.synthesis_ready.clear()
            pending = self._synthesis.submit(self._synthesize, text, voice, request.stopped)
            pending.add_done_callback(lambda _: request.synthesis_ready.set())
            return pending

    @staticmethod
    def _wait_for_synthesis(request: _Request, pending: Future[bytes | None]) -> bytes | None:
        # Both synthesis completion and request cancellation wake playback.
        request.synthesis_ready.wait()
        return None if request.stopped.is_set() else pending.result()

    def _synthesize_question(self, text: str, request: _Request) -> bytes | None:
        pending = self._queue_synthesis(text, None, request)
        if pending is None:
            return None
        try:
            return self._wait_for_synthesis(request, pending)
        finally:
            pending.cancel()

    def _play(self, request: _Request, wav: bytes, device_id: str | None, volume: float) -> None:
        if self._publish(request, "playing"):
            self._output.play(wav, device_id, volume, request.stopped)

    def _play_test(self, device_id: str | None, volume: float, request: _Request) -> None:
        self._play(request, _speaker_test_tone(), device_id, volume)

    def _read_question(
        self, text: str, device_id: str | None, volume: float, request: _Request,
    ) -> None:
        wav = self._synthesize_question(text, request)
        if wav is not None:
            self._play(request, wav, device_id, volume)

    def _read_study(
        self, question: str, answer: str, device_id: str | None, volume: float,
        answer_output: Callable[[], tuple[str | None, float]], request: _Request,
    ) -> None:
        question_wav = self._synthesize_question(question, request) if question else None
        pending_answer = self._queue_synthesis(answer, "M1", request)
        if pending_answer is None:
            return
        try:
            if question_wav is not None:
                self._play(request, question_wav, device_id, volume)
            if not pending_answer.done() and not self._publish(request, "synthesizing"):
                return
            answer_wav = self._wait_for_synthesis(request, pending_answer)
            if answer_wav is not None:
                self._play(request, answer_wav, *answer_output())
        finally:
            pending_answer.cancel()

    def _run(self, request: _Request, run: Callable[[_Request], None]) -> None:
        try:
            run(request)
        except SpeechError as error:
            self._publish(request, "failed", error.code)
        except Exception:
            logger.exception("TTS request failed during playback_failed")
            self._publish(request, "failed", "playback_failed")
        else:
            self._publish(request, "completed")
        finally:
            request.cancel()
