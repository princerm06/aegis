import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from backend.app.api.responder_calls import CallConfig, parse_phones, read_env_file
from backend.app.main import create_app

ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_API_KEY_SID": "SK" + "1" * 32,
    "TWILIO_API_KEY_SECRET": "test-secret",
    "TWILIO_TWIML_APP_SID": "AP" + "2" * 32,
    "AEGIS_RESPONDER_PHONES": "responder-001=+15551234567, responder-002=+1 555 765-4321, responder-003=12345",
}


def client(config: CallConfig) -> TestClient:
    app = create_app()
    app.state.responder_calls = config
    return TestClient(app)


def decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_unconfigured_host_says_so_and_issues_no_token(tmp_path):
    api = client(CallConfig.load({}, env_file=tmp_path / "missing.env"))
    status = api.get("/api/v1/simulations/responder-calls").json()
    assert status["configured"] is False and status["responders"] == []
    assert api.post("/api/v1/simulations/responder-calls/responder-001/token").status_code == 503


def test_status_lists_callable_responders_without_their_numbers(tmp_path):
    api = client(CallConfig.load(ENV, env_file=tmp_path / "missing.env"))
    response = api.get("/api/v1/simulations/responder-calls")
    assert response.json()["configured"] is True
    assert response.json()["responders"] == [
        {"responder_id": "responder-001", "number_hint": "•••• 4567"},
        {"responder_id": "responder-002", "number_hint": "•••• 4321"},     # spaces and dashes accepted
    ]                                                                       # responder-003: not E.164, ignored
    assert "5551234567" not in response.text and response.headers["cache-control"] == "no-store"
    assert api.post("/api/v1/simulations/responder-calls/responder-009/token").status_code == 404


def test_token_is_a_signed_twilio_voice_grant_for_that_responder_only(tmp_path):
    api = client(CallConfig.load(ENV, env_file=tmp_path / "missing.env"))
    body = api.post("/api/v1/simulations/responder-calls/responder-001/token").json()
    header, payload, signature = body["token"].split(".")
    expected = hmac.new(b"test-secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64encode(expected).rstrip(b"=").decode() == signature
    assert decode(header) == {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
    claims = decode(payload)
    assert (claims["iss"], claims["sub"]) == (ENV["TWILIO_API_KEY_SID"], ENV["TWILIO_ACCOUNT_SID"])
    assert claims["exp"] - claims["nbf"] == body["expires_in_s"] == 600
    assert claims["grants"]["voice"] == {"outgoing": {"application_sid": ENV["TWILIO_TWIML_APP_SID"],
                                                      "params": {"ResponderNumber": "+15551234567"}}}
    assert "incoming" not in claims["grants"]["voice"]                     # the browser can't be called
    assert body["number_hint"] == "•••• 4567" and "test-secret" not in json.dumps(body)


def test_env_file_is_read_and_the_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport TWILIO_ACCOUNT_SID='ACfile'\nTWILIO_API_KEY_SID=SKfile\n"
                        "AEGIS_RESPONDER_PHONES=responder-001=+15550000000\n", encoding="utf-8")
    assert read_env_file(env_file)["TWILIO_ACCOUNT_SID"] == "ACfile"
    config = CallConfig.load({"TWILIO_API_KEY_SID": "SKenv"}, env_file=env_file)
    assert (config.account_sid, config.api_key_sid, config.configured) == ("ACfile", "SKenv", False)
    assert config.phones == {"responder-001": "+15550000000"}
    assert parse_phones("bad,=+15550000000,responder-001=+0123") == {}
