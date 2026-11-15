"""Per-person track state on top of ByteTrack IDs.

Responsibilities:
- Canonical track IDs ``{session}-t{n}`` that are never reused, even when
  ByteTrack hands out a new raw ID for the same person after an occlusion
  (optional spatial re-attach to a recently occluded track).
- Floor position: ground point -> homography -> EMA -> deadband, so the
  published position only changes when the person actually moved.
- Zones mode (no homography): the ground point picks a predefined zone, which
  only changes after ``zone_switch_frames`` consecutive frames in the new zone.
  The published position is then the zone's reference point, not a measurement.
  With a current zone set (``set_current_zone``: the room a bodycam wearer is
  in, picked by key), every detected person is in that zone.
- Explicit uncertainty: ``detected`` (seen this frame), ``state``
  (visible / occluded / lost, debounced by ``occlusion_grace_s``), ``stale``,
  and ``mapping_valid``. Nothing is dropped silently.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from cv.detector import Detection
from cv.homography import Calibration, ground_point
from cv.observations import UNKNOWN_POSTURE, PostureEstimate, classify_posture, vote_posture
from cv.settings import TrackingSettings
from cv.zones import ZoneMap


@dataclass
class Track:
    track_id: str
    first_seen: float
    last_seen: float
    raw_id: int | None = None
    frames_seen: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    confidence_smoothed: float = 0.0
    posture: PostureEstimate = UNKNOWN_POSTURE
    posture_history: deque = field(default_factory=deque)
    ground_px: tuple[float, float] | None = None
    ground_method: str = ""
    map_xy: tuple[float, float] | None = None        # latest raw mapping of this frame
    smoothed_xy: tuple[float, float] | None = None
    published_xy: tuple[float, float] | None = None  # stabilized (deadband) position
    zone_id: str | None = None                       # zones mode: published zone
    zone_candidate: str | None = None
    zone_count: int = 0
    mapping_valid: bool = False
    detected: bool = False                           # detected in the latest frame
    state: str = "visible"                           # visible / occluded / lost
    stale: bool = False
    last_capture: float = 0.0
    keypoints: object = None
    keypoint_conf: object = None
    reattached: int = 0

    @property
    def is_occluded(self) -> bool:
        """Not detected in the latest frame while the track is still alive."""
        return not self.detected and self.state != "lost"

    @property
    def short_id(self) -> str:
        return self.track_id.rsplit("-", 1)[-1]


@dataclass(frozen=True)
class TrackReport:
    """One publishable snapshot of a track (monotonic times)."""

    track_id: str
    state: str
    observed_t: float
    first_seen_t: float
    last_seen_t: float
    x_m: float
    y_m: float
    confidence: float
    capture_t: float
    zone_id: str | None = None
    signs: dict | None = None        # cv.signs snapshot; visible reports only


class TrackManager:
    def __init__(self, settings: TrackingSettings, session_id: str,
                 calibration: Calibration | None = None, zones: ZoneMap | None = None,
                 image_size: tuple[int, int] | None = None):
        self.settings = settings
        self.session_id = session_id
        self.calibration = calibration
        self.zones = zones
        self.image_size = image_size
        self.current_zone: str | None = None
        self.tracks: dict[str, Track] = {}
        self._raw_to_track: dict[int, str] = {}
        self._retired_unreported: list[Track] = []
        self._counter = 0
        self.untracked = 0          # detections without a tracker ID in the last frame
        self.retired_total = 0

    @property
    def created_total(self) -> int:
        return self._counter

    def set_calibration(self, calibration: Calibration | None) -> None:
        self.calibration = calibration

    def set_zones(self, zones: ZoneMap | None, image_size: tuple[int, int] | None) -> None:
        self.zones, self.image_size = zones, image_size

    def set_current_zone(self, zone_id: str | None) -> None:
        if zone_id is not None and (self.zones is None
                                    or zone_id not in {z.zone_id for z in self.zones.zones}):
            raise ValueError(f"Unknown zone {zone_id!r}")
        self.current_zone = zone_id

    def _to_floor(self, point) -> tuple[float, float] | None:
        return self.calibration.point_to_world(point) if self.calibration else None

    def _measure(self, detection: Detection, posture: PostureEstimate):
        ground, method = ground_point(detection.bbox, posture.label, detection.keypoints,
                                      detection.keypoint_conf, self.settings.keypoint_min_conf)
        return ground, method, self._to_floor(ground)

    def _classify(self, detection: Detection) -> PostureEstimate:
        return classify_posture(
            detection.bbox, detection.keypoints, detection.keypoint_conf,
            min_conf=self.settings.keypoint_min_conf,
            to_floor=self._to_floor if self.calibration else None,
            lying_max_torso_m=self.settings.lying_max_torso_m,
        )

    def update(self, detections: list[Detection], t: float) -> list[Track]:
        """Apply one frame's detections (possibly none) captured at monotonic ``t``."""
        best: dict[int, Detection] = {}
        untracked = 0
        for detection in detections:
            if detection.raw_id is None:
                untracked += 1
            elif detection.raw_id not in best or detection.confidence > best[detection.raw_id].confidence:
                best[detection.raw_id] = detection  # duplicate raw IDs: keep the strongest
        self.untracked = untracked
        for track in self.tracks.values():
            track.detected = False

        new: list[Detection] = []
        for raw_id in sorted(best):
            track_id = self._raw_to_track.get(raw_id)
            if track_id in self.tracks:
                self._apply(self.tracks[track_id], best[raw_id], t)
            else:
                new.append(best[raw_id])

        for detection in new:
            posture = self._classify(detection)
            ground, _, xy = self._measure(detection, posture)
            target = self._reattach_candidate(xy, ground) if self.settings.reattach else None
            if target is None:
                target = self._create(t)
            else:
                target.reattached += 1
            for raw, track_id in list(self._raw_to_track.items()):
                if track_id == target.track_id:
                    del self._raw_to_track[raw]
            self._raw_to_track[detection.raw_id] = target.track_id
            self._apply(target, detection, t, posture)

        self._refresh(t)
        return list(self.tracks.values())

    def _create(self, t: float) -> Track:
        self._counter += 1
        track = Track(track_id=f"{self.session_id}-t{self._counter}", first_seen=t, last_seen=t)
        track.posture_history = deque(maxlen=max(1, self.settings.posture_window))
        self.tracks[track.track_id] = track
        return track

    def _reattach_candidate(self, xy, ground_px) -> Track | None:
        """Nearest unseen live track: floor distance when calibrated, else image distance.

        In the image the radius grows with the person's size, so someone who falls
        (the ground point jumps from the feet to the middle of a lying body, and
        the detector often starts a new ID) stays the same person.
        """
        settings = self.settings
        if self.zones is None and self.calibration is not None:
            if xy is None:
                return None
            point, radius = xy, (lambda track: settings.reattach_radius_m)

            def anchor(track):
                return track.smoothed_xy
        else:
            if ground_px is None:
                return None
            frame = settings.reattach_radius_frac * math.hypot(*self.image_size) if self.image_size else 0.0
            point = ground_px

            def radius(track):
                x1, y1, x2, y2 = track.bbox
                return max(frame, settings.reattach_body_frac * max(x2 - x1, y2 - y1))

            def anchor(track):
                if self.zones is not None and track.zone_id is None:
                    return None
                return track.ground_px
        best, best_score = None, 1.0
        for track in self.tracks.values():
            if track.detected or anchor(track) is None:
                continue
            score = math.dist(point, anchor(track)) / max(radius(track), 1e-6)
            if score <= best_score:
                best, best_score = track, score
        return best

    def _apply(self, track: Track, detection: Detection, t: float,
               posture: PostureEstimate | None = None) -> None:
        settings = self.settings
        track.raw_id = detection.raw_id
        track.detected = True
        track.last_seen = t
        track.last_capture = t
        track.frames_seen += 1
        track.bbox = detection.bbox
        track.keypoints, track.keypoint_conf = detection.keypoints, detection.keypoint_conf
        track.confidence = detection.confidence
        track.confidence_smoothed = (
            detection.confidence if track.frames_seen == 1
            else settings.confidence_alpha * detection.confidence
            + (1 - settings.confidence_alpha) * track.confidence_smoothed
        )
        track.posture_history.append(posture or self._classify(detection))
        track.posture = vote_posture(track.posture_history)
        track.ground_px, track.ground_method, xy = self._measure(detection, track.posture)
        track.map_xy = xy
        if self.zones is not None:
            self._apply_zone(track)
            return
        track.mapping_valid = xy is not None
        if xy is None:
            return
        if track.smoothed_xy is None:
            track.smoothed_xy = xy
        else:
            alpha = settings.position_alpha
            track.smoothed_xy = (alpha * xy[0] + (1 - alpha) * track.smoothed_xy[0],
                                 alpha * xy[1] + (1 - alpha) * track.smoothed_xy[1])
        if track.published_xy is None or math.dist(track.smoothed_xy, track.published_xy) > settings.deadband_m:
            track.published_xy = (round(track.smoothed_xy[0], 2), round(track.smoothed_xy[1], 2))

    def _apply_zone(self, track: Track) -> None:
        if self.current_zone is not None:  # picked by the operator: no hysteresis
            zone = self.zones.get(self.current_zone)
            track.mapping_valid = True
            track.zone_id, track.published_xy = zone.zone_id, (zone.x_m, zone.y_m)
            track.zone_candidate, track.zone_count = None, 0
            return
        zone = self.zones.zone_at(track.ground_px, self.image_size)
        track.mapping_valid = zone is not None
        if zone is None or zone.zone_id == track.zone_id:
            track.zone_candidate, track.zone_count = None, 0
            return
        if zone.zone_id == track.zone_candidate:
            track.zone_count += 1
        else:
            track.zone_candidate, track.zone_count = zone.zone_id, 1
        if track.zone_id is None or track.zone_count >= self.settings.zone_switch_frames:
            track.zone_id = zone.zone_id
            track.published_xy = (zone.x_m, zone.y_m)
            track.zone_candidate, track.zone_count = None, 0

    def _refresh(self, t: float) -> None:
        settings = self.settings
        for track in list(self.tracks.values()):
            age = t - track.last_seen
            track.stale = age > settings.stale_after_s
            if age > settings.lost_after_s:
                track.state = "lost"
                self._retire(track)
            elif age > settings.occlusion_grace_s:
                track.state = "occluded"
            else:
                track.state = "visible"

    def _retire(self, track: Track) -> None:
        del self.tracks[track.track_id]
        for raw, track_id in list(self._raw_to_track.items()):
            if track_id == track.track_id:
                del self._raw_to_track[raw]
        self._retired_unreported.append(track)
        self.retired_total += 1

    def reports(self, t: float) -> list[TrackReport]:
        """Snapshots to publish at ``t``; each retired track is reported once as lost.

        Tracks that never got a valid floor position are not publishable (the
        contract requires a position) and are omitted rather than guessed.
        """
        retired, self._retired_unreported = self._retired_unreported, []
        reports = []
        for track in [*self.tracks.values(), *retired]:
            if track.published_xy is None:
                continue
            reports.append(TrackReport(
                track_id=track.track_id, state=track.state, observed_t=max(t, track.last_seen),
                first_seen_t=track.first_seen, last_seen_t=track.last_seen,
                x_m=track.published_xy[0], y_m=track.published_xy[1],
                confidence=round(min(1.0, max(0.0, track.confidence_smoothed)), 2),
                capture_t=track.last_capture, zone_id=track.zone_id,
            ))
        return reports
