"""Audio capture adapter and media validation for push-to-talk recordings.

Validates audio format, MIME type, non-empty content, and maximum byte size
before any speech-to-text call. Never writes microphone recordings to disk by
default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ALLOWED_AUDIO_MIME_PREFIXES: tuple[str, ...] = (
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/ogg",
    "audio/mp4",
    "audio/x-m4a",
    "audio/m4a",
    "audio/mpeg",
    "audio/mp3",
    "audio/flac",
    "video/mp4",  # iOS Safari MediaRecorder sometimes labels audio-only MP4 containers as video/mp4
    "video/webm",  # Chrome/Firefox MediaRecorder fallback container label
    "application/octet-stream",
)

EXTENSION_TO_MIME: dict[str, str] = {
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
}


class AudioValidationError(ValueError):
    """Raised when an uploaded or local audio recording fails validation."""


@dataclass(frozen=True)
class AudioClip:
    """In-memory validated audio clip ready for speech-to-text transcription."""

    data: bytes
    mime_type: str
    filename: str
    duration_seconds: float | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def normalize_mime_type(raw_mime: str | None, filename: str = "recording.webm") -> str:
    ext = Path(filename).suffix.lower()
    if not raw_mime:
        return EXTENSION_TO_MIME.get(ext, "audio/webm")
    base_mime = raw_mime.split(";", 1)[0].strip().lower()
    if base_mime == "application/octet-stream" and ext in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[ext]
    return base_mime or EXTENSION_TO_MIME.get(ext, "audio/webm")


def sanitize_filename(filename: str | None) -> str:
    if not filename:
        return "recording.webm"
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        return "recording.webm"
    ext = Path(name).suffix.lower()
    if ext not in EXTENSION_TO_MIME:
        return f"{Path(name).stem or 'recording'}.webm"
    return name


def validate_audio_clip(
    data: bytes,
    *,
    mime_type: str | None = None,
    filename: str | None = "recording.webm",
    duration_seconds: float | None = None,
    max_bytes: int = 4_000_000,
    max_duration_seconds: float = 20.0,
) -> AudioClip:
    """Validate a raw audio recording buffer and return a bounded AudioClip."""
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        raise AudioValidationError("Audio recording is empty; hold the talk button while speaking.")
    if len(data) < 64:
        raise AudioValidationError("Audio recording is too short to contain speech.")
    if len(data) > max_bytes:
        raise AudioValidationError(
            f"Audio recording ({len(data)} bytes) exceeds the {max_bytes}-byte safety limit."
        )
    if duration_seconds is not None:
        if duration_seconds <= 0:
            raise AudioValidationError("Audio duration must be positive.")
        if duration_seconds > max_duration_seconds:
            raise AudioValidationError(
                f"Recording duration ({duration_seconds:.1f}s) exceeds the {max_duration_seconds:.1f}s limit."
            )

    safe_name = sanitize_filename(filename)
    normalized_mime = normalize_mime_type(mime_type, safe_name)
    if not any(normalized_mime.startswith(prefix) for prefix in ALLOWED_AUDIO_MIME_PREFIXES):
        raise AudioValidationError(f"Unsupported audio MIME type: {normalized_mime!r}")

    return AudioClip(
        data=bytes(data),
        mime_type=normalized_mime,
        filename=safe_name,
        duration_seconds=duration_seconds,
    )
