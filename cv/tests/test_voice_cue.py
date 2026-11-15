from cv.settings import SignsSettings, load_settings, CONFIG_DIR
from cv.voice_cue import BLOCK_S, LoudSpeech, find_microphone

QUIET = 0.003           # measured: quiet room on the iPhone microphone
SHOUT = 0.15


def detector() -> LoudSpeech:
    s = SignsSettings()
    return LoudSpeech(s.voice_cue_ratio, s.voice_cue_min_rms, s.voice_cue_min_s, s.command_window_s)


def play(cue: LoudSpeech, segments: list[tuple[float, float]], start: float = 0.0) -> list[float]:
    """Feed (seconds, level) segments in 50 ms blocks; times at which a check starts."""
    t, starts = start, []
    for seconds, level in segments:
        for _ in range(round(seconds / BLOCK_S)):
            if cue.feed(level, t):
                starts.append(round(t - start, 2))
            t += BLOCK_S
    return starts


def words(n: int, level: float = SHOUT) -> list[tuple[float, float]]:
    return [(0.3, level), (0.1, QUIET)] * n


def test_a_loud_question_starts_one_check():
    starts = play(detector(), [(5, QUIET), *words(5), (3, QUIET)])
    assert len(starts) == 1 and 5.35 <= starts[0] <= 5.6


def test_no_second_check_while_the_first_is_running():
    cue = detector()
    starts = play(cue, [(5, QUIET), *words(4), (3, QUIET), *words(4), (6, QUIET), *words(4)])
    assert len(starts) == 2 and starts[1] - starts[0] >= SignsSettings().command_window_s


def test_noises_and_quiet_talk_do_not_start_a_check():
    claps = [(5, QUIET), (0.1, 0.3), (2, QUIET), (0.2, 0.3), (2, QUIET)]
    murmur = [(5, QUIET), *words(10, 0.03)]
    assert play(detector(), claps) == [] and play(detector(), murmur) == []


def test_a_steady_loud_noise_starts_at_most_one_check():
    assert len(play(detector(), [(5, QUIET), (30, 0.06)])) <= 1


def test_the_phone_microphone_matches_by_model():
    microphones = [("MacBook Pro Microphone", "Digital Mic"), ("Askar Microphone", "iPhone Mic"),
                   ("C922 Pro Stream Webcam", "C922 Pro Stream Webcam:046D:085C")]
    inputs = [(0, "Askar Microphone"), (1, "C922 Pro Stream Webcam"), (2, "MacBook Pro Microphone")]
    assert find_microphone("iPhone", microphones, inputs) == (0, "Askar Microphone")
    assert find_microphone("c922", microphones, inputs) == (1, "C922 Pro Stream Webcam")
    assert find_microphone("iPhone", microphones[:1], inputs[1:]) is None


def test_demo_cameras_listen_to_the_phone():
    for config in ("demo.yaml", "bodycam.yaml"):
        assert load_settings(CONFIG_DIR / config).signs.voice_cue_mic == "iPhone"
    assert SignsSettings().voice_cue_mic == ""
