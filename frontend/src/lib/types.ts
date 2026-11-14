// Mirrors backend/app/models.py and backend/app/simulation/models.py (contract v1.0).
// Keep field names identical to the backend; do not rename.

export type ISODate = string;

export interface Position {
  frame_id: string;
  x_m: number;
  y_m: number;
}

export type Severity = "immediate" | "delayed" | "minor" | "expectant" | "deceased";

export interface MedicalAssessment {
  severity: Severity;
  source: "human";
  assessed_by: string;
  assessed_at: ISODate;
}

export interface Casualty {
  id: string;
  position: Position;
  /** null means UNASSESSED — never low severity. */
  medical_assessment: MedicalAssessment | null;
}

export type ResponderStatus = "available" | "busy" | "offline";

export interface Responder {
  id: string;
  display_name: string;
  position: Position;
  status: ResponderStatus;
  capabilities?: string[];
}

export interface CVTrack {
  track_id: string;
  state: "visible" | "occluded" | "lost";
  first_seen_at: ISODate;
  last_seen_at: ISODate;
}

/** aegis-cv-signs/0.1 — advisory camera measurements, never clinical findings. */
export interface CameraSign<V extends string> {
  value: V;
  confidence: number;
  reason?: string | null;
}
export type BodyRegion = "head" | "torso" | "left_arm" | "right_arm" | "left_leg" | "right_leg";
export interface CameraSigns {
  schema: "aegis-cv-signs/0.1";
  window_s: number;
  posture: CameraSign<"standing" | "sitting" | "lying" | "unknown">;
  movement: CameraSign<"moving" | "still" | "not_testable"> & { still_for_s?: number | null };
  response: CameraSign<"responded" | "no_response" | "not_tested"> & { command_at?: ISODate | null };
  eyes?: CameraSign<"open" | "closed" | "not_testable"> | null;
  breathing?: (CameraSign<"motion_detected" | "no_motion_detected" | "not_testable"> & { rate_bpm?: number | null }) | null;
  visible_blood?:
    | (CameraSign<"detected" | "not_detected" | "not_testable"> & { regions?: BodyRegion[] | null; method?: "color" | "model" | null })
    | null;
  camera_motion?: { value: "still" | "steady" | "moving" | "unknown"; speed_px_s?: number | null } | null;
}

export interface CVObservation {
  id: string;
  source: "cv";
  observed_at: ISODate;
  position: Position;
  kind: "person_detected";
  confidence: number;
  track?: CVTrack | null;
  signs?: CameraSigns | null;
}

export interface Obstacle {
  id: string;
  vertices: Position[];
}

export interface OccupancyGrid {
  origin: Position;
  resolution_m: number;
  width: number;
  height: number;
  /** row-major, rows advance in +y. -1 unknown, 0 free, 100 occupied. */
  cells: number[];
}

export interface SceneMap {
  frame_id: string;
  revision: string;
  obstacles: Obstacle[];
  occupancy_grid: OccupancyGrid | null;
}

export interface AStarRoute {
  algorithm: "astar";
  map_revision: string;
  waypoints: Position[];
  distance_m: number;
}

export interface Assignment {
  casualty_id: string;
  responder_id: string;
  route?: AStarRoute | null;
}

export interface RecommendationMetadata {
  solver_name: string;
  solver_version: string;
  generated_at: ISODate;
  objective: string;
  status: "feasible" | "partial" | "infeasible";
  unassigned_casualty_ids: string[];
  objective_value?: number | null;
}

export interface Scenario {
  schema_version: "1.0";
  id: string;
  name: string;
  synthetic: true;
  frame_id: string;
  created_at: ISODate;
  casualties: Casualty[];
  responders: Responder[];
  scene_map?: SceneMap | null;
}

export interface CameraRecord {
  mode: "simulation";
  camera_id: string;
  epoch: number;
  mapping_mode: "calibrated_world" | "predefined_zones";
  calibration_ref: string | null;
  zones: Record<string, Position> | null;
  registered_by: string;
  registered_at: ISODate;
}

export interface CameraProvenance {
  camera_id: string;
  local_observation_id: string;
  local_track_id: string;
  mapping_mode: "calibrated_world" | "predefined_zones";
  location_basis: "calibrated_world" | "zone_estimate";
  zone_id: string | null;
  calibration_ref: string | null;
}

export interface TrackRecord {
  track_id: string;
  casualty_id: string | null;
  latest_observation: CVObservation;
  latest_visible_signs?: CameraSigns | null;
  signs_observed_at?: ISODate | null;
  observation_count: number;
  camera?: CameraProvenance | null;
}

export type ProposalStatus =
  | "pending_approval"
  | "approved"
  | "rejected"
  | "expired"
  | "superseded"
  | "dispatched";

export interface SimulationProposal {
  mode: "simulation";
  id: string;
  scenario_id: string;
  epoch: number;
  revision: number;
  status: ProposalStatus;
  assignments: Assignment[];
  preserved_assignments: Assignment[];
  unassigned_casualty_ids: string[];
  reasons: Record<string, string[]>;
  rationale: string[];
  engine: string;
  runtime_ms: number;
  map_revision: string | null;
  objective_values: Record<string, number>;
  recommendation: RecommendationMetadata | null;
  location_provenance: Record<string, CameraProvenance>;
  created_at: ISODate;
  expires_at: ISODate;
  approval_id?: string | null;
  dispatch_id?: string | null;
  closed_reason?: string | null;
}

export interface SimulationApproval {
  mode: "simulation";
  id: string;
  proposal_id: string;
  proposal_revision: number;
  decision: "approve" | "reject";
  approved_by: string;
  operator_id: string;
  authenticated: false;
  decided_at: ISODate;
  expires_at: ISODate;
  status: "active" | "consumed" | "invalidated" | "expired" | "recorded";
  invalidated_reason?: string | null;
}

export interface SimulationDispatch {
  mode: "simulation";
  id: string;
  proposal_id: string;
  proposal_revision: number;
  approval_id: string;
  status: "dispatched";
  dispatched_by: string;
  dispatched_at: ISODate;
  assignment_ids: string[];
  real_device_effect: false;
}

export type AssignmentStatus = "dispatched" | "delivered" | "acknowledged" | "completed";

export interface SimulatedAssignment {
  mode: "simulation";
  id: string;
  dispatch_id: string;
  command_id: string;
  device_id: string;
  casualty_id: string;
  responder_id: string;
  route: AStarRoute | null;
  status: AssignmentStatus;
  created_at: ISODate;
  delivered_at?: ISODate | null;
  acknowledged_at?: ISODate | null;
  completed_at?: ISODate | null;
  device_event_ids: string[];
}

export interface RecommendationState {
  scene_revision: number;
  computed_at: ISODate;
  trigger: string;
  proposal_id: string | null;
  engine: string | null;
  unassigned_casualty_ids: string[];
  reasons: Record<string, string[]>;
  location_provenance: Record<string, CameraProvenance>;
  location_rationale: string[];
  error?: string | null;
}

export interface SimulationState {
  mode: "simulation";
  schema_version: "1.0";
  scenario_id: string;
  epoch: number;
  scene_revision: number;
  sequence: number;
  generated_at: ISODate;
  scenario: Scenario;
  required_capabilities: Record<string, string[]>;
  tracks: TrackRecord[];
  cameras: CameraRecord[];
  resolved_casualty_ids: string[];
  current_proposal_id: string | null;
  proposals: SimulationProposal[];
  approvals: SimulationApproval[];
  dispatches: SimulationDispatch[];
  assignments: SimulatedAssignment[];
  last_recommendation: RecommendationState | null;
}

export interface ActionResponse<R = Record<string, unknown>> {
  mode: "simulation";
  action: string;
  duplicate: boolean;
  result: R | null;
  state: SimulationState;
}

export interface EventEnvelope {
  schema_version: "1.0";
  mode: "simulation";
  event_id: string;
  type: string;
  scenario_id: string;
  occurred_at: ISODate;
  sequence: number;
  payload: Record<string, unknown>;
}

export interface EventPage {
  mode: "simulation";
  scenario_id: string;
  events: EventEnvelope[];
  latest_sequence: number;
  oldest_retained_sequence: number | null;
  gap: boolean;
}

export interface ExplanationFact {
  id: string;
  category: string;
  text: string;
}

export interface ExplanationResponse {
  mode: "simulation";
  proposal_id: string;
  proposal_revision: number;
  proposal_status: ProposalStatus;
  explanation: {
    mode: "simulation";
    source: string;
    facts: ExplanationFact[];
    fallback_reason: string | null;
  };
}


// --- Neural Shadow Advisory Intelligence Types ---

export interface ConstraintValidationSummary {
  valid: boolean;
  violation_count: number;
  violations: string[];
  checked_pairs: number;
}

export interface ModelComparisonVsOrtools {
  count_diff: number;
  priority_points_diff: number;
  distance_diff_m: number;
  matches_ortools_pairs: boolean;
}

export interface NeuralModelPrediction {
  model_name: string;
  checkpoint_file: string;
  checkpoint_digest: string;
  available: boolean;
  status: "success" | "unavailable" | "failed";
  selected_pairs: [string, string][];
  unassigned_casualty_ids: string[];
  assigned_count: number;
  priority_points: number;
  travel_distance_m: number;
  travel_cost_mm: number;
  prep_duration_ms: number;
  inference_duration_ms: number;
  decoding_duration_ms: number;
  total_duration_ms: number;
  hard_constraint_validation: ConstraintValidationSummary;
  unfiltered_invalid_proposals: number;
  comparison_vs_ortools?: ModelComparisonVsOrtools | null;
  error?: string | null;
  authorizes_dispatch: false;
}

export interface DifferingAssignment {
  casualty_id: string;
  ortools_responder_id: string | null;
  mlp_responder_id: string | null;
  gnn_responder_id: string | null;
}

export interface DisagreementAnalysis {
  models_agree_with_each_other: boolean;
  models_agree_with_ortools: boolean;
  differing_assignments: DifferingAssignment[];
  summary: string;
}

export interface OrtoolsSummary {
  engine: string;
  authoritative: true;
  assigned_count: number;
  priority_points: number;
  travel_distance_m: number;
  travel_cost_mm: number;
  selected_pairs: [string, string][];
  runtime_ms: number;
  hard_constraint_validation: ConstraintValidationSummary;
}

export type NeuralComparisonStatus =
  | "available"
  | "disabled"
  | "no_proposal"
  | "checkpoints_unavailable"
  | "inference_failed"
  | "stale";

export interface NeuralComparisonReport {
  mode: "simulation";
  schema_version: "1.0";
  scenario_id: string;
  scene_revision: number;
  epoch: number;
  proposal_id?: string | null;
  read_only: true;
  authorizes_dispatch: false;
  authoritative_engine: "ortools";
  status: NeuralComparisonStatus;
  available: boolean;
  context: "current" | "stale";
  stale_reasons: string[];
  evaluated_at: ISODate;
  message: string;
  ortools_summary?: OrtoolsSummary | null;
  mlp_prediction?: NeuralModelPrediction | null;
  gnn_prediction?: NeuralModelPrediction | null;
  disagreement_analysis?: DisagreementAnalysis | null;
  voice_summary?: string | null;
  limitations: string[];
}
