from fastapi.testclient import TestClient

from backend.app.api.talk import MAX_CHANNELS, MAX_SDP, TalkRelay, ice_config
from backend.app.main import create_app

BASE = "/api/v1/simulations/talk/channels"
OFFER = "v=0 operator-offer"
ANSWER = "v=0 responder-answer"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def api() -> tuple[TestClient, Clock]:
    app = create_app()
    clock = Clock()
    app.state.talk_relay = TalkRelay(clock=clock)
    return TestClient(app), clock


def test_phone_shows_online_while_its_page_sends_heartbeats():
    client, clock = api()
    assert client.get(f"{BASE}/responder-001").json() == {"channel": "responder-001", "responder_online": False, "session": None}
    client.post(f"{BASE}/responder-001/heartbeat")
    assert client.get(f"{BASE}/responder-001").json()["responder_online"] is True
    clock.now += 7
    assert client.get(f"{BASE}/responder-001").json()["responder_online"] is False


def test_operator_calls_the_phone_answers_and_either_side_hangs_up():
    client, _ = api()
    started = client.post(f"{BASE}/responder-001/offer", json={"sdp": OFFER}).json()["session"]
    assert started["state"] == "ringing" and started["offer"] is None          # operator already has its offer

    ringing = client.post(f"{BASE}/responder-001/heartbeat").json()["session"]
    assert (ringing["state"], ringing["offer"]) == ("ringing", OFFER)
    session_id = ringing["session_id"]

    client.post(f"{BASE}/responder-001/answer", json={"session_id": session_id, "sdp": ANSWER})
    connected = client.get(f"{BASE}/responder-001").json()["session"]
    assert (connected["state"], connected["answer"], connected["offer"]) == ("connected", ANSWER, None)

    ended = client.post(f"{BASE}/responder-001/hangup", json={"session_id": session_id, "by": "responder"}).json()
    assert ended["session"]["state"] == "ended" and ended["session"]["ended_by"] == "responder"
    assert client.get(f"{BASE}/responder-001").json()["session"]["answer"] is None


def test_a_new_call_replaces_the_old_one_and_stale_answers_are_refused():
    client, _ = api()
    client.post(f"{BASE}/responder-001/offer", json={"sdp": OFFER})
    first = client.post(f"{BASE}/responder-001/heartbeat").json()["session"]["session_id"]
    client.post(f"{BASE}/responder-001/offer", json={"sdp": OFFER + " again"})
    response = client.post(f"{BASE}/responder-001/answer", json={"session_id": first, "sdp": ANSWER})
    assert response.status_code == 409
    assert client.post(f"{BASE}/responder-001/hangup", json={"session_id": "nope", "by": "operator"}).status_code == 409
    assert client.get(f"{BASE}/responder-002").json()["session"] is None    # channels are separate


def test_bad_input_is_rejected():
    client, _ = api()
    assert client.post(f"{BASE}/Responder 1/heartbeat").status_code in (404, 422)
    assert client.post(f"{BASE}/responder-001/offer", json={"sdp": "x" * (MAX_SDP + 1)}).status_code == 422
    assert client.post(f"{BASE}/responder-001/offer", json={"sdp": OFFER, "extra": 1}).status_code == 422
    assert client.post(f"{BASE}/responder-001/hangup", json={"session_id": "a", "by": "someone"}).status_code == 422
    for n in range(MAX_CHANNELS):
        client.post(f"{BASE}/r-{n}/heartbeat")
    assert client.post(f"{BASE}/one-too-many/heartbeat").status_code == 429


def test_ice_servers_default_to_stun_and_add_a_turn_relay_when_configured(tmp_path):
    none = tmp_path / "missing.env"
    assert ice_config({}, env_file=none).model_dump() == {
        "ice_servers": [{"urls": ["stun:stun.l.google.com:19302"], "username": None, "credential": None}],
        "ice_transport_policy": "all",
    }
    env_file = tmp_path / ".env"
    env_file.write_text("AEGIS_TALK_TURN_URLS=turn:100.64.0.5:3478, http://not-turn\n"
                        "AEGIS_TALK_TURN_USERNAME=aegis\nAEGIS_TALK_TURN_CREDENTIAL='s3cret'\n", encoding="utf-8")
    relay = ice_config({"AEGIS_TALK_FORCE_RELAY": "1"}, env_file=env_file)
    assert relay.ice_servers[1].model_dump() == {"urls": ["turn:100.64.0.5:3478"], "username": "aegis", "credential": "s3cret"}
    assert relay.ice_transport_policy == "relay"
    assert ice_config({"AEGIS_TALK_FORCE_RELAY": "1"}, env_file=none).ice_transport_policy == "all"   # no relay to force

    client, _ = api()
    client.app.state.talk_ice = relay
    response = client.get("/api/v1/simulations/talk/ice-servers")
    assert response.json()["ice_transport_policy"] == "relay" and response.headers["cache-control"] == "no-store"


def test_talk_link_leaves_the_simulation_untouched():
    client, _ = api()
    state = lambda: {k: v for k, v in client.get("/api/v1/simulations/demo/state").json().items() if k != "generated_at"}
    before = state()
    client.post(f"{BASE}/responder-001/offer", json={"sdp": OFFER})
    client.post(f"{BASE}/responder-001/heartbeat")
    assert state() == before
