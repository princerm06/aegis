import numpy as np

from cv.camera import Camera, camera_override, find_camera
from cv.settings import CameraSettings


class FakeCapture:
    def __init__(self, frames, opened=True):
        self.frames = list(frames)
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def release(self):
        self.released = True

    def get(self, _prop):
        return 0.0


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_camera_reconnects_after_it_stops_delivering_frames():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    captures = [FakeCapture([image]), FakeCapture([], opened=False), FakeCapture([image, image])]
    clock = Clock()
    camera = Camera(CameraSettings(reconnect_after_s=1.0, retry_interval_s=0.5), clock=clock,
                    sleep=clock.sleep, opener=lambda: captures.pop(0))

    assert camera.read() is not None and camera.connected
    frames = []
    for _ in range(500):          # never raises while disconnected
        frame = camera.read()
        if frame is not None:
            frames.append(frame)
        if len(frames) == 2:
            break
    assert len(frames) == 2
    assert camera.reconnects == 2  # one failed reopen, one successful
    assert [f.index for f in frames] == [1, 2]
    assert frames[0].t >= 1.0      # monotonic time advanced through the outage


def test_file_source_finishes_instead_of_reconnecting():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    clock = Clock()
    camera = Camera(CameraSettings(), source="clip.mp4", pace=False, clock=clock, sleep=clock.sleep,
                    opener=lambda: FakeCapture([image]))
    assert camera.read() is not None
    assert camera.read() is None and camera.finished
    assert camera.read() is None


CAMERAS = [("C922 Pro Stream Webcam", "UVC Camera VendorID_1133 ProductID_2140"),
           ("FaceTime HD Camera", "FaceTime HD Camera"), ("Askar Camera", "iPhone16,1")]


def test_cameras_are_found_by_name_or_model_in_any_case():
    assert find_camera("C922", CAMERAS) == 0
    assert find_camera("facetime", CAMERAS) == 1
    assert find_camera("iPhone", CAMERAS) == 2           # by model, whatever the phone is called
    assert find_camera("Logitech Brio", CAMERAS) is None


def test_camera_flag_takes_an_index_or_a_name():
    named = CameraSettings(index=0, name="C922")
    assert camera_override(named, None) == named
    assert camera_override(named, "2") == CameraSettings(index=2, name="")
    assert camera_override(CameraSettings(index=1), "iPhone") == CameraSettings(index=1, name="iPhone")


def test_a_named_camera_follows_renumbering_and_never_opens_another_camera():
    listings = [CAMERAS[1:], [], CAMERAS]                  # phone only, nothing, then all three
    camera = Camera(CameraSettings(index=0, name="iPhone"), lister=lambda: listings.pop(0))
    assert camera.resolve_index() == 1 and camera.label == "camera:1 (Askar Camera)"
    assert camera.resolve_index() is None                 # gone: wait rather than open index 0
    assert camera.resolve_index() == 2


def test_without_a_camera_list_the_configured_index_is_used():
    camera = Camera(CameraSettings(index=3, name="C922"), lister=lambda: None)
    assert camera.resolve_index() == 3
    assert Camera(CameraSettings(index=4), lister=lambda: 1 / 0).resolve_index() == 4   # no name: no lookup


def test_a_camera_hung_upside_down_or_sideways_is_turned_upright():
    image = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    for degrees, expected in ((180, image[::-1, ::-1]), (90, np.rot90(image, -1)), (270, np.rot90(image))):
        camera = Camera(CameraSettings(rotate=degrees), opener=lambda: FakeCapture([image]))
        frame = camera.read()
        assert np.array_equal(frame.image, expected) and camera.resolution == expected.shape[1::-1]
    camera = Camera(CameraSettings(rotate=45), opener=lambda: FakeCapture([image]))
    try:
        camera.read()
    except ValueError as error:
        assert "rotate" in str(error)
    else:
        raise AssertionError("45 degrees accepted")
