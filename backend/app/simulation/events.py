"""Per-scenario bounded event log; callers hold the owning session lock."""

from collections import deque
from datetime import datetime
from typing import Any

from backend.app.simulation.models import EventEnvelope

EVENT_TYPES = frozenset({
    "simulation.reset",
    "simulation.snapshot",
    "simulation.observation_received",
    "simulation.camera_registered",
    "simulation.track_associated",
    "simulation.casualty_created",
    "simulation.assessment_recorded",
    "simulation.responder_updated",
    "simulation.map_updated",
    "simulation.capabilities_updated",
    "simulation.replan_requested",
    "simulation.recommendation_computed",
    "simulation.proposal_created",
    "simulation.proposal_superseded",
    "simulation.proposal_expired",
    "simulation.decision_recorded",
    "simulation.approval_invalidated",
    "simulation.dispatch_created",
    "simulation.device_delivered",
    "simulation.device_acknowledged",
    "simulation.device_completed",
    "simulation.responder_released",
    "simulation.intelligence_policy_updated",
    "simulation.route_invalidated",
    "simulation.route_revalidated",
})


class EventLog:
    """Sequences survive session resets so subscribers never see regression."""

    def __init__(self, scenario_id: str, nonce: str, max_events: int):
        self.scenario_id = scenario_id
        self.nonce = nonce
        self.sequence = 0
        self._events: deque[EventEnvelope] = deque(maxlen=max_events)

    def append(self, event_type: str, payload: dict[str, Any], occurred_at: datetime) -> EventEnvelope:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown simulation event type {event_type}")
        self.sequence += 1
        event = EventEnvelope(
            event_id=f"sim-{self.scenario_id}-{self.nonce}-{self.sequence:08d}",
            type=event_type, scenario_id=self.scenario_id, occurred_at=occurred_at,
            sequence=self.sequence, payload=payload,
        )
        self._events.append(event)
        return event

    @property
    def oldest_sequence(self) -> int | None:
        return self._events[0].sequence if self._events else None

    def after(self, sequence: int, limit: int) -> tuple[list[EventEnvelope], bool]:
        """Events after ``sequence`` and whether retained history has a gap."""
        oldest = self.oldest_sequence
        gap = oldest is not None and sequence + 1 < oldest and sequence < self.sequence
        events = [event for event in self._events if event.sequence > sequence][:limit]
        return [event.model_copy(deep=True) for event in events], gap
