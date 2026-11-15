"""Responder view: the video with one small card attached to each person.

Each tracked person gets a card next to their body, joined to their chest by a
leader line in the person's color, that follows them. Four round gauges sit in
fixed places with a fixed icon and label and one short value each, so a
responder learns once where to look and reads it from across the room:

    [3] LYING                             STILL 1:35      number (matches the dashboard), posture, movement
     (lungs)   (heart)    (eye)    (hand)    (drop)
       16        72      CLOSED      NO       HEAD        one value per gauge
    BREATHING PULSE EST.  EYES   RESPONSE   BLOOD

The ring is the state: white = observed, amber = notable (no chest motion,
eyes closed, no response, blood seen), dashed grey = the camera cannot tell or
nothing was asked yet, cyan = in progress (breathing being measured fills the
ring; after a spoken command the ring drains while the camera watches for a
response). When breathing cannot be measured, one grey line under the gauges
says why. A trailing "?" marks a low detector confidence. Colors mark
observations, not severity: nothing here is a diagnosis or a priority. The
pulse is an estimate from skin colour (signs/pulse.py), shown only for a still
person with a big enough face and a clear rhythm. Up to two short notes
under the gauges say what happened ("Fall seen 1:20 ago", "Waving now"). A
person who just left the picture keeps a small "NOT IN VIEW" chip for a few
seconds, then the top bar lists them with room and time ("3 ROOM 2 2:10").

Once the operator has linked a person to a casualty and set a priority on the
dashboard, a band on top of the card shows that human decision in the triage
color (red immediate, yellow delayed, green minor) with "set by operator", and
the plan for them ("TO APPROVE: responder-002", "responder-002 ON THE WAY"). From
dispatch until the responder acknowledges, the band blinks and a ring pulses
around the person. The camera's own observations never change this color.

Cards are drawn with Pillow (DIN signage faces where available) and cached
until a value changes; placement avoids other cards, faces, bodies and crossing
leader lines and is smoothed so cards glide rather than jump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cv.signs.body import FACE, L_HIP, L_SHOULDER, LIMB_CHAINS, NOSE, R_HIP, R_SHOULDER, TORSO, body_scale

VIEW_H = 720
MAX_VIEW_W = 1280
MIN_VIEW_W = 720              # narrower (portrait) pictures get dark side margins for the cards
TOP_BAR = 30
BOTTOM_MARGIN = 40            # kept clear for warnings and the disclaimer
BOX_W, BOX_H = 410, 134       # card without the hint line
HINT_H = 20                   # added per line under the gauges
MAX_LINES = 2
GAUGE_W = 78
CHIP_W, CHIP_H = 156, 34      # a person just out of view
BAND_H = 28                   # the operator's priority and plan, on top of the card
EDGE_W = 5                    # the identity color along the card's left edge
SUPERSAMPLE = 2
HIDE_UNSEEN_AFTER_S = 5.0     # people out of view this long lose their card ...
LAST_SEEN_KEEP_S = 600.0      # ... and are listed in the top bar with where and when, this long
LAST_SEEN_LISTED = 3
LOW_CONFIDENCE = 0.55         # below this a value gets a trailing "?"
SMOOTHING = 0.3               # share of the way a card moves to its target each frame

# RGB for Pillow.
WHITE = (245, 245, 245)
GREY = (150, 150, 150)
DIM = (70, 74, 78)
AMBER = (255, 176, 0)
CYAN = (70, 210, 240)
BOX_FILL = (14, 15, 17, 236)
STATE_COLORS = {"attention": AMBER, "observed": WHITE, "unknown": GREY, "active": CYAN}
AMBER_TINT = (*(round(0.8 * b + 0.2 * a) for a, b in zip(AMBER, BOX_FILL[:3])), 255)   # opaque: Pillow does not blend
AMBER_BGR = (0, 176, 255)
# Identity colors (BGR): badge, card edge and leader line. None is amber or cyan, so a person's
# color never reads as a finding or as "in progress".
PALETTE = ((255, 163, 77), (255, 140, 181), (96, 224, 168), (192, 138, 255), (130, 170, 210), (215, 215, 215))
REGION_NAMES = {"head": "HEAD", "torso": "TORSO", "left_arm": "L ARM", "right_arm": "R ARM",
                "left_leg": "L LEG", "right_leg": "R LEG"}
# Triage colors of the operator's priority (RGB), and the text color on each.
PRIORITY_COLORS = {"immediate": (220, 38, 38), "delayed": (234, 179, 8), "minor": (34, 170, 85),
                   "expectant": (110, 110, 110), "deceased": (110, 110, 110)}
PRIORITY_INK = {"delayed": (20, 20, 20), "minor": (245, 245, 245)}
UNASSESSED_COLOR = (60, 64, 70)
TASK_TEXT = {"proposed": "TO APPROVE: {name}", "dispatched": "{name} DISPATCHED",
             "delivered": "{name} ALERTED", "acknowledged": "{name} ON THE WAY", "completed": "{name} REACHED"}
DISCLAIMER = "Camera observations, not a diagnosis"
UNKNOWN = "\u2013"            # en dash: "the camera cannot tell"
PULSE = "PULSE EST."
_SANS_BOLD = (("/System/Library/Fonts/HelveticaNeue.ttc", "Bold", "face"),
              ("DejaVuSans-Bold.ttf", None, "file"), ("arialbd.ttf", None, "file"))
_FONTS = {   # (file, variation or face name, how): DIN signage faces first, then common fallbacks
    "value": (("/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf", None, "file"), *_SANS_BOLD),
    "word": (("/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf", None, "file"),
             ("/System/Library/Fonts/SFNS.ttf", "Condensed Bold", "var"), *_SANS_BOLD),
    "label": (("/System/Library/Fonts/SFNS.ttf", "Semibold", "var"),
              ("/System/Library/Fonts/HelveticaNeue.ttc", "Medium", "face"),
              ("DejaVuSans.ttf", None, "file"), ("arial.ttf", None, "file")),
}
_FONTS["bold"], _FONTS["medium"] = _FONTS["value"], _FONTS["label"]


@dataclass(frozen=True)
class Line:
    text: str
    state: str = "observed"           # observed / attention / unknown / active


@dataclass(frozen=True)
class Gauge:
    label: str                        # fixed per gauge: BREATHING, PULSE EST., EYES, RESPONSE, BLOOD
    value: str                        # one short value, UNKNOWN when the camera cannot tell
    state: str = "observed"           # observed / attention / unknown / active
    unit: str = ""
    icon: str = ""                    # icon variant, e.g. eye open / closed
    progress: float | None = None     # 0..1 for an active ring


@dataclass(frozen=True)
class Card:
    """What one person's card shows, from one signs snapshot."""

    movement: Line
    breathing: Gauge
    eyes: Gauge
    response: Gauge
    blood: Gauge
    hint: str = ""                    # why breathing cannot be measured, when it cannot
    notes: tuple[Line, ...] = ()      # what happened: fall, blood spreading, a change, ...
    pulse: Gauge = Gauge(PULSE, UNKNOWN, "unknown")

    @property
    def gauges(self) -> tuple[Gauge, Gauge, Gauge, Gauge, Gauge]:
        return self.breathing, self.pulse, self.eyes, self.response, self.blood

    @property
    def lines(self) -> tuple[Line, ...]:
        """The lines under the gauges, most important first, at most MAX_LINES."""
        hint = (Line(self.hint, "unknown"),) if self.hint else ()
        return (self.notes + hint)[:MAX_LINES]


@dataclass(frozen=True)
class Callout:
    track_id: str
    number: int
    color: tuple[int, int, int]       # BGR
    posture: str
    in_view: bool
    card: Card
    blood_regions: tuple[str, ...] = ()
    priority: str | None = None       # set by the operator on the dashboard, never by the camera
    task: str = ""                    # the plan for this person, e.g. "responder-002 ON THE WAY"
    waiting: bool = False             # dispatched, not yet acknowledged: blinks
    lit: bool = True                  # blink phase

    @property
    def band(self) -> bool:
        return self.in_view and bool(self.priority or self.task)

    @property
    def size(self) -> tuple[int, int]:
        if not self.in_view:
            return CHIP_W, CHIP_H
        return BOX_W, BOX_H + HINT_H * len(self.card.lines) + (BAND_H if self.band else 0)


@dataclass
class Scene:
    callouts: list[Callout] = field(default_factory=list)
    hidden: int = 0                   # tracked, long out of view
    out_of_view: list[tuple[int, str, float]] = field(default_factory=list)   # (number, room, seconds ago)


class LastSeen:
    """Where and when each person was last seen, kept after they leave the picture
    (and after the tracker retires them), so the top bar can say "3 ROOM 2 2:10"."""

    def __init__(self, keep_s: float = LAST_SEEN_KEEP_S):
        self.keep_s = keep_s
        self.people: dict[str, tuple[int, str, float]] = {}

    def update(self, tracks: Sequence, now: float) -> list[tuple[int, str, float]]:
        in_view = set()
        for track in tracks:
            self.people[track.track_id] = (track_number(track), room_label(getattr(track, "zone_id", None)),
                                           track.last_seen)
            if track.detected or now - track.last_seen <= HIDE_UNSEEN_AFTER_S:
                in_view.add(track.track_id)
        for track_id, (_, _, seen) in list(self.people.items()):
            if now - seen > self.keep_s:
                del self.people[track_id]
        return sorted(((number, room, now - seen) for track_id, (number, room, seen) in self.people.items()
                       if track_id not in in_view), key=lambda person: person[2])


def _marked(text: str, sign: dict) -> str:
    return f"{text}?" if sign.get("confidence", 1.0) < LOW_CONFIDENCE else text


def _still(seconds: float) -> str:
    seconds = int(seconds)
    return f"STILL {seconds} s" if seconds < 60 else f"STILL {seconds // 60}:{seconds % 60:02d}"


def card_for(signs: dict, context: dict | None = None) -> Card:
    """Card values for a signs snapshot; ``context`` is SignsEstimator.display_context."""
    context = context or {}

    def moving_camera(sign: dict) -> bool:
        return sign.get("reason") == "camera_moving"

    movement = signs["movement"]
    if movement["value"] == "still":
        move = Line(_marked(_still(movement["still_for_s"]), movement))
    elif movement["value"] == "moving":
        move = Line(_marked("MOVING", movement))
    else:
        move = Line("CAMERA MOVING" if moving_camera(movement) else UNKNOWN, "unknown")

    breathing = signs.get("breathing", {"value": "not_testable"})
    hint = ""
    if breathing["value"] == "motion_detected":
        breath = Gauge("BREATHING", _marked(f"{breathing['rate_bpm']:.0f}", breathing), unit="/min")
    elif breathing["value"] == "no_motion_detected":
        breath = Gauge("BREATHING", _marked("NO MOTION", breathing), "attention")
    elif moving_camera(breathing):
        breath, hint = Gauge("BREATHING", UNKNOWN, "unknown"), "Hold the camera still to measure breathing"
    elif movement["value"] != "still":
        breath, hint = Gauge("BREATHING", UNKNOWN, "unknown"), "Breathing is measured while the person is still"
    elif "breathing_remaining_s" not in context:
        breath, hint = Gauge("BREATHING", UNKNOWN, "unknown"), "Chest not visible"
    elif context["breathing_remaining_s"] > 0:
        needed = max(context.get("breathing_needed_s", 9.0), 1e-6)
        breath = Gauge("BREATHING", f"{context['breathing_remaining_s']:.0f} s", "active",
                       progress=min(1.0, max(0.0, 1 - context["breathing_remaining_s"] / needed)))
    else:
        breath, hint = Gauge("BREATHING", UNKNOWN, "unknown"), "No clear breathing rhythm"

    eyes = signs.get("eyes", {"value": "not_testable"})
    if eyes["value"] == "closed":
        eye = Gauge("EYES", _marked("CLOSED", eyes), "attention", icon="closed")
    elif eyes["value"] == "open":
        eye = Gauge("EYES", _marked("OPEN", eyes), icon="open")
    else:
        eye = Gauge("EYES", UNKNOWN, "unknown", icon="open")

    response = signs["response"]
    if context.get("listening_left_s") is not None:
        window = max(context.get("command_window_s", 8.0), 1e-6)
        respond = Gauge("RESPONSE", f"{context['listening_left_s']:.0f} s", "active",
                        progress=min(1.0, max(0.0, context["listening_left_s"] / window)))
    elif response["value"] == "responded":
        respond = Gauge("RESPONSE", _marked("YES", response))
    elif response["value"] == "no_response":
        respond = Gauge("RESPONSE", _marked("NO", response), "attention")
    elif "command_at" not in response and not moving_camera(response):
        respond = Gauge("RESPONSE", "NOT ASKED", "unknown")
    else:
        respond = Gauge("RESPONSE", UNKNOWN, "unknown")

    blood = signs.get("visible_blood", {"value": "not_testable"})
    if blood["value"] == "detected":
        regions = [REGION_NAMES.get(r, r.upper()) for r in blood["regions"]]
        text = regions[0] + (f" +{len(regions) - 1}" if len(regions) > 1 else "")
        bleed = Gauge("BLOOD", _marked(text, blood), "attention", icon="filled")
    elif blood["value"] == "not_detected":
        bleed = Gauge("BLOOD", _marked("NOT SEEN", blood))
    else:
        bleed = Gauge("BLOOD", UNKNOWN, "unknown")
    return Card(move, breath, eye, respond, bleed, hint, _notes(signs, context), _pulse(signs, context))


def _pulse(signs: dict, context: dict) -> Gauge:
    pulse = signs.get("pulse", {"value": "not_testable"})
    if pulse["value"] == "estimated":
        return Gauge(PULSE, _marked(f"{pulse['rate_bpm']:.0f}", pulse), unit="/min")
    needed = context.get("pulse_needed_s")
    if pulse.get("reason") == "measuring" and needed:
        measured = context.get("pulse_measured_s", 0.0)
        return Gauge(PULSE, f"{max(0.0, needed - measured):.0f} s", "active",
                     progress=min(1.0, max(0.0, measured / needed)))
    return Gauge(PULSE, UNKNOWN, "unknown")


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60}:{seconds % 60:02d}"


_CHANGE_NOTES = {   # (sign, new value): text with {ago} and {was}; only changes toward a notable value
    ("eyes", "closed"): "Eyes closed {ago} ago, were {was}",
    ("response", "no_response"): "No response now, responded before",
    ("breathing", "no_motion_detected"): "No chest motion for {ago}",
    ("visible_blood", "detected"): "Blood first seen {ago} ago",
    ("posture", "lying"): "Lying for {ago}, was {was}",
}


def _notes(signs: dict, context: dict) -> tuple[Line, ...]:
    """What happened, most important first: fall, face down, waving, blood spreading, a change,
    limbs, then someone with them and walking (observed, not notable)."""
    notes = []
    if signs.get("fall", {}).get("value") == "seen":
        ago = context.get("fall_ago_s")
        notes.append(Line("Fall seen" + (f" {_ago(ago)} ago" if ago is not None else ""), "attention"))
    face_down = signs.get("face_down", {})
    if face_down.get("value") == "seen":
        notes.append(Line(f"Face down for {_ago(face_down.get('for_s', 0))}", "attention"))
    if signs.get("waving", {}).get("value") == "seen" and context.get("waved_ago_s") is not None:
        ago = context["waved_ago_s"]
        notes.append(Line("Waving now" if ago < 2 else f"Waved {_ago(ago)} ago", "attention"))
    spread = signs.get("blood_spread", {})
    if spread.get("value") == "growing":
        notes.append(Line(f"Blood area growing, +{spread['change_pct']}% in {spread['window_s']:.0f} s",
                          "attention"))
    changes = signs.get("changes", [])
    ages = context.get("changes_ago_s", [])
    for index in range(len(changes) - 1, -1, -1):
        change = changes[index]
        text = _CHANGE_NOTES.get((change["sign"], change["to"]))
        if text:
            ago = ages[index] if index < len(ages) else None
            if ago is not None or "{ago}" not in text:
                notes.append(Line(text.format(ago=_ago(ago or 0), was=change["from"].replace("_", " ")),
                                  "attention"))
                break
    limbs = signs.get("limbs", {})
    if limbs.get("value") == "some_not_moved":
        names = ", ".join(REGION_NAMES.get(n, n) for n in limbs["not_moved"])
        notes.append(Line(f"Not moved in {limbs['window_s']:.0f} s: {names}", "attention"))
    nearby = signs.get("person_nearby", {})
    if nearby.get("value") == "seen":
        notes.append(Line(f"Someone next to them for {_ago(nearby.get('for_s', 0))}"))
    if signs.get("walking", {}).get("value") == "seen" and context.get("walked_ago_s") is not None:
        notes.append(Line(f"Walked {_ago(context['walked_ago_s'])} ago"))
    return tuple(notes)


def track_number(track) -> int:
    digits = "".join(ch for ch in track.short_id if ch.isdigit())
    return int(digits) if digits else 0


def identity_color(number: int) -> tuple[int, int, int]:
    return PALETTE[(number - 1) % len(PALETTE)]


def task_text(mark) -> str:
    if mark is None or mark.status is None:
        return ""
    return TASK_TEXT.get(mark.status, "{name}").format(name=(mark.responder or "responder").upper())


def build_scene(tracks: Sequence, signs, now: float, last_seen: LastSeen | None = None,
                marks: dict | None = None) -> Scene:
    """Cards for everyone in view or just out of it, in ID order; with ``last_seen``,
    also who left the picture, where and when; with ``marks`` (local track ID: the
    operator's decision, from ``cv.assignments``), the priority band."""
    marks = marks or {}
    lit = int(now * 2) % 2 == 0
    scene = Scene(out_of_view=last_seen.update(tracks, now) if last_seen is not None else [])
    for track in sorted(tracks, key=track_number):
        snapshot = signs.snapshot(track, now)
        if snapshot is None:
            continue
        if not track.detected and now - track.last_seen > HIDE_UNSEEN_AFTER_S:
            scene.hidden += 1
            continue
        blood = snapshot.get("visible_blood", {})
        number = track_number(track)
        posture = snapshot["posture"]["value"]
        mark = marks.get(track.track_id)
        waiting = mark is not None and mark.waiting
        scene.callouts.append(Callout(
            track.track_id, number, identity_color(number),
            "" if posture == "unknown" else posture.upper(), track.detected,
            card_for(snapshot, signs.display_context(track.track_id, now)),
            tuple(blood.get("regions", ())) if blood.get("value") == "detected" else (),
            mark.priority if mark is not None else None, task_text(mark), waiting, lit or not waiting))
    return scene


# --- card drawing (Pillow, cached) --------------------------------------------------------------

@lru_cache(maxsize=64)
def _font(weight: str, size: int):
    for path, style, how in _FONTS[weight]:
        try:
            if how == "file":
                return ImageFont.truetype(path, size)
            if how == "var":
                font = ImageFont.truetype(path, size)
                font.set_variation_by_name(style)
                return font
            for index in range(24):
                candidate = ImageFont.truetype(path, size, index=index)
                if candidate.getname()[1] == style:
                    return candidate
        except OSError:
            continue
    return ImageFont.load_default(size)


def _text(draw, x: float, y: float, text: str, weight: str, size: float, color, k: float,
          max_width: float | None = None, anchor: str = "lm") -> float:
    """Draw text at (x, y) in card units; shrinks, then cuts with an ellipsis, to ``max_width``.

    Returns its width in card units.
    """
    pixels = size * k
    font = _font(weight, max(6, round(pixels)))
    if max_width is not None:
        while draw.textlength(text, font=font) > max_width * k and pixels > size * k * 0.6:
            pixels -= 1
            font = _font(weight, max(6, round(pixels)))
        while len(text) > 1 and draw.textlength(text, font=font) > max_width * k:
            text = text[:-2].rstrip() + "\u2026"
    draw.text((x * k, y * k), text, font=font, fill=color, anchor=anchor)
    return draw.textlength(text, font=font) / k


def _eye(draw, cx, cy, kind: str, color, k: float) -> None:
    w, h = 22, 14
    cx, cy, width = cx * k, cy * k, max(2, round(1.8 * k))
    ts = [i / 16 for i in range(-16, 17)]
    top = [(cx + t * w / 2 * k, cy - h / 2 * k * (1 - t * t)) for t in ts]
    bottom = [(cx + t * w / 2 * k, cy + h / 2 * k * (1 - t * t)) for t in ts]
    if kind == "closed":
        draw.line(bottom, fill=color, width=width, joint="curve")
        for t in (-0.55, 0.0, 0.55):
            px, py = cx + t * w / 2 * k, cy + h / 2 * k * (1 - t * t)
            draw.line([(px, py), (px + t * 4 * k, py + 4 * k)], fill=color, width=width)
        return
    draw.line(top + bottom[::-1] + top[:1], fill=color, width=width, joint="curve")
    radius = h * 0.3 * k
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)


def _lungs(draw, cx, cy, color, k: float) -> None:
    w, h = 22, 18
    x, y = cx - w / 2, cy - h / 2
    width, cx = max(2, round(1.8 * k)), cx * k
    draw.line([(cx, y * k), (cx, (y + h * 0.42) * k)], fill=color, width=width)
    for side in (-1, 1):
        draw.line([(cx, (y + h * 0.42) * k), (cx + side * w * 0.2 * k, (y + h * 0.58) * k)], fill=color, width=width)
        inner, outer = cx + side * w * 0.09 * k, cx + side * w / 2 * k
        draw.rounded_rectangle((min(inner, outer), (y + h * 0.24) * k, max(inner, outer), (y + h) * k),
                               radius=w * 0.17 * k, outline=color, width=width)


def _heart(draw, cx, cy, color, k: float) -> None:
    t = np.linspace(0, 2 * np.pi, 40)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    scale = 10.5 / 16
    points = [((cx + scale * a) * k, (cy + scale * b) * k) for a, b in zip(x, y)]
    draw.line(points + points[:1], fill=color, width=max(2, round(1.8 * k)), joint="curve")


def _hand(draw, cx, cy, color, k: float) -> None:
    """A raised open hand: the response to "raise your hand if you hear me"."""
    width = max(2, round(1.8 * k))
    palm = ((cx - 7) * k, (cy - 1) * k, (cx + 6) * k, (cy + 10) * k)
    draw.rounded_rectangle(palm, radius=4 * k, outline=color, width=width)
    for dx, top in ((-5, -9), (-1.5, -11), (2, -10), (5, -7)):
        draw.line([((cx + dx) * k, (cy + 1) * k), ((cx + dx) * k, (cy + top) * k)], fill=color, width=width)
    draw.line([((cx - 7) * k, (cy + 4) * k), ((cx - 11) * k, (cy - 1) * k)], fill=color, width=width)


def _drop(draw, cx, cy, color, filled: bool, k: float) -> None:
    w, h = 13, 18
    x, y = cx - w / 2, cy - h / 2

    def shape(inset: float, fill) -> None:
        radius = (w / 2 - inset) * k
        ox, oy = (x + w / 2) * k, (y + h) * k - (w / 2) * k
        draw.polygon([(ox, (y + inset * 2.2) * k), (ox - radius * 0.92, oy - radius * 0.4),
                      (ox + radius * 0.92, oy - radius * 0.4)], fill=fill)
        draw.ellipse((ox - radius, oy - radius, ox + radius, oy + radius), fill=fill)

    shape(0.0, color)
    if not filled:
        shape(1.8, BOX_FILL)


def _ring(draw, cx, cy, radius, gauge: Gauge, k: float) -> None:
    box = ((cx - radius) * k, (cy - radius) * k, (cx + radius) * k, (cy + radius) * k)
    color = STATE_COLORS[gauge.state]
    if gauge.state == "unknown":
        for start in range(0, 360, 30):                         # dashed: the camera cannot tell
            draw.arc(box, start, start + 16, fill=DIM, width=max(2, round(2.5 * k)))
        return
    if gauge.state == "active":
        draw.arc(box, 0, 360, fill=DIM, width=max(2, round(3.5 * k)))
        sweep = 360 * (gauge.progress or 0.0)
        if sweep > 1:
            draw.arc(box, -90, -90 + sweep, fill=color, width=max(2, round(3.5 * k)))
        return
    if gauge.state == "attention":
        inner = radius - 3
        draw.ellipse(((cx - inner) * k, (cy - inner) * k, (cx + inner) * k, (cy + inner) * k),
                     fill=AMBER_TINT)
    draw.arc(box, 0, 360, fill=color, width=max(2, round((3.5 if gauge.state == "attention" else 2.5) * k)))


def _gauge(draw, x: float, top: float, gauge: Gauge, k: float) -> None:
    cx, cy, radius = x + GAUGE_W / 2, top + 23, 21
    color = STATE_COLORS[gauge.state]
    icon_color = GREY if gauge.state == "unknown" else color
    _ring(draw, cx, cy, radius, gauge, k)
    if gauge.label == "BREATHING":
        _lungs(draw, cx, cy, icon_color, k)
    elif gauge.label == PULSE:
        _heart(draw, cx, cy + 1, icon_color, k)
    elif gauge.label == "EYES":
        _eye(draw, cx, cy, gauge.icon or "open", icon_color, k)
    elif gauge.label == "RESPONSE":
        _hand(draw, cx, cy, icon_color, k)
    else:
        _drop(draw, cx, cy + 1, icon_color, gauge.icon == "filled", k)
    value_y = top + 62
    if gauge.unit:
        width = draw.textlength(gauge.value, font=_font("value", round(23 * k))) / k
        unit = draw.textlength(gauge.unit, font=_font("label", round(10 * k))) / k
        left = cx - (width + 2 + unit) / 2
        _text(draw, left, value_y, gauge.value, "value", 23, color, k)
        _text(draw, left + width + 2, value_y + 4, gauge.unit, "label", 10, GREY, k)
    elif gauge.value[:1].isdigit() or len(gauge.value) <= 3:
        _text(draw, cx, value_y, gauge.value, "value", 21, color, k, max_width=GAUGE_W - 4, anchor="mm")
    else:
        _text(draw, cx, value_y + 1, gauge.value, "word", 20, color, k, max_width=GAUGE_W - 6, anchor="mm")
    _text(draw, cx, top + 84, gauge.label, "label", 10.5, GREY, k, max_width=GAUGE_W - 4, anchor="mm")


def _band(draw, callout: Callout, width: int, k: float) -> None:
    """The operator's priority and the plan, across the top of the card; dim in the blink's off phase."""
    color = PRIORITY_COLORS.get(callout.priority, UNASSESSED_COLOR)
    ink = PRIORITY_INK.get(callout.priority, WHITE)
    if not callout.lit:
        color, ink = tuple(round(0.35 * c + 0.65 * b) for c, b in zip(color, BOX_FILL[:3])), GREY
    draw.rounded_rectangle((EDGE_W * k, 0, width * k - 1, (BAND_H + 7) * k), radius=7 * k, fill=(*color, 255))
    draw.rectangle((EDGE_W * k, (BAND_H - 1) * k, width * k - 1, (BAND_H + 8) * k), fill=BOX_FILL)
    label = callout.priority.upper() if callout.priority else "UNASSESSED"
    x = EDGE_W + 10
    x += _text(draw, x, BAND_H / 2, label, "word", 17, ink, k) + 6
    x += _text(draw, x, BAND_H / 2 + 1, "set by operator" if callout.priority else "", "label", 10.5, ink, k)
    if callout.task:
        _text(draw, width - 10, BAND_H / 2 + 1, callout.task, "label", 11, ink, k,
              max_width=width - x - 22, anchor="rm")


@lru_cache(maxsize=256)
def _box(callout: Callout, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """(BGR image, alpha 0..1) of one card."""
    k = scale * SUPERSAMPLE
    width, height = callout.size
    image = Image.new("RGBA", (round(width * k), round(height * k)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    card = callout.card
    identity = callout.color[::-1]
    radius = 7 * k
    draw.rounded_rectangle((0, 0, width * k - 1, height * k - 1), radius=radius, fill=(*identity, 255))
    draw.rounded_rectangle((EDGE_W * k, 0, width * k - 1, height * k - 1), radius=radius, fill=BOX_FILL)
    draw.rectangle((EDGE_W * k, 0, (EDGE_W + 8) * k, height * k - 1), fill=BOX_FILL)   # square inner edge

    top = 0
    if callout.band:
        top = BAND_H
        _band(draw, callout, width, k)
    number = str(callout.number)
    badge_w = 14 + 10 * len(number)
    mid = height / 2 if not callout.in_view else top + 20
    left = EDGE_W + 9
    draw.rounded_rectangle((left * k, (mid - 11) * k, (left + badge_w) * k, (mid + 11) * k), radius=4 * k,
                           fill=identity)
    _text(draw, left + badge_w / 2, mid + 1, number, "value", 16, (10, 10, 10), k, anchor="mm")
    x = left + badge_w + 10
    if not callout.in_view:
        _text(draw, x, mid + 1, "NOT IN VIEW", "word", 17, GREY, k)
    else:
        if callout.posture:
            _text(draw, x, top + 21, callout.posture, "word", 19, WHITE, k, max_width=140)
        _text(draw, width - 12, top + 21, card.movement.text, "word", 17, STATE_COLORS[card.movement.state], k,
              max_width=140, anchor="rm")
        draw.line(((left * k, (top + 37) * k), ((width - 12) * k, (top + 37) * k)), fill=(255, 255, 255, 34),
                  width=max(1, round(k)))
        for index, gauge in enumerate(card.gauges):
            _gauge(draw, EDGE_W + 6 + index * GAUGE_W, top + 40, gauge, k)
        for index, line in enumerate(card.lines):
            _text(draw, (width + EDGE_W) / 2, top + BOX_H + 4 + index * HINT_H, line.text, "label", 12,
                  STATE_COLORS[line.state], k, max_width=width - 28, anchor="mm")

    size = (round(width * scale), round(height * scale))
    pixels = np.asarray(image.resize(size, Image.LANCZOS), dtype=np.float32)
    alpha = pixels[..., 3:] / 255.0
    if not callout.in_view:
        alpha = alpha * 0.6
    return pixels[..., 2::-1].copy(), alpha


@lru_cache(maxsize=32)
def _strip(items: tuple[str, ...], right: str, width: int) -> tuple[np.ndarray, np.ndarray]:
    """The top status bar."""
    k = SUPERSAMPLE
    image = Image.new("RGBA", (width * k, TOP_BAR * k), (10, 12, 14, 185))
    draw = ImageDraw.Draw(image)
    x = 12 + _text(draw, 12, TOP_BAR / 2, "AEGIS", "bold", 15, WHITE, k) + 14
    for item in items:
        x += _text(draw, x, TOP_BAR / 2, item, "medium", 12, GREY, k) + 16
    if right:
        _text(draw, width - 12, TOP_BAR / 2, right, "medium", 11, GREY, k, max_width=max(40, width - x - 24),
              anchor="rm")
    pixels = np.asarray(image.resize((width, TOP_BAR), Image.LANCZOS), dtype=np.float32)
    return pixels[..., 2::-1].copy(), pixels[..., 3:] / 255.0


@lru_cache(maxsize=32)
def _label(text: str, color: tuple[int, int, int], size: int) -> tuple[np.ndarray, np.ndarray]:
    """A line of text on a dark pill (warnings, the disclaimer)."""
    k = SUPERSAMPLE
    font = _font("medium", size * k)
    width, height = int(font.getlength(text) / k) + 20, size + 12
    image = Image.new("RGBA", (width * k, height * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width * k - 1, height * k - 1), radius=5 * k, fill=(10, 12, 14, 185))
    draw.text((10 * k, height * k / 2), text, font=font, fill=color, anchor="lm")
    pixels = np.asarray(image.resize((width, height), Image.LANCZOS), dtype=np.float32)
    return pixels[..., 2::-1].copy(), pixels[..., 3:] / 255.0


def _blend(canvas, layer: tuple[np.ndarray, np.ndarray], x: int, y: int) -> None:
    bgr, alpha = layer
    height, width = bgr.shape[:2]
    x1, y1, x2, y2 = max(0, x), max(0, y), min(canvas.shape[1], x + width), min(canvas.shape[0], y + height)
    if x2 <= x1 or y2 <= y1:
        return
    part = (slice(y1 - y, y2 - y), slice(x1 - x, x2 - x))
    region = canvas[y1:y2, x1:x2]
    region[:] = (region * (1 - alpha[part]) + bgr[part] * alpha[part]).astype(np.uint8)


# --- placement ----------------------------------------------------------------------------------

def _overlap(a, b) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _leader_end(rect, anchor) -> tuple[float, float]:
    """Where a leader line leaves its card: the card point nearest the anchor."""
    return min(max(anchor[0], rect[0]), rect[2]), min(max(anchor[1], rect[1]), rect[3])


def _crosses(p1, p2, q1, q2) -> bool:
    """Whether two segments cross (touching ends do not count)."""
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (turn(q1, q2, p1) * turn(q1, q2, p2) < 0) and (turn(p1, p2, q1) * turn(p1, p2, q2) < 0)


def _through(p, q, rect, inset: float = 3.0) -> bool:
    """Whether segment p-q passes through the inside of ``rect``."""
    x1, y1, x2, y2 = rect[0] + inset, rect[1] + inset, rect[2] - inset, rect[3] - inset
    return any(x1 < p[0] + t * (q[0] - p[0]) < x2 and y1 < p[1] + t * (q[1] - p[1]) < y2
               for t in np.linspace(0.05, 0.95, 12))


def _inside(point, rect, pad: float = 10.0) -> bool:
    return rect[0] - pad < point[0] < rect[2] + pad and rect[1] - pad < point[1] < rect[3] + pad


class CalloutLayout:
    """Where each box goes: beside its person, clear of other boxes and bodies, moving smoothly."""

    def __init__(self):
        self.sides: dict[str, str] = {}
        self.positions: dict[str, tuple[float, float]] = {}

    def place(self, people: Sequence[tuple], size: tuple[int, int],
              frame: tuple[int, int]) -> dict[str, tuple[int, int]]:
        """``people``: (track_id, body box, anchor point, face box or None[, card size[, leader]])
        in canvas pixels, in ID order; ``size`` is the card size for people without their own,
        and ``leader`` (default True) whether a line joins the card to the anchor.

        Candidates are the four sides of the body box and the four sides of the
        anchor (for people who fill the picture). The cheapest wins: covering
        another box or a face costs most, then leader lines that cross or run
        under a card, a card over anyone's chest point, other bodies, the own
        body, a long leader line and being pushed back inside the frame.
        """
        frame_w, frame_h = frame
        gap, near = 26, 70
        faces = [person[3] for person in people if person[3] is not None]
        anchors = [person[2] for person in people]
        placed: list[tuple] = []
        leaders: list[tuple] = []
        result = {}
        for person in people:
            track_id, body, anchor = person[:3]
            width, height = person[4] if len(person) > 4 else size
            has_leader = person[5] if len(person) > 5 else True
            area = width * height
            x1, y1, x2, y2 = body
            ax, ay = anchor
            best = None
            candidates = [("above", ax - width / 2, y1 - gap - height), ("below", ax - width / 2, y2 + gap),
                          ("near-above", ax - width / 2, ay - near - height),
                          ("near-below", ax - width / 2, ay + near)]
            for shift in (0.4, -0.35, 1.15, -1.1, 1.9):           # slide along each side to clear other cards
                for side, x in (("right", x2 + gap), ("left", x1 - gap - width),
                                ("near-right", ax + near), ("near-left", ax - near - width)):
                    candidates.append((f"{side}{shift:+}", x, ay - height * shift))
            for side, x, y in candidates:
                cx = min(max(x, 8), max(8, frame_w - width - 8))
                cy = min(max(y, TOP_BAR + 6), max(TOP_BAR + 6, frame_h - height - BOTTOM_MARGIN))
                rect = (cx, cy, cx + width, cy + height)
                start = _leader_end(rect, anchor)
                leader = float(np.hypot(ax - start[0], ay - start[1])) if has_leader else 0.0
                tangle = 0
                if has_leader:
                    tangle += sum(_crosses(start, anchor, a, b) for a, b in leaders)
                    tangle += sum(_through(start, anchor, other) for other in placed)
                    tangle += 0.5 * sum(_through(start, anchor, face, inset=0.0) for face in faces)
                tangle += sum(_through(a, b, rect) for a, b in leaders)
                cost = (25.0 * sum(_overlap(rect, other) for other in placed)
                        + 6.0 * sum(_overlap(rect, face) for face in faces)
                        + 0.8 * area * tangle
                        + 0.6 * area * sum(_inside(point, rect) for point in anchors)
                        + 1.5 * sum(_overlap(rect, other[1]) for other in people if other[0] != track_id)
                        + _overlap(rect, body) + 40.0 * leader + 60.0 * (abs(cx - x) + abs(cy - y)))
                if side != self.sides.get(track_id, side):
                    cost += 0.25 * area                    # stay in place unless it clearly pays
                if best is None or cost < best[0]:
                    best = (cost, side, cx, cy)
            _, side, tx, ty = best
            self.sides[track_id] = side
            px, py = self.positions.get(track_id, (tx, ty))
            px, py = px + SMOOTHING * (tx - px), py + SMOOTHING * (ty - py)
            if any(_overlap((px, py, px + width, py + height), other) for other in placed):
                px, py = tx, ty                            # jump rather than glide across another card
            self.positions[track_id] = (px, py)
            rect = (px, py, px + width, py + height)
            placed.append(rect)
            if has_leader:
                leaders.append((_leader_end(rect, anchor), anchor))
            result[track_id] = (int(round(px)), int(round(py)))
        for known in list(self.positions):
            if known not in result:
                del self.positions[known], self.sides[known]
        return result


def _anchor(track, min_conf: float) -> tuple[float, float]:
    """The chest: below the shoulder line toward the hips, else a shoulder, else the nose,
    else the top of the box. Only a pointer, so shoulders count from a lower confidence."""
    conf, points = track.keypoint_conf, track.keypoints
    if points is not None and conf is not None:
        points = np.asarray(points, dtype=float)
        loose = min_conf * 0.6
        shoulders = [i for i in (L_SHOULDER, R_SHOULDER) if conf[i] >= loose]
        hips = [i for i in (L_HIP, R_HIP) if conf[i] >= loose]
        if len(shoulders) == 2:
            chest = points[shoulders].mean(axis=0)
            if hips:
                chest = chest + 0.25 * (points[hips].mean(axis=0) - chest)
            return float(chest[0]), float(chest[1])
        if shoulders:
            return float(points[shoulders[0]][0]), float(points[shoulders[0]][1])
        if conf[NOSE] >= min_conf:
            return float(points[NOSE][0]), float(points[NOSE][1])
    x1, y1, x2, y2 = track.bbox
    return (x1 + x2) / 2, y1 + 0.15 * (y2 - y1)


def _face_box(track, factor: float, min_conf: float) -> tuple | None:
    conf, points = track.keypoint_conf, track.keypoints
    if points is None or conf is None:
        return None
    face = [i for i in FACE if conf[i] >= min_conf * 0.6]
    if not face:
        return None
    cx, cy = np.asarray(points, dtype=float)[face].mean(axis=0) * factor
    half = 0.15 * body_scale(track.bbox, points, conf, min_conf) * factor
    return cx - half, cy - half, cx + half, cy + half


def _draw_blood_regions(canvas, track, regions: Sequence[str], factor: float, min_conf: float) -> None:
    """Outline the body regions where blood is seen; the region itself stays visible."""
    if track.keypoints is None or track.keypoint_conf is None:
        return
    points = np.asarray(track.keypoints, dtype=float) * factor
    conf = track.keypoint_conf
    scale = body_scale(track.bbox, track.keypoints, conf, min_conf) * factor
    for region in regions:
        if region == "torso" and all(conf[i] >= min_conf for i in TORSO):
            cv2.polylines(canvas, [points[list(TORSO)].astype(np.int32)], True, AMBER_BGR, 3, cv2.LINE_AA)
        elif region == "head":
            face = [i for i in FACE if conf[i] >= min_conf * 0.6]
            if face:
                cv2.circle(canvas, tuple(int(v) for v in points[face].mean(axis=0)),
                           max(8, int(scale * 0.14)), AMBER_BGR, 3, cv2.LINE_AA)
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
                cv2.polylines(canvas, [box.astype(np.int32)], True, AMBER_BGR, 3, cv2.LINE_AA)


def _draw_leader(canvas, rect, anchor, color) -> None:
    """A line in the person's color from the card edge to their chest, ending in a dot."""
    x, y = _leader_end(rect, anchor)
    start, end = (int(x), int(y)), (int(anchor[0]), int(anchor[1]))
    if (x, y) != tuple(anchor):
        cv2.line(canvas, start, end, (16, 16, 16), 5, cv2.LINE_AA)
        cv2.line(canvas, start, end, color, 2, cv2.LINE_AA)
    cv2.circle(canvas, end, 7, (16, 16, 16), -1, cv2.LINE_AA)
    cv2.circle(canvas, end, 5, color, -1, cv2.LINE_AA)


def box_scale(people: int, view_w: int) -> float:
    """Smaller boxes for crowds and narrow pictures."""
    crowd = 1.0 if people <= 2 else 0.85 if people <= 4 else 0.72
    return round(max(0.6, min(crowd, 0.5 * view_w / BOX_W)), 2)


def room_label(zone_id: str | None) -> str:
    return (zone_id or "").replace("-", " ").upper()


def _out_of_view(scene: Scene) -> tuple[str, ...]:
    """Top bar: "OUT OF VIEW  #3 ROOM 2, 2:10 ago  ·  #5, 40 s ago", newest first."""
    if not scene.out_of_view:
        return (f"+{scene.hidden} not in view",) if scene.hidden else ()
    people = [f"#{number}{' ' + room if room else ''}, {_ago(ago)} ago"
              for number, room, ago in scene.out_of_view[:LAST_SEEN_LISTED]]
    more = len(scene.out_of_view) - LAST_SEEN_LISTED
    return ("OUT OF VIEW  " + "  \u00b7  ".join(people) + (f"  \u00b7  +{more}" if more > 0 else ""),)


def render(image, tracks: Sequence, scene: Scene, layout: CalloutLayout | None = None,
           status: Sequence[str] = (), warnings: Sequence[str] = (), keys: str = "",
           keypoint_min_conf: float = 0.5) -> np.ndarray:
    """The frame, fitted to VIEW_H x MAX_VIEW_W, with a card on each person."""
    layout = layout if layout is not None else CalloutLayout()
    height, width = image.shape[:2]
    factor = min(VIEW_H / height, MAX_VIEW_W / width)
    picture_w, view_h = max(2, round(width * factor)), max(2, round(height * factor))
    picture = cv2.resize(image, (picture_w, view_h), interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR)
    view_w = max(picture_w, MIN_VIEW_W) if view_h >= MIN_VIEW_W * 0.75 else picture_w
    offset = (view_w - picture_w) // 2
    by_id = {track.track_id: track for track in tracks}
    for callout in scene.callouts:
        track = by_id.get(callout.track_id)
        if track is not None and callout.blood_regions and callout.in_view:
            _draw_blood_regions(picture, track, callout.blood_regions, factor, keypoint_min_conf)
    if offset:
        canvas = np.full((view_h, view_w, 3), 18, np.uint8)
        canvas[:, offset:offset + picture_w] = picture
    else:
        canvas = picture
    scale = box_scale(len(scene.callouts), view_w)
    sizes = {c.track_id: (round(c.size[0] * scale), round(c.size[1] * scale)) for c in scene.callouts}
    people = []
    for callout in scene.callouts:
        track = by_id.get(callout.track_id)
        if track is None:
            continue
        anchor = _anchor(track, keypoint_min_conf)
        x1, y1, x2, y2 = (v * factor for v in track.bbox)
        face = _face_box(track, factor, keypoint_min_conf)
        people.append((callout.track_id, (x1 + offset, y1, x2 + offset, y2),
                       (min(max(anchor[0] * factor, 0), picture_w - 1) + offset,
                        min(max(anchor[1] * factor, 0), view_h - 1)),
                       None if face is None else (face[0] + offset, face[1], face[2] + offset, face[3]),
                       sizes[callout.track_id], callout.in_view))
    positions = layout.place(people, (round(BOX_W * scale), round(BOX_H * scale)), (view_w, view_h))
    anchors = {person[0]: person[2] for person in people}
    for callout in scene.callouts:
        if callout.waiting and callout.lit and callout.in_view and callout.track_id in anchors:
            color = PRIORITY_COLORS.get(callout.priority, UNASSESSED_COLOR)[::-1]
            x, y = (int(v) for v in anchors[callout.track_id])
            radius = max(24, round(0.06 * view_h))
            cv2.circle(canvas, (x, y), radius + 3, (16, 16, 16), 7, cv2.LINE_AA)
            cv2.circle(canvas, (x, y), radius, color, 4, cv2.LINE_AA)
    for callout in scene.callouts:                       # leaders first, so cards sit on top of them
        if callout.track_id in positions and callout.in_view:
            x, y = positions[callout.track_id]
            w, h = sizes[callout.track_id]
            _draw_leader(canvas, (x, y, x + w, y + h), anchors[callout.track_id], callout.color)
    for callout in scene.callouts:
        if callout.track_id in positions:
            x, y = positions[callout.track_id]
            _blend(canvas, _box(callout, scale), x, y)
    items = tuple(status) + _out_of_view(scene)
    _blend(canvas, _strip(items, keys, view_w), 0, 0)
    for index, line in enumerate(warnings):
        _blend(canvas, _label(line, AMBER, 13), 10, view_h - 34 - 30 * index)
    note = _label(DISCLAIMER, GREY, 11)
    _blend(canvas, note, view_w - note[0].shape[1] - 10, view_h - note[0].shape[0] - 10)
    return canvas
