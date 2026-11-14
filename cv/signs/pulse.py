"""Pulse estimate from skin colour (remote photoplethysmography, POS method).

Each heartbeat changes the skin's colour very slightly. Per frame, the mean
colour of skin pixels on the forehead and both cheeks is taken from a face
patch aligned with the eyes (so lying people work too). Over ``pulse_window_s``
of a still person the colours are combined with POS (Wang et al., "Algorithmic
Principles of Remote PPG", IEEE TBME 2017), band-passed to 42-180 /min, and the
strongest frequency is the estimate. It is only reported when the face is big
enough, the person was still for at least ``pulse_min_s`` and the peak stands
out (``pulse_min_snr_db``, signal around the peak and its harmonic against the
rest of the band, as in de Haan and Jeanne 2013); otherwise ``not_testable``
with the reason. An estimate, not a measurement: display only, never sent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import L_EYE, NOSE, R_EYE

FS = 15.0                      # Hz: samples are resampled to this rate (cameras run 12-30 fps)
PATCH = 64                     # px: the aligned face patch, eye distance = PATCH / 3
POS_WINDOW_S = 1.6
MIN_HZ, MAX_HZ = 0.7, 3.0      # 42-180 /min
MAX_GAP_S = 0.5                # a longer gap (not still, face lost) restarts the measurement
RECOMPUTE_S = 1.0
MIN_SKIN_SHARE = 0.3
MIN_BRIGHTNESS = 40.0
# Regions in patch coordinates, eyes at (PATCH/3, PATCH*0.4) and (2*PATCH/3, PATCH*0.4).
_REGIONS = ((slice(8, 18), slice(20, 44)),       # forehead, above the eyebrows
            (slice(32, 44), slice(14, 27)),      # cheek under the image-left eye
            (slice(32, 44), slice(37, 50)))      # cheek under the image-right eye


@dataclass
class PulseState:
    samples: deque = field(default_factory=deque)   # (t, (r, g, b)) of skin, or (t, None)
    reason: str = "face_not_visible"                 # why the last sample was None
    cached: tuple[float, dict] | None = None


def skin_color(image_bgr, keypoints, keypoint_conf, min_conf: float, min_eye_px: float):
    """Mean (r, g, b) of forehead and cheek skin, or (None, reason)."""
    import cv2

    if keypoints is None or keypoint_conf is None or min(
            keypoint_conf[L_EYE], keypoint_conf[R_EYE], keypoint_conf[NOSE]) < min_conf:
        return None, "face_not_visible"
    points = np.asarray(keypoints, dtype=np.float64)
    right, left, nose = points[R_EYE], points[L_EYE], points[NOSE]
    across = left - right
    distance = float(np.linalg.norm(across))
    if distance < min_eye_px:
        return None, "face_too_small"
    across /= distance
    down = np.array([-across[1], across[0]])
    middle = (left + right) / 2
    if np.dot(nose - middle, down) < 0:
        down = -down
    scale = (PATCH / 3) / distance
    # Image point p maps to scale * [across; down] (p - middle) + (PATCH/2, 0.4 PATCH).
    rotation = scale * np.stack([across, down])
    offset = np.array([PATCH / 2, 0.4 * PATCH]) - rotation @ middle
    matrix = np.hstack([rotation, offset[:, None]])
    patch = cv2.warpAffine(image_bgr, matrix, (PATCH, PATCH), flags=cv2.INTER_AREA,
                           borderMode=cv2.BORDER_CONSTANT)
    ycrcb = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
    pixels = []
    for rows, cols in _REGIONS:
        region, tone = patch[rows, cols].reshape(-1, 3), ycrcb[rows, cols].reshape(-1, 3)
        skin = (tone[:, 1] >= 133) & (tone[:, 1] <= 173) & (tone[:, 2] >= 77) & (tone[:, 2] <= 127)
        pixels.append(region[skin])
    skin = np.concatenate(pixels)
    total = sum((r.stop - r.start) * (c.stop - c.start) for r, c in _REGIONS)
    if len(skin) < MIN_SKIN_SHARE * total:
        return None, "face_not_visible"
    b, g, r = skin.mean(axis=0)
    if max(r, g, b) < MIN_BRIGHTNESS:
        return None, "too_dark"
    return (float(r), float(g), float(b)), ""


def pos_signal(rgb: np.ndarray, fs: float = FS) -> np.ndarray:
    """POS pulse signal from (N, 3) RGB means sampled at ``fs``."""
    n, window = len(rgb), max(2, int(round(POS_WINDOW_S * fs)))
    projection = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])
    pulse = np.zeros(n)
    for start in range(0, n - window + 1):
        chunk = rgb[start:start + window]
        normalized = chunk / np.maximum(chunk.mean(axis=0), 1e-6)
        s1, s2 = projection @ normalized.T
        h = s1 + (s1.std() / max(s2.std(), 1e-9)) * s2
        pulse[start:start + window] += h - h.mean()
    return pulse


def rate_and_snr(pulse: np.ndarray, fs: float = FS) -> tuple[float, float]:
    """(beats per minute, SNR in dB) of the strongest frequency in 42-180 /min."""
    from scipy.signal import butter, filtfilt

    b, a = butter(3, [MIN_HZ, MAX_HZ], btype="band", fs=fs)
    filtered = filtfilt(b, a, pulse - pulse.mean())
    nfft = 4096
    spectrum = np.abs(np.fft.rfft(filtered * np.hanning(len(filtered)), nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    band = (freqs >= MIN_HZ) & (freqs <= MAX_HZ)
    peak = float(freqs[band][np.argmax(spectrum[band])])
    wide = (freqs >= MIN_HZ - 0.1) & (freqs <= MAX_HZ + 0.5)
    signal = wide & ((np.abs(freqs - peak) <= 0.1) | (np.abs(freqs - 2 * peak) <= 0.2))
    noise = wide & ~signal
    snr = 10 * np.log10(spectrum[signal].sum() / max(spectrum[noise].sum(), 1e-12))
    return peak * 60.0, float(snr)


class PulseEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    def update(self, state: PulseState, image_bgr, keypoints, keypoint_conf, still: bool, t: float) -> None:
        if not still:
            color, reason = None, "moving"
        else:
            color, reason = skin_color(image_bgr, keypoints, keypoint_conf, self.min_conf,
                                       self.settings.pulse_min_eye_px)
        state.reason = reason
        state.samples.append((t, color))
        while state.samples and t - state.samples[0][0] > self.settings.pulse_window_s:
            state.samples.popleft()

    def measured_s(self, state: PulseState) -> float:
        """How long the current unbroken run of usable samples is."""
        run = self._run(state)
        return run[-1][0] - run[0][0] if len(run) > 1 else 0.0

    def _run(self, state: PulseState) -> list:
        run: list = []
        for t, color in reversed(state.samples):
            if color is None or (run and run[-1][0] - t > MAX_GAP_S):
                break
            run.append((t, color))
        return run[::-1]

    def value(self, state: PulseState, t: float) -> dict:
        if state.cached is not None and t - state.cached[0] < RECOMPUTE_S:
            return state.cached[1]
        result = self._estimate(state)
        state.cached = (t, result)
        return result

    def _estimate(self, state: PulseState) -> dict:
        run = self._run(state)
        if not run:
            return {"value": "not_testable", "confidence": 0.0, "reason": state.reason}
        span = run[-1][0] - run[0][0]
        if span < self.settings.pulse_min_s:
            return {"value": "not_testable", "confidence": 0.0, "reason": "measuring"}
        times = np.array([s[0] for s in run])
        colors = np.array([s[1] for s in run])
        grid = np.arange(times[0], times[-1], 1.0 / FS)
        rgb = np.stack([np.interp(grid, times, colors[:, i]) for i in range(3)], axis=1)
        bpm, snr = rate_and_snr(pos_signal(rgb))
        if snr < self.settings.pulse_min_snr_db:
            return {"value": "not_testable", "confidence": 0.0, "reason": "no_clear_rhythm",
                    "snr_db": round(snr, 1)}
        confidence = float(np.clip(0.4 + 0.05 * (snr - self.settings.pulse_min_snr_db), 0.4, 0.85))
        return {"value": "estimated", "rate_bpm": round(bpm), "confidence": round(confidence, 2),
                "snr_db": round(snr, 1)}
