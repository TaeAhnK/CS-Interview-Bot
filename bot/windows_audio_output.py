"""Play PCM WAV files on Windows WASAPI endpoints."""

from io import BytesIO
from threading import Event

from bot.tts import OutputDevice, SpeechError
from bot.windows_audio_devices import wasapi_devices, wasapi_stream


class WindowsAudioOutput:
    def devices(self) -> list[OutputDevice]:
        try:
            _, _, outputs = wasapi_devices("output")
        except Exception as error:
            raise SpeechError("audio_unavailable") from error
        return [OutputDevice(name, name) for name in outputs]

    def play(self, wav: bytes, device_id: str | None, volume: float, stopped: Event) -> None:
        if stopped.is_set():
            return
        import soundfile

        audio, sample_rate = soundfile.read(BytesIO(wav), dtype="float32", always_2d=True)

        def open_output(sd, default, outputs):
            nonlocal audio, sample_rate
            index = default if device_id is None else outputs.get(device_id, -1)
            if index < 0:
                raise SpeechError("device_unavailable")
            device = sd.query_devices(index)
            stream, audio, sample_rate = self._open(sd, index, device, audio, sample_rate)
            return stream

        with wasapi_stream("output", open_output, unavailable=SpeechError("audio_unavailable")) as stream:
            if stopped.is_set():
                return
            stream.start()
            block = max(1, sample_rate // 100)
            for offset in range(0, len(audio), block):
                if stopped.is_set():
                    return
                stream.write(audio[offset : offset + block] * volume)
            # Wait for queued audio while still responding to cancellation.
            stopped.wait(stream.latency)
            if not stopped.is_set():
                stream.stop()

    @staticmethod
    def _open(sd, index, device, audio, sample_rate):
        import numpy as np

        # WASAPI shared-mode conversion handles ordinary stereo endpoints.
        # Some endpoints (including Sonar Gaming) require their full mix format.
        channels = min(2, device["max_output_channels"])
        stereo = np.repeat(audio, channels, axis=1) if audio.shape[1] == 1 else audio[:, :channels].copy()
        try:
            stream = sd.OutputStream(
                device=index, samplerate=sample_rate, channels=channels, dtype="float32",
                extra_settings=sd.WasapiSettings(auto_convert=True),
            )
            return stream, stereo, sample_rate
        except sd.PortAudioError:
            rate = int(device["default_samplerate"])
            channels = device["max_output_channels"]
            frames = max(1, round(len(stereo) * rate / sample_rate))
            source_frames = np.arange(len(stereo))
            target_frames = np.arange(frames) * sample_rate / rate
            mix = np.zeros((frames, channels), dtype="float32")
            for channel in range(stereo.shape[1]):
                mix[:, channel] = np.interp(target_frames, source_frames, stereo[:, channel])
            try:
                stream = sd.OutputStream(device=index, samplerate=rate, channels=channels, dtype="float32")
            except sd.PortAudioError as error:
                raise SpeechError("device_unavailable") from error
            return stream, mix, rate

