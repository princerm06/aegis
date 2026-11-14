"""State-grounded response generator for AEGIS Voice V1.

Every response is deterministically synthesized from authoritative read-only
``SimulationState`` and ``EventPage`` models. Every generated response records
the exact backend field paths (`trace_fields`) used to build it and preserves
simulation-only, human-supervised framing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.models import Casualty, Responder
from backend.app.simulation.models import (
    EventPage,
    SimulatedAssignment,
    SimulationProposal,
    SimulationState,
    TrackRecord,
)
from voice.context import ConversationContext
from voice.intents import ParsedIntent

EVENT_DESCRIPTIONS: dict[str, str] = {
    "simulation.reset": "a simulation scenario reset",
    "simulation.observation_received": "a camera observation update",
    "simulation.camera_registered": "a simulation camera registration",
    "simulation.casualty_created": "a new confirmed casualty registration",
    "simulation.track_associated": "a camera track-to-casualty association",
    "simulation.assessment_recorded": "a human-entered medical assessment",
    "simulation.capabilities_updated": "a casualty required-capability update",
    "simulation.responder_updated": "a responder availability or position update",
    "simulation.map_updated": "a scene obstacle map update",
    "simulation.replan_requested": "an optimization replan request",
    "simulation.recommendation_computed": "an optimizer recommendation computation",
    "simulation.proposal_created": "a new pending dispatch proposal",
    "simulation.proposal_superseded": "a proposal supersession due to scene changes",
    "simulation.proposal_expired": "a proposal expiration",
    "simulation.decision_recorded": "an operator proposal decision",
    "simulation.approval_invalidated": "an approval invalidation after a scene change",
    "simulation.dispatch_created": "an operator-authorized simulation dispatch",
    "simulation.device_delivered": "a simulated responder device delivery receipt",
    "simulation.device_acknowledged": "a simulated responder acknowledgment",
    "simulation.device_completed": "a simulated assignment completion report",
    "simulation.responder_released": "a responder release back to available status",
    "simulation.intelligence_policy_updated": "an intelligence policy update",
}

REASON_EXPLANATIONS: dict[str, str] = {
    "assigned_feasible_route": "matched with a verified feasible A* route",
    "active_assignment_preserved": "covered by an existing active dispatched assignment",
    "feasible_but_capacity_or_objective_limited": "feasible route exists, but all available responders were allocated to higher-priority or shorter-route assignments",
    "responder_unavailable": "responders are currently busy or offline",
    "responder_reserved_active": "responder is reserved on an active dispatched assignment",
    "required_capabilities_missing": "no available responder has the required capability tags",
    "unreachable": "no traversable A* route exists on the current occupancy grid",
    "no_responders": "no available responders in the scenario",
    "map_unavailable": "scene map is unavailable",
    "occupancy_grid_unavailable": "occupancy grid is unavailable",
    "observation_stale": "associated camera observation is stale (older than the freshness threshold)",
    "observation_low_confidence": "associated camera observation confidence is below the minimum threshold",
    "observation_position_mismatch": "camera observation position differs from the casualty position",
    "observation_future_dated": "camera observation timestamp is future-dated",
    "track_occluded": "associated camera track is currently occluded",
    "track_lost": "associated camera track is lost",
    "track_stale": "associated camera track detection is stale",
}


@dataclass
class GroundedAnswer:
    """Grounded spoken and display response with full field provenance."""

    intent: str
    spoken_text: str
    display_text: str
    responder_id: str | None = None
    casualty_id: str | None = None
    proposal_id: str | None = None
    assignment_id: str | None = None
    salient_responder_ids: list[str] = field(default_factory=list)
    salient_casualty_ids: list[str] = field(default_factory=list)
    trace_fields: list[str] = field(default_factory=list)
    read_only: bool = True
    state_mutated: bool = False
    mode: str = "simulation"


def _active_assignments(state: SimulationState) -> list[SimulatedAssignment]:
    return [a for a in state.assignments if a.status != "completed"]


def _completed_assignments(state: SimulationState) -> list[SimulatedAssignment]:
    return [a for a in state.assignments if a.status == "completed"]


def _current_proposal(state: SimulationState) -> SimulationProposal | None:
    if state.current_proposal_id is not None:
        for p in state.proposals:
            if p.id == state.current_proposal_id:
                return p
    # Fallback to most recent open proposal if any
    for p in reversed(state.proposals):
        if p.status in ("pending_approval", "approved"):
            return p
    return None


def _latest_proposal_any_status(state: SimulationState) -> SimulationProposal | None:
    cur = _current_proposal(state)
    if cur is not None:
        return cur
    return state.proposals[-1] if state.proposals else None


def _find_responder(state: SimulationState, responder_id: str) -> Responder | None:
    for r in state.scenario.responders:
        if r.id == responder_id:
            return r
    return None


def _find_casualty(state: SimulationState, casualty_id: str) -> Casualty | None:
    for c in state.scenario.casualties:
        if c.id == casualty_id:
            return c
    return None


def _find_track_for_casualty(state: SimulationState, casualty_id: str) -> TrackRecord | None:
    for t in state.tracks:
        if t.casualty_id == casualty_id:
            return t
    return None


def _humanize_reasons(reasons: list[str]) -> str:
    if not reasons:
        return "no blocking reason recorded"
    parts = []
    for r in reasons:
        clean = r.removeprefix("temporal_gate:")
        desc = REASON_EXPLANATIONS.get(clean, clean.replace("_", " "))
        parts.append(f"{r} ({desc})" if r not in REASON_EXPLANATIONS else desc)
    return "; ".join(parts)


def _truncate_for_speech(text: str, max_chars: int = 420) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text[: max_chars - 3].rsplit(" ", 1)[0]
    return f"{cutoff}..."


def generate_grounded_answer(
    parsed: ParsedIntent,
    state: SimulationState,
    events_page: EventPage | None,
    context: ConversationContext,
    max_tts_chars: int = 420,
) -> GroundedAnswer:
    """Generate a state-grounded response for `parsed` from live `state` and `events_page`."""
    intent = parsed.intent

    if intent == "prohibited_mutation":
        return _handle_prohibited_mutation(parsed, state, max_tts_chars)
    if intent == "unknown_entity":
        return _handle_unknown_entity(parsed, state, max_tts_chars)
    if intent == "clarification_needed":
        return _handle_clarification(parsed, state, context, max_tts_chars)
    if intent == "incident_status":
        return _handle_incident_status(parsed, state, max_tts_chars)
    if intent == "explain_optimization":
        return _handle_explain_optimization(parsed, state, max_tts_chars)
    if intent == "responder_status":
        return _handle_responder_status(parsed, state, context, max_tts_chars)
    if intent == "unassigned_casualties":
        return _handle_unassigned_casualties(parsed, state, max_tts_chars)
    if intent == "recent_changes":
        return _handle_recent_changes(state, events_page, max_tts_chars)
    if intent == "acknowledgment_status":
        return _handle_acknowledgment_status(parsed, state, max_tts_chars)
    if intent == "casualty_status":
        return _handle_casualty_status(parsed, state, max_tts_chars)

    return _handle_clarification(parsed, state, context, max_tts_chars)


def _handle_prohibited_mutation(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    action = parsed.prohibited_action or "state_mutation"
    cur_prop = _current_proposal(state)
    prop_note = (
        f"Current simulation proposal {cur_prop.id} (revision {cur_prop.revision}) is {cur_prop.status}."
        if cur_prop is not None
        else "There is currently no open proposal."
    )

    guidance_map = {
        "dispatch": (
            "Simulation advisory only: AEGIS Voice is strictly read-only and cannot authorize or execute dispatch. "
            f"{prop_note} To dispatch in simulation, an operator must manually approve the proposal and click Dispatch in the command dashboard."
        ),
        "proposal_decision": (
            "Simulation advisory only: AEGIS Voice is strictly read-only and cannot approve or reject proposals. "
            f"{prop_note} Please review and approve or reject the proposal manually in the dashboard."
        ),
        "medical_assessment": (
            "Simulation advisory only: AEGIS Voice cannot record or modify medical assessments or triage priorities. "
            "All triage classifications must be entered manually by a human operator (source: human) in the dashboard."
        ),
        "track_association": (
            "Simulation advisory only: AEGIS Voice is read-only and cannot create casualties or relink camera tracks. "
            "Use the dashboard association controls to confirm or relink a track."
        ),
        "simulation_reset": (
            "Simulation advisory only: AEGIS Voice is read-only and will never reset or clear the simulation scenario."
        ),
        "map_modification": (
            "Simulation advisory only: AEGIS Voice is read-only and cannot modify the scene obstacle map."
        ),
        "responder_mutation": (
            "Simulation advisory only: AEGIS Voice is read-only and cannot alter responder availability or positions."
        ),
        "device_event_mutation": (
            "Simulation advisory only: AEGIS Voice is read-only and cannot emit responder device acknowledgments or completions."
        ),
    }
    text = guidance_map.get(
        action,
        "Simulation advisory only: AEGIS Voice is strictly read-only and cannot perform state-changing actions. "
        "All assessments, approvals, and dispatches must be performed manually by the human operator in the dashboard.",
    )
    return GroundedAnswer(
        intent="prohibited_mutation",
        spoken_text=_truncate_for_speech(text, max_tts_chars),
        display_text=text,
        proposal_id=cur_prop.id if cur_prop else None,
        trace_fields=["state.mode", "state.scene_revision", "state.current_proposal_id"],
    )


def _handle_unknown_entity(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    if parsed.unknown_entity_kind == "responder":
        known = [f"{r.display_name} ({r.id})" for r in state.scenario.responders]
        known_str = ", ".join(known) if known else "none"
        text = (
            f"Simulation status: I could not find {parsed.unknown_entity_label} in scenario {state.scenario_id}. "
            f"Known simulation responders are: {known_str}."
        )
        trace = ["state.scenario.responders"]
    else:
        known = [c.id for c in state.scenario.casualties]
        known_str = ", ".join(known) if known else "none"
        text = (
            f"Simulation status: I could not find {parsed.unknown_entity_label} in scenario {state.scenario_id}. "
            f"Known simulation casualties are: {known_str}."
        )
        trace = ["state.scenario.casualties"]
    return GroundedAnswer(
        intent="unknown_entity",
        spoken_text=_truncate_for_speech(text, max_tts_chars),
        display_text=text,
        trace_fields=trace,
    )


def _handle_clarification(
    parsed: ParsedIntent,
    state: SimulationState,
    context: ConversationContext,
    max_tts_chars: int,
) -> GroundedAnswer:
    reason = parsed.clarification_reason or "unrecognized_command"
    responders = [f"{r.display_name} ({r.id})" for r in state.scenario.responders]
    casualties = [c.id for c in state.scenario.casualties]

    if reason in {
        "ambiguous_responder_reference",
        "ambiguous_pronoun_reference",
        "ambiguous_acknowledgment_target",
    }:
        text = (
            "Simulation clarification needed: that reference is ambiguous. "
            f"Please specify which responder ({', '.join(responders) or 'none'}) "
            f"or casualty ({', '.join(casualties) or 'none'}) you are asking about."
        )
    elif reason == "ambiguous_casualty_reference":
        text = (
            "Simulation clarification needed: please specify which casualty you mean "
            f"({', '.join(casualties) or 'none'})."
        )
    else:
        text = (
            "Simulation assistant ready: please ask about current incident status, "
            "why the optimization plan was chosen, responder status, unassigned casualties, "
            "recent changes, or acknowledgment status."
        )
    return GroundedAnswer(
        intent="clarification_needed",
        spoken_text=_truncate_for_speech(text, max_tts_chars),
        display_text=text,
        trace_fields=["state.scenario.responders", "state.scenario.casualties"],
    )


_COUNT_WORDS = {
    0: "Zero",
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
}


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    word = _COUNT_WORDS.get(count, str(count))
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{word} {noun}"


def _handle_incident_status(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    unresolved = [
        c for c in state.scenario.casualties if c.id not in state.resolved_casualty_ids
    ]
    assessed = [c for c in unresolved if c.medical_assessment is not None]
    unassessed = [c for c in unresolved if c.medical_assessment is None]
    visible_tracks = [
        t
        for t in state.tracks
        if t.latest_observation.track is not None
        and t.latest_observation.track.state == "visible"
    ]

    avail_res = [r for r in state.scenario.responders if r.status == "available"]
    busy_res = [r for r in state.scenario.responders if r.status == "busy"]
    offline_res = [r for r in state.scenario.responders if r.status == "offline"]

    active = _active_assignments(state)
    awaiting_ack = [a for a in active if a.status in ("dispatched", "delivered")]
    acknowledged = [a for a in active if a.status == "acknowledged"]
    cur_prop = _current_proposal(state)

    parts = [
        f"Simulation status for scenario {state.scenario_id} (revision {state.scene_revision}): "
        f"{len(unresolved)} unresolved casualties ({len(assessed)} human-assessed, {len(unassessed)} unassessed) "
        f"and {len(visible_tracks)} visible camera tracks."
    ]
    parts.append(
        f"Responders: {len(avail_res)} available, {len(busy_res)} busy, {len(offline_res)} offline."
    )

    salient_responders: list[str] = []
    salient_casualties: list[str] = []

    if active:
        if len(awaiting_ack) == 1:
            a0 = awaiting_ack[0]
            salient_responders = [a0.responder_id]
            salient_casualties = [a0.casualty_id]
            parts.append(
                f"One responder ({a0.responder_id}) is awaiting acknowledgment on active assignment {a0.id} ({a0.status}) for {a0.casualty_id}."
            )
        elif len(awaiting_ack) > 1:
            salient_responders = [a.responder_id for a in awaiting_ack]
            parts.append(
                f"{len(awaiting_ack)} active assignments are awaiting acknowledgment."
            )
        if acknowledged:
            parts.append(f"{len(acknowledged)} active assignment(s) have been acknowledged.")

    if cur_prop is not None:
        if not salient_responders:
            salient_responders = [a.responder_id for a in cur_prop.assignments]
        if not salient_casualties:
            salient_casualties = [a.casualty_id for a in cur_prop.assignments]
        if cur_prop.status == "pending_approval":
            parts.append(
                f"Current proposal {cur_prop.id} recommends {len(cur_prop.assignments)} assignment(s) and requires manual operator approval."
            )
        elif cur_prop.status == "approved":
            parts.append(
                f"Current proposal {cur_prop.id} is approved by an operator and awaiting simulation dispatch."
            )
    else:
        parts.append("There is no open pending proposal requiring approval.")

    full_text = " ".join(parts)

    # Build concise ~10-20 word spoken summary for default operational mode
    if len(unresolved) > 0 and len(unassessed) == len(unresolved):
        cas_spoken = f"{_count_phrase(len(unassessed), 'unassessed casualty', 'unassessed casualties')}."
    elif len(unresolved) > 0 and len(assessed) == len(unresolved):
        cas_spoken = f"{_count_phrase(len(assessed), 'assessed casualty', 'assessed casualties')}."
    else:
        cas_spoken = f"{_count_phrase(len(unresolved), 'casualty', 'casualties')}, {_COUNT_WORDS.get(len(unassessed), str(len(unassessed))).lower()} unassessed."

    res_spoken = f"{_count_phrase(len(avail_res), 'responder')} available."

    if awaiting_ack:
        act_spoken = f"{_count_phrase(len(awaiting_ack), 'assignment')} awaiting acknowledgment."
    elif cur_prop is not None and cur_prop.status == "pending_approval":
        act_spoken = f"{_count_phrase(len(cur_prop.assignments), 'proposed assignment')} awaiting approval."
    elif cur_prop is not None and cur_prop.status == "approved":
        act_spoken = f"{_count_phrase(len(cur_prop.assignments), 'approved assignment')} awaiting dispatch."
    elif acknowledged:
        act_spoken = f"{_count_phrase(len(acknowledged), 'active assignment')} acknowledged."
    else:
        act_spoken = "No open proposals awaiting approval."

    if parsed.response_style == "full":
        spoken_summary = full_text
    elif parsed.response_style == "detailed":
        spoken_summary = (
            f"Simulation status: {cas_spoken} {res_spoken} "
            f"{_count_phrase(len(busy_res), 'responder')} busy. {act_spoken}"
        )
    else:
        spoken_summary = f"{cas_spoken} {res_spoken} {act_spoken}"

    return GroundedAnswer(
        intent="incident_status",
        spoken_text=_truncate_for_speech(spoken_summary, max_tts_chars),
        display_text=full_text,
        responder_id=salient_responders[0] if len(salient_responders) == 1 else None,
        casualty_id=salient_casualties[0] if len(salient_casualties) == 1 else None,
        proposal_id=cur_prop.id if cur_prop else None,
        salient_responder_ids=salient_responders,
        salient_casualty_ids=salient_casualties,
        trace_fields=[
            "state.scenario_id",
            "state.scene_revision",
            "state.scenario.casualties",
            "state.scenario.responders",
            "state.tracks",
            "state.current_proposal_id",
            "state.proposals",
            "state.assignments",
        ],
    )


def _handle_explain_optimization(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    cur_prop = _current_proposal(state)
    latest_prop = _latest_proposal_any_status(state)
    active = _active_assignments(state)
    rec = state.last_recommendation

    # Case 1: Specific responder or casualty asked about
    target_res_id = parsed.responder_id
    target_cas_id = parsed.casualty_id

    if cur_prop is None and not active and latest_prop is None:
        reason_str = ""
        if rec is not None and rec.reasons:
            summarised = [
                f"{cid}: {_humanize_reasons(rlist)}"
                for cid, rlist in sorted(rec.reasons.items())
            ]
            reason_str = " Unassigned reasons: " + "; ".join(summarised) + "."
        text = (
            f"Simulation optimization status: no proposal currently exists at scene revision {state.scene_revision}.{reason_str}"
        )
        return GroundedAnswer(
            intent="explain_optimization",
            spoken_text=_truncate_for_speech(text, max_tts_chars),
            display_text=text,
            trace_fields=["state.current_proposal_id", "state.last_recommendation"],
        )

    prop = cur_prop or latest_prop
    assert prop is not None

    # Check if asking about a specific responder
    if target_res_id is not None:
        resp_obj = _find_responder(state, target_res_id)
        r_label = f"{resp_obj.display_name} ({resp_obj.id})" if resp_obj else target_res_id

        # Check proposed assignments first, then active dispatched assignments
        prop_match = next(
            (a for a in prop.assignments if a.responder_id == target_res_id), None
        )
        act_match = next(
            (a for a in active if a.responder_id == target_res_id), None
        )

        if prop_match is not None:
            cas_obj = _find_casualty(state, prop_match.casualty_id)
            assess_desc = (
                f"human-assessed {cas_obj.medical_assessment.severity}"
                if (cas_obj and cas_obj.medical_assessment is not None)
                else "unassessed (protected immediate allocation parity)"
            )
            req_caps = state.required_capabilities.get(prop_match.casualty_id, [])
            cap_desc = (
                f"required capabilities {req_caps}"
                if req_caps
                else "no special capability restrictions"
            )
            dist_desc = (
                f"{prop_match.route.distance_m:.1f} meters via verified A* route"
                if prop_match.route is not None
                else "a verified route"
            )
            status_label = (
                "proposed (pending operator approval)"
                if prop.status == "pending_approval"
                else f"in proposal {prop.id} (status: {prop.status})"
            )
            text = (
                f"Simulation optimization explanation: {r_label} is {status_label} for {prop_match.casualty_id} "
                f"using engine {prop.engine}. {prop_match.casualty_id} is {assess_desc} with {cap_desc}, "
                f"and {r_label} is available at a planned route distance of {dist_desc}. "
                "The optimizer maximizes feasible assignment count and human-priority points before minimizing travel distance."
            )
            return GroundedAnswer(
                intent="explain_optimization",
                spoken_text=_truncate_for_speech(text, max_tts_chars),
                display_text=text,
                responder_id=target_res_id,
                casualty_id=prop_match.casualty_id,
                proposal_id=prop.id,
                salient_responder_ids=[target_res_id],
                salient_casualty_ids=[prop_match.casualty_id],
                trace_fields=[
                    f"state.proposals[{prop.id}].assignments",
                    f"state.proposals[{prop.id}].engine",
                    f"state.proposals[{prop.id}].objective_values",
                    "state.scenario.casualties",
                    "state.required_capabilities",
                ],
            )
        if act_match is not None:
            dist_desc = (
                f"{act_match.route.distance_m:.1f} meters"
                if act_match.route is not None
                else "no route distance recorded"
            )
            text = (
                f"Simulation optimization explanation: {r_label} is already on an operator-dispatched active assignment "
                f"({act_match.id}, status: {act_match.status}) for {act_match.casualty_id} with route distance {dist_desc}. "
                "Active dispatched assignments are hard-reserved by the solver and never silently reassigned."
            )
            return GroundedAnswer(
                intent="explain_optimization",
                spoken_text=_truncate_for_speech(text, max_tts_chars),
                display_text=text,
                responder_id=target_res_id,
                casualty_id=act_match.casualty_id,
                assignment_id=act_match.id,
                salient_responder_ids=[target_res_id],
                salient_casualty_ids=[act_match.casualty_id],
                trace_fields=[
                    f"state.assignments[{act_match.id}]",
                    f"state.proposals[{prop.id}].preserved_assignments",
                ],
            )

        text = (
            f"Simulation optimization explanation: {r_label} is not selected in proposal {prop.id} "
            f"(responder status is {resp_obj.status if resp_obj else 'unknown'})."
        )
        return GroundedAnswer(
            intent="explain_optimization",
            spoken_text=_truncate_for_speech(text, max_tts_chars),
            display_text=text,
            responder_id=target_res_id,
            proposal_id=prop.id,
            salient_responder_ids=[target_res_id],
            trace_fields=[f"state.proposals[{prop.id}].assignments", "state.scenario.responders"],
        )

    # Check if asking about a specific casualty
    if target_cas_id is not None:
        prop_match = next(
            (a for a in prop.assignments if a.casualty_id == target_cas_id), None
        )
        act_match = next(
            (a for a in active if a.casualty_id == target_cas_id), None
        )
        reasons = prop.reasons.get(target_cas_id, [])
        if prop_match is not None:
            dist = (
                f"{prop_match.route.distance_m:.1f} meters"
                if prop_match.route is not None
                else "verified route"
            )
            text = (
                f"Simulation optimization explanation: {target_cas_id} is proposed for {prop_match.responder_id} "
                f"in proposal {prop.id} ({prop.status}) over an A* route of {dist} ({_humanize_reasons(reasons)})."
            )
            return GroundedAnswer(
                intent="explain_optimization",
                spoken_text=_truncate_for_speech(text, max_tts_chars),
                display_text=text,
                responder_id=prop_match.responder_id,
                casualty_id=target_cas_id,
                proposal_id=prop.id,
                salient_responder_ids=[prop_match.responder_id],
                salient_casualty_ids=[target_cas_id],
                trace_fields=[
                    f"state.proposals[{prop.id}].assignments",
                    f"state.proposals[{prop.id}].reasons[{target_cas_id}]",
                ],
            )
        if act_match is not None:
            text = (
                f"Simulation optimization explanation: {target_cas_id} is already covered by active dispatched assignment "
                f"{act_match.id} with responder {act_match.responder_id} (status: {act_match.status})."
            )
            return GroundedAnswer(
                intent="explain_optimization",
                spoken_text=_truncate_for_speech(text, max_tts_chars),
                display_text=text,
                responder_id=act_match.responder_id,
                casualty_id=target_cas_id,
                assignment_id=act_match.id,
                trace_fields=[f"state.assignments[{act_match.id}]"],
            )
        text = (
            f"Simulation optimization explanation: {target_cas_id} is unassigned in proposal {prop.id} "
            f"because: {_humanize_reasons(reasons)}."
        )
        return GroundedAnswer(
            intent="explain_optimization",
            spoken_text=_truncate_for_speech(text, max_tts_chars),
            display_text=text,
            casualty_id=target_cas_id,
            proposal_id=prop.id,
            salient_casualty_ids=[target_cas_id],
            trace_fields=[
                f"state.proposals[{prop.id}].unassigned_casualty_ids",
                f"state.proposals[{prop.id}].reasons[{target_cas_id}]",
            ],
        )

    # General plan explanation
    pair_descriptions = []
    salient_res = []
    salient_cas = []
    for a in prop.assignments:
        salient_res.append(a.responder_id)
        salient_cas.append(a.casualty_id)
        r_obj = _find_responder(state, a.responder_id)
        r_name = f"{r_obj.display_name} ({a.responder_id})" if r_obj else a.responder_id
        d_str = f"{a.route.distance_m:.1f}m A* route" if a.route else "verified route"
        pair_descriptions.append(f"{r_name} -> {a.casualty_id} ({d_str})")

    unassigned_notes = []
    for cid in prop.unassigned_casualty_ids:
        rlist = prop.reasons.get(cid, [])
        unassigned_notes.append(f"{cid} ({_humanize_reasons(rlist)})")

    stage_desc = {
        "pending_approval": "pending manual operator approval (not yet approved or dispatched)",
        "approved": "approved by an operator (awaiting simulation dispatch)",
        "dispatched": "dispatched in simulation",
        "superseded": f"superseded ({prop.closed_reason})",
        "expired": f"expired ({prop.closed_reason})",
        "rejected": f"rejected ({prop.closed_reason})",
    }.get(prop.status, prop.status)

    total_dist = prop.objective_values.get("new_travel_distance_m", 0.0)
    prio_pts = prop.objective_values.get("allocation_priority_points", 0.0)

    parts = [
        f"Simulation optimization plan (proposal {prop.id}, revision {prop.revision}, engine {prop.engine}): "
        f"status is {stage_desc}."
    ]
    if pair_descriptions:
        parts.append(
            f"Proposed pairs: {', '.join(pair_descriptions)} "
            f"(priority points: {prio_pts:.0f}, total route distance: {total_dist:.1f}m)."
        )
    if prop.preserved_assignments:
        pres = [f"{a.responder_id} -> {a.casualty_id}" for a in prop.preserved_assignments]
        parts.append(f"Preserved active assignments: {', '.join(pres)}.")
    if unassigned_notes:
        parts.append(f"Unassigned casualties: {', '.join(unassigned_notes)}.")

    full_text = " ".join(parts)
    return GroundedAnswer(
        intent="explain_optimization",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        proposal_id=prop.id,
        salient_responder_ids=salient_res,
        salient_casualty_ids=salient_cas,
        trace_fields=[
            f"state.proposals[{prop.id}].status",
            f"state.proposals[{prop.id}].engine",
            f"state.proposals[{prop.id}].assignments",
            f"state.proposals[{prop.id}].unassigned_casualty_ids",
            f"state.proposals[{prop.id}].reasons",
            f"state.proposals[{prop.id}].objective_values",
        ],
    )


def _handle_responder_status(
    parsed: ParsedIntent,
    state: SimulationState,
    context: ConversationContext,
    max_tts_chars: int,
) -> GroundedAnswer:
    active = _active_assignments(state)
    cur_prop = _current_proposal(state)
    target_id = parsed.responder_id

    if target_id is not None:
        r = _find_responder(state, target_id)
        if r is None:
            return _handle_unknown_entity(
                ParsedIntent(
                    intent="unknown_entity",
                    normalized_query=parsed.normalized_query,
                    unknown_entity_kind="responder",
                    unknown_entity_label=target_id,
                ),
                state,
                max_tts_chars,
            )

        act_assign = next((a for a in active if a.responder_id == r.id), None)
        prop_assign = (
            next((a for a in cur_prop.assignments if a.responder_id == r.id), None)
            if cur_prop is not None
            else None
        )
        caps = ", ".join(r.capabilities) if r.capabilities else "none declared"

        parts = [
            f"Simulation responder {r.display_name} ({r.id}) is currently {r.status} "
            f"at simulated map coordinates ({r.position.x_m:.1f}, {r.position.y_m:.1f}) meters "
            f"in frame {r.position.frame_id} (capabilities: {caps})."
        ]
        matched_cas_id: str | None = None
        matched_assign_id: str | None = None

        if act_assign is not None:
            matched_cas_id = act_assign.casualty_id
            matched_assign_id = act_assign.id
            ack_detail = {
                "dispatched": "dispatched, awaiting device delivery and responder acknowledgment",
                "delivered": "delivered to simulated device, awaiting responder acknowledgment",
                "acknowledged": "acknowledged by the responder",
            }.get(act_assign.status, act_assign.status)
            parts.append(
                f"Active dispatched assignment {act_assign.id}: assigned to {act_assign.casualty_id} ({ack_detail})."
            )
        elif prop_assign is not None and cur_prop is not None:
            matched_cas_id = prop_assign.casualty_id
            dist = (
                f" ({prop_assign.route.distance_m:.1f}m A* route)"
                if prop_assign.route is not None
                else ""
            )
            stage = (
                "proposed and awaiting operator approval"
                if cur_prop.status == "pending_approval"
                else "approved by operator and awaiting simulation dispatch"
            )
            parts.append(
                f"In proposal {cur_prop.id}, {r.display_name} is {stage} for {prop_assign.casualty_id}{dist}."
            )
        else:
            parts.append("Not currently assigned to any active dispatch or open proposal.")

        full_text = " ".join(parts)
        return GroundedAnswer(
            intent="responder_status",
            spoken_text=_truncate_for_speech(full_text, max_tts_chars),
            display_text=full_text,
            responder_id=r.id,
            casualty_id=matched_cas_id,
            proposal_id=cur_prop.id if cur_prop else None,
            assignment_id=matched_assign_id,
            salient_responder_ids=[r.id],
            salient_casualty_ids=[matched_cas_id] if matched_cas_id else [],
            trace_fields=[
                f"state.scenario.responders[{r.id}]",
                "state.assignments",
                "state.proposals",
            ],
        )

    # All responders summary
    summaries = []
    salient_res = []
    for r in state.scenario.responders:
        salient_res.append(r.id)
        act_assign = next((a for a in active if a.responder_id == r.id), None)
        prop_assign = (
            next((a for a in cur_prop.assignments if a.responder_id == r.id), None)
            if cur_prop is not None
            else None
        )
        if act_assign is not None:
            task = f"active dispatch to {act_assign.casualty_id} ({act_assign.status})"
        elif prop_assign is not None and cur_prop is not None:
            task = f"{cur_prop.status} proposal for {prop_assign.casualty_id}"
        else:
            task = "no current assignment"
        summaries.append(
            f"{r.display_name} ({r.id}): {r.status} at simulated coordinates ({r.position.x_m:.1f}, {r.position.y_m:.1f})m, {task}"
        )

    full_text = "Simulation responders: " + "; ".join(summaries) + "."
    return GroundedAnswer(
        intent="responder_status",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        salient_responder_ids=salient_res,
        trace_fields=["state.scenario.responders", "state.assignments", "state.proposals"],
    )


def _handle_unassigned_casualties(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    active = _active_assignments(state)
    active_by_cas = {a.casualty_id: a for a in active}
    cur_prop = _current_proposal(state)
    prop_by_cas = (
        {a.casualty_id: a for a in cur_prop.assignments} if cur_prop is not None else {}
    )
    reasons_map = (
        cur_prop.reasons
        if cur_prop is not None
        else (state.last_recommendation.reasons if state.last_recommendation else {})
    )

    unassigned_list: list[str] = []
    proposed_list: list[str] = []
    dispatched_list: list[str] = []
    unassigned_ids: list[str] = []

    for c in state.scenario.casualties:
        if c.id in state.resolved_casualty_ids:
            continue
        assess = (
            f"human-assessed {c.medical_assessment.severity}"
            if c.medical_assessment is not None
            else "unassessed"
        )
        if c.id in active_by_cas:
            a = active_by_cas[c.id]
            dispatched_list.append(f"{c.id} ({assess}, dispatched to {a.responder_id}, {a.status})")
        elif c.id in prop_by_cas and cur_prop is not None:
            a = prop_by_cas[c.id]
            stage = (
                "proposed pending approval"
                if cur_prop.status == "pending_approval"
                else "approved awaiting dispatch"
            )
            proposed_list.append(f"{c.id} ({assess}, {stage} with {a.responder_id})")
        else:
            unassigned_ids.append(c.id)
            rlist = reasons_map.get(c.id, [])
            unassigned_list.append(f"{c.id} ({assess}; reason: {_humanize_reasons(rlist)})")

    parts = []
    if unassigned_list:
        parts.append(
            f"Simulation unassigned casualties ({len(unassigned_list)} without a responder): "
            + "; ".join(unassigned_list)
            + "."
        )
    else:
        parts.append(
            "Simulation coverage status: no unresolved casualties are completely unassigned."
        )

    if proposed_list:
        parts.append(
            f"Covered only by unexecuted proposal ({len(proposed_list)}): "
            + "; ".join(proposed_list)
            + "."
        )
    if dispatched_list:
        parts.append(
            f"Actively dispatched ({len(dispatched_list)}): "
            + "; ".join(dispatched_list)
            + "."
        )

    full_text = " ".join(parts)
    return GroundedAnswer(
        intent="unassigned_casualties",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        casualty_id=unassigned_ids[0] if len(unassigned_ids) == 1 else None,
        proposal_id=cur_prop.id if cur_prop else None,
        salient_casualty_ids=unassigned_ids,
        trace_fields=[
            "state.scenario.casualties",
            "state.resolved_casualty_ids",
            "state.assignments",
            "state.proposals",
            "state.last_recommendation",
        ],
    )


def _handle_recent_changes(
    state: SimulationState,
    events_page: EventPage | None,
    max_tts_chars: int,
) -> GroundedAnswer:
    if events_page is None or not events_page.events:
        text = (
            f"Simulation event log for scenario {state.scenario_id}: no recorded events are available "
            f"(current scene revision is {state.scene_revision})."
        )
        return GroundedAnswer(
            intent="recent_changes",
            spoken_text=_truncate_for_speech(text, max_tts_chars),
            display_text=text,
            trace_fields=["events.events", "state.scene_revision"],
        )

    # Prefer domain events over internal auto-replan bookkeeping events when summarizing what changed
    bookkeeping_types = {"simulation.replan_requested", "simulation.recommendation_computed"}
    domain_events = [e for e in events_page.events if e.type not in bookkeeping_types]
    source_events = domain_events if domain_events else events_page.events
    recent = source_events[-3:]
    latest = recent[-1]
    latest_desc = EVENT_DESCRIPTIONS.get(latest.type, latest.type)
    stamp = latest.occurred_at.isoformat().replace("+00:00", "Z")

    detail = ""
    payload = latest.payload or {}
    if latest.type == "simulation.observation_received":
        tid = payload.get("track_id") or "untracked"
        cid = payload.get("casualty_id")
        detail = f" for track {tid}" + (f" (linked to {cid})" if cid else "")
    elif latest.type == "simulation.track_associated":
        tid = payload.get("track_id")
        cid = payload.get("casualty_id")
        repl = payload.get("replaced_track_id")
        detail = f" linking {tid} to {cid}" + (f" (replaced {repl})" if repl else "")
    elif latest.type == "simulation.assessment_recorded":
        cid = payload.get("casualty_id")
        sev = (payload.get("medical_assessment") or {}).get("severity")
        detail = f" setting {cid} to human-assessed {sev}"
    elif latest.type == "simulation.proposal_created":
        pid = payload.get("id")
        detail = f" ({pid})"
    elif latest.type == "simulation.dispatch_created":
        did = (payload.get("dispatch") or {}).get("id")
        detail = f" ({did})"
    elif latest.type.startswith("simulation.device_"):
        assign = payload.get("assignment") or {}
        detail = f" for responder {assign.get('responder_id')} on {assign.get('casualty_id')}"

    dispatch_note = (
        f"{len(state.dispatches)} simulation dispatch(es) recorded so far."
        if state.dispatches
        else "No simulation dispatch has been authorized."
    )

    prev_types = [
        f"#{e.sequence} {EVENT_DESCRIPTIONS.get(e.type, e.type)}"
        for e in reversed(recent[:-1])
    ]
    prev_note = f" Earlier events: {', '.join(prev_types)}." if prev_types else ""

    full_text = (
        f"Simulation recent changes: the latest recorded event (#{latest.sequence} at {stamp}) "
        f"was {latest_desc}{detail}. {dispatch_note}{prev_note}"
    )
    return GroundedAnswer(
        intent="recent_changes",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        trace_fields=[
            f"events.events[{latest.sequence}]",
            "events.latest_sequence",
            "state.dispatches",
        ],
    )


def _handle_acknowledgment_status(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    cur_prop = _current_proposal(state)
    active = _active_assignments(state)
    completed = _completed_assignments(state)

    # Filter if specific responder or casualty was requested
    if parsed.responder_id is not None or parsed.casualty_id is not None:
        r_id = parsed.responder_id
        c_id = parsed.casualty_id

        for a in state.assignments:
            if (r_id and a.responder_id == r_id) or (c_id and a.casualty_id == c_id):
                stage_explain = {
                    "dispatched": "dispatched in simulation, but not yet delivered or acknowledged by the responder device",
                    "delivered": "delivered to the simulated responder device, but not yet acknowledged",
                    "acknowledged": "acknowledged by the responder, not yet marked completed",
                    "completed": "completed",
                }[a.status]
                text = (
                    f"Simulation acknowledgment status: assignment {a.id} ({a.responder_id} -> {a.casualty_id}) "
                    f"is {stage_explain}."
                )
                return GroundedAnswer(
                    intent="acknowledgment_status",
                    spoken_text=_truncate_for_speech(text, max_tts_chars),
                    display_text=text,
                    responder_id=a.responder_id,
                    casualty_id=a.casualty_id,
                    assignment_id=a.id,
                    salient_responder_ids=[a.responder_id],
                    salient_casualty_ids=[a.casualty_id],
                    trace_fields=[f"state.assignments[{a.id}].status"],
                )

        if cur_prop is not None:
            for pa in cur_prop.assignments:
                if (r_id and pa.responder_id == r_id) or (c_id and pa.casualty_id == c_id):
                    stage_explain = (
                        "only proposed (pending manual operator approval) and has NOT been dispatched or delivered"
                        if cur_prop.status == "pending_approval"
                        else "approved by an operator, but not yet dispatched or delivered to the responder"
                    )
                    text = (
                        f"Simulation acknowledgment status: {pa.responder_id} -> {pa.casualty_id} in proposal {cur_prop.id} "
                        f"is {stage_explain}."
                    )
                    return GroundedAnswer(
                        intent="acknowledgment_status",
                        spoken_text=_truncate_for_speech(text, max_tts_chars),
                        display_text=text,
                        responder_id=pa.responder_id,
                        casualty_id=pa.casualty_id,
                        proposal_id=cur_prop.id,
                        salient_responder_ids=[pa.responder_id],
                        salient_casualty_ids=[pa.casualty_id],
                        trace_fields=[
                            f"state.proposals[{cur_prop.id}].status",
                            f"state.proposals[{cur_prop.id}].assignments",
                        ],
                    )

        target_label = r_id or c_id
        text = (
            f"Simulation acknowledgment status: {target_label} has no proposed or dispatched assignments."
        )
        return GroundedAnswer(
            intent="acknowledgment_status",
            spoken_text=_truncate_for_speech(text, max_tts_chars),
            display_text=text,
            responder_id=r_id,
            casualty_id=c_id,
            trace_fields=["state.assignments", "state.proposals"],
        )

    # Scene-wide lifecycle breakdown across all 6 stages
    dispatched_only = [a for a in active if a.status == "dispatched"]
    delivered_only = [a for a in active if a.status == "delivered"]
    acknowledged_only = [a for a in active if a.status == "acknowledged"]
    unacked = dispatched_only + delivered_only

    parts = []
    salient_res: list[str] = []
    if unacked:
        items = [f"{a.responder_id} -> {a.casualty_id} ({a.status})" for a in unacked]
        salient_res = [a.responder_id for a in unacked]
        parts.append(
            f"Simulation acknowledgment status: {len(unacked)} dispatched assignment(s) awaiting acknowledgment: "
            + ", ".join(items)
            + "."
        )
    elif active:
        parts.append(
            "Simulation acknowledgment status: all active dispatched assignments have been acknowledged."
        )
    else:
        parts.append(
            "Simulation acknowledgment status: there are no active dispatched assignments."
        )

    if acknowledged_only:
        items = [f"{a.responder_id} -> {a.casualty_id}" for a in acknowledged_only]
        parts.append(f"Acknowledged ({len(acknowledged_only)}): {', '.join(items)}.")
    if completed:
        items = [f"{a.responder_id} -> {a.casualty_id}" for a in completed]
        parts.append(f"Completed ({len(completed)}): {', '.join(items)}.")

    if cur_prop is not None:
        if not salient_res:
            salient_res = [a.responder_id for a in cur_prop.assignments]
        prop_items = [f"{a.responder_id} -> {a.casualty_id}" for a in cur_prop.assignments]
        if cur_prop.status == "pending_approval":
            parts.append(
                f"Proposed (pending operator approval, not yet dispatched): {', '.join(prop_items)}."
            )
        elif cur_prop.status == "approved":
            parts.append(
                f"Approved (awaiting dispatch execution, not yet delivered): {', '.join(prop_items)}."
            )

    full_text = " ".join(parts)
    return GroundedAnswer(
        intent="acknowledgment_status",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        proposal_id=cur_prop.id if cur_prop else None,
        salient_responder_ids=salient_res,
        trace_fields=["state.assignments", "state.proposals"],
    )


def _handle_casualty_status(
    parsed: ParsedIntent,
    state: SimulationState,
    max_tts_chars: int,
) -> GroundedAnswer:
    cid = parsed.casualty_id
    assert cid is not None
    c = _find_casualty(state, cid)
    if c is None:
        return _handle_unknown_entity(
            ParsedIntent(
                intent="unknown_entity",
                normalized_query=parsed.normalized_query,
                unknown_entity_kind="casualty",
                unknown_entity_label=cid,
            ),
            state,
            max_tts_chars,
        )

    assess_str = (
        f"human-assessed {c.medical_assessment.severity} by {c.medical_assessment.assessed_by}"
        if c.medical_assessment is not None
        else "unassessed (receives protected immediate allocation parity; no medical severity is inferred)"
    )
    track = _find_track_for_casualty(state, c.id)
    if track is not None and track.latest_observation.track is not None:
        t_state = track.latest_observation.track.state
        conf = track.latest_observation.confidence
        track_str = f"linked to camera track {track.track_id} (state: {t_state}, detector confidence: {conf:.2f})"
        if track.latest_visible_signs is not None:
            posture = track.latest_visible_signs.posture.value
            movement = track.latest_visible_signs.movement.value
            track_str += (
                f"; advisory visual signs report posture={posture}, movement={movement} "
                "(unverified visual evidence only, not a clinical diagnosis)"
            )
    else:
        track_str = "no linked camera track (using human-known scenario position)"

    active = _active_assignments(state)
    cur_prop = _current_proposal(state)
    act_match = next((a for a in active if a.casualty_id == c.id), None)
    prop_match = (
        next((a for a in cur_prop.assignments if a.casualty_id == c.id), None)
        if cur_prop is not None
        else None
    )

    if c.id in state.resolved_casualty_ids:
        cov_str = "marked resolved after assignment completion."
    elif act_match is not None:
        cov_str = f"covered by active dispatched assignment {act_match.id} with {act_match.responder_id} ({act_match.status})."
    elif prop_match is not None and cur_prop is not None:
        cov_str = (
            f"recommended for {prop_match.responder_id} in proposal {cur_prop.id} ({cur_prop.status})."
        )
    else:
        reasons = (
            cur_prop.reasons.get(c.id, [])
            if cur_prop is not None
            else (
                state.last_recommendation.reasons.get(c.id, [])
                if state.last_recommendation
                else []
            )
        )
        cov_str = f"currently unassigned ({_humanize_reasons(reasons)})."

    full_text = (
        f"Simulation casualty {c.id} at simulated coordinates ({c.position.x_m:.1f}, {c.position.y_m:.1f})m: "
        f"{assess_str}; {track_str}; {cov_str}"
    )
    return GroundedAnswer(
        intent="casualty_status",
        spoken_text=_truncate_for_speech(full_text, max_tts_chars),
        display_text=full_text,
        casualty_id=c.id,
        responder_id=(
            act_match.responder_id
            if act_match
            else (prop_match.responder_id if prop_match else None)
        ),
        salient_casualty_ids=[c.id],
        trace_fields=[
            f"state.scenario.casualties[{c.id}]",
            "state.tracks",
            "state.assignments",
            "state.proposals",
        ],
    )
