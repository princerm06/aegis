"""Publishes track observations to the backend without blocking the video loop.

Wire format (see shared/CONTRACT.md and docs/INTEGRATION.md):
- ``endpoint: camera`` (default): ``PUT /api/v1/simulations/{scenario}/cameras/{camera_id}``
  once, then ``POST .../cameras/{camera_id}/observations`` with a
  ``CameraObservation``. With a homography the camera is ``calibrated_world``
  and reports a ``position``; with a zone map it is ``predefined_zones`` and
  reports a ``zone_id`` (the backend uses the zone's reference point).
- ``endpoint: legacy``: ``POST /api/v1/simulations/{scenario}/observations`` with a
  contract ``CVObservation``.

Behaviour:
- ``submit()`` only stores the newest report per track; a background thread
  sends. Under backpressure older reports are replaced, never queued forever,
  so an old observation is never sent after a newer one (the backend rejects that).
- Unchanged tracks are re-sent every ``heartbeat_s``; changes go out on the next tick.
- ``send_signs`` adds the ``signs`` object (cv/CONTRACT_PROPOSAL.md) to visible
  reports; a change of a sign's value counts as a change. ``send_history``
  (on by default) also sends the history and position signs (``HISTORY_SIGNS``),
  accepted since backend PR #12. ``DISPLAY_ONLY_SIGNS`` (the pulse estimate)
  are never sent, and breathing is left out while it is ``not_testable``.
- Occluded/lost reports repeat the position, confidence and ``last_seen_at`` of
  the last report the backend *accepted* for that track, as the contract
  requires. Tracks the backend never accepted get no occluded/lost report.
- Network errors and 5xx back off exponentially and retry the same report (same
  ID and body, so a retry after an unseen success is deduplicated server-side).
  404 on a camera observation means the camera registration is gone (scenario
  reset): re-register, then retry. Other 4xx are logged and dropped.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cv.clock import Timebase
from cv.settings import PublisherSettings
from cv.tracker import TrackReport
from cv.zones import ZoneMap

log = logging.getLogger(__name__)
MIN_CONFIDENCE_BUCKET = 0.5  # backend treats crossing this as an impactful change
HISTORY_SIGNS = ("fall", "walking", "limbs", "waving", "blood_spread", "changes", "face_down", "person_nearby")
DISPLAY_ONLY_SIGNS = ("pulse",)        # in no contract: never sent


class TransportError(Exception):
    """The request did not complete (connection refused, timeout, ...)."""


Transport = Callable[[str, str, dict], "tuple[int, Any]"]


class HttpTransport:
    def __init__(self, base_url: str, timeout_s: float):
        import requests

        self._requests = requests
        self._session = requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def __call__(self, method: str, path: str, body: dict) -> tuple[int, Any]:
        try:
            response = self._session.request(method, self.base_url + path, json=body,
                                             timeout=self.timeout_s)
        except self._requests.RequestException as error:
            raise TransportError(str(error)) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, data


class MockTransport:
    """Accepts everything and logs the exact request as one JSON line."""

    def __init__(self, path: str | Path | None, echo: bool = True):
        self.path = Path(path) if path else None
        self.echo = echo
        self.requests: list[dict] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, method: str, path: str, body: dict) -> tuple[int, Any]:
        record = {"method": method, "path": path, "body": body}
        self.requests.append(record)
        line = json.dumps(record, separators=(",", ":"))
        if self.echo:
            print(line, flush=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return (200 if method == "PUT" else 201), {"mock": True}


@dataclass
class _Outgoing:
    report: TrackReport
    observation_id: str


@dataclass
class PublisherStats:
    submitted: int = 0
    sent: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    skipped: int = 0
    network_errors: int = 0
    server_errors: int = 0
    registrations: int = 0
    last_error: str | None = None
    latencies_ms: list[float] = field(default_factory=list)


class Publisher:
    def __init__(self, settings: PublisherSettings, *, frame_id: str, calibration_ref: str | None,
                 session_id: str, timebase: Timebase, transport: Transport,
                 clock: Callable[[], float] | None = None, zones: ZoneMap | None = None):
        if settings.endpoint not in ("camera", "legacy"):
            raise ValueError("publisher.endpoint must be 'camera' or 'legacy'")
        if zones is not None and settings.endpoint != "camera":
            raise ValueError("zones mapping needs publisher.endpoint: camera")
        if zones is None and not calibration_ref:
            raise ValueError("a calibrated camera needs a calibration_ref")
        self.settings = settings
        self.frame_id = frame_id
        self.calibration_ref = calibration_ref
        self.zones = zones
        self.session_id = session_id
        self.timebase = timebase
        self.transport = transport
        self._clock = clock or timebase.now
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: dict[str, _Outgoing] = {}
        self._last_submitted: dict[str, tuple[tuple, float]] = {}
        self._accepted: dict[str, TrackReport] = {}
        self._counter = 0
        self._registered = settings.endpoint == "legacy"
        self._backoff_s = 0.0
        self._backoff_until = 0.0
        self._last_logged: dict[str, float] = {}
        self.stats = PublisherStats()

    # ------------------------------------------------------------------ paths
    @property
    def _base(self) -> str:
        return f"/api/v1/simulations/{self.settings.scenario_id}"

    @property
    def camera_path(self) -> str:
        return f"{self._base}/cameras/{self.settings.camera_id}"

    @property
    def observation_path(self) -> str:
        if self.settings.endpoint == "camera":
            return f"{self.camera_path}/observations"
        return f"{self._base}/observations"

    # ------------------------------------------------------------- producer
    def submit(self, reports: list[TrackReport], now: float | None = None) -> int:
        """Queue changed tracks and due heartbeats. Never blocks on the network."""
        now = self._clock() if now is None else now
        queued = 0
        with self._lock:
            for report in reports:
                key = (report.state, report.x_m, report.y_m, report.zone_id,
                       report.confidence >= MIN_CONFIDENCE_BUCKET, self._signs_key(report))
                previous = self._last_submitted.get(report.track_id)
                due = (previous is None or previous[0] != key or report.state == "lost"
                       or now - previous[1] >= self.settings.heartbeat_s)
                if not due:
                    continue
                self._counter += 1
                self._pending[report.track_id] = _Outgoing(
                    report, f"{self.session_id}-o{self._counter:06d}")
                self._last_submitted[report.track_id] = (key, now)
                if report.state == "lost":
                    self._last_submitted.pop(report.track_id, None)
                queued += 1
            self.stats.submitted += queued
        if queued:
            self._wake.set()
        return queued

    def _wire_signs(self, signs: dict) -> dict:
        dropped = DISPLAY_ONLY_SIGNS if self.settings.send_history else HISTORY_SIGNS + DISPLAY_ONLY_SIGNS
        if signs.get("breathing", {}).get("value") != "motion_detected":
            # Optional in the contract. No chest motion is not evidence of no breathing: on the
            # RespView clips it came as often while breathing (41-75%) as during held breath.
            dropped = (*dropped, "breathing")
        return {name: sign for name, sign in signs.items() if name not in dropped}

    def _signs_key(self, report: TrackReport) -> tuple:
        """Sign values (not confidences or timers): a change goes out on the next tick."""
        if not self.settings.send_signs or not report.signs:
            return ()
        signs = self._wire_signs(report.signs)
        values = [(name, sign.get("value")) for name, sign in signs.items() if isinstance(sign, dict)]
        if signs.get("changes"):
            values.append(("changes", signs["changes"][-1]["at"]))
        return tuple(sorted(values))

    # ------------------------------------------------------------- payloads
    def build_body(self, outgoing: _Outgoing) -> dict | None:
        report = outgoing.report
        if report.state == "visible":
            source = report
        else:
            source = self._accepted.get(report.track_id)
            if source is None:
                return None
        timebase = self.timebase
        if self.zones is not None:
            if source.zone_id is None:
                return None
            location = {"zone_id": source.zone_id}
        else:
            location = {"position": {"frame_id": self.frame_id, "x_m": source.x_m, "y_m": source.y_m}}
        track_id = report.track_id
        observation_id = outgoing.observation_id
        if self.settings.endpoint == "legacy":
            track_id = f"{self.settings.camera_id}-{track_id}"
            observation_id = f"{self.settings.camera_id}-{observation_id}"
        track = {
            "track_id": track_id,
            "state": report.state,
            "first_seen_at": timebase.iso(report.first_seen_t),
            "last_seen_at": timebase.iso(source.last_seen_t),
        }
        common = {"id": observation_id, "observed_at": timebase.iso(report.observed_t),
                  "confidence": source.confidence, "track": track, **location}
        if self.settings.send_signs and report.state == "visible" and report.signs:
            common["signs"] = self._wire_signs(report.signs)
        if self.settings.endpoint == "camera":
            return {"mode": "simulation", **common}
        return {"source": "cv", "kind": "person_detected", **common}

    def registration_body(self) -> dict:
        body = {"mode": "simulation", "actor": self.settings.actor}
        if self.zones is not None:
            return {**body, "mapping_mode": "predefined_zones", "zones": self.zones.registration()}
        return {**body, "mapping_mode": "calibrated_world", "calibration_ref": self.calibration_ref}

    @property
    def mapping_label(self) -> str:
        if self.zones is not None:
            return "predefined_zones " + ",".join(zone.zone_id for zone in self.zones.zones)
        return f"calibrated_world {self.calibration_ref}"

    # ------------------------------------------------------------- consumer
    def step(self) -> None:
        """One send cycle; the background thread calls this repeatedly."""
        if self._clock() < self._backoff_until:
            return
        with self._lock:
            batch = list(self._pending.values())
            self._pending.clear()
        if not batch:
            return
        if not self._registered and not self._register():
            self._requeue(batch)
            return
        for index, outgoing in enumerate(batch):
            body = self.build_body(outgoing)
            if body is None:
                self.stats.skipped += 1
                continue
            try:
                status, data = self.transport("POST", self.observation_path, body)
            except TransportError as error:
                self._network_failure(str(error))
                self._requeue(batch[index:])
                return
            self.stats.sent += 1
            if 200 <= status < 300:
                self._on_accepted(outgoing, data)
            elif status == 404 and self.settings.endpoint == "camera":
                self._registered = False
                self._log_once("unregistered", "Camera registration missing (scenario reset?); re-registering")
                self._requeue(batch[index:])
                return
            elif status >= 500:
                self.stats.server_errors += 1
                self._backoff(f"HTTP {status}: {_detail(data)}")
                self._requeue(batch[index:])
                return
            else:
                self.stats.rejected += 1
                self.stats.last_error = f"HTTP {status}: {_detail(data)}"
                self._log_once(f"reject-{status}", "Observation rejected: %s", self.stats.last_error)

    def _on_accepted(self, outgoing: _Outgoing, data: Any) -> None:
        report = outgoing.report
        self.stats.accepted += 1
        if isinstance(data, dict) and data.get("duplicate"):
            self.stats.duplicates += 1
        if report.state == "visible":
            self._accepted[report.track_id] = report
        elif report.state == "lost":
            self._accepted.pop(report.track_id, None)
        if report.state == "visible":  # occluded/lost carry the last capture, not a new one
            self.stats.latencies_ms.append((self._clock() - report.capture_t) * 1000.0)
            if len(self.stats.latencies_ms) > 2000:
                del self.stats.latencies_ms[:1000]
        self._backoff_s = 0.0

    def _register(self) -> bool:
        try:
            status, data = self.transport("PUT", self.camera_path, self.registration_body())
        except TransportError as error:
            self._network_failure(str(error))
            return False
        if 200 <= status < 300:
            self._registered = True
            self.stats.registrations += 1
            log.info("Registered camera %s (%s)", self.settings.camera_id, self.mapping_label)
            return True
        if status == 409:
            # Immutable for this epoch: someone registered a different mapping.
            self._registered = True
            self.stats.registrations += 1
            log.warning("Camera %s is already registered with a different mapping this epoch; "
                        "observations will use it. Reset the scenario to apply %s.",
                        self.settings.camera_id, self.mapping_label)
            return True
        self._backoff(f"Camera registration HTTP {status}: {_detail(data)}")
        return False

    def _requeue(self, items: list[_Outgoing]) -> None:
        with self._lock:
            for outgoing in items:
                self._pending.setdefault(outgoing.report.track_id, outgoing)

    def _network_failure(self, message: str) -> None:
        self.stats.network_errors += 1
        self._backoff(f"Backend unreachable: {message}")

    def _backoff(self, message: str) -> None:
        self.stats.last_error = message
        self._backoff_s = min(self.settings.max_backoff_s, max(0.25, self._backoff_s * 2))
        self._backoff_until = self._clock() + self._backoff_s
        self._log_once(message.split(":")[0], "%s (retry in %.1f s)", message, self._backoff_s)

    def _log_once(self, key: str, message: str, *args: Any, every_s: float = 5.0) -> None:
        now = self._clock()
        if now - self._last_logged.get(key, -1e9) >= every_s:
            self._last_logged[key] = now
            log.warning(message, *args)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="aegis-publisher", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=0.05)
            self._wake.clear()
            try:
                self.step()
            except Exception as error:  # the sender thread must survive anything
                log.exception("Publisher step failed: %s", error)
                time.sleep(0.5)

    def stop(self, flush_timeout_s: float = 2.0) -> None:
        deadline = self._clock() + flush_timeout_s
        while self.pending and self._clock() < deadline and self._thread is not None:
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def status(self) -> dict[str, Any]:
        latencies = sorted(self.stats.latencies_ms[-200:])
        return {
            "registered": self._registered,
            "pending": self.pending,
            "accepted": self.stats.accepted,
            "rejected": self.stats.rejected,
            "network_errors": self.stats.network_errors,
            "backing_off": self._clock() < self._backoff_until,
            "last_error": self.stats.last_error,
            "latency_ms_median": round(latencies[len(latencies) // 2], 1) if latencies else None,
        }


def _detail(data: Any) -> str:
    if isinstance(data, dict) and "detail" in data:
        return json.dumps(data["detail"])[:300]
    return json.dumps(data)[:300] if data is not None else "no body"
