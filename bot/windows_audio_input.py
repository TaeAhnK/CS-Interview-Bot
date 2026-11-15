"""Capture Windows WASAPI input in the PCM format expected by the recorder."""

from time import monotonic

from bot.recording import CaptureError, SAMPLE_RATE
from bot.windows_audio_devices import wasapi_devices, wasapi_stream


_MICROPHONE_CONSENT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


class WindowsAudioInput:
    def devices(self) -> list[dict[str, str]]:
        try:
            _, _, inputs = wasapi_devices("input")
        except Exception as error:
            raise CaptureError("audio_unavailable") from error
        return [{"id": name, "name": name} for name in inputs]

    def capture(self, device_id, started, receive, stopped, deadline) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as error:
            raise CaptureError("audio_unavailable") from error

        last_input = monotonic()
        overflowed = False

        def on_audio(indata, frames, time_info, status):
            nonlocal last_input, overflowed
            last_input = monotonic()
            if status.input_overflow:
                overflowed = True
                raise sd.CallbackAbort
            receive(bytes(indata))

        def open_input(sd, default, inputs):
            index = default if device_id is None else inputs.get(device_id, -1)
            if index < 0:
                raise CaptureError("device_unavailable")
            return sd.RawInputStream(
                device=index, channels=1, samplerate=SAMPLE_RATE, dtype="int16",
                extra_settings=sd.WasapiSettings(auto_convert=True), callback=on_audio,
            )

        if stopped.is_set() or monotonic() >= deadline:
            return
        # PortAudio can reduce a WASAPI initialization error to paInvalidDevice
        # and omit the original HRESULT. Check explicit Windows privacy denials.
        if _desktop_access_denied():
            raise CaptureError("permission_denied")
        try:
            with wasapi_stream("input", open_input) as stream:
                if stopped.is_set() or monotonic() >= deadline:
                    return
                last_input = monotonic()
                stream.start()
                started()
                while not stopped.is_set():
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    if overflowed:
                        raise CaptureError("capture_failed")
                    # Some hosts stop delivering callbacks when the endpoint disappears.
                    if not stream.active or monotonic() - last_input > 2:
                        raise CaptureError("device_unavailable")
                    stopped.wait(min(remaining, 0.05))
        except sd.PortAudioError as error:
            if _desktop_access_denied():
                raise CaptureError("permission_denied") from error
            raise CaptureError(_capture_error_code(error)) from error
        except StopIteration as error:
            raise CaptureError("audio_unavailable") from error


def _desktop_access_denied() -> bool:
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (_MICROPHONE_CONSENT, _MICROPHONE_CONSENT + r"\NonPackaged"):
            try:
                with winreg.OpenKey(hive, key) as consent:
                    value, _ = winreg.QueryValueEx(consent, "Value")
            except OSError:
                continue
            if value == "Deny":
                return True
    return False


def _capture_error_code(error: Exception) -> str:
    pa_code = error.args[1] if len(error.args) > 1 else None
    host_code = error.args[2][1] & 0xFFFFFFFF if len(error.args) > 2 else None
    if host_code == 0x80070005:  # E_ACCESSDENIED
        return "permission_denied"
    if pa_code in (-9996, -9985) or host_code in (0x88890004, 0x88890026):
        return "device_unavailable"
    return "capture_failed"
