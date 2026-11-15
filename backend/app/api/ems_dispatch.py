from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/v1/simulations/advisories", tags=["historical-ems-advisory"])


class EMSAdvisoryResponse(BaseModel):
    status: Literal["disabled", "available", "unavailable"]
    mode: Literal["simulation_advisory"]
    read_only: Literal[True]
    prediction_seconds: float | None = Field(default=None, ge=0)
    target: Literal["dispatch_response_seconds_qy"]
    model_id: str | None
    model_sha256: str | None
    provenance: dict | None
    benchmark: dict | None
    warnings: list[str]
    error_code: str | None
    distinction: dict[str, str]
    limitations: list[str]


@router.get("/ems-dispatch", response_model=EMSAdvisoryResponse)
def historical_dispatch_advisory(
    request: Request, response: Response,
    incident_datetime: datetime | None = Query(default=None, description="Creation time; naive timestamps are NYC local time; aware timestamps convert to NYC."),
    initial_call_type: str | None = Query(default=None, min_length=1, max_length=64),
    initial_severity_level_code: str | None = Query(default=None, min_length=1, max_length=16),
    borough: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict:
    result = request.app.state.ems_dispatch_advisory.evaluate(
        incident_datetime, initial_call_type, initial_severity_level_code, borough)
    if result["status"] == "unavailable":
        response.status_code = 503
    response.headers["Cache-Control"] = "no-store"
    return result
