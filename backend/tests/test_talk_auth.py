import threading

import pytest
from fastapi.testclient import TestClient

from backend.app.api.talk import MAX_CHANNELS, TalkRelay, talk_auth_from_env
from backend.app.api.talk_auth import MIN_TTL_S, TalkAuth
from backend.app.main import create_app

BASE = "/api/v1/simulations/talk/channels"
ICE = "/api/v1/simulations/talk/ice-servers"
OFFER = "v=0 operator-offer"
ANSWER = "v=0 responder-answer"
OPERATOR_TOKEN = "test-operator-secret-0123456789abcdef"
OP = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


class Wall:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def api(**auth) -> tuple[TestClient, Wall]:
    app = create_app()
    wall = Wall()
    app.state.talk_relay = TalkRelay()
    app.state.talk_auth = TalkAuth(operator_token=OPERATOR_TOKEN, wall=wall, **auth)
    return TestClient(app), wall


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def enroll(client: TestClient, channel: str) -> dict[str, str]:
    response = client.post(f"{BASE}/{channel}/enroll", headers=OP)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    return bearer(response.json()["token"])


def ring(client: TestClient, channel: str, phone: dict[str, str]) -> str:
    client.post(f"{BASE}/{channel}/offer", headers=OP, json={"sdp": OFFER})
    return client.post(f"{BASE}/{channel}/heartbeat", headers=phone).json()["session"]["session_id"]


def every_route(channel: str, session_id: str = "s") -> list[tuple[str, str, dict | None]]:
    return [
        ("GET", f"{BASE}/{channel}", None),
        ("POST", f"{BASE}/{channel}/heartbeat", None),
        ("POST", f"{BASE}/{channel}/offer", {"sdp": OFFER}),
        ("POST", f"{BASE}/{channel}/answer", {"session_id": session_id, "sdp": ANSWER}),
        ("POST", f"{BASE}/{channel}/hangup", {"session_id": session_id}),
        ("POST", f"{BASE}/{channel}/enroll", None),
        ("POST", f"{BASE}/{channel}/revoke", None),
        ("GET", ICE, None),
    ]


# --- the full authorized flow -------------------------------------------------------------

def test_enrolled_phone_and_operator_complete_a_call():
    client, _ = api()
    enrolled = client.post(f"{BASE}/responder-001/enroll", headers=OP).json()
    assert enrolled["channel"] == "responder-001"
    assert enrolled["phone_path"] == f"/responder?id=responder-001#token={enrolled['token']}"
    phone = bearer(enrolled["token"])

    assert client.post(f"{BASE}/responder-001/heartbeat", headers=phone).json()["responder_online"] is True
    session_id = ring(client, "responder-001", phone)
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=phone).json()["session"]["offer"] == OFFER
    assert client.post(f"{BASE}/responder-001/answer", headers=phone,
                       json={"session_id": session_id, "sdp": ANSWER}).status_code == 200
    seen = client.get(f"{BASE}/responder-001", headers=OP).json()["session"]
    assert (seen["state"], seen["answer"], seen["offer"]) == ("connected", ANSWER, None)
    assert client.get(ICE, headers=phone).status_code == 200 and client.get(ICE, headers=OP).status_code == 200


@pytest.mark.parametrize("who", ["operator", "responder"])
def test_either_participant_can_hang_up_and_the_role_comes_from_the_credential(who):
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    headers = OP if who == "operator" else phone
    ended = client.post(f"{BASE}/responder-001/hangup", headers=headers, json={"session_id": session_id}).json()
    assert ended["session"]["state"] == "ended" and ended["session"]["ended_by"] == who


def test_a_claimed_role_that_does_not_match_the_credential_is_refused():
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    hangup = f"{BASE}/responder-001/hangup"
    assert client.post(hangup, headers=phone, json={"session_id": session_id, "by": "operator"}).status_code == 403
    assert client.post(hangup, headers=OP, json={"session_id": session_id, "by": "responder"}).status_code == 403
    assert client.get(f"{BASE}/responder-001", headers=OP).json()["session"]["state"] == "ringing"


# --- unauthenticated and wrong-role requests ----------------------------------------------

@pytest.mark.parametrize("headers", [
    {},
    bearer("wrong-secret-wrong-secret-wrong-secret"),
    bearer(OPERATOR_TOKEN + "x"),
    {"Authorization": f"Basic {OPERATOR_TOKEN}"},
    {"Authorization": OPERATOR_TOKEN},
    bearer("rt1.responder-001.0.9999999999.forged"),
    {"X-Role": "operator", "X-Responder-Id": "responder-001"},
])
def test_missing_or_invalid_credentials_are_rejected_everywhere(headers):
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    for method, url, body in every_route("responder-001", session_id):
        response = client.request(method, url, headers=headers, json=body)
        assert response.status_code == 401, (method, url)
        assert response.headers["www-authenticate"] == "Bearer"
        assert OFFER not in response.text and "rt1." not in response.text
    # nothing changed: the call is still ringing with its offer
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=phone).json()["session"]["offer"] == OFFER


def test_a_responder_token_cannot_do_operator_things():
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    client.post(f"{BASE}/responder-001/answer", headers=phone, json={"session_id": session_id, "sdp": ANSWER})
    assert client.get(f"{BASE}/responder-001", headers=phone).status_code == 403          # can't read the answer
    assert client.post(f"{BASE}/responder-001/offer", headers=phone, json={"sdp": OFFER}).status_code == 403
    assert client.post(f"{BASE}/responder-001/enroll", headers=phone).status_code == 403
    assert client.post(f"{BASE}/responder-001/revoke", headers=phone).status_code == 403
    assert client.get(f"{BASE}/responder-001", headers=OP).json()["session"]["session_id"] == session_id


def test_the_operator_secret_cannot_act_as_a_phone():
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=OP).status_code == 403
    assert client.post(f"{BASE}/responder-002/heartbeat", headers=OP).status_code == 403
    assert client.post(f"{BASE}/responder-001/answer", headers=OP,
                       json={"session_id": session_id, "sdp": ANSWER}).status_code == 403
    assert client.get(f"{BASE}/responder-001", headers=OP).json()["session"]["state"] == "ringing"
    assert client.get(f"{BASE}/responder-002", headers=OP).json()["responder_online"] is False


def test_responder_a_cannot_touch_responder_b():
    client, _ = api()
    a = enroll(client, "responder-001")
    b = enroll(client, "responder-002")
    session_b = ring(client, "responder-002", b)
    other = f"{BASE}/responder-002"
    assert client.post(f"{other}/heartbeat", headers=a).status_code == 403            # can't read B's offer
    assert client.post(f"{other}/answer", headers=a, json={"session_id": session_b, "sdp": ANSWER}).status_code == 403
    assert client.post(f"{other}/hangup", headers=a, json={"session_id": session_b}).status_code == 403
    assert client.get(other, headers=OP).json()["session"]["state"] == "ringing"
    # and the token can't be re-pointed at B by editing it: the MAC covers the channel
    forged = a["Authorization"].replace("responder-001", "responder-002")
    assert client.post(f"{other}/heartbeat", headers={"Authorization": forged}).status_code == 401


def test_unauthorized_heartbeats_neither_create_channels_nor_refresh_presence():
    client, _ = api()
    relay: TalkRelay = client.app.state.talk_relay
    for n in range(MAX_CHANNELS + 5):
        assert client.post(f"{BASE}/x-{n}/heartbeat").status_code == 401
    assert relay.channels == {}
    phone = enroll(client, "responder-001")
    client.post(f"{BASE}/responder-001/heartbeat", headers=phone)
    seen = relay.channels["responder-001"].seen_at
    relay.clock = lambda: seen + 5
    client.post(f"{BASE}/responder-001/heartbeat", headers=bearer("nope"))
    assert relay.channels["responder-001"].seen_at == seen and len(relay.channels) == 1


def test_unauthorized_requests_cannot_replace_or_end_a_call():
    client, _ = api()
    phone = enroll(client, "responder-001")
    session_id = ring(client, "responder-001", phone)
    assert client.post(f"{BASE}/responder-001/offer", json={"sdp": "v=0 attacker"}).status_code == 401
    assert client.post(f"{BASE}/responder-001/hangup", json={"session_id": session_id, "by": "operator"}).status_code == 401
    session = client.post(f"{BASE}/responder-001/heartbeat", headers=phone).json()["session"]
    assert (session["session_id"], session["state"], session["offer"]) == (session_id, "ringing", OFFER)


def test_turn_credentials_are_only_given_to_signed_in_participants():
    client, _ = api()
    assert client.get(ICE).status_code == 401
    assert client.get(ICE, headers=bearer("guess")).status_code == 401


# --- fail closed, expiry, revocation ------------------------------------------------------

@pytest.mark.parametrize("operator_token", [None, "", "too-short", "rt1." + "x" * 40])
def test_without_a_strong_operator_secret_the_talk_link_refuses_everything(operator_token):
    values = {} if operator_token is None else {"AEGIS_TALK_OPERATOR_TOKEN": operator_token}
    auth = TalkAuth.from_values(values)
    assert not auth.configured and auth.principal(operator_token) is None
    client, _ = api()
    client.app.state.talk_auth = auth
    for method, url, body in every_route("responder-001"):
        response = client.request(method, url, headers=bearer(operator_token or "x"), json=body)
        assert response.status_code == 503, (method, url)
    del client.app.state.talk_auth
    assert client.get(f"{BASE}/responder-001", headers=OP).status_code == 503


def test_settings_come_from_the_environment_and_secrets_stay_out_of_repr(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"AEGIS_TALK_OPERATOR_TOKEN={OPERATOR_TOKEN}\nAEGIS_TALK_RESPONDER_KEY={'k' * 40}\n"
                        "AEGIS_TALK_RESPONDER_TTL_S=5\n", encoding="utf-8")
    auth = talk_auth_from_env({}, env_file=env_file)
    assert auth.configured and auth.ttl_s == MIN_TTL_S and auth.responder_key == b"k" * 40
    assert OPERATOR_TOKEN not in repr(auth) and "k" * 40 not in repr(auth)
    # the same key on a restarted backend keeps phones signed in; a random key does not
    token, _ = auth.issue("responder-001")
    assert talk_auth_from_env({}, env_file=env_file).principal(token).channel == "responder-001"
    assert talk_auth_from_env({"AEGIS_TALK_OPERATOR_TOKEN": OPERATOR_TOKEN}, env_file=tmp_path / "none").principal(token) is None


def test_responder_tokens_expire():
    client, wall = api(ttl_s=600)
    phone = enroll(client, "responder-001")
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=phone).status_code == 200
    wall.now += 600
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=phone).status_code == 401
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=enroll(client, "responder-001")).status_code == 200


def test_revoking_a_channel_signs_out_its_phones_and_ends_the_call():
    client, _ = api()
    old = enroll(client, "responder-001")
    other = enroll(client, "responder-002")
    session_id = ring(client, "responder-001", old)
    assert client.post(f"{BASE}/responder-001/revoke", headers=OP).status_code == 204
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=old).status_code == 401
    assert client.post(f"{BASE}/responder-001/answer", headers=old,
                       json={"session_id": session_id, "sdp": ANSWER}).status_code == 401
    view = client.get(f"{BASE}/responder-001", headers=OP).json()
    assert view["responder_online"] is False and view["session"]["state"] == "ended"
    assert client.post(f"{BASE}/responder-002/heartbeat", headers=other).status_code == 200   # others unaffected
    fresh = enroll(client, "responder-001")
    assert client.post(f"{BASE}/responder-001/heartbeat", headers=fresh).status_code == 200


# --- sessions -----------------------------------------------------------------------------

def test_ended_and_replaced_sessions_cannot_be_resumed():
    client, _ = api()
    phone = enroll(client, "responder-001")
    first = ring(client, "responder-001", phone)
    client.post(f"{BASE}/responder-001/hangup", headers=OP, json={"session_id": first})
    answer = {"session_id": first, "sdp": ANSWER}
    assert client.post(f"{BASE}/responder-001/answer", headers=phone, json=answer).status_code == 409
    assert client.post(f"{BASE}/responder-001/hangup", headers=phone, json={"session_id": first}).status_code == 409
    second = ring(client, "responder-001", phone)
    assert second != first
    assert client.post(f"{BASE}/responder-001/answer", headers=phone, json=answer).status_code == 409
    assert client.get(f"{BASE}/responder-001", headers=OP).json()["session"]["state"] == "ringing"


def test_input_validation_still_applies_to_signed_in_callers():
    client, _ = api()
    phone = enroll(client, "responder-001")
    assert client.post(f"{BASE}/responder-001/offer", headers=OP, json={"sdp": "short"}).status_code == 422
    assert client.post(f"{BASE}/responder-001/answer", headers=phone, json={"session_id": "", "sdp": ANSWER}).status_code == 422
    assert client.post(f"{BASE}/responder-001/answer", headers=phone,
                       json={"session_id": "a", "sdp": ANSWER, "by": "operator"}).status_code == 422
    assert client.post(f"{BASE}/Bad_Id/enroll", headers=OP).status_code == 422


def test_concurrent_calls_and_answers_leave_one_consistent_session():
    client, _ = api()
    phone = enroll(client, "responder-001")
    errors: list[int] = []

    def operator_side():
        for _ in range(25):
            r = client.post(f"{BASE}/responder-001/offer", headers=OP, json={"sdp": OFFER})
            errors.extend([r.status_code] if r.status_code != 200 else [])

    def phone_side():
        for _ in range(25):
            session = client.post(f"{BASE}/responder-001/heartbeat", headers=phone).json()["session"]
            if session and session["state"] == "ringing":
                r = client.post(f"{BASE}/responder-001/answer", headers=phone,
                                json={"session_id": session["session_id"], "sdp": ANSWER})
                errors.extend([r.status_code] if r.status_code not in (200, 409) else [])

    threads = [threading.Thread(target=f) for f in (operator_side, operator_side, phone_side, phone_side)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    session = client.app.state.talk_relay.channels["responder-001"].session
    assert session.state in ("ringing", "connected") and session.offer == OFFER
    assert (session.answer is None) == (session.state == "ringing")
