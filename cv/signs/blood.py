"""Visible-blood cue by color: deep red pixels inside body regions.

Stage blood is a deep red with little green or blue, unlike skin and lips
(see ``red_mask``). Each check measures the red share of every body region (from the
pose keypoints); a region counts when the share and pixel count pass the
thresholds in most checks of the window. Red clothing or props will trigger it,
so actors should not wear red. ``method: color`` says which detector made the call;
a fine-tuned model can replace it later with ``method: model``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import body_scale, region_masks


def red_mask(image_bgr, settings) -> np.ndarray:
    """Blood-red pixels: red hue, and both green and blue far below red.

    The green/blue ratio is what separates stage blood from skin and lips: a
    webcam in dim warm light renders skin as saturated pink (G/R and B/R around
    0.45), while stage blood keeps G/R and B/R under about 0.3. The hue range is
    narrow on the magenta side so pink clothing and lenses do not count.
    """
    import cv2

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0]
    blue, green, red = (image_bgr[..., i].astype(np.int32) for i in range(3))
    red_hue = (hue <= settings.blood_hue_max) | (hue >= settings.blood_hue_min_wrap)
    deep = np.maximum(green, blue) <= settings.blood_max_gb_ratio * red
    mask = (red_hue & deep & (red >= settings.blood_val_min)).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)


def region_fractions(image_bgr, bbox, keypoints, keypoint_conf, settings,
                     min_conf: float = 0.5) -> dict[str, tuple[float, int]] | None:
    """{region: (red share, red pixels)}, or None when no body region is visible."""
    height, width = image_bgr.shape[:2]
    (x0, y0), masks = region_masks(bbox, keypoints, keypoint_conf, min_conf, (width, height))
    if not masks:
        return None
    any_mask = next(iter(masks.values()))
    crop = image_bgr[y0:y0 + any_mask.shape[0], x0:x0 + any_mask.shape[1]]
    if crop.size == 0:
        return None
    red = red_mask(crop, settings)
    red = red[:any_mask.shape[0], :any_mask.shape[1]]
    result = {}
    for name, mask in masks.items():
        mask = mask[:red.shape[0], :red.shape[1]]
        area = int(mask.sum())
        if area:
            pixels = int((red & mask).sum())
            result[name] = (pixels / area, pixels)
    return result or None


@dataclass
class BloodState:
    checks: deque = field(default_factory=deque)   # (t, {region: detected} or None)
    amounts: deque = field(default_factory=deque)  # (t, red pixels on the body / body size squared)
    last_check_t: float = -1e9


class BloodEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    def _min_fraction(self, region: str) -> float:
        return self.settings.blood_min_fraction_head if region == "head" else self.settings.blood_min_fraction

    def due(self, state: BloodState, t: float) -> bool:
        return t - state.last_check_t >= 1.0 / max(0.1, self.settings.blood_hz)

    def update(self, state: BloodState, image_bgr, bbox, keypoints, keypoint_conf, t: float) -> None:
        state.last_check_t = t
        fractions = region_fractions(image_bgr, bbox, keypoints, keypoint_conf, self.settings, self.min_conf)
        if fractions is None:
            state.checks.append((t, None))
        else:
            state.checks.append((t, {
                name: share >= self._min_fraction(name) and pixels >= self.settings.blood_min_pixels
                for name, (share, pixels) in fractions.items()}))
            scale = body_scale(bbox, keypoints, keypoint_conf, self.min_conf)
            state.amounts.append((t, sum(pixels for _, pixels in fractions.values()) / scale ** 2))
        while state.checks and t - state.checks[0][0] > self.settings.window_s:
            state.checks.popleft()
        while state.amounts and t - state.amounts[0][0] > self.settings.blood_spread_window_s:
            state.amounts.popleft()

    def spread(self, state: BloodState, t: float, detected: bool) -> dict:
        """Whether the visible red area on the body grew over the last ``blood_spread_window_s``:
        the median of the last third against the first third. Only while blood is detected."""
        settings = self.settings
        window = settings.blood_spread_window_s
        result = {"value": "not_testable", "confidence": 0.0, "window_s": window}
        recent = [(ts, a) for ts, a in state.amounts if t - ts <= window]
        if not detected or len(recent) < 12 or recent[-1][0] - recent[0][0] < 0.8 * window:
            return result
        span = recent[-1][0] - recent[0][0]
        early = float(np.median([a for ts, a in recent if ts - recent[0][0] <= span / 3]))
        late = float(np.median([a for ts, a in recent if recent[-1][0] - ts <= span / 3]))
        if early <= 0:
            return result
        ratio = late / early
        change = {"change_pct": round(100 * (ratio - 1))}
        if ratio >= settings.blood_spread_ratio:
            return {**result, "value": "growing", "confidence": round(min(0.8, 0.4 + 0.4 * (ratio - 1)), 2), **change}
        if ratio <= 1 / settings.blood_spread_ratio:
            return {**result, "value": "shrinking", "confidence": round(min(0.8, 0.4 + 0.4 * (1 / ratio - 1)), 2),
                    **change}
        return {**result, "value": "stable", "confidence": 0.5, **change}

    def value(self, state: BloodState, t: float) -> dict:
        recent = [c for ts, c in state.checks if t - ts <= self.settings.window_s and c is not None]
        if len(recent) < 2:
            return {"value": "not_testable", "confidence": 0.0, "method": "color"}
        votes: dict[str, list[bool]] = {}
        for check in recent:
            for name, hit in check.items():
                votes.setdefault(name, []).append(hit)
        regions = sorted(name for name, hits in votes.items() if np.mean(hits) >= 0.6)
        if regions:
            strength = max(np.mean(votes[name]) for name in regions)
            return {"value": "detected", "confidence": round(0.4 + 0.5 * strength, 2),
                    "regions": regions, "method": "color"}
        clean = 1.0 - max(np.mean(hits) for hits in votes.values())
        return {"value": "not_detected", "confidence": round(0.3 + 0.4 * clean, 2), "method": "color"}
