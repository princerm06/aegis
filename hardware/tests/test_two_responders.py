"""Two responder devices, each with its own bridge, against one backend."""

from fastapi.testclient import TestClient

from backend.app.main import create_app
from hardware.bridge.backend import BackendSync, device_label
from hardware.bridge.bridge import Bridge
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import LoopbackTransport

from test_backend_sync import Rig


class Unit:
    def __init__(self, client: TestClient, number: int) -> None:
        self.responder = f"responder-{number:03d}"
        self.device = SimulatedDevice(f"responder-device-{number:02d}")
        self.bridge = Bridge(lambda: LoopbackTransport(self.device))

        def request(method, path, body, headers):
            response = client.request(method, path, json=body, headers=headers)
            return response.status_code, (response.json() if response.content else None)

        self.sync = BackendSync(self.bridge, request, scenario_id="demo", responder_id=self.responder)
        assert self.bridge.connect()

    def step(self) -> None:
        self.bridge.poll()
        self.sync.refresh()
        self.sync.flush()
        self.bridge.poll()


def test_each_device_shows_and_reports_only_its_own_responders_task():
    rig = Rig()
    rig.reset()
    one, two = rig, Unit(rig.client, 2)
    assignments = {a["responder_id"]: a for a in rig.approve_and_dispatch()}
    assert set(assignments) == {"responder-001", "responder-002"}, "demo scenario dispatches both responders"

    one.step(), two.step()
    assert one.device.display == device_label(assignments["responder-001"]["casualty_id"])
    assert two.device.display == device_label(assignments["responder-002"]["casualty_id"])
    assert one.device.display != two.device.display

    two.device.short_press()   # only responder two accepts
    one.step(), two.step()
    status = {a["responder_id"]: a["status"] for a in rig.state()["assignments"]}
    assert status == {"responder-001": "delivered", "responder-002": "acknowledged"}

    two.device.long_press()
    two.step(), two.step(), one.step()
    status = {a["responder_id"]: a["status"] for a in rig.state()["assignments"]}
    assert status == {"responder-001": "delivered", "responder-002": "completed"}
    assert one.device.state == "ALERTING" and two.device.state == "IDLE"
    assert one.sync.stats.rejected == [] and two.sync.stats.rejected == []

    # event IDs carry the physical device, so the two devices can never collide
    by_responder = {a["responder_id"]: a["device_event_ids"] for a in rig.state()["assignments"]}
    assert len(by_responder["responder-002"]) == 3
    assert all(e.startswith("responder-device-02-") for e in by_responder["responder-002"])
    assert all(not e.startswith("responder-device-02-") for e in by_responder["responder-001"])
