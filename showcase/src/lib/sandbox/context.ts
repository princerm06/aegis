export const CAMERA_OBSERVATIONS = [
  { casualtyId: 'C1', personId: 'SIM-P01', camera: 'SIM-CAM / WEST', timestamp: 'T+00:12', visibility: 'Visible', posture: 'Lying', movement: 'Still' },
  { casualtyId: 'C2', personId: 'SIM-P02', camera: 'SIM-CAM / EAST', timestamp: 'T+00:10', visibility: 'Partially occluded', posture: 'Not testable', movement: 'Not testable' },
  { casualtyId: 'C3', personId: 'SIM-P03', camera: 'SIM-CAM / WEST', timestamp: 'T+00:11', visibility: 'Visible', posture: 'Standing', movement: 'Moving' },
  { casualtyId: 'C4', personId: 'SIM-P04', camera: 'SIM-CAM / EAST', timestamp: 'T+00:04', visibility: 'Lost from view', posture: 'Not testable', movement: 'Not testable' },
] as const;

export const RECORDED_CV = {
  source: 'cv/eval/signs/pexels-three-lying-overhead-20261114T065011.json',
  sha256: '2e1bbd372bc68a3dd63d5f8718ccf41363d2e73bbabd59a8d83c27e30d8f49bd',
  runAtUtc: '2026-11-14T06:50:11.433Z',
  clip: 'pexels-three-lying-overhead.mp4',
  track: 't1',
  firstLyingAtSeconds: 0,
  firstMovingAtSeconds: 1,
  firstStillAtSeconds: 2,
} as const;

export const EMS_RESEARCH = {
  source: 'ml/ems/checkpoint/report.json',
  auditSource: 'ml/ems/checkpoint/generalization_audit.json',
  sourceIncidents: 377606,
  heldOutIncidents: 67798,
  xgboostMaeSeconds: 78.38800684320292,
  temporalMaeSeconds: 82.6293474733768,
  severity8Incidents: 158,
} as const;

export const SYSTEM_STAGES = [
  ['Camera observations', 'Track people and observable signs; occlusion limits what is known.'],
  ['Human confirmation & assessment', 'An operator confirms casualties and enters priorities.'],
  ['OR-Tools optimization', 'CP-SAT produces the authoritative constraint-based proposal; A* plans routes.'],
  ['MLP/GNN advisory comparison', 'Optional trained models offer comparisons, never dispatch authority.'],
  ['Human approval', 'The operator reviews and authorizes a specific current proposal.'],
  ['Physical Arduino dispatch', 'Approved assignments reach connected responder devices.'],
  ['ACK / DONE', 'Responder acknowledgment and completion events update coordination state.'],
  ['Dynamic route updates', 'Changed conditions invalidate routes and require renewed authorization.'],
] as const;
