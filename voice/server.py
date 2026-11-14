"""Standalone FastAPI server for AEGIS Voice V1 (port 8010 by default).

Provides:
- ``GET /health`` — Voice service status, capabilities, and safe config summary
- ``POST /api/v1/voice/query`` — Grounded text query + optional TTS audio
- ``POST /api/v1/voice/audio-turn`` — Push-to-talk audio (base64 JSON) -> STT -> grounded answer -> optional TTS
- ``POST /api/v1/voice/turn`` — Push-to-talk audio (raw binary or multipart/form-data) -> STT -> grounded answer -> optional TTS
- ``GET /api/v1/voice/session/{session_id}`` — Current session context & conversation history
- ``POST /api/v1/voice/session/{session_id}/reset`` — Reset conversation context only (never resets simulation!)
- ``GET /`` — Responsive desktop & mobile push-to-talk prototype UI
"""

from __future__ import annotations

import base64
from email.parser import BytesParser
from email.policy import default as email_default_policy
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from voice.config import VoiceConfig
from voice.service import VoiceAssistantService

STATIC_INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"


class VoiceQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["simulation"] = "simulation"
    session_id: Annotated[str, Field(min_length=1, max_length=128)] = "default"
    scenario_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=500)]
    speak: bool = False


class VoiceAudioTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["simulation"] = "simulation"
    session_id: Annotated[str, Field(min_length=1, max_length=128)] = "default"
    scenario_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    audio_base64: Annotated[str, Field(min_length=16)]
    mime_type: str = "audio/webm"
    filename: str = "recording.webm"
    duration_seconds: float | None = Field(default=None, gt=0, le=60, allow_inf_nan=False)
    speak: bool = True


def _parse_multipart_audio(
    content_type: str, raw_body: bytes
) -> tuple[bytes, str, str, dict[str, str]]:
    """Parse multipart/form-data using Python's standard library email parser."""
    header_bytes = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    )
    msg = BytesParser(policy=email_default_policy).parsebytes(header_bytes + raw_body)
    if not msg.is_multipart():
        raise ValueError("Invalid multipart/form-data payload.")

    audio_bytes = b""
    mime_type = "audio/webm"
    filename = "recording.webm"
    fields: dict[str, str] = {}

    for part in msg.iter_parts():
        disp = part.get("Content-Disposition", "")
        if "form-data" not in disp:
            continue
        name = part.get_param("name", header="content-disposition")
        part_filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if part_filename is not None or name in {"file", "audio"}:
            audio_bytes = payload
            mime_type = part.get_content_type() or "audio/webm"
            if part_filename:
                filename = part_filename
        elif isinstance(name, str):
            fields[name] = payload.decode("utf-8", errors="replace").strip()

    return audio_bytes, mime_type, filename, fields


def create_voice_app(service: VoiceAssistantService | None = None) -> FastAPI:
    voice_service = service or VoiceAssistantService()
    cfg: VoiceConfig = voice_service.config

    app = FastAPI(
        title="AEGIS Voice V1 Service",
        version="0.1.0",
        description=(
            "Read-only conversational situational-awareness assistant for AEGIS. "
            "Simulation-only; never mutates state or authorizes dispatch."
        ),
    )
    app.state.voice_service = voice_service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.get("/health")
    def health(request: Request) -> dict:
        svc: VoiceAssistantService = request.app.state.voice_service
        return {
            "status": "ok",
            "service": "aegis-voice",
            "version": "0.1.0",
            **svc.config.public_summary(),
        }

    @app.post("/api/v1/voice/query")
    def query_text(request: Request, body: VoiceQueryRequest) -> dict:
        svc: VoiceAssistantService = request.app.state.voice_service
        turn = svc.handle_text_query(
            body.text,
            session_id=body.session_id,
            scenario_id=body.scenario_id,
            speak=body.speak,
        )
        return turn.to_dict()

    @app.post("/api/v1/voice/audio-turn")
    def audio_turn_json(request: Request, body: VoiceAudioTurnRequest) -> dict:
        svc: VoiceAssistantService = request.app.state.voice_service
        try:
            raw_audio = base64.b64decode(body.audio_base64, validate=True)
        except Exception:
            raise HTTPException(422, "Invalid base64 audio payload") from None

        turn = svc.handle_audio_turn(
            raw_audio,
            mime_type=body.mime_type,
            filename=body.filename,
            duration_seconds=body.duration_seconds,
            session_id=body.session_id,
            scenario_id=body.scenario_id,
            speak=body.speak,
        )
        return turn.to_dict()

    @app.post("/api/v1/voice/turn")
    async def audio_turn_raw_or_multipart(
        request: Request,
        session_id: str = Query(default="default", min_length=1, max_length=128),
        scenario_id: str | None = Query(default=None, min_length=1, max_length=128),
        speak: bool = Query(default=True),
        duration_seconds: float | None = Query(default=None, gt=0, le=60),
    ) -> dict:
        svc: VoiceAssistantService = request.app.state.voice_service
        raw_body = await request.body()
        if len(raw_body) > svc.config.max_audio_bytes + 65536:
            raise HTTPException(413, "Uploaded audio exceeds maximum allowed size.")

        content_type = request.headers.get("content-type", "audio/webm")
        if content_type.lower().startswith("multipart/form-data"):
            try:
                audio_bytes, mime_type, filename, form_fields = _parse_multipart_formdata_safe(
                    content_type, raw_body
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            effective_session = form_fields.get("session_id") or session_id
            effective_scenario = form_fields.get("scenario_id") or scenario_id
            effective_speak = (
                form_fields["speak"].lower() in {"1", "true", "yes"}
                if "speak" in form_fields
                else speak
            )
            dur = (
                float(form_fields["duration_seconds"])
                if "duration_seconds" in form_fields
                else duration_seconds
            )
        else:
            audio_bytes = raw_body
            mime_type = content_type
            filename = "recording.webm"
            effective_session = session_id
            effective_scenario = scenario_id
            effective_speak = speak
            dur = duration_seconds

        turn = svc.handle_audio_turn(
            audio_bytes,
            mime_type=mime_type,
            filename=filename,
            duration_seconds=dur,
            session_id=effective_session,
            scenario_id=effective_scenario,
            speak=effective_speak,
        )
        return turn.to_dict()

    @app.get("/api/v1/voice/session/{session_id}")
    def get_session(request: Request, session_id: str) -> dict:
        svc: VoiceAssistantService = request.app.state.voice_service
        ctx = svc.sessions.get(session_id)
        return {
            "mode": "simulation",
            "read_only": True,
            **ctx.snapshot(),
        }

    @app.post("/api/v1/voice/session/{session_id}/reset")
    def reset_session(request: Request, session_id: str) -> dict:
        """Clear in-memory voice conversation context only. Never touches simulation state."""
        svc: VoiceAssistantService = request.app.state.voice_service
        ctx = svc.sessions.reset(session_id)
        return {
            "mode": "simulation",
            "read_only": True,
            "state_mutated": False,
            "session_reset": True,
            **ctx.snapshot(),
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        if STATIC_INDEX_PATH.is_file():
            return HTMLResponse(STATIC_INDEX_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>AEGIS Voice V1 Service Running</h1>")

    return app


def _parse_multipart_formdata_safe(
    content_type: str, raw_body: bytes
) -> tuple[bytes, str, str, dict[str, str]]:
    return _parse_multipart_audio(content_type, raw_body)


app = create_voice_app()
