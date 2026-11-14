"""Responder view: the video with one callout box attached to each person.

Each tracked person gets a box next to their body, joined to their chest by a
leader line, that follows them:

    [3] LYING | STILL 42 s          number (matches the dashboard), posture, movement
    (eye)  EYES CLOSED              eyes
           NO RESPONSE              response to a spoken command (press C)
    (drop) BLOOD: HEAD, L ARM       visible blood by body region (also outlined on the body)
    ------------------------
    (lungs) 17 /min  ~~~~           breathing rate with the live chest trace

White is an observation, amber a notable one (eyes closed, no response, blood
seen, no chest motion), grey something the camera cannot tell (with the
reason), cyan the countdown after a command. A trailing "?" marks a low
detector confidence. Colors mark observations, not severity: nothing here is a
diagnosis or a priority, and there is no heart rate (the camera cannot measure
it).

Boxes are drawn with Pillow (real fonts, antialiased) and cached until their
text changes; placement avoids other boxes and bodies and is smoothed so the
boxes glide rather than jump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cv.signs.body import FACE, L_SHOULDER, LIMB_CHAINS, NOSE, R_SHOULDER, TORSO, body_scale

VIEW_H = 720
MAX_VIEW_W = 1280
TOP_BAR = 30
BOTTOM_MARGIN = 40            # kept clear for warnings and the disclaimer
BOX_W, BOX_H = 250, 154
TRACE_W, TRACE_H = 70, 20     # live chest trace in the breathing row
SUPERSAMPLE = 2
HIDE_UNSEEN_AFTER_S = 5.0     # people out of view this long lose their box
LOW_CONFIDENCE = 0.55         # below this a value gets a trailing "?"
SMOOTHING = 0.3               # share of the way a box moves to its target each frame

# RGB for Pillow.
WHITE = (245, 245, 245)
GREY = (160, 160, 160)
AMBER = (255, 176, 0)
CYAN = (70, 210, 240)
BOX_FILL = (10, 12, 14, 200)
STATE_COLORS = {"attention": AMBER, "observed": WHITE, "unknown": GREY, "active": CYAN}
AMBER_BGR = (0, 176, 255)
# Identity colors (BGR): the number badge only, so a person's color never reads as a finding.
PALETTE = ((255, 163, 77), (255, 140, 181), (96, 224, 168), (192, 138, 255), (232, 216, 64), (210, 210, 210))
REGION_NAMES = {"head": "HEAD", "torso": "TORSO", "left_arm": "L ARM", "right_arm": "R ARM",
                "left_leg": "L LEG", "right_leg": "R LEG"}
DISCLAIMER = "Camera observations, not a diagnosis"
_FONTS = {
    "bold": (("/System/Library/Fonts/Avenir Next.ttc", "Bold"), ("/System/Library/Fonts/HelveticaNeue.ttc", "Bold"),
             ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None), ("DejaVuSans-Bold.ttf", None),
             ("arialbd.ttf", None)),
    "medium": (("/System/Library/Fonts/Avenir Next.ttc", "Demi Bold"),
               ("/System/Library/Fonts/HelveticaNeue.ttc", "Medium"),
               ("/System/Library/Fonts/Supplemental/Arial.ttf", None), ("DejaVuSans.ttf", None), ("arial.ttf", None)),
}


@dataclass(frozen=True)
class Line:
    text: str
    state: str = "observed"           # observed / attention / unknown / active


@dataclass(frozen=True)
class Lines:
    """The texts of one callout, from one signs snapshot."""

    movement: Line
    eyes: Line
    eye_icon: str                     # open / closed / unknown
    response: Line
    blood: Line
    breathing: Line                   # the rate as a number when ``breathing_rate``, else a short note
    breathing_rate: bool = False
    show_trace: bool = False


@dataclass(frozen=True)
class Callout:
    track_id: str
    number: int
    color: tuple[int, int, int]       # BGR
    posture: str
    in_view: bool
    lines: Lines
    blood_regions: tuple[str, ...] = ()


@dataclass
class Scene:
    callouts: list[Callout] = field(default_factory=list)
    traces: dict[str, np.ndarray] = field(default_factory=dict)   # track_id -> chest trace
    hidden: int = 0                                               # tracked, long out of view


def _marked(text: str, sign: dict) -> str:
    return f"{text} ?" if sign.get("confidence", 1.0) < LOW_CONFIDENCE else text


def _still(seconds: float) -> str:
    seconds = int(seconds)
    return f"STILL {seconds} s" if seconds < 60 else f"STILL {seconds // 60}:{seconds % 60:02d} min"


def lines_for(signs: dict, context: dict | None = None) -> Lines:
    """Callout texts for a signs snapshot; ``context`` is SignsEstimator.display_context."""
    context = context or {}

    def moving_camera(sign: dict) -> bool:
        return sign.get("reason") == "camera_moving"

    movement = signs["movement"]
    if movement["value"] == "still":
        move = Line(_marked(_still(movement["still_for_s"]), movement))
    elif movement["value"] == "moving":
        move = Line(_marked("MOVING", movement))
    else:
        move = Line("CAMERA MOVING" if moving_camera(movement) else "MOVEMENT: MEASURING", "unknown")

    eyes = signs.get("eyes", {"value": "not_testable"})
    if eyes["value"] == "closed":
        eye, icon = Line(_marked("EYES CLOSED", eyes), "attention"), "closed"
    elif eyes["value"] == "open":
        eye, icon = Line(_marked("EYES OPEN", eyes)), "open"
    else:
        eye, icon = Line("EYES NOT VISIBLE", "unknown"), "unknown"

    response = signs["response"]
    if context.get("listening_left_s") is not None:
        respond = Line(f"LISTENING {context['listening_left_s']:.0f} s", "active")
    elif response["value"] == "responded":
        respond = Line(_marked("RESPONDED", response))
    elif response["value"] == "no_response":
        respond = Line(_marked("NO RESPONSE", response), "attention")
    else:
        respond = Line("NOT TESTED (CAMERA MOVING)" if moving_camera(response) else "RESPONSE NOT TESTED",
                       "unknown")

    blood = signs.get("visible_blood", {"value": "not_testable"})
    if blood["value"] == "detected":
        bleed = Line(_marked("BLOOD: " + ", ".join(REGION_NAMES.get(r, r.upper()) for r in blood["regions"]),
                             blood), "attention")
    elif blood["value"] == "not_detected":
        bleed = Line(_marked("NO VISIBLE BLOOD", blood))
    else:
        bleed = Line("BLOOD: BODY NOT VISIBLE", "unknown")

    breathing = signs.get("breathing", {"value": "not_testable"})
    rate = trace = False
    if breathing["value"] == "motion_detected":
        breath, rate, trace = Line(_marked(f"{breathing['rate_bpm']:.0f}", breathing)), True, True
    elif breathing["value"] == "no_motion_detected":
        breath = Line(_marked("NO CHEST MOTION", breathing), "attention")
    elif moving_camera(breathing):
        breath = Line("CAMERA MUST BE STILL", "unknown")
    elif movement["value"] != "still":
        breath = Line("PERSON MUST BE STILL", "unknown")
    elif "breathing_remaining_s" not in context:
        breath = Line("CHEST NOT VISIBLE", "unknown")
    elif context["breathing_remaining_s"] > 0:
        breath, trace = Line(f"MEASURING {context['breathing_remaining_s']:.0f} s", "unknown"), True
    else:
        breath = Line("NO CLEAR RHYTHM", "unknown")
    return Lines(move, eye, icon, respond, bleed, breath, rate, trace)


def track_number(track) -> int:
    digits = "".join(ch for ch in track.short_id if ch.isdigit())
    return int(digits) if digits else 0


def identity_color(number: int) -> tuple[int, int, int]:
    return PALETTE[(number - 1) % len(PALETTE)]


def build_scene(tracks: Sequence, signs, now: float) -> Scene:
    """Callouts for everyone in view or just out of it, in ID order."""
    scene = Scene()
    for track in sorted(tracks, key=track_number):
        snapshot = signs.snapshot(track, now)
        if snapshot is None:
            continue
        if not track.detected and now - track.last_seen > HIDE_UNSEEN_AFTER_S:
            scene.hidden += 1
            continue
        context = signs.display_context(track.track_id, now)
        lines = lines_for(snapshot, context)
        blood = snapshot.get("visible_blood", {})
        number = track_number(track)
        posture = snapshot["posture"]["value"]
        scene.callouts.append(Callout(
            track.track_id, number, identity_color(number),
            "POSTURE UNKNOWN" if posture == "unknown" else posture.upper(), track.detected, lines,
            tuple(blood.get("regions", ())) if blood.get("value") == "detected" else ()))
        if lines.show_trace and context.get("breathing_trace") is not None:
            scene.traces[track.track_id] = context["breathing_trace"]
    return scene


# --- box drawing (Pillow, cached) ---------------------------------------------------------------

@lru_cache(maxsize=64)
def _font(weight: str, size: int):
    for path, style in _FONTS[weight]:
        try:
            if style is None:
                return ImageFont.truetype(path, size)
            for index in range(24):
                candidate = ImageFont.truetype(path, size, index=index)
                if candidate.getname()[1] == style:
                    return candidate
        except OSError:
            continue
    return ImageFont.load_default(size)


def _text(draw, x: float, y: float, text: str, weight: str, size: float, color, k: float,
          max_width: float | None = None, anchor: str = "lm") -> float:
    """Draw text at (x, y) in box units; shrinks to ``max_width``. Returns its width in box units."""
    pixels = size * k
    font = _font(weight, max(6, round(pixels)))
    if max_width is not None:
        while draw.textlength(text, font=font) > max_width * k and pixels > size * k * 0.6:
            pixels -= 1
            font = _font(weight, max(6, round(pixels)))
    draw.text((x * k, y * k), text, font=font, fill=color, anchor=anchor)
    return draw.textlength(text, font=font) / k


def _eye(draw, x, y, w, h, kind: str, color, k: float) -> None:
    cx, cy, width = (x + w / 2) * k, (y + h / 2) * k, max(2, round(2 * k))
    ts = [i / 16 for i in range(-16, 17)]
    top = [(cx + t * w / 2 * k, cy - h / 2 * k * (1 - t * t)) for t in ts]
    bottom = [(cx + t * w / 2 * k, cy + h / 2 * k * (1 - t * t)) for t in ts]
    if kind == "closed":
        draw.line(bottom, fill=color, width=width, joint="curve")
        for t in (-0.55, 0.0, 0.55):
            px, py = cx + t * w / 2 * k, cy + h / 2 * k * (1 - t * t)
            draw.line([(px, py), (px + t * 5 * k, py + 5 * k)], fill=color, width=width)
        return
    draw.line(top + bottom[::-1] + top[:1], fill=color, width=width, joint="curve")
    radius = h * 0.27 * k
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=width)
    draw.ellipse((cx - radius * 0.4, cy - radius * 0.4, cx + radius * 0.4, cy + radius * 0.4), fill=color)
    if kind == "unknown":
        draw.line([((x + 3) * k, (y + h + 2) * k), ((x + w - 3) * k, (y - 2) * k)], fill=color, width=width)


def _lungs(draw, x, y, w, h, color, k: float) -> None:
    width, cx = max(2, round(2 * k)), (x + w / 2) * k
    draw.line([(cx, y * k), (cx, (y + h * 0.42) * k)], fill=color, width=width)
    for side in (-1, 1):
        draw.line([(cx, (y + h * 0.42) * k), (cx + side * w * 0.2 * k, (y + h * 0.58) * k)], fill=color, width=width)
        inner, outer = cx + side * w * 0.09 * k, cx + side * w / 2 * k
        draw.rounded_rectangle((min(inner, outer), (y + h * 0.24) * k, max(inner, outer), (y + h) * k),
                               radius=w * 0.17 * k, outline=color, width=width)


def _drop(draw, x, y, w, h, color, filled: bool, k: float) -> None:
    def shape(inset: float, fill) -> None:
        radius = (w / 2 - inset) * k
        cx, cy = (x + w / 2) * k, (y + h) * k - (w / 2) * k
        draw.polygon([(cx, (y + inset * 2.2) * k), (cx - radius * 0.92, cy - radius * 0.4),
                      (cx + radius * 0.92, cy - radius * 0.4)], fill=fill)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)

    shape(0.0, color)
    if not filled:
        shape(2.0, BOX_FILL)


@lru_cache(maxsize=256)
def _box(callout: Callout, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """(BGR image, alpha 0..1) of one callout box, without its breathing trace."""
    k = scale * SUPERSAMPLE
    image = Image.new("RGBA", (round(BOX_W * k), round(BOX_H * k)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    lines = callout.lines
    edge = max(2, round(2 * k))
    draw.rounded_rectangle((edge / 2, edge / 2, BOX_W * k - edge / 2 - 1, BOX_H * k - edge / 2 - 1),
                           radius=9 * k, fill=BOX_FILL, outline=WHITE, width=edge)

    def color(line: Line):
        return STATE_COLORS[line.state]

    # Header: number badge, posture, movement.
    badge = callout.color[::-1]
    number = str(callout.number)
    badge_w = 14 + 8 * len(number)
    draw.rounded_rectangle((12 * k, 10 * k, (12 + badge_w) * k, 30 * k), radius=4 * k, fill=badge)
    _text(draw, 12 + badge_w / 2, 20, number, "bold", 14, (15, 15, 15), k, anchor="mm")
    x = 12 + badge_w + 9
    if callout.in_view:
        x += _text(draw, x, 20, callout.posture, "bold", 13, WHITE, k, max_width=110)
        _text(draw, x + 7, 20, "|", "medium", 12, GREY, k)
        _text(draw, x + 17, 20, lines.movement.text, "medium", 12, color(lines.movement), k,
              max_width=BOX_W - 12 - (x + 17))
    else:
        _text(draw, x, 20, "NOT IN VIEW", "bold", 13, GREY, k)

    _eye(draw, 14, 46, 38, 26, lines.eye_icon, color(lines.eyes), k)
    _text(draw, 64, 50, lines.eyes.text, "bold", 15, color(lines.eyes), k, max_width=BOX_W - 76)
    _text(draw, 64, 70, lines.response.text, "bold", 15, color(lines.response), k, max_width=BOX_W - 76)

    seen = lines.blood.state == "attention"
    _drop(draw, 25, 86, 15, 21, color(lines.blood), seen, k)
    _text(draw, 64, 97, lines.blood.text, "bold", 15, color(lines.blood), k, max_width=BOX_W - 76)

    draw.line([(12 * k, 114 * k), ((BOX_W - 12) * k, 114 * k)], fill=WHITE, width=max(1, round(k)))
    _lungs(draw, 16, 121, 32, 26, color(lines.breathing), k)
    if lines.breathing_rate:
        width = _text(draw, 64, 134, lines.breathing.text, "bold", 26, color(lines.breathing), k)
        _text(draw, 64 + width + 7, 128, "BREATHS", "medium", 9, GREY, k)
        _text(draw, 64 + width + 7, 140, "PER MIN", "medium", 9, GREY, k)
    else:
        room = BOX_W - 76 - (TRACE_W + 8 if lines.show_trace else 0)
        _text(draw, 64, 134, lines.breathing.text, "bold", 13, color(lines.breathing), k, max_width=room)

    size = (round(BOX_W * scale), round(BOX_H * scale))
    pixels = np.asarray(image.resize(size, Image.LANCZOS), dtype=np.float32)
    alpha = pixels[..., 3:] / 255.0
    if not callout.in_view:
        alpha = alpha * 0.55
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


class CalloutLayout:
    """Where each box goes: beside its person, clear of other boxes and bodies, moving smoothly."""

    def __init__(self):
        self.sides: dict[str, str] = {}
        self.positions: dict[str, tuple[float, float]] = {}

    def place(self, people: Sequence[tuple[str, tuple, tuple, tuple | None]], size: tuple[int, int],
              frame: tuple[int, int]) -> dict[str, tuple[int, int]]:
        """``people``: (track_id, body box, anchor point, face box or None) in canvas pixels, in ID order.

        Candidates are the four sides of the body box and the four sides of the
        anchor (for people who fill the picture). The cheapest wins: covering
        another box or a face costs most, then other bodies, the own body, a long
        leader line and being pushed back inside the frame.
        """
        width, height = size
        frame_w, frame_h = frame
        gap, near = 26, 70
        faces = [face for *_, face in people if face is not None]
        placed: list[tuple] = []
        result = {}
        for track_id, body, anchor, _ in people:
            x1, y1, x2, y2 = body
            ax, ay = anchor
            best = None
            for side, x, y in (("right", x2 + gap, ay - height * 0.4), ("left", x1 - gap - width, ay - height * 0.4),
                               ("above", ax - width / 2, y1 - gap - height), ("below", ax - width / 2, y2 + gap),
                               ("near-right", ax + near, ay - height / 2),
                               ("near-left", ax - near - width, ay - height / 2),
                               ("near-above", ax - width / 2, ay - near - height),
                               ("near-below", ax - width / 2, ay + near)):
                cx = min(max(x, 8), max(8, frame_w - width - 8))
                cy = min(max(y, TOP_BAR + 6), max(TOP_BAR + 6, frame_h - height - BOTTOM_MARGIN))
                rect = (cx, cy, cx + width, cy + height)
                leader = float(np.hypot(ax - min(max(ax, rect[0]), rect[2]), ay - min(max(ay, rect[1]), rect[3])))
                cost = (4.0 * sum(_overlap(rect, other) for other in placed)
                        + 6.0 * sum(_overlap(rect, face) for face in faces)
                        + 1.5 * sum(_overlap(rect, b) for tid, b, *_ in people if tid != track_id)
                        + _overlap(rect, body) + 40.0 * leader + 60.0 * (abs(cx - x) + abs(cy - y)))
                if side != self.sides.get(track_id, side):
                    cost += 0.25 * width * height          # stay on the same side unless it clearly pays
                if best is None or cost < best[0]:
                    best = (cost, side, cx, cy)
            _, side, tx, ty = best
            self.sides[track_id] = side
            px, py = self.positions.get(track_id, (tx, ty))
            px, py = px + SMOOTHING * (tx - px), py + SMOOTHING * (ty - py)
            self.positions[track_id] = (px, py)
            placed.append((px, py, px + width, py + height))
            result[track_id] = (int(round(px)), int(round(py)))
        for known in list(self.positions):
            if known not in result:
                del self.positions[known], self.sides[known]
        return result


def _anchor(track, min_conf: float) -> tuple[float, float]:
    """Upper chest: between the shoulders, else the nose, else the top of the box."""
    conf, points = track.keypoint_conf, track.keypoints
    if points is not None and conf is not None:
        if conf[L_SHOULDER] >= min_conf and conf[R_SHOULDER] >= min_conf:
            x, y = (np.asarray(points[L_SHOULDER], dtype=float) + np.asarray(points[R_SHOULDER], dtype=float)) / 2
            return float(x), float(y)
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


def _draw_leader(canvas, rect, anchor, dim: bool) -> None:
    """A line from the box edge to the person's chest, ending in a ring."""
    ax, ay = anchor
    x = min(max(ax, rect[0]), rect[2])
    y = min(max(ay, rect[1]), rect[3])
    color = (150, 150, 150) if dim else (245, 245, 245)
    start, end = (int(x), int(y)), (int(ax), int(ay))
    if (x, y) != (ax, ay):
        cv2.line(canvas, start, end, (20, 20, 20), 5, cv2.LINE_AA)
        cv2.line(canvas, start, end, color, 2, cv2.LINE_AA)
    cv2.circle(canvas, end, 7, (20, 20, 20), -1, cv2.LINE_AA)
    cv2.circle(canvas, end, 6, color, 2, cv2.LINE_AA)
    cv2.circle(canvas, end, 2, color, -1, cv2.LINE_AA)


def _draw_trace(canvas, trace: np.ndarray, x: int, y: int, width: int, height: int) -> None:
    span = float(np.ptp(trace))
    xs = np.linspace(x, x + width, len(trace))
    ys = (np.full(len(trace), y + height / 2) if span <= 0
          else y + height - (trace - trace.min()) / span * height)
    cv2.polylines(canvas, [np.column_stack([xs, ys]).astype(np.int32)], False, (245, 245, 245), 1, cv2.LINE_AA)


def box_scale(people: int, view_w: int) -> float:
    """Smaller boxes for crowds and narrow pictures."""
    crowd = 1.0 if people <= 2 else 0.85 if people <= 4 else 0.72
    return round(max(0.6, min(crowd, 0.45 * view_w / BOX_W)), 2)


def render(image, tracks: Sequence, scene: Scene, layout: CalloutLayout | None = None,
           status: Sequence[str] = (), warnings: Sequence[str] = (), keys: str = "",
           keypoint_min_conf: float = 0.5) -> np.ndarray:
    """The frame, fitted to VIEW_H x MAX_VIEW_W, with a callout box on each person."""
    layout = layout if layout is not None else CalloutLayout()
    height, width = image.shape[:2]
    factor = min(VIEW_H / height, MAX_VIEW_W / width)
    view_w, view_h = max(2, round(width * factor)), max(2, round(height * factor))
    canvas = cv2.resize(image, (view_w, view_h), interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR)
    by_id = {track.track_id: track for track in tracks}
    scale = box_scale(len(scene.callouts), view_w)
    size = (round(BOX_W * scale), round(BOX_H * scale))
    people = []
    for callout in scene.callouts:
        track = by_id.get(callout.track_id)
        if track is None:
            continue
        if callout.blood_regions and callout.in_view:
            _draw_blood_regions(canvas, track, callout.blood_regions, factor, keypoint_min_conf)
        anchor = _anchor(track, keypoint_min_conf)
        people.append((callout.track_id, tuple(v * factor for v in track.bbox),
                       (min(max(anchor[0] * factor, 0), view_w - 1), min(max(anchor[1] * factor, 0), view_h - 1)),
                       _face_box(track, factor, keypoint_min_conf)))
    positions = layout.place(people, size, (view_w, view_h))
    anchors = {track_id: anchor for track_id, _, anchor, _ in people}
    for callout in scene.callouts:                       # leaders first, so boxes sit on top of them
        if callout.track_id in positions:
            x, y = positions[callout.track_id]
            _draw_leader(canvas, (x, y, x + size[0], y + size[1]), anchors[callout.track_id], not callout.in_view)
    for callout in scene.callouts:
        if callout.track_id not in positions:
            continue
        x, y = positions[callout.track_id]
        _blend(canvas, _box(callout, scale), x, y)
        trace = scene.traces.get(callout.track_id)
        if trace is not None and callout.in_view and callout.lines.show_trace:
            _draw_trace(canvas, trace, x + round((BOX_W - 12 - TRACE_W) * scale), y + round(124 * scale),
                        round(TRACE_W * scale), round(TRACE_H * scale))
    items = tuple(status) + ((f"+{scene.hidden} not in view",) if scene.hidden else ())
    _blend(canvas, _strip(items, keys, view_w), 0, 0)
    for index, line in enumerate(warnings):
        _blend(canvas, _label(line, AMBER, 13), 10, view_h - 34 - 30 * index)
    note = _label(DISCLAIMER, GREY, 11)
    _blend(canvas, note, view_w - note[0].shape[1] - 10, view_h - note[0].shape[0] - 10)
    return canvas
