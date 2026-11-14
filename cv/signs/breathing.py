"""Breathing motion from optical flow on the torso while the person is still.

When a track becomes still, its torso rectangle (shoulders and hips) is locked
so keypoint jitter cannot move the region. Each frame, dense Farneback flow
between consecutive crops gives a mean motion vector; its running sum is the
chest displacement. Over ``breathing_window_s`` the displacement is resampled
to 10 Hz, projected on its main axis and detrended. A rate is reported only when
the velocity spectrum has a clear peak in ``breathing_min_bpm``..``breathing_max_bpm``
(SNR >= ``breathing_min_snr``) at the same rate in both halves of the window.
Any movement resets the window.

This is an observation of periodic chest motion, not a respiratory assessment:
clothing, lighting and distance all matter. ``no_motion_detected`` (chest motion
under ``breathing_min_amplitude_px``) is weak evidence with a low confidence;
irregular motion is ``not_testable``. Thresholds favour missing faint breathing
over reporting breathing that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import L_SHOULDER, NOSE, R_SHOULDER, TORSO, confident

RESAMPLE_HZ = 10.0
FLOW_SIDE = 96
PAD = 8


@dataclass
class BreathingState:
    roi: tuple[int, int, int, int] | None = None
    previous: np.ndarray | None = None
    times: list[float] = field(default_factory=list)
    displacement: list[tuple[float, float]] = field(default_factory=list)
    started_t: float | None = None
    last_result: dict | None = None
    last_eval_t: float = -1e9

    def reset(self) -> None:
        self.roi = self.previous = self.started_t = None
        self.times, self.displacement = [], []
        self.last_result = None


def torso_roi(keypoints, keypoint_conf, image_size, min_conf: float):
    """Shoulders-to-hips rectangle; upper chest below the shoulders if the hips are hidden."""
    if keypoints is None or keypoint_conf is None:
        return None
    keypoints = np.asarray(keypoints, dtype=float)
    if confident(keypoint_conf, TORSO, min_conf):
        points = keypoints[list(TORSO)]
    elif confident(keypoint_conf, (L_SHOULDER, R_SHOULDER), min_conf):
        left, right = keypoints[L_SHOULDER], keypoints[R_SHOULDER]
        across = right - left
        down = np.array([-across[1], across[0]])          # perpendicular to the shoulder line
        if keypoint_conf[NOSE] >= min_conf:
            towards_head = keypoints[NOSE] - (left + right) / 2
            if np.dot(down, towards_head) > 0:
                down = -down
        elif down[1] < 0:
            down = -down
        points = np.array([left, right, right + 0.7 * down, left + 0.7 * down])
    else:
        return None
    lo, hi = points.min(axis=0), points.max(axis=0)
    pad = (hi - lo) * 0.1
    x1, y1 = np.floor(lo - pad).astype(int)
    x2, y2 = np.ceil(hi + pad).astype(int)
    width, height = image_size
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    if x2 - x1 < 12 or y2 - y1 < 12:
        return None
    return int(x1), int(y1), int(x2), int(y2)


def _peak(signal, settings) -> tuple[float, float]:
    """(peak SNR, rate /min) of the velocity spectrum; SNR 0 if the peak is not a local maximum.

    The velocity is used because integrated flow drifts like a random walk,
    whose displacement spectrum piles up at the low end of the band. SNR is the
    peak power over the mean band power outside +-2 native bins; the peak must
    also beat everything within +-2 bins, including just outside the band.
    """
    velocity = np.gradient(signal) * RESAMPLE_HZ
    windowed = velocity * np.hanning(len(velocity))
    # Zero-padding interpolates the peak; a 15 s window alone resolves only 4 /min.
    n_fft = PAD * len(windowed)
    power = np.abs(np.fft.rfft(windowed, n=n_fft)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / RESAMPLE_HZ)
    band = (freqs >= settings.breathing_min_bpm / 60.0) & (freqs <= settings.breathing_max_bpm / 60.0)
    if not band.any() or power[band].max() <= 0:
        return 0.0, 0.0
    indices = np.flatnonzero(band)
    peak = int(indices[np.argmax(power[band])])
    near = slice(max(0, peak - 2 * PAD), peak + 2 * PAD + 1)
    rest = band.copy()
    rest[near] = False
    if power[peak] < power[near].max() or not rest.any():
        return 0.0, float(freqs[peak] * 60.0)
    return float(power[peak] / max(power[rest].mean(), 1e-12)), float(freqs[peak] * 60.0)


def chest_signal(times, displacement) -> np.ndarray | None:
    """Detrended chest displacement along its main axis at RESAMPLE_HZ; None if too short."""
    times = np.asarray(times, dtype=float)
    series = np.asarray(displacement, dtype=float)
    if len(times) < 2:
        return None
    grid = np.arange(times[0], times[-1], 1.0 / RESAMPLE_HZ)
    if len(grid) < 32:
        return None
    resampled = np.column_stack([np.interp(grid, times, series[:, i]) for i in range(2)])
    resampled -= resampled.mean(axis=0)
    _, _, axes = np.linalg.svd(resampled, full_matrices=False)
    axis = axes[0] if axes[0][np.argmax(np.abs(axes[0]))] >= 0 else -axes[0]  # stable sign for display
    signal = resampled @ axis
    return signal - np.polyval(np.polyfit(grid - grid[0], signal, 1), grid - grid[0])


def estimate_rate(times, displacement, settings) -> dict:
    """Breathing observation from a chest displacement series (pixels)."""
    signal = chest_signal(times, displacement)
    if signal is None:
        return {"value": "not_testable", "confidence": 0.0}
    duration = float(times[-1] - times[0])
    amplitude = float(np.ptp(signal))
    if amplitude < settings.breathing_min_amplitude_px:
        return {"value": "no_motion_detected", "confidence": 0.45}
    snr, rate = _peak(signal, settings)
    half = len(signal) // 2
    # A real rhythm shows the same rate in both halves of the window.
    consistent = all(abs(_peak(part - part.mean(), settings)[1] - rate) <= 3.0
                     for part in (signal[:half], signal[half:]))
    if snr >= settings.breathing_min_snr and consistent:
        cycles = rate / 60.0 * duration
        strength = min(1.0, np.log10(snr / settings.breathing_min_snr) / 1.5)
        confidence = (0.5 + 0.4 * strength) * min(1.0, cycles / 3.0)
        return {"value": "motion_detected", "confidence": round(float(confidence), 2),
                "rate_bpm": round(rate, 1)}
    # Chest motion without a clear rhythm: cannot tell either way.
    return {"value": "not_testable", "confidence": 0.0}


class BreathingEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    def update(self, state: BreathingState, image_bgr, keypoints, keypoint_conf, still: bool, t: float,
               camera_shift: tuple[float, float] | None = None) -> None:
        """``camera_shift``: this frame's background shift in image pixels, removed from the chest motion."""
        import cv2

        if not still:
            state.reset()
            return
        if state.roi is None:
            state.roi = torso_roi(keypoints, keypoint_conf, (image_bgr.shape[1], image_bgr.shape[0]),
                                  self.min_conf)
            if state.roi is None:
                return
            state.started_t = t
        x1, y1, x2, y2 = state.roi
        crop = cv2.cvtColor(image_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        factor = FLOW_SIDE / max(crop.shape)
        crop = cv2.resize(crop, (max(8, int(crop.shape[1] * factor)), max(8, int(crop.shape[0] * factor))))
        if state.previous is not None and state.previous.shape == crop.shape:
            flow = cv2.calcOpticalFlowFarneback(state.previous, crop, None, 0.5, 2, 9, 2, 5, 1.1, 0)
            step = flow.reshape(-1, 2).mean(axis=0) / factor   # back to image pixels
            if camera_shift is not None:
                step = step - np.asarray(camera_shift, dtype=float)
            last = state.displacement[-1] if state.displacement else (0.0, 0.0)
            state.displacement.append((last[0] + float(step[0]), last[1] + float(step[1])))
            state.times.append(t)
        state.previous = crop
        window = self.settings.breathing_window_s
        while state.times and t - state.times[0] > window:
            state.times.pop(0)
            state.displacement.pop(0)

    def value(self, state: BreathingState, t: float) -> dict:
        if state.started_t is None or t - state.started_t < self.settings.breathing_window_s * 0.6:
            return {"value": "not_testable", "confidence": 0.0}
        if t - state.last_eval_t >= 1.0 or state.last_result is None:
            state.last_eval_t = t
            state.last_result = (estimate_rate(state.times, state.displacement, self.settings)
                                 if len(state.times) >= 16 else {"value": "not_testable", "confidence": 0.0})
        return state.last_result
