"""Read-only AEGIS simulation API client with strict HTTP method and route allowlist.

Safety guarantee:
- Only HTTP ``GET`` is permitted. Any other method raises ``ReadOnlyViolationError``
  before any network or transport call can occur.
- Only explicitly allowlisted read-only paths may be requested. State-mutating
  routes (reset, observations, associations, assessment, decision, dispatches,
  replan, device-events, etc.) are structurally blocked at the client layer.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from backend.app.simulation.models import EventPage, IntelligenceSnapshot, SimulationState

# Callable(method, path, query_params) -> (status_code, parsed_json_body)
HttpTransport = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]

ALLOWLISTED_GET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/health$"),
    re.compile(r"^/api/v1/scenarios/[A-Za-z0-9_.-]+$"),
    re.compile(r"^/api/v1/simulations/[A-Za-z0-9_.-]+/state$"),
    re.compile(r"^/api/v1/simulations/[A-Za-z0-9_.-]+/events$"),
    re.compile(r"^/api/v1/simulations/[A-Za-z0-9_.-]+/intelligence$"),
    re.compile(r"^/api/v1/simulations/[A-Za-z0-9_.-]+/proposals/[A-Za-z0-9_.-]+/explanation$"),
)

MAX_RESPONSE_BYTES = 2_000_000


class ReadOnlyViolationError(PermissionError):
    """Raised if any code attempts a non-GET or non-allowlisted backend request."""


class BackendUnavailableError(RuntimeError):
    """Raised when the AEGIS simulation backend cannot be reached or returns an error."""

    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def validate_readonly_request(method: str, path: str) -> None:
    """Enforce that `method` is GET and `path` matches the strict read-only allowlist."""
    normalized_method = method.strip().upper()
    if normalized_method != "GET":
        raise ReadOnlyViolationError(
            f"AEGIS Voice is strictly read-only; HTTP method {normalized_method!r} is prohibited."
        )
    clean_path = path.split("?", 1)[0]
    if not any(pattern.match(clean_path) for pattern in ALLOWLISTED_GET_PATTERNS):
        raise ReadOnlyViolationError(
            f"AEGIS Voice path {clean_path!r} is not in the read-only allowlist."
        )


def _urllib_transport(
    base_url: str, timeout_seconds: float
) -> HttpTransport:
    base = base_url.rstrip("/")

    def call(method: str, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        validate_readonly_request(method, path)
        url = f"{base}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise BackendUnavailableError("Backend response exceeded maximum size limit.")
                return response.status, json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            detail = f"Backend returned HTTP {exc.code}"
            try:
                err_body = json.loads(exc.read(16384).decode("utf-8"))
                if isinstance(err_body, dict) and "detail" in err_body:
                    detail = f"{detail}: {err_body['detail']}"
            except Exception:
                pass
            raise BackendUnavailableError(detail, status_code=exc.code) from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise BackendUnavailableError(
                f"Cannot reach AEGIS simulation backend at {base}: {exc}"
            ) from None

    return call


class ReadOnlyAegisClient:
    """Read-only client for authoritative AEGIS simulation state, events, and explanations."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout_seconds: float = 3.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or _urllib_transport(self.base_url, self.timeout_seconds)

    def request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Execute a verified read-only GET request."""
        validate_readonly_request(method, path)
        status, payload = self._transport("GET", path, params)
        if status != 200:
            detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
            raise BackendUnavailableError(
                f"Backend returned HTTP {status}: {detail}", status_code=status
            )
        return payload

    def get_state(self, scenario_id: str = "demo") -> SimulationState:
        raw = self.request("GET", f"/api/v1/simulations/{scenario_id}/state")
        try:
            return SimulationState.model_validate(raw)
        except ValidationError as exc:
            raise BackendUnavailableError(
                f"Invalid SimulationState payload from backend: {exc}"
            ) from None

    def get_events(
        self, scenario_id: str = "demo", after_sequence: int = 0, limit: int = 50
    ) -> EventPage:
        bounded_limit = max(1, min(int(limit), 200))
        raw = self.request(
            "GET",
            f"/api/v1/simulations/{scenario_id}/events",
            params={"after_sequence": max(0, int(after_sequence)), "limit": bounded_limit},
        )
        try:
            return EventPage.model_validate(raw)
        except ValidationError as exc:
            raise BackendUnavailableError(
                f"Invalid EventPage payload from backend: {exc}"
            ) from None

    def get_intelligence(self, scenario_id: str = "demo") -> IntelligenceSnapshot:
        raw = self.request("GET", f"/api/v1/simulations/{scenario_id}/intelligence")
        try:
            return IntelligenceSnapshot.model_validate(raw)
        except ValidationError as exc:
            raise BackendUnavailableError(
                f"Invalid IntelligenceSnapshot payload from backend: {exc}"
            ) from None

    def get_proposal_explanation(
        self, scenario_id: str, proposal_id: str
    ) -> dict[str, Any]:
        raw = self.request(
            "GET",
            f"/api/v1/simulations/{scenario_id}/proposals/{proposal_id}/explanation",
        )
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Invalid proposal explanation payload from backend.")
        return raw
