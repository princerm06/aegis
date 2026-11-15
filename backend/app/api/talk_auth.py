"""Who may use the talk link: one operator secret, and short-lived per-channel responder tokens.

* Operator: a shared bearer secret (``AEGIS_TALK_OPERATOR_TOKEN``, at least 32 characters)
  that the Command Center operator enters in the dashboard. Never shipped in the frontend bundle.
* Responder: ``rt1.<channel>.<generation>.<expires>.<mac>``, an HMAC-SHA256 over the channel,
  generation and expiry. Only the operator can mint one (``POST …/enroll``), it only works for
  that one channel, it expires (``AEGIS_TALK_RESPONDER_TTL_S``, default 12 h), and
  ``POST …/revoke`` bumps the channel's generation so every earlier token for it stops working.
  The signing key is ``AEGIS_TALK_RESPONDER_KEY``, or a random one per process when unset.

With no operator secret configured, the talk link refuses every request (fails closed).
Tokens are never logged or echoed in error messages.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)

PREFIX = "rt1"
MIN_SECRET = 32
DEFAULT_TTL_S = 12 * 3600
MIN_TTL_S, MAX_TTL_S = 60, 7 * 24 * 3600
MAX_TRACKED_CHANNELS = 256
_CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _canonical_int(text: str, digits: int) -> bool:
    return text.isdigit() and len(text) <= digits and str(int(text)) == text


@dataclass(frozen=True)
class Principal:
    role: Literal["operator", "responder"]
    channel: str | None = None


@dataclass
class TalkAuth:
    operator_token: str | None
    responder_key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    ttl_s: int = DEFAULT_TTL_S
    wall: Callable[[], float] = time.time
    generations: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"TalkAuth(configured={self.configured}, ttl_s={self.ttl_s})"

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> TalkAuth:
        operator = values.get("AEGIS_TALK_OPERATOR_TOKEN", "").strip() or None
        if operator is not None and (len(operator) < MIN_SECRET or operator.startswith(PREFIX + ".")):
            log.warning("Talk link disabled: AEGIS_TALK_OPERATOR_TOKEN must be at least %d characters", MIN_SECRET)
            operator = None
        elif operator is None:
            log.info("Talk link disabled: AEGIS_TALK_OPERATOR_TOKEN is not set")
        key = values.get("AEGIS_TALK_RESPONDER_KEY", "").strip()
        if key and len(key) < MIN_SECRET:
            log.warning("AEGIS_TALK_RESPONDER_KEY is shorter than %d characters; using a random per-process key", MIN_SECRET)
            key = ""
        try:
            ttl = int(values.get("AEGIS_TALK_RESPONDER_TTL_S", "") or DEFAULT_TTL_S)
        except ValueError:
            ttl = DEFAULT_TTL_S
        return cls(
            operator_token=operator,
            responder_key=key.encode() if key else secrets.token_bytes(32),
            ttl_s=min(max(ttl, MIN_TTL_S), MAX_TTL_S),
        )

    @property
    def configured(self) -> bool:
        return self.operator_token is not None

    def _mac(self, channel: str, generation: int, expires: int) -> str:
        digest = hmac.new(self.responder_key, f"{PREFIX}|{channel}|{generation}|{expires}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def issue(self, channel: str) -> tuple[str, int]:
        """A responder token for ``channel`` and its expiry (Unix seconds)."""
        with self.lock:
            generation = self._generation(channel, create=True)
        expires = int(self.wall()) + self.ttl_s
        return f"{PREFIX}.{channel}.{generation}.{expires}.{self._mac(channel, generation, expires)}", expires

    def revoke(self, channel: str) -> None:
        """Every responder token issued so far for ``channel`` stops working."""
        with self.lock:
            self.generations[channel] = self._generation(channel, create=True) + 1

    def _generation(self, channel: str, create: bool) -> int:
        if channel not in self.generations and create:
            if len(self.generations) >= MAX_TRACKED_CHANNELS:
                raise OverflowError("too many talk channels")
            self.generations[channel] = 0
        return self.generations.get(channel, 0)

    def principal(self, token: str | None) -> Principal | None:
        """Who presented ``token``, or None if it is missing, malformed, forged, expired or revoked."""
        if not self.configured or not token or len(token) > 512:
            return None
        if hmac.compare_digest(token.encode(), self.operator_token.encode()):
            return Principal("operator")
        parts = token.split(".")
        if len(parts) != 5 or parts[0] != PREFIX or not _CHANNEL.match(parts[1]):
            return None
        _, channel, generation, expires, mac = parts
        if not (_canonical_int(generation, 9) and _canonical_int(expires, 12)):
            return None
        if not hmac.compare_digest(mac.encode(), self._mac(channel, int(generation), int(expires)).encode()):
            return None
        with self.lock:
            current = self._generation(channel, create=False)
        if int(generation) != current or int(expires) <= self.wall():
            return None
        return Principal("responder", channel)
