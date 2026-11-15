"""Proposed backend models for the history signs (cv/CONTRACT_PROPOSAL.md, "History signs").

For Prince to copy into backend/app/models.py next to ``CameraSigns``; nothing
in the backend imports this file. cv/tests/test_history.py checks a real
snapshot against these models on top of the backend's current ones, so the
proposal and the CV output cannot drift apart. Until the backend has them, the
CV side keeps these keys off the wire (``publisher.send_history``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from backend.app.models import CameraSign, CameraSigns, ContractModel, SignDuration

LimbRegion = Literal["left_arm", "right_arm", "left_leg", "right_leg"]
ChangedSign = Literal["posture", "movement", "response", "eyes", "breathing", "visible_blood"]


class FallSign(CameraSign):
    value: Literal["seen", "not_seen", "not_testable"]
    at: AwareDatetime | None = None                     # when the drop started, only when seen


class WalkingSign(CameraSign):
    value: Literal["seen", "not_seen", "not_testable"]
    at: AwareDatetime | None = None                     # last time walking was seen


class WavingSign(CameraSign):
    value: Literal["seen", "not_seen", "not_testable"]
    at: AwareDatetime | None = None                     # last time waving was seen


class FaceDownSign(CameraSign):
    value: Literal["seen", "not_seen", "not_testable"]
    for_s: SignDuration | None = None                   # only when seen


class PersonNearbySign(CameraSign):
    value: Literal["seen", "not_seen", "not_testable"]  # not_testable unless lying or sitting
    for_s: SignDuration | None = None                   # only when seen


class LimbsSign(CameraSign):
    value: Literal["all_moved", "some_not_moved", "none_moved", "not_testable"]
    window_s: SignDuration
    not_moved: list[LimbRegion] | None = None           # only with some_not_moved


class BloodSpreadSign(CameraSign):
    value: Literal["growing", "stable", "shrinking", "not_testable"]
    window_s: SignDuration
    change_pct: int | None = None                       # red area now vs the start of the window


class SignChange(ContractModel):
    sign: ChangedSign
    from_value: str = Field(alias="from")
    to: str
    at: AwareDatetime


class CameraSignsWithHistory(CameraSigns):
    fall: FallSign | None = None
    walking: WalkingSign | None = None
    limbs: LimbsSign | None = None
    waving: WavingSign | None = None
    face_down: FaceDownSign | None = None
    person_nearby: PersonNearbySign | None = None
    blood_spread: BloodSpreadSign | None = None
    changes: list[SignChange] | None = Field(default=None, max_length=5)
