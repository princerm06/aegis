"""Talk link: relays WebRTC call setup between the operator's dashboard and a responder's phone.

Only the setup messages (SDP offer and answer, a few KB each) pass through here;
the audio goes directly between the two browsers, or through an optional TURN
relay when they can't reach each other directly. The responder's page sends a
heartbeat while it is open, so the dashboard can show the phone as online and
start a call whenever the operator presses Talk. The phone answers by itself.

In memory only, no recording, no simulation state. This is a communications aid
for the demo team, not dispatch.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path as FilePath
from typing import Annotated, Callable, Literal, Mapping

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

ENV_FILE = FilePath(__file__).resolve().parents[2] / ".env"
DEFAULT_STUN = "stun:stun.l.google.com:19302"
ONLINE_FOR_S = 6.0
MAX_CHANNELS = 16
MAX_SDP = 20_000

router = APIRouter(prefix="/api/v1/simulations/talk", tags=["talk-link"])

ChannelId = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Sdp = Annotated[str, Field(min_length=10, max_length=MAX_SDP)]


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: Sdp


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)
    sdp: Sdp


class Hangup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)
    by: Literal["operator", "responder"]


class Session(BaseModel):
    session_id: str
    state: Literal["ringing", "connected", "ended"]
    offer: str | None = None
    answer: str | None = None
    ended_by: Literal["operator", "responder"] | None = None


class ChannelView(BaseModel):
    channel: str
    responder_online: bool
    session: Session | None


@dataclass
class _Channel:
    seen_at: float = float("-inf")
    session: Session | None = None


@dataclass
class TalkRelay:
    clock: Callable[[], float] = time.monotonic
    channels: dict[str, _Channel] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _channel(self, channel: str) -> _Channel:
        if channel not in self.channels:
            if len(self.channels) >= MAX_CHANNELS:
                raise HTTPException(429, "Too many talk channels on this host")
            self.channels[channel] = _Channel()
        return self.channels[channel]

    def _view(self, channel: str, c: _Channel, for_responder: bool) -> ChannelView:
        session = c.session
        if session is not None:
            # Each side only needs the other side's description.
            hide = {"answer"} if for_responder else {"offer"}
            session = session.model_copy(update={k: None for k in hide})
        return ChannelView(channel=channel, responder_online=self.clock() - c.seen_at <= ONLINE_FOR_S, session=session)

    def view(self, channel: str) -> ChannelView:
        with self.lock:
            return self._view(channel, self.channels.get(channel) or _Channel(), for_responder=False)

    def heartbeat(self, channel: str) -> ChannelView:
        with self.lock:
            c = self._channel(channel)
            c.seen_at = self.clock()
            return self._view(channel, c, for_responder=True)

    def offer(self, channel: str, sdp: str) -> ChannelView:
        with self.lock:
            c = self._channel(channel)
            c.session = Session(session_id=uuid.uuid4().hex, state="ringing", offer=sdp)
            return self._view(channel, c, for_responder=False)

    def answer(self, channel: str, session_id: str, sdp: str) -> ChannelView:
        with self.lock:
            c = self._live(channel, session_id)
            c.session = c.session.model_copy(update={"state": "connected", "answer": sdp})
            return self._view(channel, c, for_responder=True)

    def hangup(self, channel: str, session_id: str, by: str) -> ChannelView:
        with self.lock:
            c = self._live(channel, session_id)
            c.session = c.session.model_copy(update={"state": "ended", "offer": None, "answer": None, "ended_by": by})
            return self._view(channel, c, for_responder=by == "responder")

    def _live(self, channel: str, session_id: str) -> _Channel:
        c = self.channels.get(channel)
        if c is None or c.session is None or c.session.session_id != session_id or c.session.state == "ended":
            raise HTTPException(409, "That call is no longer active")
        return c


def _relay(request: Request) -> TalkRelay:
    return request.app.state.talk_relay


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/channels/{channel}", response_model=ChannelView)
def channel_status(channel: ChannelId, request: Request, response: Response) -> ChannelView:
    """Operator side: is the phone online, and the current call (with the phone's answer)."""
    _no_store(response)
    return _relay(request).view(channel)


@router.post("/channels/{channel}/heartbeat", response_model=ChannelView)
def responder_heartbeat(channel: ChannelId, request: Request, response: Response) -> ChannelView:
    """Phone side, every ~1.5 s while the page is open: marks it online and returns any call (with the offer)."""
    _no_store(response)
    return _relay(request).heartbeat(channel)


@router.post("/channels/{channel}/offer", response_model=ChannelView)
def start_call(channel: ChannelId, body: Offer, request: Request, response: Response) -> ChannelView:
    """Operator presses Talk; replaces any earlier call on this channel."""
    _no_store(response)
    return _relay(request).offer(channel, body.sdp)


@router.post("/channels/{channel}/answer", response_model=ChannelView)
def answer_call(channel: ChannelId, body: Answer, request: Request, response: Response) -> ChannelView:
    _no_store(response)
    return _relay(request).answer(channel, body.session_id, body.sdp)


@router.post("/channels/{channel}/hangup", response_model=ChannelView)
def hang_up(channel: ChannelId, body: Hangup, request: Request, response: Response) -> ChannelView:
    _no_store(response)
    return _relay(request).hangup(channel, body.session_id, body.by)


class IceServer(BaseModel):
    urls: list[str]
    username: str | None = None
    credential: str | None = None


class IceConfig(BaseModel):
    ice_servers: list[IceServer]
    ice_transport_policy: Literal["all", "relay"]


def read_env_file(path: FilePath) -> dict[str, str]:
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


def ice_config(environ: Mapping[str, str] | None = None, env_file: FilePath = ENV_FILE) -> IceConfig:
    """STUN by default. With AEGIS_TALK_TURN_URLS (comma-separated ``turn:host:port``), a TURN relay
    carries the audio when the two devices can't reach each other; AEGIS_TALK_FORCE_RELAY=1 always uses it."""
    values = {**read_env_file(env_file), **(os.environ if environ is None else environ)}
    servers = [IceServer(urls=[DEFAULT_STUN])]
    turn = [u.strip() for u in values.get("AEGIS_TALK_TURN_URLS", "").split(",") if u.strip().startswith(("turn:", "turns:"))]
    if turn:
        servers.append(IceServer(urls=turn, username=values.get("AEGIS_TALK_TURN_USERNAME") or None,
                                 credential=values.get("AEGIS_TALK_TURN_CREDENTIAL") or None))
    force = values.get("AEGIS_TALK_FORCE_RELAY", "").strip().lower() in {"1", "true", "yes"}
    return IceConfig(ice_servers=servers, ice_transport_policy="relay" if turn and force else "all")


@router.get("/ice-servers", response_model=IceConfig)
def ice_servers(request: Request, response: Response) -> IceConfig:
    """Where both browsers look for a network path; the TURN password is meant for team devices on the tailnet."""
    _no_store(response)
    config = getattr(request.app.state, "talk_ice", None)
    return config if config is not None else ice_config()
