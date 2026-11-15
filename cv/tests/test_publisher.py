from datetime import datetime, timezone

from cv.clock import Timebase
from cv.publisher import MockTransport, Publisher, TransportError
from cv.settings import PublisherSettings
from cv.tracker import TrackReport


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeTransport:
    """Records requests; responses are scripted per call (default 201/200)."""

    def __init__(self):
        self.calls = []
        self.script = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return (200 if method == "PUT" else 201), {"duplicate": False}

    def posts(self):
        return [body for method, _, body in self.calls if method == "POST"]


def make(endpoint="camera", heartbeat_s=1.0):
    clock = FakeClock()
    timebase = Timebase(monotonic=clock, wall=lambda: datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
    transport = FakeTransport()
    publisher = Publisher(PublisherSettings(endpoint=endpoint, heartbeat_s=heartbeat_s),
                          frame_id="demo-floor-1", calibration_ref="cv-demo-floor-1-abc",
                          session_id="s1", timebase=timebase, transport=transport, clock=clock)
    return publisher, transport, clock


def report(t, state="visible", x=1.0, y=2.0, conf=0.8, last_seen=None, track="s1-t1"):
    return TrackReport(track_id=track, state=state, observed_t=t, first_seen_t=90.0,
                       last_seen_t=t if last_seen is None else last_seen,
                       x_m=x, y_m=y, confidence=conf, capture_t=t)


def test_registers_once_then_posts_a_camera_observation():
    publisher, transport, clock = make()
    publisher.submit([report(100.0)])
    publisher.step()
    (put, put_path, registration), (post, post_path, body) = transport.calls
    assert (put, put_path) == ("PUT", "/api/v1/simulations/demo/cameras/logitech-1")
    assert registration == {"mode": "simulation", "actor": "cv-askar",
                            "mapping_mode": "calibrated_world",
                            "calibration_ref": "cv-demo-floor-1-abc"}
    assert (post, post_path) == ("POST", "/api/v1/simulations/demo/cameras/logitech-1/observations")
    assert body == {
        "mode": "simulation", "id": "s1-o000001", "observed_at": "2026-11-14T05:00:00.000Z",
        "confidence": 0.8,
        "track": {"track_id": "s1-t1", "state": "visible",
                  "first_seen_at": "2026-11-14T04:59:50.000Z", "last_seen_at": "2026-11-14T05:00:00.000Z"},
        "position": {"frame_id": "demo-floor-1", "x_m": 1.0, "y_m": 2.0},
    }
    clock.t = 101.5
    publisher.submit([report(101.5, x=3.0)])
    publisher.step()
    assert [method for method, _, _ in transport.calls].count("PUT") == 1


def test_unchanged_tracks_are_only_resent_on_heartbeat():
    publisher, _, clock = make(heartbeat_s=1.0)
    assert publisher.submit([report(100.0)]) == 1
    clock.t = 100.2
    assert publisher.submit([report(100.2)]) == 0            # same position/state
    assert publisher.submit([report(100.2, x=1.5)]) == 1     # moved
    clock.t = 101.3
    assert publisher.submit([report(101.3, x=1.5)]) == 1     # heartbeat due


def test_confidence_crossing_half_counts_as_a_change():
    publisher, _, clock = make()
    publisher.submit([report(100.0, conf=0.62)])
    clock.t = 100.2
    assert publisher.submit([report(100.2, conf=0.55)]) == 0
    assert publisher.submit([report(100.2, conf=0.45)]) == 1


def test_occluded_report_repeats_the_last_accepted_detection():
    publisher, transport, clock = make()
    publisher.submit([report(100.0, x=1.0, y=2.0, conf=0.8)])
    publisher.step()
    clock.t = 101.0
    # The tracker's latest values differ from what the backend accepted.
    publisher.submit([report(101.0, state="occluded", x=1.3, y=2.4, conf=0.7, last_seen=100.6)])
    publisher.step()
    occluded = transport.posts()[-1]
    assert occluded["track"]["state"] == "occluded"
    assert occluded["position"] == {"frame_id": "demo-floor-1", "x_m": 1.0, "y_m": 2.0}
    assert occluded["confidence"] == 0.8
    assert occluded["track"]["last_seen_at"] == "2026-11-14T05:00:00.000Z"
    assert occluded["observed_at"] == "2026-11-14T05:00:01.000Z"


def test_occluded_report_for_a_never_accepted_track_is_skipped():
    publisher, transport, _ = make()
    publisher.submit([report(100.0, state="occluded")])
    publisher.step()
    assert transport.posts() == [] and publisher.stats.skipped == 1


def test_network_errors_back_off_and_retry_with_the_same_id():
    publisher, transport, clock = make()
    transport.script = [(200, {}), TransportError("connection refused")]
    publisher.submit([report(100.0)])
    publisher.step()
    assert publisher.stats.network_errors == 1 and publisher.pending == 1
    publisher.step()                                          # still backing off
    assert len(transport.posts()) == 1
    clock.t = 101.0
    publisher.step()
    first, retry = transport.posts()
    assert first == retry and publisher.stats.accepted == 1 and publisher.pending == 0


def test_a_newer_report_replaces_a_failed_older_one():
    publisher, transport, clock = make()
    transport.script = [(200, {}), TransportError("timeout")]
    publisher.submit([report(100.0)])
    publisher.step()
    clock.t = 100.5
    publisher.submit([report(100.5, x=2.0)])
    clock.t = 101.0
    publisher.step()
    assert [body["position"]["x_m"] for body in transport.posts()] == [1.0, 2.0]


def test_submit_never_blocks_on_a_dead_backend():
    publisher, transport, clock = make()
    transport.script = [TransportError("down")] * 100
    for i in range(50):
        clock.t = 100.0 + i
        publisher.submit([report(clock.t, x=float(i))])
    assert publisher.pending == 1                             # newest per track only


def test_missing_camera_registration_triggers_re_registration():
    publisher, transport, _ = make()
    transport.script = [(200, {}), (404, {"detail": "Camera logitech-1 is not registered"}), (200, {}), (201, {})]
    publisher.submit([report(100.0)])
    publisher.step()   # registers, 404
    publisher.step()   # re-registers, retries
    methods = [method for method, _, _ in transport.calls]
    assert methods == ["PUT", "POST", "PUT", "POST"]
    assert publisher.stats.accepted == 1


def test_rejected_reports_are_dropped_not_retried():
    publisher, transport, _ = make()
    transport.script = [(200, {}), (409, {"detail": "Stale observation"})]
    publisher.submit([report(100.0)])
    publisher.step()
    publisher.step()
    assert publisher.stats.rejected == 1 and len(transport.posts()) == 1
    assert "409" in publisher.stats.last_error


def test_conflicting_registration_is_reported_but_not_fatal():
    publisher, transport, _ = make()
    transport.script = [(409, {"detail": "Camera mapping is immutable"})]
    publisher.submit([report(100.0)])
    publisher.step()
    assert publisher.stats.accepted == 1


def test_an_announced_stream_registers_at_once_with_its_address():
    publisher, transport, _ = make()
    publisher.step()
    assert transport.calls == []                                       # nothing to say yet
    publisher.announce_stream("http://192.168.137.145:8090/stream.mjpg")
    publisher.step()                                                   # no one in view: registers anyway
    ((method, _, body),) = transport.calls
    assert method == "PUT" and body["stream_url"] == "http://192.168.137.145:8090/stream.mjpg"
    publisher.step()
    assert len(transport.calls) == 1


def test_a_backend_without_stream_url_still_registers_the_camera():
    publisher, transport, _ = make()
    publisher.announce_stream("http://10.0.0.5:8090/stream.mjpg")
    transport.script = [(422, {"detail": [{"type": "extra_forbidden", "loc": ["body", "stream_url"]}]})]
    publisher.step()
    (_, _, first), (_, _, second) = transport.calls
    assert "stream_url" in first and "stream_url" not in second
    publisher.submit([report(100.0)])
    publisher.step()
    assert [m for m, _, _ in transport.calls] == ["PUT", "PUT", "POST"]   # registered once, then posting


def test_lost_report_is_always_sent_and_clears_state():
    publisher, transport, clock = make()
    publisher.submit([report(100.0)])
    publisher.step()
    clock.t = 100.1
    assert publisher.submit([report(100.1, state="lost", last_seen=100.0)]) == 1
    publisher.step()
    assert transport.posts()[-1]["track"]["state"] == "lost"


def test_legacy_endpoint_sends_a_contract_cv_observation():
    publisher, transport, _ = make(endpoint="legacy")
    publisher.submit([report(100.0)])
    publisher.step()
    [(method, path, body)] = transport.calls
    assert (method, path) == ("POST", "/api/v1/simulations/demo/observations")
    assert body["source"] == "cv" and body["kind"] == "person_detected"
    assert body["track"]["track_id"] == "logitech-1-s1-t1" and "mode" not in body


def test_mock_transport_logs_exact_requests(tmp_path):
    path = tmp_path / "mock.jsonl"
    mock = MockTransport(path, echo=False)
    assert mock("POST", "/x", {"a": 1}) == (201, {"mock": True})
    assert path.read_text().strip() == '{"method":"POST","path":"/x","body":{"a":1}}'
