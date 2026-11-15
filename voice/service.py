"""End-to-end AEGIS Voice orchestration service.

Coordinates:
1. Audio validation & Speech-to-Text (ElevenLabs Scribe)
2. Fresh read-only simulation state & event retrieval (`ReadOnlyAegisClient`)
3. Deterministic intent & session context resolution (`parse_intent`)
4. State-grounded response generation (`generate_grounded_answer`)
5. Optional Text-to-Speech synthesis (`ElevenLabsTTSAdapter`) with graceful
   fallback to text-only or partial mode on any speech service failure.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Literal

from voice.audio import AudioValidationError, validate_audio_clip
from voice.client import BackendUnavailableError, ReadOnlyAegisClient
from voice.config import VoiceConfig, redact_secrets
from voice.context import SessionStore
from voice.elevenlabs import (
    ElevenLabsSTTAdapter,
    ElevenLabsTTSAdapter,
    SpeechServiceError,
)
from voice.intents import parse_intent, sanitize_utterance
from voice.responder import generate_grounded_answer

DeliveryMode = Literal[
    "full_speech",
    "text_only",
    "partial_text_fallback",
    "backend_unavailable",
    "stt_failed",
]


@dataclass
class VoiceTurnResponse:
    """Structured response returned by ``VoiceAssistantService`` for every turn."""

    session_id: str
    scenario_id: str
    user_transcript: str
    intent: str
    spoken_text: str
    display_text: str
    delivery_mode: DeliveryMode
    mode: Literal["simulation"] = "simulation"
    read_only: Literal[True] = True
    state_mutated: Literal[False] = False
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_cached: bool = False
    referenced_entities: dict[str, str | None] = field(default_factory=dict)
    trace_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "read_only": self.read_only,
            "state_mutated": self.state_mutated,
            "session_id": self.session_id,
            "scenario_id": self.scenario_id,
            "user_transcript": self.user_transcript,
            "intent": self.intent,
            "spoken_text": self.spoken_text,
            "display_text": self.display_text,
            "delivery_mode": self.delivery_mode,
            "audio_base64": self.audio_base64,
            "audio_mime_type": self.audio_mime_type,
            "tts_cached": self.tts_cached,
            "referenced_entities": dict(self.referenced_entities),
            "trace_fields": list(self.trace_fields),
            "warnings": list(self.warnings),
            "error_category": self.error_category,
        }


class VoiceAssistantService:
    """Stateful-per-session, read-only situational-awareness voice service."""

    def __init__(
        self,
        config: VoiceConfig | None = None,
        client: ReadOnlyAegisClient | None = None,
        stt: ElevenLabsSTTAdapter | None = None,
        tts: ElevenLabsTTSAdapter | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        self.config = config or VoiceConfig.from_env()
        self.client = client or ReadOnlyAegisClient(
            base_url=self.config.aegis_backend_url,
            timeout_seconds=self.config.backend_timeout_seconds,
        )
        self.stt = stt or ElevenLabsSTTAdapter(self.config)
        self.tts = tts or ElevenLabsTTSAdapter(self.config)
        self.sessions = sessions or SessionStore(
            ttl_seconds=self.config.session_ttl_seconds
        )

    def handle_text_query(
        self,
        text: str,
        *,
        session_id: str = "default",
        scenario_id: str | None = None,
        speak: bool = False,
    ) -> VoiceTurnResponse:
        """Process a text query (or transcribed speech) against fresh simulation state."""
        scen = (scenario_id or self.config.default_scenario_id).strip() or "demo"
        clean_query = sanitize_utterance(text, max_chars=self.config.max_input_chars)
        ctx = self.sessions.get(session_id)
        warnings: list[str] = []

        try:
            state = self.client.get_state(scen)
        except BackendUnavailableError as exc:
            safe_err = redact_secrets(exc.detail, self.config.get_elevenlabs_api_key())
            msg = (
                f"Simulation backend unavailable: live simulation state for scenario {scen!r} "
                f"could not be retrieved ({safe_err}). No fabricated or cached state is reported."
            )
            return VoiceTurnResponse(
                session_id=ctx.session_id,
                scenario_id=scen,
                user_transcript=clean_query,
                intent="backend_unavailable",
                spoken_text=msg,
                display_text=msg,
                delivery_mode="backend_unavailable",
                warnings=[safe_err],
                error_category="backend_unavailable",
            )

        parsed = parse_intent(
            clean_query,
            state=state,
            context=ctx,
            max_chars=self.config.max_input_chars,
        )

        events_page = None
        if parsed.intent == "recent_changes":
            try:
                events_page = self.client.get_events(scen, after_sequence=0, limit=50)
            except BackendUnavailableError as exc:
                warnings.append(
                    f"Could not fetch event history ({redact_secrets(exc.detail, self.config.get_elevenlabs_api_key())})."
                )

        grounded = generate_grounded_answer(
            parsed=parsed,
            state=state,
            events_page=events_page,
            context=ctx,
            max_tts_chars=self.config.max_tts_chars,
        )

        turn = ctx.record_turn(
            user_query=clean_query,
            resolved_intent=grounded.intent,
            response_style=parsed.response_style,
            spoken_response=grounded.spoken_text,
            display_response=grounded.display_text,
            responder_id=grounded.responder_id,
            casualty_id=grounded.casualty_id,
            proposal_id=grounded.proposal_id,
            assignment_id=grounded.assignment_id,
            salient_responder_ids=grounded.salient_responder_ids,
            salient_casualty_ids=grounded.salient_casualty_ids,
            trace_fields=grounded.trace_fields,
            max_turns=self.config.max_session_turns,
        )

        audio_b64: str | None = None
        audio_mime: str | None = None
        tts_cached = False
        delivery_mode: DeliveryMode = "text_only"
        error_category: str | None = None

        if speak:
            if not self.config.has_elevenlabs_key:
                delivery_mode = "partial_text_fallback"
                error_category = "tts_unconfigured"
                warnings.append(
                    "ElevenLabs API key is not configured; returning grounded text response without audio."
                )
            else:
                try:
                    synth = self.tts.synthesize(grounded.spoken_text)
                    audio_b64 = base64.b64encode(synth.audio_bytes).decode("ascii")
                    audio_mime = synth.mime_type
                    tts_cached = synth.cached
                    delivery_mode = "full_speech"
                except SpeechServiceError as exc:
                    delivery_mode = "partial_text_fallback"
                    error_category = f"tts_{exc.category}"
                    warnings.append(
                        redact_secrets(exc.detail, self.config.get_elevenlabs_api_key())
                    )

        return VoiceTurnResponse(
            session_id=ctx.session_id,
            scenario_id=scen,
            user_transcript=clean_query,
            intent=grounded.intent,
            spoken_text=grounded.spoken_text,
            display_text=grounded.display_text,
            delivery_mode=delivery_mode,
            audio_base64=audio_b64,
            audio_mime_type=audio_mime,
            tts_cached=tts_cached,
            referenced_entities=turn.referenced_entities,
            trace_fields=grounded.trace_fields,
            warnings=warnings,
            error_category=error_category,
        )

    def handle_audio_turn(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str | None = "audio/webm",
        filename: str | None = "recording.webm",
        duration_seconds: float | None = None,
        session_id: str = "default",
        scenario_id: str | None = None,
        speak: bool = True,
    ) -> VoiceTurnResponse:
        """Process a push-to-talk audio recording: validate -> STT -> grounded answer -> optional TTS."""
        scen = (scenario_id or self.config.default_scenario_id).strip() or "demo"
        ctx = self.sessions.get(session_id)

        try:
            clip = validate_audio_clip(
                audio_bytes,
                mime_type=mime_type,
                filename=filename,
                duration_seconds=duration_seconds,
                max_bytes=self.config.max_audio_bytes,
                max_duration_seconds=self.config.max_recording_seconds,
            )
        except AudioValidationError as exc:
            msg = f"Audio input error: {exc} You can also type your question in the text box."
            return VoiceTurnResponse(
                session_id=ctx.session_id,
                scenario_id=scen,
                user_transcript="",
                intent="audio_validation_error",
                spoken_text=msg,
                display_text=msg,
                delivery_mode="stt_failed",
                warnings=[str(exc)],
                error_category="audio_validation_error",
            )

        try:
            stt_res = self.stt.transcribe(clip)
        except SpeechServiceError as exc:
            safe_err = redact_secrets(exc.detail, self.config.get_elevenlabs_api_key())
            msg = (
                f"Speech recognition unavailable ({safe_err}). "
                "Please use the text input box to ask your question."
            )
            return VoiceTurnResponse(
                session_id=ctx.session_id,
                scenario_id=scen,
                user_transcript="",
                intent="stt_failed",
                spoken_text=msg,
                display_text=msg,
                delivery_mode="stt_failed",
                warnings=[safe_err],
                error_category=f"stt_{exc.category}",
            )

        return self.handle_text_query(
            stt_res.text,
            session_id=ctx.session_id,
            scenario_id=scen,
            speak=speak,
        )
