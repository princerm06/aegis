"""Operator-to-responder phone calls from the dashboard (Twilio Programmable Voice).

The dashboard asks for a short-lived Twilio access token for one responder and
places the call in the browser; Twilio rings that responder's real phone, so it
works with the phone locked in a pocket. The account secret never leaves this
process, and responder numbers are only ever shown masked.

Configuration (environment, or git-ignored ``backend/.env``; environment wins):

    TWILIO_ACCOUNT_SID      AC...   account
    TWILIO_API_KEY_SID      SK...   API key used to sign tokens
    TWILIO_API_KEY_SECRET           its secret
    TWILIO_TWIML_APP_SID    AP...   TwiML App whose Voice URL is a TwiML Bin that
                                    dials {{ResponderNumber}}
    AEGIS_RESPONDER_PHONES  responder-001=+15551234567,responder-002=+1...

This is a communications aid for the demo team, not dispatch: it changes no
simulation state and records nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
TOKEN_TTL_S = 600
E164 = re.compile(r"^\+[1-9]\d{6,14}$")
KEYS = ("TWILIO_ACCOUNT_SID", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "TWILIO_TWIML_APP_SID")

router = APIRouter(prefix="/api/v1/simulations/responder-calls", tags=["responder-calls"])


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines, comments and ``export`` prefixes allowed."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def parse_phones(spec: str) -> dict[str, str]:
    """``responder-001=+1555...,responder-002=+1...``; entries that are not E.164 are ignored."""
    phones = {}
    for item in spec.split(","):
        responder_id, _, number = item.partition("=")
        number = number.strip().replace(" ", "").replace("-", "")
        if responder_id.strip() and E164.match(number):
            phones[responder_id.strip()] = number
    return phones


def mask(number: str) -> str:
    return f"•••• {number[-4:]}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True)
class CallConfig:
    account_sid: str = ""
    api_key_sid: str = ""
    api_key_secret: str = ""
    twiml_app_sid: str = ""
    phones: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, environ: Mapping[str, str] | None = None, env_file: Path = ENV_FILE) -> "CallConfig":
        values = {**read_env_file(env_file), **(os.environ if environ is None else environ)}
        return cls(*(values.get(key, "").strip() for key in KEYS),
                   phones=parse_phones(values.get("AEGIS_RESPONDER_PHONES", "")))

    @property
    def configured(self) -> bool:
        return all((self.account_sid, self.api_key_sid, self.api_key_secret, self.twiml_app_sid))

    def token(self, responder_id: str, now: Callable[[], float] = time.time) -> str:
        """Twilio Voice access token (JWT, HS256) for one outgoing call to this responder."""
        issued = int(now())
        header = {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
        payload = {
            "jti": f"{self.api_key_sid}-{issued}",
            "iss": self.api_key_sid,
            "sub": self.account_sid,
            "nbf": issued,
            "exp": issued + TOKEN_TTL_S,
            "grants": {
                "identity": f"aegis-operator-{responder_id}",
                "voice": {"outgoing": {"application_sid": self.twiml_app_sid,
                                       "params": {"ResponderNumber": self.phones[responder_id]}}},
            },
        }
        signing_input = ".".join(_b64(json.dumps(part, separators=(",", ":")).encode())
                                 for part in (header, payload))
        signature = hmac.new(self.api_key_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{_b64(signature)}"


class CallableResponder(BaseModel):
    responder_id: str
    number_hint: str


class CallStatus(BaseModel):
    configured: bool
    responders: list[CallableResponder]
    note: str


class CallToken(BaseModel):
    responder_id: str
    number_hint: str
    token: str
    expires_in_s: int


def _config(request: Request) -> CallConfig:
    config = getattr(request.app.state, "responder_calls", None)
    return config if config is not None else CallConfig.load()


@router.get("", response_model=CallStatus)
def call_status(request: Request, response: Response) -> CallStatus:
    config = _config(request)
    response.headers["Cache-Control"] = "no-store"
    responders = [CallableResponder(responder_id=r, number_hint=mask(n)) for r, n in sorted(config.phones.items())]
    note = ("Calls ring the responder's real phone." if config.configured and responders
            else "Phone calls are not set up on this host (see backend README, Responder phone calls).")
    return CallStatus(configured=config.configured and bool(responders), responders=responders, note=note)


@router.post("/{responder_id}/token", response_model=CallToken)
def call_token(responder_id: str, request: Request, response: Response) -> CallToken:
    config = _config(request)
    if not config.configured:
        raise HTTPException(503, "Phone calls are not set up on this host")
    if responder_id not in config.phones:
        raise HTTPException(404, f"No phone number set for {responder_id}")
    response.headers["Cache-Control"] = "no-store"
    return CallToken(responder_id=responder_id, number_hint=mask(config.phones[responder_id]),
                     token=config.token(responder_id), expires_in_s=TOKEN_TTL_S)
