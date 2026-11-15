"""Server-side ElevenLabs Speech-to-Text (Scribe) and Text-to-Speech adapters.

Security & Cost Controls:
- Credentials are read strictly server-side from ``VoiceConfig`` and sent via
  the ``xi-api-key`` header over HTTPS.
- All error messages are passed through ``redact_secrets`` so the API key can
  never leak into logs, exceptions, or HTTP responses.
- TTS enforces a strict character limit (``max_tts_chars``) and an in-memory
  LRU cache keyed by ``(voice_id, model_id, text)`` to prevent duplicate paid
  synthesis on repeated queries or frontend rerenders.
- Injectable ``transport`` allows 100% deterministic offline unit testing with
  zero paid API calls.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import os
import ssl

from voice.audio import AudioClip
from voice.config import VoiceConfig, redact_secrets

# Transport signature: (method, url, headers, body_bytes) -> (status_code, content_type, body_bytes)
ElevenLabsTransport = Callable[
    [str, str, dict[str, str], bytes | None], tuple[int, str, bytes]
]


def _verified_ssl_context() -> ssl.SSLContext:
    """Create a strict TLS context, loading macOS /etc/ssl/cert.pem if Python's bundled store is empty."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for ca_path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.isfile(ca_path):
                ctx.load_verify_locations(cafile=ca_path)
                break
    return ctx


class SpeechServiceError(RuntimeError):
    """Raised when an ElevenLabs STT or TTS call fails."""

    def __init__(
        self,
        detail: str,
        *,
        category: str = "service_error",
        status_code: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.category = category
        self.status_code = status_code


@dataclass(frozen=True)
class TranscriptionResult:
    """Output from ElevenLabs Scribe speech-to-text."""

    text: str
    model_id: str
    language_code: str | None = None
    audio_size_bytes: int = 0


@dataclass(frozen=True)
class SynthesisResult:
    """Output from ElevenLabs text-to-speech."""

    audio_bytes: bytes
    mime_type: str
    voice_id: str
    model_id: str
    cached: bool = False
    char_count: int = 0


def _default_elevenlabs_transport(timeout_seconds: float) -> ElevenLabsTransport:
    ssl_ctx = _verified_ssl_context()

    def call(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, str, bytes]:
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout_seconds, context=ssl_ctx) as resp:
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                data = resp.read(10_000_000)
                return resp.status, content_type, data
        except HTTPError as exc:
            err_bytes = b""
            try:
                err_bytes = exc.read(32768)
            except Exception:
                pass
            return exc.code, "application/json", err_bytes
        except (URLError, TimeoutError, OSError) as exc:
            raise SpeechServiceError(
                f"ElevenLabs network error: {exc}", category="network_error"
            ) from None

    return call


def _classify_elevenlabs_http_error(
    status_code: int, raw_body: bytes, secret: str | None
) -> SpeechServiceError:
    message = f"ElevenLabs API returned HTTP {status_code}"
    detail_code = ""
    try:
        parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
        if isinstance(parsed, dict):
            detail_obj = parsed.get("detail")
            if isinstance(detail_obj, dict):
                detail_code = str(detail_obj.get("status", "")).lower()
                msg = detail_obj.get("message") or detail_obj.get("status")
                if msg:
                    message = f"{message}: {msg}"
            elif isinstance(detail_obj, str):
                message = f"{message}: {detail_obj}"
    except Exception:
        pass

    safe_message = redact_secrets(message, secret)
    lower_msg = safe_message.lower()
    if (
        "quota" in lower_msg
        or "credit" in lower_msg
        or "quota_exceeded" in detail_code
        or status_code == 402
    ):
        category = "quota_exceeded"
    elif status_code in (401, 403):
        category = "auth_error"
    elif status_code == 429:
        category = "rate_limited"
    else:
        category = "service_error"

    return SpeechServiceError(safe_message, category=category, status_code=status_code)


def _encode_multipart_formdata(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_content_type: str,
    file_bytes: bytes,
) -> tuple[str, bytes]:
    boundary = f"----AegisVoiceBoundary{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(
                "utf-8"
            )
        )
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {file_content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return content_type, b"".join(chunks)


class ElevenLabsSTTAdapter:
    """Official ElevenLabs Scribe speech-to-text adapter (`POST /v1/speech-to-text`)."""

    def __init__(
        self,
        config: VoiceConfig,
        transport: ElevenLabsTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_elevenlabs_transport(
            config.elevenlabs_timeout_seconds
        )

    def transcribe(
        self, clip: AudioClip, language_code: str | None = "en"
    ) -> TranscriptionResult:
        api_key = self.config.get_elevenlabs_api_key()
        if not api_key:
            raise SpeechServiceError(
                "ElevenLabs API key is not configured; speech recognition is unavailable. Use text input instead.",
                category="unconfigured",
            )

        fields = {"model_id": self.config.elevenlabs_stt_model}
        if language_code:
            fields["language_code"] = language_code

        content_type, body = _encode_multipart_formdata(
            fields=fields,
            file_field="file",
            filename=clip.filename,
            file_content_type=clip.mime_type,
            file_bytes=clip.data,
        )
        url = f"{self.config.elevenlabs_base_url}/v1/speech-to-text"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        try:
            status, _, resp_bytes = self._transport("POST", url, headers, body)
        except SpeechServiceError as exc:
            raise SpeechServiceError(
                redact_secrets(exc.detail, api_key),
                category=exc.category,
                status_code=exc.status_code,
            ) from None

        if status != 200:
            raise _classify_elevenlabs_http_error(status, resp_bytes, api_key)

        try:
            payload = json.loads(resp_bytes.decode("utf-8"))
        except Exception:
            raise SpeechServiceError(
                "Invalid JSON response from ElevenLabs speech-to-text.",
                category="service_error",
            ) from None

        text = str(payload.get("text", "")).strip()
        if not text:
            raise SpeechServiceError(
                "No speech was detected in the recording. Please speak clearly or type your question.",
                category="empty_transcript",
            )
        return TranscriptionResult(
            text=text,
            model_id=self.config.elevenlabs_stt_model,
            language_code=payload.get("language_code"),
            audio_size_bytes=clip.size_bytes,
        )


class ElevenLabsTTSAdapter:
    """Official ElevenLabs text-to-speech adapter (`POST /v1/text-to-speech/{voice_id}`)."""

    def __init__(
        self,
        config: VoiceConfig,
        transport: ElevenLabsTransport | None = None,
        cache_size: int = 64,
    ) -> None:
        self.config = config
        self._transport = transport or _default_elevenlabs_transport(
            config.elevenlabs_timeout_seconds
        )
        self._cache_size = max(1, cache_size)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def _cache_key(self, voice_id: str, model_id: str, text: str) -> str:
        raw = f"{voice_id}:{model_id}:{text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model_id: str | None = None,
    ) -> SynthesisResult:
        api_key = self.config.get_elevenlabs_api_key()
        if not api_key:
            raise SpeechServiceError(
                "ElevenLabs API key is not configured; spoken output is unavailable.",
                category="unconfigured",
            )

        clean_text = text.strip()
        if not clean_text:
            raise SpeechServiceError(
                "Cannot synthesize empty text.", category="invalid_input"
            )
        if len(clean_text) > self.config.max_tts_chars:
            clean_text = clean_text[: self.config.max_tts_chars - 3].rsplit(" ", 1)[0] + "..."

        vid = (voice_id or self.config.elevenlabs_voice_id).strip()
        mid = (model_id or self.config.elevenlabs_tts_model).strip()
        key = self._cache_key(vid, mid, clean_text)

        with self._lock:
            cached_audio = self._cache.get(key)
            if cached_audio is not None:
                self._cache.move_to_end(key)
                return SynthesisResult(
                    audio_bytes=cached_audio,
                    mime_type="audio/mpeg",
                    voice_id=vid,
                    model_id=mid,
                    cached=True,
                    char_count=len(clean_text),
                )

        query = urlencode({"output_format": "mp3_44100_128"})
        url = f"{self.config.elevenlabs_base_url}/v1/text-to-speech/{vid}?{query}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = json.dumps(
            {
                "text": clean_text,
                "model_id": mid,
                "voice_settings": {
                    "stability": 0.55,
                    "similarity_boost": 0.75,
                },
            }
        ).encode("utf-8")

        try:
            status, content_type, resp_bytes = self._transport("POST", url, headers, body)
        except SpeechServiceError as exc:
            raise SpeechServiceError(
                redact_secrets(exc.detail, api_key),
                category=exc.category,
                status_code=exc.status_code,
            ) from None

        if status != 200:
            raise _classify_elevenlabs_http_error(status, resp_bytes, api_key)

        if not resp_bytes:
            raise SpeechServiceError(
                "ElevenLabs TTS returned an empty audio stream.",
                category="service_error",
            )

        with self._lock:
            self._cache[key] = resp_bytes
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

        mime = content_type.split(";", 1)[0].strip() if content_type else "audio/mpeg"
        if not mime.startswith("audio/"):
            mime = "audio/mpeg"

        return SynthesisResult(
            audio_bytes=resp_bytes,
            mime_type=mime,
            voice_id=vid,
            model_id=mid,
            cached=False,
            char_count=len(clean_text),
        )


def check_elevenlabs_subscription(
    config: VoiceConfig,
    transport: ElevenLabsTransport | None = None,
) -> dict[str, Any]:
    """Verify ElevenLabs API key validity and remaining character quota without leaking secrets."""
    api_key = config.get_elevenlabs_api_key()
    if not api_key:
        return {
            "configured": False,
            "valid": False,
            "detail": "ELEVENLABS_API_KEY is not set or uses a placeholder value.",
        }

    call = transport or _default_elevenlabs_transport(config.elevenlabs_timeout_seconds)
    url = f"{config.elevenlabs_base_url}/v1/user/subscription"
    headers = {"xi-api-key": api_key, "Accept": "application/json"}
    try:
        status, _, resp_bytes = call("GET", url, headers, None)
    except SpeechServiceError as exc:
        return {
            "configured": True,
            "valid": False,
            "category": exc.category,
            "detail": redact_secrets(exc.detail, api_key),
        }

    if status != 200:
        err = _classify_elevenlabs_http_error(status, resp_bytes, api_key)
        return {
            "configured": True,
            "valid": False,
            "status_code": status,
            "category": err.category,
            "detail": err.detail,
        }

    try:
        data = json.loads(resp_bytes.decode("utf-8"))
    except Exception:
        return {
            "configured": True,
            "valid": False,
            "detail": "Invalid JSON returned from ElevenLabs subscription endpoint.",
        }

    char_count = int(data.get("character_count", 0))
    char_limit = int(data.get("character_limit", 0))
    remaining = max(0, char_limit - char_count)
    return {
        "configured": True,
        "valid": True,
        "tier": data.get("tier"),
        "status": data.get("status"),
        "character_count": char_count,
        "character_limit": char_limit,
        "characters_remaining": remaining,
        "has_remaining_credits": remaining > 0,
    }
