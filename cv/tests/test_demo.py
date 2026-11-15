from cv.demo import BODYCAM, FIXED, command, plan

C922 = ("C922 Pro Stream Webcam", "UVC Camera VendorID_1133 ProductID_2140")
IPHONE = ("Askar Camera", "iPhone16,1")
BUILT_IN = ("FaceTime HD Camera", "")


def test_both_cameras_start_when_connected():
    feeds, skipped = plan([C922, IPHONE, BUILT_IN])
    assert feeds == [FIXED, BODYCAM] and skipped == []


def test_a_missing_phone_skips_only_the_body_cam():
    feeds, skipped = plan([C922, BUILT_IN])
    assert feeds == [FIXED]
    assert len(skipped) == 1 and "Body cam" in skipped[0] and "iPhone" in skipped[0]


def test_no_bodycam_and_unknown_camera_names():
    assert plan([C922, IPHONE], bodycam=False)[0] == [FIXED]
    assert plan(None)[0] == [FIXED, BODYCAM]


def test_each_camera_sends_signs_and_serves_its_own_tile():
    fixed, body = command(FIXED, "room-2", True), command(BODYCAM, "room-1", False)
    assert fixed[1:4] == ["-m", "cv.main", "--config"] and fixed[4].endswith("demo.yaml")
    assert ["--room", "room-2", "--send-signs", "--stream-port", "8090", "--stream-host", "127.0.0.1"] == fixed[5:]
    assert body[4].endswith("bodycam.yaml") and body[-1] == "--no-display" and "8091" in body
    assert FIXED.url() == "http://127.0.0.1:8090/stream.mjpg" and BODYCAM.url("10.0.0.5").endswith("5:8091/stream.mjpg")


def test_shared_backend_and_a_view_only_body_cam():
    shared = command(FIXED, "room-2", True, "http://192.168.137.1:8000", "0.0.0.0")
    assert shared[-4:] == ["--stream-host", "0.0.0.0", "--backend-url", "http://192.168.137.1:8000"]
    assert "--mock-backend" not in shared
    view = command(BODYCAM, "room-2", True, "http://192.168.137.1:8000", view_only=True)
    assert view[-2:] == ["--mock-backend", "--quiet-mock"]          # draws and streams, sends nothing
