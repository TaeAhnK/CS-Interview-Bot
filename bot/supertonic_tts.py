"""Local Supertonic adapter and its asset configuration."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from bot.tts import SpeechError


@dataclass(frozen=True)
class SupertonicConfig:
    directory: Path
    model: str = "supertonic-3"
    voice: str = "F1"


class SupertonicSynthesizer:
    def __init__(self, config: SupertonicConfig) -> None:
        self._config = config
        self._engine = None
        self._voices = {}

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        voice_name = voice or self._config.voice
        if voice_name not in self._voices:
            voice_file = self._config.directory / "voice_styles" / f"{voice_name}.json"
            if not voice_file.is_file():
                raise SpeechError("model_missing")
        if self._engine is None:
            self._load()
        if voice_name not in self._voices:
            self._voices[voice_name] = self._engine.get_voice_style(voice_name)
        import soundfile

        audio, _ = self._engine.synthesize(text, voice_style=self._voices[voice_name], lang="ko")
        buffer = BytesIO()
        soundfile.write(buffer, audio.squeeze(), self._engine.sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue()

    def _load(self) -> None:
        config = self._config
        try:
            from supertonic import TTS
        except ImportError as error:
            raise SpeechError("engine_unavailable") from error

        try:
            engine = TTS(
                model=config.model,
                model_dir=config.directory,
                auto_download=False,
                intra_op_num_threads=4,
                inter_op_num_threads=1,
            )
        except FileNotFoundError as error:
            raise SpeechError("model_missing") from error
        self._engine = engine
