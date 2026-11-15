"""Coordinate PortAudio device refreshes across input and output streams."""

from collections import Counter
from contextlib import contextmanager
from threading import RLock


_lock = RLock()
_open_streams = 0


def _refresh_portaudio(sd) -> None:
    # PortAudio caches endpoints and defaults at initialization. Refresh before each
    # listing or open when idle. The shared lock keeps both directions from opening
    # a stream during the reset, which would invalidate that stream.
    if _open_streams:
        return
    if sd._initialized:
        sd._terminate()
    sd._initialize()


def _query_wasapi_devices(sd, direction: str):
    api = next(api for api in sd.query_hostapis() if api["name"] == "Windows WASAPI")
    devices = sd.query_devices()
    endpoints = [
        (devices[index]["name"], index)
        for index in api["devices"]
        if devices[index][f"max_{direction}_channels"]
    ]
    # Names survive index changes. Exclude ambiguous names to avoid misrouting.
    counts = Counter(name for name, _ in endpoints)
    return api[f"default_{direction}_device"], {
        name: index for name, index in endpoints if counts[name] == 1
    }


def wasapi_devices(direction: str):
    import sounddevice as sd

    with _lock:
        _refresh_portaudio(sd)
        default, endpoints = _query_wasapi_devices(sd, direction)
        return sd, default, endpoints


@contextmanager
def wasapi_stream(direction: str, open_stream, *, unavailable: Exception | None = None):
    global _open_streams
    with _lock:
        try:
            devices = wasapi_devices(direction)
        except Exception as error:
            if unavailable is not None:
                raise unavailable from error
            raise
        stream = open_stream(*devices)
        _open_streams += 1
    try:
        yield stream
    finally:
        try:
            stream.close()
        finally:
            with _lock:
                _open_streams -= 1
