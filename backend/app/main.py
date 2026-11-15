import os
from pathlib import Path

from fastapi import FastAPI

from backend.app.api.ems_dispatch import router as ems_dispatch_router
from backend.app.api.routes import router
from backend.app.services.ems_dispatch import DEFAULT_CHECKPOINT, EMSDispatchAdvisory
from backend.app.simulation.routes import router as simulation_router
from backend.app.simulation.store import SimulationStore


def create_app() -> FastAPI:
    application = FastAPI(
        title="AEGIS Development API",
        version="0.1.0",
        description="Synthetic incident coordination. Simulation-only actions; no real dispatch or clinical inference.",
    )
    application.include_router(router)
    application.state.simulation_store = SimulationStore()
    application.include_router(simulation_router)
    application.state.ems_dispatch_advisory = EMSDispatchAdvisory(
        enabled=os.getenv("AEGIS_EMS_ADVISORY_ENABLED", "false").lower() == "true",
        checkpoint=Path(os.getenv("AEGIS_EMS_MODEL_DIR", str(DEFAULT_CHECKPOINT))),
    )
    application.include_router(ems_dispatch_router)
    return application


app = create_app()
