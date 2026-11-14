"""Responder view: a clean video with numbered people and one signs card per person.

The video shows each tracked person with a numbered tag in their own color and,
where blood is seen, the body region marked. The side panel has a card per
person with the same number and color. Rows follow the order responders check
(blood, breathing, response, eyes, movement):

- amber   an observation to look at (blood seen, no chest motion, no response, eyes closed)
- teal    observed, nothing flagged
- grey    the camera cannot tell, with the reason ("face not visible", "camera moving")
- cyan    listening for a response after a command, with the time left

Colors mark observations, not severity: nothing here is a diagnosis or a
priority, and there is no heart rate (the camera cannot measure it). Text is
ASCII because OpenCV's built-in fonts have no other characters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from cv.signs.body import FACE, LIMB_CHAINS, TORSO, body_scale

VIEW_H = 720
MAX_VIDEO_W = 1280          # wider frames are letterboxed so the panel stays readable
PANEL_W = 430
TOP_BAR = 32
HIDE_UNSEEN_AFTER_S = 5.0   # cards of people out of view this long leave the panel
MAX_FULL_CARDS = 4
MAX_COMPACT_CARDS = 10
FONT = cv2.FONT_HERSHEY_DUPLEX
MIN_SHRINK = 0.75
LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX

PANEL_BG = (22, 20, 18)
CARD_BG = (40, 36, 33)
WHITE = (240, 240, 240)
LABEL = (150, 145, 140)
GREY = (125, 122, 118)
AMBER = (0, 176, 255)
TEAL = (170, 205, 60)
CYAN = (235, 205, 60)
STATE_COLORS = {"attention": AMBER, "observed": TEAL, "unknown": GREY, "active": CYAN}
# Identity colors (BGR); none is amber, so a person's color never reads as a finding.
PALETTE = ((255, 163, 77), (255, 140, 181), (96, 224, 168), (192, 138, 255), (232, 216, 64), (210, 210, 210))
REGION_NAMES = {"head": "head", "torso": "torso", "left_arm": "L arm", "right_arm": "R arm",
                "left_leg": "L leg", "right_leg": "R leg"}
COMPACT_TEXT = {"BLOOD": "blood seen", "BREATHING": "no chest motion", "RESPONSE": "no response",
                "EYES": "eyes closed"}
FOOTER = ("Amber: notable observation.  Grey: camera cannot tell.  Bar: confidence.",
          "Camera observations, not a diagnosis. The medic decides.")


@dataclass(frozen=True)
class Row:
    label: str
    text: str
    state: str                        # attention / observed / unknown / active
    confidence: float | None = None
    progress: float | None = None     # 0..1, listening countdown


@dataclass(frozen=True)
class Card:
    track_id: str
    number: int
    color: tuple[int, int, int]
    title: str
    location: str
    in_view: bool
    rows: tuple[Row, ...]
    trace: np.ndarray | None = None
    blood_regions: tuple[str, ...] = ()


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02d} s"


def _because(sign: dict, otherwise: str) -> str:
    return "camera moving" if sign.get("reason") == "camera_moving" else otherwise


def rows_for(signs: dict, context: dict | None = None) -> tuple[Row, ...]:
    """The card rows for one signs snapshot; ``context`` is SignsEstimator.display_context."""
    context = context or {}
    rows = []

    blood = signs.get("visible_blood")
    if blood is not None:
        if blood["value"] == "detected":
            rows.append(Row("BLOOD", "seen: " + ", ".join(REGION_NAMES.get(r, r) for r in blood["regions"]),
                            "attention", blood["confidence"]))
        elif blood["value"] == "not_detected":
            rows.append(Row("BLOOD", "none seen", "observed", blood["confidence"]))
        else:
            rows.append(Row("BLOOD", "body not visible", "unknown"))

    breathing = signs.get("breathing")
    if breathing is not None:
        if breathing["value"] == "motion_detected":
            rows.append(Row("BREATHING", f"{breathing['rate_bpm']:.0f} /min", "observed", breathing["confidence"]))
        elif breathing["value"] == "no_motion_detected":
            rows.append(Row("BREATHING", "no chest motion seen", "attention", breathing["confidence"]))
        elif breathing.get("reason") == "camera_moving":
            rows.append(Row("BREATHING", "camera must be still", "unknown"))
        elif signs["movement"]["value"] != "still":
            rows.append(Row("BREATHING", "person must be still", "unknown"))
        elif "breathing_remaining_s" not in context:
            rows.append(Row("BREATHING", "chest not visible", "unknown"))
        elif context["breathing_remaining_s"] > 0:
            rows.append(Row("BREATHING", f"measuring, {context['breathing_remaining_s']:.0f} s", "unknown"))
        else:
            rows.append(Row("BREATHING", "no clear rhythm", "unknown"))

    response = signs["response"]
    if context.get("listening_left_s") is not None:
        left = context["listening_left_s"]
        rows.append(Row("RESPONSE", f"listening, {left:.0f} s", "active",
                        progress=left / max(1e-6, context.get("command_window_s", 8.0))))
    elif response["value"] == "responded":
        rows.append(Row("RESPONSE", "responded", "observed", response["confidence"]))
    elif response["value"] == "no_response":
        rows.append(Row("RESPONSE", "no response", "attention", response["confidence"]))
    else:
        rows.append(Row("RESPONSE", _because(response, "press C after a command"), "unknown"))

    eyes = signs.get("eyes")
    if eyes is not None:
        if eyes["value"] == "closed":
            rows.append(Row("EYES", "closed", "attention", eyes["confidence"]))
        elif eyes["value"] == "open":
            rows.append(Row("EYES", "open", "observed", eyes["confidence"]))
        else:
            rows.append(Row("EYES", "face not visible", "unknown"))

    movement = signs["movement"]
    if movement["value"] == "still":
        rows.append(Row("MOVEMENT", f"still {_duration(movement['still_for_s'])}", "observed",
                        movement["confidence"]))
    elif movement["value"] == "moving":
        rows.append(Row("MOVEMENT", "moving", "observed", movement["confidence"]))
    else:
        rows.append(Row("MOVEMENT", _because(movement, "measuring"), "unknown"))
    return tuple(rows)


def track_number(track) -> int:
    digits = "".join(ch for ch in track.short_id if ch.isdigit())
    return int(digits) if digits else 0


def identity_color(number: int) -> tuple[int, int, int]:
    return PALETTE[(number - 1) % len(PALETTE)]


def build_cards(tracks: Sequence, signs, now: float) -> tuple[list[Card], int]:
    """(cards, people hidden): one card per person in view or just out of it.

    Cards are in ID order so they never jump around. People unseen for more than
    HIDE_UNSEEN_AFTER_S are counted instead of shown.
    """
    cards, hidden = [], 0
    for track in sorted(tracks, key=track_number):
        snapshot = signs.snapshot(track, now)
        if snapshot is None:
            continue
        if not track.detected and now - track.last_seen > HIDE_UNSEEN_AFTER_S:
            hidden += 1
            continue
        context = signs.display_context(track.track_id, now)
        if track.zone_id is not None:
            location = str(track.zone_id)
        elif track.published_xy:
            location = f"{track.published_xy[0]:.1f}, {track.published_xy[1]:.1f} m"
        else:
            location = ""
        posture = snapshot["posture"]["value"]
        blood = snapshot.get("visible_blood", {})
        number = track_number(track)
        cards.append(Card(
            track.track_id, number, identity_color(number),
            "POSTURE UNKNOWN" if posture == "unknown" else posture.upper(), location, track.detected,
            rows_for(snapshot, context),
            trace=context.get("breathing_trace") if snapshot.get("breathing", {}).get("reason") is None else None,
            blood_regions=tuple(blood.get("regions", ())) if blood.get("value") == "detected" else ()))
    return cards, hidden


def _put(image, text: str, x: int, y: int, scale: float, color, font=FONT, max_width: int | None = None,
         align_right: bool = False) -> int:
    """Draw text (shrunk to ``max_width``); returns its width."""
    width = cv2.getTextSize(text, font, scale, 1)[0][0]
    if max_width is not None and width > max_width > 0:
        scale = max(scale * max_width / width, scale * MIN_SHRINK)   # never shrink below legible
        while len(text) > 4 and cv2.getTextSize(text, font, scale, 1)[0][0] > max_width:
            text = text[:-4].rstrip(" |,") + "..."
        width = cv2.getTextSize(text, font, scale, 1)[0][0]
    cv2.putText(image, text, (x - width if align_right else x, y), font, scale, color, 1, cv2.LINE_AA)
    return width


def _dim(color, factor: float = 0.55):
    return tuple(int(c * factor) for c in color)


def _draw_trace(image, trace: np.ndarray, x: int, y: int, width: int, height: int, color) -> None:
    span = float(np.ptp(trace))
    if span <= 0:
        cv2.line(image, (x, y + height // 2), (x + width, y + height // 2), color, 1, cv2.LINE_AA)
        return
    xs = np.linspace(x, x + width, len(trace))
    ys = y + height - (trace - trace.min()) / span * height
    cv2.polylines(image, [np.column_stack([xs, ys]).astype(np.int32)], False, color, 1, cv2.LINE_AA)


def _draw_card(canvas, card: Card, x: int, y: int, width: int, height: int, compact: bool) -> None:
    cv2.rectangle(canvas, (x, y), (x + width, y + height), CARD_BG, -1)
    cv2.rectangle(canvas, (x, y), (x + 4, y + height), card.color, -1)
    fade = (lambda c: c) if card.in_view else _dim
    cv2.rectangle(canvas, (x + 14, y + 7), (x + 44, y + 31), fade(card.color), -1)
    _put(canvas, str(card.number), x + 29 + cv2.getTextSize(str(card.number), FONT, 0.6, 1)[0][0] // 2,
         y + 26, 0.6, (20, 20, 20), align_right=True)
    side = card.location if card.in_view else "NOT IN VIEW"
    side_width = _put(canvas, side, x + width - 12, y + 25, 0.45, LABEL, LABEL_FONT, align_right=True)
    _put(canvas, card.title, x + 54, y + 27, 0.65, fade(WHITE), max_width=width - 54 - side_width - 24)
    if compact:
        flagged = [COMPACT_TEXT.get(row.label) or row.text for row in card.rows if row.state == "attention"]
        unknown = sum(row.state == "unknown" for row in card.rows)
        if flagged:
            _put(canvas, " | ".join(flagged), x + 14, y + 52, 0.45, fade(AMBER), max_width=width - 28)
        else:
            _put(canvas, f"nothing flagged, {unknown} of {len(card.rows)} signs not testable", x + 14, y + 52,
                 0.42, GREY, LABEL_FONT, max_width=width - 28)
        return
    for index, row in enumerate(card.rows):
        baseline = y + 54 + 24 * index
        color = fade(STATE_COLORS[row.state])
        cv2.circle(canvas, (x + 20, baseline - 5), 4, color, -1, cv2.LINE_AA)
        _put(canvas, row.label, x + 32, baseline, 0.4, LABEL, LABEL_FONT)
        right = x + width - 12
        if row.confidence is not None or row.progress is not None:
            fill = row.progress if row.progress is not None else row.confidence
            cv2.rectangle(canvas, (right - 44, baseline - 7), (right, baseline - 3), (70, 66, 62), -1)
            cv2.rectangle(canvas, (right - 44, baseline - 7),
                          (right - 44 + int(44 * min(1.0, max(0.0, fill))), baseline - 3), color, -1)
            right -= 54
        measured = row.state == "observed" or row.text.startswith("measuring")
        if row.label == "BREATHING" and card.trace is not None and card.in_view and measured:
            _draw_trace(canvas, card.trace, right - 96, baseline - 16, 96, 16, color)
            right -= 106
        text_color = fade(WHITE) if row.state == "observed" else color
        _put(canvas, row.text, x + 124, baseline, 0.55, text_color, max_width=right - (x + 124))


def _draw_panel(canvas, cards: Sequence[Card], x0: int, hidden: int = 0) -> None:
    canvas[:, x0:] = PANEL_BG
    x, width = x0 + 12, PANEL_W - 24
    for i, line in enumerate(FOOTER):
        _put(canvas, line, x, VIEW_H - 24 + 15 * i, 0.38, GREY, LABEL_FONT, max_width=width)
    if not cards:
        _put(canvas, "No people in view", x, 40, 0.6, GREY)
    compact = len(cards) > MAX_FULL_CARDS
    height, gap = (62, 6) if compact else (160, 7)
    shown = cards[:MAX_COMPACT_CARDS if compact else MAX_FULL_CARDS]
    for index, card in enumerate(shown):
        _draw_card(canvas, card, x, 10 + index * (height + gap), width, height, compact)
    notes = []
    if len(cards) > len(shown):
        notes.append(f"+{len(cards) - len(shown)} more in view")
    if hidden:
        notes.append(f"{hidden} tracked, not in view")
    if notes:
        _put(canvas, " | ".join(notes), x, VIEW_H - 38, 0.42, LABEL, LABEL_FONT)


def _draw_blood_regions(video, track, regions: Sequence[str], factor: float, min_conf: float) -> None:
    """Outline the body regions where blood is seen; the region itself stays visible."""
    if track.keypoints is None or track.keypoint_conf is None:
        return
    points = np.asarray(track.keypoints, dtype=float) * factor
    conf = track.keypoint_conf
    scale = body_scale(track.bbox, track.keypoints, conf, min_conf) * factor
    for region in regions:
        if region == "torso" and all(conf[i] >= min_conf for i in TORSO):
            cv2.polylines(video, [points[list(TORSO)].astype(np.int32)], True, AMBER, 3, cv2.LINE_AA)
        elif region == "head":
            face = [i for i in FACE if conf[i] >= min_conf * 0.6]
            if face:
                cv2.circle(video, tuple(int(v) for v in points[face].mean(axis=0)),
                           max(8, int(scale * 0.14)), AMBER, 3, cv2.LINE_AA)
        elif region in LIMB_CHAINS:
            joints = [i for i in LIMB_CHAINS[region] if conf[i] >= min_conf]
            half = max(4.0, scale * 0.06)
            for a, b in zip(joints, joints[1:]):
                along = points[b] - points[a]
                length = float(np.linalg.norm(along))
                if length < 1:
                    continue
                normal = np.array([-along[1], along[0]]) / length * half
                box = np.array([points[a] + normal, points[b] + normal, points[b] - normal, points[a] - normal])
                cv2.polylines(video, [box.astype(np.int32)], True, AMBER, 3, cv2.LINE_AA)


def _draw_person(video, track, card: Card, factor: float, min_top: int = 0) -> None:
    x1, y1, x2, y2 = (int(v * factor) for v in track.bbox)
    color = card.color if card.in_view else _dim(card.color)
    arm = max(8, min(26, (x2 - x1) // 4, (y2 - y1) // 4))
    for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(video, (cx, cy), (cx + dx * arm, cy), color, 2, cv2.LINE_AA)
        cv2.line(video, (cx, cy), (cx, cy + dy * arm), color, 2, cv2.LINE_AA)
    label = str(card.number)
    width = cv2.getTextSize(label, FONT, 0.7, 1)[0][0] + 16
    # Keep the tag inside the picture and clear of the top bar.
    left = min(max(0, x1), max(0, video.shape[1] - width))
    top = min(max(min_top, y1 - 30), max(0, video.shape[0] - 26))
    cv2.rectangle(video, (left, top), (left + width, top + 26), color, -1)
    cv2.putText(video, label, (left + 8, top + 20), FONT, 0.7, (20, 20, 20), 1, cv2.LINE_AA)


def render(image, tracks: Sequence, cards: Sequence[Card], status: Sequence[str] = (),
           warnings: Sequence[str] = (), keys: str = "", keypoint_min_conf: float = 0.5,
           hidden: int = 0) -> np.ndarray:
    """The responder view: the frame fitted to VIEW_H x MAX_VIDEO_W with the cards panel on its right."""
    height, width = image.shape[:2]
    factor = min(VIEW_H / height, MAX_VIDEO_W / width)
    video_w, video_h = max(2, round(width * factor)), max(2, min(VIEW_H, round(height * factor)))
    canvas = np.zeros((VIEW_H, video_w + PANEL_W, 3), dtype=np.uint8)
    top = (VIEW_H - video_h) // 2
    picture = canvas[top:top + video_h, :video_w]
    picture[:] = cv2.resize(image, (video_w, video_h),
                            interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR)
    by_id = {card.track_id: card for card in cards}
    for track in tracks:
        card = by_id.get(track.track_id)
        if card is None:
            continue
        if card.blood_regions and card.in_view:
            _draw_blood_regions(picture, track, card.blood_regions, factor, keypoint_min_conf)
        _draw_person(picture, track, card, factor, max(0, TOP_BAR + 4 - top))
    video = canvas[:, :video_w]
    video[:TOP_BAR] = (video[:TOP_BAR] * 0.35).astype(np.uint8)
    x = 12 + _put(video, "AEGIS", 12, 22, 0.6, WHITE) + 14
    for item in status:
        x += _put(video, item, x, 22, 0.45, LABEL, LABEL_FONT) + 18
    if keys:
        _put(video, keys, video_w - 12, 22, 0.42, LABEL, LABEL_FONT, max_width=max(0, video_w - x - 24),
             align_right=True)
    for index, line in enumerate(warnings):
        top = VIEW_H - 30 * (index + 1)
        video[top:top + 26] = (video[top:top + 26] * 0.35).astype(np.uint8)
        _put(video, line, 12, top + 19, 0.5, AMBER, LABEL_FONT, max_width=video_w - 24)
    _draw_panel(canvas, cards, video_w, hidden)
    return canvas
