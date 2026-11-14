"""Server-side configuration and secret loading for AEGIS Voice.

Loads ``voice/.env`` if present without exposing ``ELEVENLABS_API_KEY`` in
string representations, serialized dicts, logs, or API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any

VOICE_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = VOICE_DIR / ".env"

DEFAULT_ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs 'George' premade voice
DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"
DEFAULT_ELEVENLABS_STT_MODEL = "scribe_v1"

SUPPORTED_TTS_MODELS: tuple[str, ...] = (
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
    "eleven_monolingual_v1",
)
SUPPORTED_STT_MODELS: tuple[str, ...] = (
    "scribe_v1",
    "scribe_v1_experimental",
)

PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "replace_with_your_key",
        "replace_with_voice_id",
        "replace_with_supported_model",
        "your_api_key_here",
        "changeme",
    }
)


def parse_dotenv_file(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file without third-party dependencies."""
    if not env_path.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def redact_secrets(text: str, secret: str | None = None) -> str:
    """Redact any occurrence of a configured secret or ElevenLabs key pattern."""
    if not text:
        return text
    cleaned = text
    if secret and len(secret) >= 4 and secret not in PLACEHOLDER_VALUES:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = re.sub(r"sk_[A-Za-z0-9_]{12,}", "[REDACTED_KEY]", cleaned)
    cleaned = re.sub(r"(?i)(xi-api-key\s*[:=]\s*)([^\s,;\"']+)", r"\1[REDACTED]", cleaned)
    return cleaned


@dataclass(frozen=True)
class VoiceConfig:
    """Immutable server-side configuration. Secret key is hidden from repr/serialization."""

    aegis_backend_url: str = "http://127.0.0.1:8000"
    default_scenario_id: str = "demo"
    elevenlabs_base_url: str = DEFAULT_ELEVENLABS_BASE_URL
    elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE_ID
    elevenlabs_tts_model: str = DEFAULT_ELEVENLABS_TTS_MODEL
    elevenlabs_stt_model: str = DEFAULT_ELEVENLABS_STT_MODEL
    backend_timeout_seconds: float = 3.0
    elevenlabs_timeout_seconds: float = 10.0
    max_audio_bytes: int = 4_000_000  # ~25s ceiling for short PTT clips
    max_recording_seconds: float = 20.0
    max_tts_chars: int = 420
    max_input_chars: int = 500
    session_ttl_seconds: float = 1800.0
    max_session_turns: int = 20
    host: str = "127.0.0.1"
    port: int = 8010
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8010",
        "http://127.0.0.1:8010",
    )
    _elevenlabs_api_key: str | None = field(default=None, repr=False, compare=False)

    @property
    def has_elevenlabs_key(self) -> bool:
        return bool(
            self._elevenlabs_api_key
            and self._elevenlabs_api_key.strip()
            and self._elevenlabs_api_key.strip() not in PLACEHOLDER_VALUES
        )

    def get_elevenlabs_api_key(self) -> str | None:
        """Server-side accessor for the ElevenLabs API key."""
        if not self.has_elevenlabs_key:
            return None
        assert self._elevenlabs_api_key is not None
        return self._elevenlabs_api_key.strip()

    def public_summary(self) -> dict[str, Any]:
        """Safe diagnostic summary suitable for /health and UI status (never includes key)."""
        return {
            "mode": "simulation",
            "read_only": True,
            "aegis_backend_url": self.aegis_backend_url,
            "default_scenario_id": self.default_scenario_id,
            "speech_Configured": self.has_elevenlabs_key,
            "operational_mode": "full_speech" if self.has_elevenlabs_key else "text_only",
            "elevenlabs_voice_id": self.elevenlabs_voice_id,
            "elevenlabs_tts_model": self.elevenlabs_tts_model,
            "elevenlabs_stt_model": self.elevenlabs_stt_model,
            "limits": {
                "max_audio_bytes": self.max_audio_bytes,
                "max_recording_seconds": self.max_recording_seconds,
                "max_tts_chars": self.max_tts_chars,
                "max_input_chars": self.max_input_chars,
            },
        }

    @classmethod
    def from_env(
        cls,
        env_path: Path | None = DEFAULT_ENV_PATH,
        overrides: dict[str, str] | None = None,
    ) -> "VoiceConfig":
        file_vars = parse_dotenv_file(env_path) if env_path is not None else {}
        merged: dict[str, str] = {**file_vars, **os.environ}
        if overrides:
            merged.update(overrides)

        def pick(name: str, default: str) -> str:
            val = merged.get(name, "").strip()
            if not val or val in PLACEHOLDER_VALUES:
                return default
            return val

        raw_key = merged.get("ELEVENLABS_API_KEY", "").strip()
        api_key = None if (not raw_key or raw_key in PLACEHOLDER_VALUES) else raw_key

        origins_raw = merged.get("VOICE_CORS_ORIGINS", "").strip()
        origins = (
            tuple(o.strip() for o in origins_raw.split(",") if o.strip())
            if origins_raw
            else cls.cors_origins
        )

        return cls(
            aegis_backend_url=pick("AEGIS_BACKEND_URL", cls.aegis_backend_url).rstrip("/"),
            default_scenario_id=pick("AEGIS_SCENARIO_ID", cls.default_scenario_id),
            elevenlabs_base_url=pick(
                "ELEVENLABS_BASE_URL", DEFAULT_ELEVENLABS_BASE_URL
            ).rstrip("/"),
            elevenlabs_voice_id=pick("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID),
            elevenlabs_tts_model=pick("ELEVENLABS_TTS_MODEL", DEFAULT_ELEVENLABS_TTS_MODEL),
            elevenlabs_stt_model=pick("ELEVENLABS_STT_MODEL", DEFAULT_ELEVENLABS_STT_MODEL),
            backend_timeout_seconds=float(
                pick("VOICE_BACKEND_TIMEOUT_S", str(cls.backend_timeout_seconds))
            ),
            elevenlabs_timeout_seconds=float(
                pick("VOICE_ELEVENLABS_TIMEOUT_S", str(cls.elevenlabs_timeout_seconds))
            ),
            max_audio_bytes=int(pick("VOICE_MAX_AUDIO_BYTES", str(cls.max_audio_bytes))),
            max_recording_seconds=float(
                pick("VOICE_MAX_RECORDING_SECONDS", str(cls.max_recording_seconds))
            ),
            max_tts_chars=int(pick("VOICE_MAX_TTS_CHARS", str(cls.max_tts_chars))),
            max_input_chars=int(pick("VOICE_MAX_INPUT_CHARS", str(cls.max_input_chars))),
            host=pick("VOICE_HOST", cls.host),
            port=int(pick("VOICE_PORT", str(cls.port))),
            cors_origins=origins,
            _elevenlabs_api_key=api_key,
        )
