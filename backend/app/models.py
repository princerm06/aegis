"""Typed v1 payloads; reserved schemas do not enable integration features."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_serializer, model_validator


Identifier = Annotated[str, Field(min_length=1)]
FiniteCoordinate = Annotated[float, Field(allow_inf_nan=False)]
NonnegativeNumber = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveInteger = Annotated[int, Field(gt=0, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(ContractModel):
    frame_id: Identifier
    x_m: FiniteCoordinate
    y_m: FiniteCoordinate


class MedicalAssessment(ContractModel):
    severity: Literal["immediate", "delayed", "minor", "expectant", "deceased"]
    source: Literal["human"]
    assessed_by: Identifier
    assessed_at: AwareDatetime


class Casualty(ContractModel):
    id: Identifier
    position: Position
    medical_assessment: MedicalAssessment | None


class Responder(ContractModel):
    id: Identifier
    display_name: Identifier
    position: Position
    status: Literal["available", "busy", "offline"]
    capabilities: list[Identifier] = Field(default_factory=list)


class CVTrack(ContractModel):
    track_id: Identifier
    state: Literal["visible", "occluded", "lost"]
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "CVTrack":
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("first_seen_at must not follow last_seen_at")
        return self


# Camera measurements are advisory evidence, never clinical diagnoses or priorities.
SignConfidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
SignDuration = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class CameraSign(ContractModel):
    value: str
    confidence: SignConfidence
    reason: str | None = None


class PostureSign(CameraSign):
    value: Literal["standing", "sitting", "lying", "unknown"]


class MovementSign(CameraSign):
    value: Literal["moving", "still", "not_testable"]
    still_for_s: SignDuration | None = None


class ResponseSign(CameraSign):
    value: Literal["responded", "no_response", "not_tested"]
    command_at: AwareDatetime | None = None


class EyesSign(CameraSign):
    value: Literal["open", "closed", "not_testable"]


class BreathingMotionSign(CameraSign):
    value: Literal["motion_detected", "no_motion_detected", "not_testable"]
    rate_bpm: Annotated[float, Field(gt=0, le=40, allow_inf_nan=False)] | None = None


class VisibleColorSign(CameraSign):
    value: Literal["detected", "not_detected", "not_testable"]
    regions: list[Literal["head", "torso", "left_arm", "right_arm", "left_leg", "right_leg"]] | None = None
    method: Literal["color", "model"] | None = None


class CameraMotion(ContractModel):
    value: Literal["still", "steady", "moving", "unknown"]
    speed_px_s: SignDuration | None = None


class CameraSigns(ContractModel):
    schema_version: Literal["aegis-cv-signs/0.1"] = Field(alias="schema")
    window_s: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)]
    posture: PostureSign
    movement: MovementSign
    response: ResponseSign
    eyes: EyesSign | None = None
    breathing: BreathingMotionSign | None = None
    visible_blood: VisibleColorSign | None = None
    camera_motion: CameraMotion | None = None

    @model_serializer(mode="wrap")
    def serialize_wire_schema(self, handler):
        # Preserve the CV producer's external "schema" key in nested snapshots/events.
        values = handler(self)
        if "schema_version" in values:
            values["schema"] = values.pop("schema_version")
        return values

    @model_validator(mode="after")
    def validate_observed_measurements(self) -> "CameraSigns":
        if self.breathing is not None and self.breathing.rate_bpm is not None and self.breathing.value != "motion_detected":
            raise ValueError("rate_bpm is only permitted for detected chest motion")
        if self.visible_blood is not None and self.visible_blood.regions and self.visible_blood.value != "detected":
            raise ValueError("regions require detected visible color")
        return self


class CVObservation(ContractModel):
    id: Identifier
    source: Literal["cv"]
    observed_at: AwareDatetime
    position: Position
    kind: Literal["person_detected"]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    track: CVTrack | None = None
    signs: CameraSigns | None = None

    @model_validator(mode="after")
    def validate_track_time(self) -> "CVObservation":
        if self.track is not None and self.track.last_seen_at > self.observed_at:
            raise ValueError("last_seen_at must not follow observed_at")
        if self.signs is not None and (self.track is None or self.track.state != "visible"):
            raise ValueError("signs are only valid on visible tracked observations")
        return self


class Obstacle(ContractModel):
    id: Identifier
    vertices: Annotated[list[Position], Field(min_length=3)]


class OccupancyGrid(ContractModel):
    origin: Position
    resolution_m: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    width: PositiveInteger
    height: PositiveInteger
    cells: list[Annotated[int, Field(strict=True)]]

    @model_validator(mode="after")
    def validate_cell_count(self) -> "OccupancyGrid":
        if len(self.cells) != self.width * self.height:
            raise ValueError("cells must contain width * height entries")
        if any(cell not in {-1, 0, 100} for cell in self.cells):
            raise ValueError("cells must contain only -1, 0, or 100")
        return self


class SceneMap(ContractModel):
    frame_id: Identifier
    revision: Identifier
    obstacles: list[Obstacle]
    occupancy_grid: OccupancyGrid | None

    @model_validator(mode="after")
    def validate_map(self) -> "SceneMap":
        identifiers = [obstacle.id for obstacle in self.obstacles]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Obstacle IDs must be unique")
        positions = [vertex for obstacle in self.obstacles for vertex in obstacle.vertices]
        if self.occupancy_grid is not None:
            positions.append(self.occupancy_grid.origin)
        if any(position.frame_id != self.frame_id for position in positions):
            raise ValueError("Map positions must use the map coordinate frame")
        return self


class AStarRoute(ContractModel):
    algorithm: Literal["astar"]
    map_revision: Identifier
    waypoints: Annotated[list[Position], Field(min_length=2)]
    distance_m: NonnegativeNumber

    @model_validator(mode="after")
    def validate_route_frame(self) -> "AStarRoute":
        if any(point.frame_id != self.waypoints[0].frame_id for point in self.waypoints):
            raise ValueError("Route waypoints must use one coordinate frame")
        return self


class RecommendationMetadata(ContractModel):
    solver_name: Identifier
    solver_version: Identifier
    generated_at: AwareDatetime
    objective: Identifier
    status: Literal["feasible", "partial", "infeasible"]
    unassigned_casualty_ids: list[Identifier]
    objective_value: FiniteCoordinate | None = None


class Assignment(ContractModel):
    casualty_id: Identifier
    responder_id: Identifier
    route: AStarRoute | None = None


class DispatchProposal(ContractModel):
    id: Identifier
    scenario_id: Identifier
    revision: PositiveInteger
    status: Literal["pending_approval", "approved", "rejected", "expired"]
    assignments: list[Assignment]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    recommendation: RecommendationMetadata | None = None


class DeviceAssignmentEvent(ContractModel):
    id: Identifier
    scenario_id: Identifier
    device_id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    kind: Literal["delivered", "acknowledged", "completed"]
    occurred_at: AwareDatetime
    mode: Literal["simulation", "real"]


class Scenario(ContractModel):
    schema_version: Literal["1.0"]
    id: Identifier
    name: Identifier
    synthetic: Literal[True]
    frame_id: Identifier
    created_at: AwareDatetime
    casualties: list[Casualty]
    responders: list[Responder]
    scene_map: SceneMap | None = None

    @model_validator(mode="after")
    def validate_entities(self) -> "Scenario":
        entities = [*self.casualties, *self.responders]
        identifiers = [entity.id for entity in entities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scenario entity IDs must be unique")
        if any(entity.position.frame_id != self.frame_id for entity in entities):
            raise ValueError("All positions must use the scenario coordinate frame")
        if self.scene_map is not None and self.scene_map.frame_id != self.frame_id:
            raise ValueError("Scene map must use the scenario coordinate frame")
        return self


class HealthResponse(ContractModel):
    status: Literal["ok"]
    service: Literal["aegis-backend"]
    version: Literal["0.1.0"]
