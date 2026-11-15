"""What the operator decided about each person this camera sees, read back from the backend.

The camera never sets a priority. After the operator links a track to a casualty
and assesses it, ``GET /state`` holds the human priority (``source: human``) and,
once planned and dispatched, the responder assigned to it. The responder view
shows that on the person's card so everyone in the room can see who is being
helped first.

    marks = OperatorMarks(base_url, "demo", "room-cam-1"); marks.start()
    marks.get()        # {local track ID: Mark}
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

PRIORITIES = ("immediate", "delayed", "minor", "expectant", "deceased")
STALE_AFTER_S = 10.0          # no fresh state this long: show nothing rather than an old decision


@dataclass(frozen=True)
class Mark:
    casualty_id: str
    priority: str | None = None       # human severity; None until assessed
    responder: str | None = None      # display name of the proposed or dispatched responder
    status: str | None = None         # proposed / dispatched / delivered / acknowledged / completed

    @property
    def waiting(self) -> bool:
        """Help sent but not yet confirmed by the responder: the card blinks."""
        return self.status in ("dispatched", "delivered")


def marks_from_state(state: dict, camera_id: str) -> dict[str, Mark]:
    """{local track ID: Mark} for this camera's tracks linked to a casualty."""
    scenario = state.get("scenario") or {}
    priority = {}
    for casualty in scenario.get("casualties") or ():
        assessment = casualty.get("medical_assessment") or {}
        if assessment.get("source") == "human" and assessment.get("severity") in PRIORITIES:
            priority[casualty["id"]] = assessment["severity"]
    names = {r["id"]: r.get("display_name") or r["id"] for r in scenario.get("responders") or ()}

    task: dict[str, tuple[str, str]] = {}
    current = state.get("current_proposal_id")
    for proposal in state.get("proposals") or ():
        if proposal.get("id") == current and proposal.get("status") in ("pending_approval", "approved"):
            for assignment in proposal.get("assignments") or ():
                task[assignment["casualty_id"]] = (assignment["responder_id"], "proposed")
    for assignment in sorted(state.get("assignments") or (), key=lambda a: a.get("created_at") or ""):
        task[assignment["casualty_id"]] = (assignment["responder_id"], assignment["status"])

    marks = {}
    for track in state.get("tracks") or ():
        camera, casualty = track.get("camera") or {}, track.get("casualty_id")
        if casualty is None or camera.get("camera_id") != camera_id:
            continue
        responder, status = task.get(casualty, (None, None))
        marks[camera["local_track_id"]] = Mark(casualty, priority.get(casualty),
                                               names.get(responder, responder), status)
    return marks


class OperatorMarks:
    """Polls ``GET /state`` in a background thread; ``get()`` never blocks."""

    def __init__(self, base_url: str, scenario_id: str, camera_id: str, poll_s: float = 1.0,
                 timeout_s: float = 2.0):
        import requests

        self.url = f"{base_url.rstrip('/')}/api/v1/simulations/{scenario_id}/state"
        self.camera_id = camera_id
        self.poll_s, self.timeout_s = poll_s, timeout_s
        self._session = requests.Session()
        self._marks: dict[str, Mark] = {}
        self._fresh_t = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="operator-marks", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.timeout_s + 1.0)

    def get(self) -> dict[str, Mark]:
        if time.monotonic() - self._fresh_t > STALE_AFTER_S:
            return {}
        return self._marks

    def poll(self) -> None:
        try:
            response = self._session.get(self.url, timeout=self.timeout_s)
            response.raise_for_status()
            self._marks = marks_from_state(response.json(), self.camera_id)
            self._fresh_t = time.monotonic()
        except Exception as error:          # the view must keep running whatever the backend does
            log.debug("Operator state not read: %s", error)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.poll_s)
