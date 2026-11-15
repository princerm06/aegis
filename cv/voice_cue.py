"""Response check started by the responder's voice (``signs.voice_cue_mic``).

When the responder says something loud, like "Can you hear me? Raise your hand",
the microphone hears it and the pipeline marks a spoken command, exactly as
pressing c does; nobody presses anything. It hears loudness, not words: sound
``voice_cue_ratio`` times above the room's background level, and above
``voice_cue_min_rms``, for ``voice_cue_min_s`` starts one check, and no new one
starts until that check's ``command_window_s`` is over. The phone on the
responder's chest hears its wearer far louder than people across the room, so
use its microphone (``iPhone`` matches a Continuity microphone whatever the
phone is called).

    python -m cv.voice_cue --mic iPhone     # level meter: speak and see when a check would start
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Sequence

from cv.settings import CONFIG_DIR, SignsSettings, load_settings

log = logging.getLogger(__name__)

BLOCK_S = 0.05
GAP_S = 0.25            # quieter than this long between words still counts as one utterance


@dataclass
class LoudSpeech:
    """Loudness per block in, True once per utterance that should start a check."""

    ratio: float
    min_rms: float
    min_s: float
    cooldown_s: float
    background: float | None = None
    loud_since: float | None = None
    last_loud: float = -math.inf
    ready_at: float = -math.inf

    def threshold(self) -> float:
        return max((self.background or 0.0) * self.ratio, self.min_rms)

    def feed(self, rms: float, t: float) -> bool:
        if self.background is None:
            self.background = rms
        loud = rms > self.threshold()
        if loud:
            if self.loud_since is None or t - self.last_loud > GAP_S:
                self.loud_since = t
            self.last_loud = t
            rate = 0.002                            # long speech must not become the background
        else:
            rate = 0.2 if rms < self.background else 0.01
        self.background += (rms - self.background) * rate
        if loud and t - self.loud_since >= self.min_s and t >= self.ready_at:
            self.ready_at = t + self.cooldown_s
            self.loud_since = None
            return True
        return False


def list_microphones() -> list[tuple[str, str]]:
    """(name, model) of each microphone macOS knows; empty elsewhere."""
    if sys.platform != "darwin":
        return []
    try:
        import AVFoundation
    except ImportError:
        return []
    devices = AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeAudio)
    return [(str(device.localizedName()), str(device.modelID() or "")) for device in devices]


def find_microphone(wanted: str, microphones: Sequence[tuple[str, str]],
                    inputs: Sequence[tuple[int, str]]) -> tuple[int, str] | None:
    """Input device (index, name) whose name, or macOS model, contains ``wanted`` (any case)."""
    key = wanted.casefold()
    names = {name for name, model in microphones if key in name.casefold() or key in model.casefold()}
    return next(((index, name) for index, name in inputs if name in names or key in name.casefold()), None)


class VoiceCue:
    """Listens on a microphone in the background; ``take()`` is True once per loud utterance."""

    def __init__(self, settings: SignsSettings, device: int, name: str):
        self.name = name
        self.device = device
        self.detector = LoudSpeech(settings.voice_cue_ratio, settings.voice_cue_min_rms,
                                   settings.voice_cue_min_s, settings.command_window_s)
        self._pending = 0
        self._lock = threading.Lock()
        self._stream = None
        self.level = 0.0

    @classmethod
    def open(cls, settings: SignsSettings) -> VoiceCue | None:
        """The configured microphone, or None (logged) when it is off, missing or unavailable."""
        if not settings.voice_cue_mic:
            return None
        try:
            import sounddevice
        except ImportError:
            log.warning("Voice cue off: sounddevice is not installed (cv/requirements.txt)")
            return None
        inputs = [(index, str(device["name"])) for index, device in enumerate(sounddevice.query_devices())
                  if device["max_input_channels"] > 0]
        found = find_microphone(settings.voice_cue_mic, list_microphones(), inputs)
        if found is None:
            log.warning("Voice cue off: no microphone matching '%s' (%s); press c instead",
                        settings.voice_cue_mic, ", ".join(name for _, name in inputs) or "none")
            return None
        return cls(settings, *found)

    def start(self) -> None:
        import numpy as np
        import sounddevice

        rate = int(sounddevice.query_devices(self.device)["default_samplerate"])

        def on_block(block, frames, _time, _status) -> None:
            self.level = float(np.sqrt(np.mean(np.square(block[:, 0], dtype=np.float64))))
            if self.detector.feed(self.level, time.monotonic()):
                with self._lock:
                    self._pending += 1

        self._stream = sounddevice.InputStream(device=self.device, channels=1, samplerate=rate,
                                               blocksize=int(rate * BLOCK_S), dtype="float32",
                                               callback=on_block)
        self._stream.start()
        log.info("Voice cue on %s: loud speech starts the response check", self.name)

    def take(self) -> bool:
        with self._lock:
            pending, self._pending = self._pending, 0
        return pending > 0

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice cue level meter")
    parser.add_argument("--mic", help="microphone name or model (default: bodycam.yaml's)")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(CONFIG_DIR / "bodycam.yaml").signs
    if args.mic:
        from dataclasses import replace
        settings = replace(settings, voice_cue_mic=args.mic)
    cue = VoiceCue.open(settings)
    if cue is None:
        return 1
    cue.start()
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            time.sleep(0.2)
            bar = "#" * min(60, int(cue.level / max(cue.detector.threshold(), 1e-6) * 20))
            started = cue.take()
            print(f"level {cue.level:.3f}  needs {cue.detector.threshold():.3f}  {bar:<60}"
                  + ("  << CHECK STARTS" if started else ""), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        cue.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
