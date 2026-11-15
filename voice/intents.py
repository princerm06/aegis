"""Deterministic intent and context interpreter for AEGIS Voice V1.

Classifies operator utterances into read-only situational-awareness intents,
determines the requested response style (`operational`, `detailed`, or `full`),
intercepts prohibited state-mutating or clinical-inference commands before any
action can occur, and resolves entity references, follow-ups ("Tell me more"),
and pronouns conservatively against the current ``SimulationState`` and
``ConversationContext``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from backend.app.simulation.models import SimulationState
from voice.context import ConversationContext, ResponseStyle

IntentKind = Literal[
    "incident_status",
    "explain_optimization",
    "responder_status",
    "unassigned_casualties",
    "recent_changes",
    "acknowledgment_status",
    "casualty_status",
    "prohibited_mutation",
    "unknown_entity",
    "clarification_needed",
]

NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "first": 1,
    "1": 1,
    "001": 1,
    "two": 2,
    "second": 2,
    "2": 2,
    "002": 2,
    "three": 3,
    "third": 3,
    "3": 3,
    "003": 3,
    "four": 4,
    "fourth": 4,
    "4": 4,
    "004": 4,
    "five": 5,
    "fifth": 5,
    "5": 5,
    "005": 5,
    "six": 6,
    "sixth": 6,
    "6": 6,
    "006": 6,
}

LETTER_TO_INDEX: dict[str, int] = {
    "a": 1,
    "alpha": 1,
    "b": 2,
    "bravo": 2,
    "c": 3,
    "charlie": 3,
    "d": 4,
    "delta": 4,
}


@dataclass(frozen=True)
class ParsedIntent:
    """Resolved deterministic intent, response style, and entity bindings for a single turn."""

    intent: IntentKind
    normalized_query: str
    response_style: ResponseStyle = "operational"
    include_identifiers: bool = False
    responder_id: str | None = None
    casualty_id: str | None = None
    prohibited_action: str | None = None
    unknown_entity_kind: Literal["responder", "casualty"] | None = None
    unknown_entity_label: str | None = None
    clarification_reason: str | None = None
    used_context: bool = False


def sanitize_utterance(text: str, max_chars: int = 500) -> str:
    """Strip control characters and collapse whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def _detect_requested_style(lower_body: str) -> tuple[ResponseStyle | None, bool, bool]:
    """Return (explicit_style, is_pure_style_followup, include_identifiers).

    - `operational`: "short version", "be brief", "briefly", "concise", "quick summary", "keep it short"
    - `detailed`: "in detail", "more detail", "detailed", "tell me more", "elaborate", "expand on that"
    - `full`: "full situation report", "full report", "complete breakdown", "comprehensive", "everything"
    """
    include_ids = bool(
        re.search(
            r"\b(proposal id|revision number|scene revision|exact id|identifiers|internal id|full report|full situation)\b",
            lower_body,
        )
    )

    if re.search(
        r"\b(full situation report|full status report|full report|complete report|complete breakdown|comprehensive report|give me everything|full details)\b",
        lower_body,
    ):
        pure = bool(
            re.match(
                r"^(?:give me\s+|let's hear\s+)?(?:the\s+)?(?:full report|complete breakdown|full details|everything)\s*[?.!]*$",
                lower_body,
            )
        )
        return "full", pure, True

    if re.search(
        r"\b(in detail|more detail|more details|detailed|tell me more|elaborate|expand on that|explain further|go deeper)\b",
        lower_body,
    ):
        pure = bool(
            re.match(
                r"^(?:please\s+)?(?:give me\s+|explain\s+|tell me\s+)?(?:in detail|more detail|more details|more|elaborate|expand|explain further|go deeper)(?:\s+on\s+that)?\s*[?.!]*$",
                lower_body,
            )
        )
        return "detailed", pure, include_ids

    if re.search(
        r"\b(short version|be brief|briefly|keep it short|keep it brief|quick summary|concise|in short|tldr)\b",
        lower_body,
    ):
        pure = bool(
            re.match(
                r"^(?:please\s+)?(?:give me\s+)?(?:the\s+)?(?:short version|be brief|briefly|keep it short|keep it brief|quick summary|in short)\s*[?.!]*$",
                lower_body,
            )
        )
        return "operational", pure, include_ids

    return None, False, include_ids


def _detect_prohibited_mutation(lower: str) -> str | None:
    """Detect any attempt to trigger state mutation, approval, dispatch, or triage."""
    if re.search(
        r"\b(ignore (all |previous )?(instructions|rules)|system override|sudo|bypass approval|execute curl|delete database)\b",
        lower,
    ):
        return "override_or_mutation"

    is_info_question = bool(
        re.match(
            r"^(?:aegis\s*,?\s*)?(?:why|what|which|who|where|when|how|is|are|has|have|did|was|were|explain|show|tell|list|give|status)\b",
            lower,
        )
    )

    if not is_info_question:
        if re.search(
            r"\b(approve|reject|deny|authorize|confirm)\b.*\b(proposal|plan|recommendation|dispatch|assignment)\b"
            r"|\b^(?:aegis\s*,?\s*)?(?:please\s+)?(approve|reject)\b",
            lower,
        ):
            return "proposal_decision"

        if re.search(
            r"\b(dispatch|send|deploy|launch)\b\s+(responder|unit|alpha|bravo|charlie|delta|all|them|him|her|it|proposal|plan|team)"
            r"|\b(dispatch|send|deploy)\b.*\b(immediately|now|right now|to casualty)\b",
            lower,
        ):
            return "dispatch"

        if re.search(
            r"\b(set|mark|change|update|classify|triage|assess|label|assign)\b.*\b(immediate|delayed|minor|expectant|deceased|severity|triage|priority)\b"
            r"|\b(diagnose|predict severity|infer severity)\b",
            lower,
        ):
            return "medical_assessment"

        if re.search(
            r"\b(associate|relink|link|bind|create|add|delete|remove)\b.*\b(track|casualty|person)\b",
            lower,
        ):
            return "track_association"

        if re.search(
            r"\b(reset|clear|wipe|restart)\b.*\b(simulation|scenario|demo|scene|state)\b",
            lower,
        ):
            return "simulation_reset"

        if re.search(
            r"\b(add|remove|place|place|clear|update|modify)\b.*\b(obstacle|wall|map|grid|blockage)\b",
            lower,
        ):
            return "map_modification"

        if re.search(
            r"\b(set|mark|make|change|update)\b.*\b(responder|alpha|bravo|unit)\b.*\b(busy|offline|available|position|location)\b",
            lower,
        ):
            return "responder_mutation"

        if re.search(
            r"\b(acknowledge|ack|complete|finish)\b\s+(the\s+)?(assignment|command|dispatch|task)\b"
            r"|\bsimulate\s+(device|ack|acknowledgment|completion|arduino)\b",
            lower,
        ):
            return "device_event_mutation"

    if re.search(
        r"^(?:aegis\s*,?\s*)?(?:can|could|would|will)\s+you\s+(?:please\s+)?"
        r"(dispatch|approve|reject|reset|triage|assess|relink|associate|deploy|send|mark|set)\b",
        lower,
    ):
        if "dispatch" in lower or "deploy" in lower or "send" in lower:
            return "dispatch"
        if "approve" in lower or "reject" in lower:
            return "proposal_decision"
        if "reset" in lower:
            return "simulation_reset"
        if "triage" in lower or "assess" in lower or "mark" in lower or "set" in lower:
            return "medical_assessment"
        return "override_or_mutation"

    return None


def _match_responder_in_text(
    lower: str, state: SimulationState
) -> tuple[str | None, str | None]:
    """Return (matched_responder_id, unknown_responder_label)."""
    responders = state.scenario.responders

    for r in responders:
        if re.search(rf"\b{re.escape(r.id.lower())}\b", lower):
            return r.id, None

    for r in responders:
        dn_lower = r.display_name.lower()
        if dn_lower in lower:
            return r.id, None
        for token in re.findall(r"[a-z0-9]+", dn_lower):
            if token in {"mock", "responder", "unit", "team"}:
                continue
            if len(token) >= 3 and re.search(rf"\b{re.escape(token)}\b", lower):
                return r.id, None

    m = re.search(
        r"\b(?:responder|unit)\s+#?([a-z0-9-]+)\b",
        lower,
    )
    if m:
        token = m.group(1).strip()
        if token not in {
            "is",
            "are",
            "was",
            "were",
            "selected",
            "assigned",
            "available",
            "status",
            "there",
            "that",
            "this",
            "which",
            "who",
            "has",
            "have",
            "in",
            "on",
            "for",
            "to",
            "at",
            "with",
            "without",
        }:
            num = NUMBER_WORDS.get(token) or LETTER_TO_INDEX.get(token)
            if num is not None:
                candidate_ids = [
                    f"responder-{num:03d}",
                    f"responder-{num}",
                    f"r{num - 1}",
                    f"r{num}",
                ]
                for cid in candidate_ids:
                    for r in responders:
                        if r.id.lower() == cid:
                            return r.id, None
                if 1 <= num <= len(responders):
                    return responders[num - 1].id, None
            return None, f"Responder {token.capitalize()}"

    for callsign, idx in (
        ("alpha", 1),
        ("bravo", 2),
        ("charlie", 3),
        ("delta", 4),
        ("echo", 5),
        ("foxtrot", 6),
    ):
        if re.search(rf"\b{callsign}\b", lower):
            for r in responders:
                if callsign in r.display_name.lower() or r.id.lower() == f"responder-{idx:03d}":
                    return r.id, None
            return None, f"Responder {callsign.capitalize()}"

    return None, None


def _match_casualty_in_text(
    lower: str, state: SimulationState
) -> tuple[str | None, str | None]:
    """Return (matched_casualty_id, unknown_casualty_label)."""
    casualties = state.scenario.casualties

    for c in sorted(casualties, key=lambda item: len(item.id), reverse=True):
        if re.search(rf"\b{re.escape(c.id.lower())}\b", lower):
            return c.id, None

    m = re.search(r"\b(?:casualty|patient|victim)\s+#?([a-z0-9-]+)\b", lower)
    if m:
        token = m.group(1).strip()
        if token not in {
            "is",
            "are",
            "was",
            "were",
            "status",
            "count",
            "counts",
            "priority",
            "priorities",
            "assessment",
            "assessments",
            "unassigned",
            "assigned",
            "without",
            "with",
            "that",
            "this",
            "which",
            "who",
            "in",
            "on",
            "for",
            "to",
            "at",
            "needs",
            "need",
        }:
            for c in casualties:
                if c.id.lower().endswith(f"-{token}") or c.id.lower() == f"casualty-{token}":
                    return c.id, None
            num = NUMBER_WORDS.get(token) or LETTER_TO_INDEX.get(token)
            if num is not None:
                candidate_ids = [
                    f"casualty-{num:03d}",
                    f"casualty-{num}",
                    f"c{num - 1}",
                    f"c{num}",
                ]
                for cid in candidate_ids:
                    for c in casualties:
                        if c.id.lower() == cid:
                            return c.id, None
            return None, f"casualty {token}"

    return None, None


def parse_intent(
    raw_query: str,
    state: SimulationState,
    context: ConversationContext,
    max_chars: int = 500,
    style_override: ResponseStyle | None = None,
) -> ParsedIntent:
    """Deterministically parse an operator query with conservative context and style resolution."""
    cleaned = sanitize_utterance(raw_query, max_chars=max_chars)
    lower = cleaned.lower()
    lower_body = re.sub(r"^(?:hey\s+)?aegis\s*[,:;-]?\s*", "", lower).strip()

    explicit_style, is_pure_style_followup, include_ids = _detect_requested_style(
        lower_body
    )
    style: ResponseStyle = (
        style_override
        or explicit_style
        or context.preferred_style
        or "operational"
    )
    if explicit_style in {"operational", "detailed", "full"} and is_pure_style_followup:
        # Update sticky preference if the user said "Be brief" or "Give me the short version"
        if explicit_style == "operational":
            context.preferred_style = "operational"

    if not lower_body:
        return ParsedIntent(
            intent="clarification_needed",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            clarification_reason="empty_query",
        )

    # 1. Check prohibited mutations first
    prohibited = _detect_prohibited_mutation(lower_body)
    if prohibited is not None:
        return ParsedIntent(
            intent="prohibited_mutation",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            prohibited_action=prohibited,
        )

    # 2. Pure follow-up style expansion/compression ("Tell me more.", "Give me the short version.", "Explain in detail.")
    if is_pure_style_followup:
        prev_intent: IntentKind = (
            context.last_intent  # type: ignore[assignment]
            if context.last_intent
            in {
                "incident_status",
                "explain_optimization",
                "responder_status",
                "unassigned_casualties",
                "recent_changes",
                "acknowledgment_status",
                "casualty_status",
            }
            else "incident_status"
        )
        upgraded_style: ResponseStyle = explicit_style or "detailed"
        if (
            explicit_style == "detailed"
            and context.last_response_style == "detailed"
            and "more" in lower_body
        ):
            upgraded_style = "full"
            include_ids = True
        return ParsedIntent(
            intent=prev_intent,
            normalized_query=cleaned,
            response_style=upgraded_style,
            include_identifiers=include_ids,
            responder_id=context.last_responder_id,
            casualty_id=context.last_casualty_id,
            used_context=True,
        )

    # 3. Match explicit responder or casualty mentions against live SimulationState
    responder_id, unknown_responder = _match_responder_in_text(lower_body, state)
    casualty_id, unknown_casualty = _match_casualty_in_text(lower_body, state)

    if unknown_responder is not None:
        return ParsedIntent(
            intent="unknown_entity",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            unknown_entity_kind="responder",
            unknown_entity_label=unknown_responder,
        )
    if unknown_casualty is not None:
        return ParsedIntent(
            intent="unknown_entity",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            unknown_entity_kind="casualty",
            unknown_entity_label=unknown_casualty,
        )

    # 4. Short contextual follow-ups ("Which responder?", "Which casualty?")
    if re.match(
        r"^(?:and\s+)?which\s+(?:responder|unit)s?(?:\s+is\s+(?:that|it))?\s*[?.!]*$",
        lower_body,
    ) or lower_body.rstrip("?.!") in {"who is that", "which one"}:
        if context.last_responder_id is not None:
            return ParsedIntent(
                intent="responder_status",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                responder_id=context.last_responder_id,
                used_context=True,
            )
        if len(context.salient_responder_ids) == 1:
            return ParsedIntent(
                intent="responder_status",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                responder_id=context.salient_responder_ids[0],
                used_context=True,
            )
        return ParsedIntent(
            intent="responder_status",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            responder_id=None,
            used_context=bool(context.salient_responder_ids),
        )

    if re.match(
        r"^(?:and\s+)?which\s+(?:casualty|casualties|patient|person)(?:\s+is\s+(?:that|it))?\s*[?.!]*$",
        lower_body,
    ):
        if context.last_casualty_id is not None:
            return ParsedIntent(
                intent="casualty_status",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                casualty_id=context.last_casualty_id,
                used_context=True,
            )
        return ParsedIntent(
            intent="unassigned_casualties",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            used_context=True,
        )

    has_responder_pronoun = bool(
        re.search(r"\b(that responder|this responder|that unit|this unit)\b", lower_body)
    )
    has_casualty_pronoun = bool(
        re.search(r"\b(that casualty|this casualty|that patient|that person)\b", lower_body)
    )
    has_generic_pronoun = bool(
        re.search(r"\b(they|them|their|theirs|he|him|his|she|her|hers|it|its)\b", lower_body)
    )

    used_context = False
    if responder_id is None and has_responder_pronoun:
        if context.last_responder_id is not None:
            responder_id = context.last_responder_id
            used_context = True
        elif len(context.salient_responder_ids) == 1:
            responder_id = context.salient_responder_ids[0]
            used_context = True
        else:
            return ParsedIntent(
                intent="clarification_needed",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                clarification_reason="ambiguous_responder_reference",
            )

    if casualty_id is None and has_casualty_pronoun:
        if context.last_casualty_id is not None:
            casualty_id = context.last_casualty_id
            used_context = True
        elif len(context.salient_casualty_ids) == 1:
            casualty_id = context.salient_casualty_ids[0]
            used_context = True
        else:
            return ParsedIntent(
                intent="clarification_needed",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                clarification_reason="ambiguous_casualty_reference",
            )

    # 5. Classify primary intent
    # E. Recent changes / events
    if re.search(
        r"\b(what changed|what happened recently|latest update|recent changes|recent events|last event|latest event|what just happened|any updates)\b",
        lower_body,
    ):
        return ParsedIntent(
            intent="recent_changes",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            responder_id=responder_id,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    # F. Acknowledgment / delivery / milestone status
    if re.search(
        r"\b(acknowledg\w*|ack\b|unacknowledged|awaiting ack\w*|received the assignment|delivered|delivery status|outstanding acknowledgment)\b",
        lower_body,
    ):
        if responder_id is None and casualty_id is None and has_generic_pronoun:
            if context.last_responder_id is not None:
                responder_id = context.last_responder_id
                used_context = True
            elif context.last_casualty_id is not None:
                casualty_id = context.last_casualty_id
                used_context = True
            elif len(context.salient_responder_ids) == 1:
                responder_id = context.salient_responder_ids[0]
                used_context = True
            else:
                return ParsedIntent(
                    intent="clarification_needed",
                    normalized_query=cleaned,
                    response_style=style,
                    include_identifiers=include_ids,
                    clarification_reason="ambiguous_acknowledgment_target",
                )
        return ParsedIntent(
            intent="acknowledgment_status",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            responder_id=responder_id,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    # B. Explain optimization / route / why selected
    if re.search(
        r"\b(why did you|why was|why were|why is|why isn't|why wasn't|explain|rationale|reason for|route selected|how was .* chosen|how did the solver|current plan|optimization)\b",
        lower_body,
    ):
        if (
            responder_id is None
            and casualty_id is None
            and (has_generic_pronoun or "that" in lower_body)
        ):
            if context.last_responder_id is not None:
                responder_id = context.last_responder_id
                used_context = True
            elif context.last_casualty_id is not None:
                casualty_id = context.last_casualty_id
                used_context = True
            elif len(context.salient_responder_ids) == 1:
                responder_id = context.salient_responder_ids[0]
                used_context = True
            elif len(context.salient_responder_ids) > 1 and has_responder_pronoun:
                return ParsedIntent(
                    intent="clarification_needed",
                    normalized_query=cleaned,
                    response_style=style,
                    include_identifiers=include_ids,
                    clarification_reason="ambiguous_responder_reference",
                )
        return ParsedIntent(
            intent="explain_optimization",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            responder_id=responder_id,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    # D. Unassigned casualties / coverage gaps
    if re.search(
        r"\b(unassigned|without coverage|needs? a responder|waiting for a responder|uncovered|not assigned|left unassigned|who still needs)\b",
        lower_body,
    ):
        return ParsedIntent(
            intent="unassigned_casualties",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    # C. Responder status / location / assignment follow-ups
    if responder_id is not None or re.search(
        r"\b(where is|where are|what is .* doing|what's .* doing|their assignment|his assignment|her assignment|its assignment|assigned to|responder status|available responders|which responders are available)\b",
        lower_body,
    ):
        if responder_id is None and casualty_id is None and has_generic_pronoun:
            if context.last_responder_id is not None and (
                context.last_casualty_id is None
                or context.last_intent
                in {"responder_status", "explain_optimization", "acknowledgment_status"}
                or "assignment" in lower_body
                or "doing" in lower_body
            ):
                responder_id = context.last_responder_id
                used_context = True
            elif context.last_casualty_id is not None and context.last_responder_id is None:
                casualty_id = context.last_casualty_id
                used_context = True
                return ParsedIntent(
                    intent="casualty_status",
                    normalized_query=cleaned,
                    response_style=style,
                    include_identifiers=include_ids,
                    casualty_id=casualty_id,
                    used_context=True,
                )
            else:
                return ParsedIntent(
                    intent="clarification_needed",
                    normalized_query=cleaned,
                    response_style=style,
                    include_identifiers=include_ids,
                    clarification_reason="ambiguous_pronoun_reference",
                )
        return ParsedIntent(
            intent="responder_status",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            responder_id=responder_id,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    # Casualty-specific query
    if casualty_id is not None:
        return ParsedIntent(
            intent="casualty_status",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            casualty_id=casualty_id,
            used_context=used_context,
        )

    if has_generic_pronoun and re.search(
        r"\b(priority|assessment|assessed|severity|signs|track|position|coordinates)\b",
        lower_body,
    ):
        if context.last_casualty_id is not None and context.last_responder_id is None:
            return ParsedIntent(
                intent="casualty_status",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                casualty_id=context.last_casualty_id,
                used_context=True,
            )
        if context.last_responder_id is not None and context.last_casualty_id is None:
            return ParsedIntent(
                intent="responder_status",
                normalized_query=cleaned,
                response_style=style,
                include_identifiers=include_ids,
                responder_id=context.last_responder_id,
                used_context=True,
            )
        return ParsedIntent(
            intent="clarification_needed",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
            clarification_reason="ambiguous_pronoun_reference",
        )

    # A. Incident status / situation report
    if re.search(
        r"\b(status|situation|happening|overview|summary|report|sitrep|scene|casualties|responders|how many)\b",
        lower_body,
    ):
        return ParsedIntent(
            intent="incident_status",
            normalized_query=cleaned,
            response_style=style,
            include_identifiers=include_ids,
        )

    return ParsedIntent(
        intent="clarification_needed",
        normalized_query=cleaned,
        response_style=style,
        include_identifiers=include_ids,
        clarification_reason="unrecognized_command",
    )
