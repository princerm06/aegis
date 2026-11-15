"""In-memory session context manager for AEGIS Voice V1.

Maintains short-term conversational reference state per ``session_id`` so
operators can ask natural follow-up questions ("Which responder?", "Why was
that responder selected?", "What is their assignment?", "Has it been
acknowledged?", "Tell me more.").

Safety rule:
- Session context stores ONLY entity identifiers, response style preferences,
  and turn transcripts.
- It NEVER caches or trusts simulation state across turns; the responder always
  queries fresh backend state on every turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Literal

ResponseStyle = Literal["operational", "detailed", "full"]


@dataclass(frozen=True)
class ConversationTurn:
    """One recorded question-and-answer turn within a voice session."""

    turn_index: int
    timestamp: str
    user_query: str
    resolved_intent: str
    response_style: ResponseStyle
    spoken_response: str
    display_response: str
    referenced_entities: dict[str, str | None]
    trace_fields: list[str]
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "timestamp": self.timestamp,
            "user_query": self.user_query,
            "resolved_intent": self.resolved_intent,
            "response_style": self.response_style,
            "spoken_response": self.spoken_response,
            "display_response": self.display_response,
            "referenced_entities": dict(self.referenced_entities),
            "trace_fields": list(self.trace_fields),
            "timings_ms": dict(self.timings_ms),
        }


@dataclass
class ConversationContext:
    """Scoped conversational reference state for a single operator session."""

    session_id: str
    created_at_monotonic: float = field(default_factory=time.monotonic)
    updated_at_monotonic: float = field(default_factory=time.monotonic)
    last_responder_id: str | None = None
    last_casualty_id: str | None = None
    last_proposal_id: str | None = None
    last_assignment_id: str | None = None
    last_intent: str | None = None
    last_response_style: ResponseStyle = "operational"
    preferred_style: ResponseStyle | None = None
    salient_responder_ids: list[str] = field(default_factory=list)
    salient_casualty_ids: list[str] = field(default_factory=list)
    turns: list[ConversationTurn] = field(default_factory=list)

    def reset(self) -> None:
        self.updated_at_monotonic = time.monotonic()
        self.last_responder_id = None
        self.last_casualty_id = None
        self.last_proposal_id = None
        self.last_assignment_id = None
        self.last_intent = None
        self.last_response_style = "operational"
        self.preferred_style = None
        self.salient_responder_ids.clear()
        self.salient_casualty_ids.clear()
        self.turns.clear()

    def record_turn(
        self,
        *,
        user_query: str,
        resolved_intent: str,
        response_style: ResponseStyle = "operational",
        spoken_response: str,
        display_response: str,
        responder_id: str | None = None,
        casualty_id: str | None = None,
        proposal_id: str | None = None,
        assignment_id: str | None = None,
        salient_responder_ids: list[str] | None = None,
        salient_casualty_ids: list[str] | None = None,
        trace_fields: list[str] | None = None,
        timings_ms: dict[str, float] | None = None,
        max_turns: int = 20,
    ) -> ConversationTurn:
        self.updated_at_monotonic = time.monotonic()
        self.last_intent = resolved_intent
        self.last_response_style = response_style
        if responder_id is not None:
            self.last_responder_id = responder_id
        elif salient_responder_ids is not None:
            self.last_responder_id = (
                salient_responder_ids[0] if len(salient_responder_ids) == 1 else None
            )

        if casualty_id is not None:
            self.last_casualty_id = casualty_id
        elif salient_casualty_ids is not None:
            self.last_casualty_id = (
                salient_casualty_ids[0] if len(salient_casualty_ids) == 1 else None
            )

        if proposal_id is not None:
            self.last_proposal_id = proposal_id
        if assignment_id is not None:
            self.last_assignment_id = assignment_id
        if salient_responder_ids is not None:
            self.salient_responder_ids = list(salient_responder_ids)
        if salient_casualty_ids is not None:
            self.salient_casualty_ids = list(salient_casualty_ids)

        turn = ConversationTurn(
            turn_index=(self.turns[-1].turn_index + 1) if self.turns else 1,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            user_query=user_query,
            resolved_intent=resolved_intent,
            response_style=response_style,
            spoken_response=spoken_response,
            display_response=display_response,
            referenced_entities={
                "responder_id": self.last_responder_id,
                "casualty_id": self.last_casualty_id,
                "proposal_id": self.last_proposal_id,
                "assignment_id": self.last_assignment_id,
            },
            trace_fields=list(trace_fields or []),
            timings_ms=dict(timings_ms or {}),
        )
        self.turns.append(turn)
        if len(self.turns) > max_turns:
            self.turns = self.turns[-max_turns:]
        return turn

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "last_responder_id": self.last_responder_id,
            "last_casualty_id": self.last_casualty_id,
            "last_proposal_id": self.last_proposal_id,
            "last_assignment_id": self.last_assignment_id,
            "last_intent": self.last_intent,
            "last_response_style": self.last_response_style,
            "preferred_style": self.preferred_style,
            "salient_responder_ids": list(self.salient_responder_ids),
            "salient_casualty_ids": list(self.salient_casualty_ids),
            "turn_count": len(self.turns),
            "history": [t.to_dict() for t in self.turns],
        }


class SessionStore:
    """Thread-safe store of active voice conversation sessions with TTL eviction."""

    def __init__(self, ttl_seconds: float = 1800.0, max_sessions: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, ConversationContext] = {}

    def _evict_expired_locked(self, now_mono: float) -> None:
        expired = [
            sid
            for sid, ctx in self._sessions.items()
            if (now_mono - ctx.updated_at_monotonic) > self.ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]
        while len(self._sessions) > self.max_sessions:
            oldest_sid = min(
                self._sessions, key=lambda k: self._sessions[k].updated_at_monotonic
            )
            del self._sessions[oldest_sid]

    def get(self, session_id: str) -> ConversationContext:
        clean_id = (session_id or "default").strip()[:128] or "default"
        now_mono = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now_mono)
            ctx = self._sessions.get(clean_id)
            if ctx is None:
                ctx = ConversationContext(session_id=clean_id)
                self._sessions[clean_id] = ctx
            return ctx

    def reset(self, session_id: str) -> ConversationContext:
        clean_id = (session_id or "default").strip()[:128] or "default"
        now_mono = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now_mono)
            ctx = ConversationContext(session_id=clean_id)
            self._sessions[clean_id] = ctx
            return ctx
