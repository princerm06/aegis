export type Priority = 'unassessed' | 'first' | 'next' | 'later';
export type Point = { x: number; y: number };
export type Casualty = { id: string; label: string; observation: string; position: Point; confirmed: boolean; priority: Priority };
export type Responder = { id: string; name: string; board: string; position: Point; available: boolean };
export type TaskStatus = 'dispatched' | 'received' | 'acknowledged' | 'blocked' | 'done' | 'replaced';
export type Assignment = { responderId: string; casualtyId: string; route: Point[] };
export type Task = Assignment & { id: string; status: TaskStatus; revision: number };
export type Plan = { revision: number; assignments: Assignment[]; warnings: string[] };
export type SandboxState = {
  revision: number; casualties: Casualty[]; responders: Responder[]; blocked: string[];
  tasks: Task[]; plan: Plan | null; approvedRevision: number | null; notice: string; showHint: boolean;
};
export type Action =
  | { type: 'dismissHint' }
  | { type: 'confirm'; id: string }
  | { type: 'priority'; id: string; priority: Priority }
  | { type: 'availability'; id: string }
  | { type: 'corridor'; id: string }
  | { type: 'recommend' }
  | { type: 'approve'; revision: number }
  | { type: 'dispatch'; revision: number }
  | { type: 'progress'; taskId: string; stage: 'received' | 'acknowledged' | 'done' }
  | { type: 'reset' };

export const WIDTH = 7;
export const HEIGHT = 5;
export const CORRIDORS = [
  { id: 'central', label: 'Central crossing', edges: [edge({ x: 2, y: 1 }, { x: 3, y: 1 }), edge({ x: 2, y: 2 }, { x: 3, y: 2 })] },
  { id: 'east', label: 'East access gates', edges: Array.from({ length: HEIGHT }, (_, row) => edge({ x: 4, y: row }, { x: 5, y: row })) },
];

function key(point: Point): string { return `${point.x},${point.y}`; }
export function edge(start: Point, end: Point): string { return [key(start), key(end)].sort().join('|'); }
export function createInitialState(): SandboxState {
  return {
    revision: 1,
    casualties: [
      { id: 'C1', label: 'Courtyard', observation: 'Synthetic person marker near the central crossing; no clinical assessment.', position: { x: 3, y: 1 }, confirmed: false, priority: 'unassessed' },
      { id: 'C2', label: 'East platform', observation: 'Synthetic person marker beyond the east access gates; access must be checked.', position: { x: 6, y: 1 }, confirmed: false, priority: 'unassessed' },
      { id: 'C3', label: 'South passage', observation: 'Synthetic person marker beside the southern alternate corridor.', position: { x: 3, y: 4 }, confirmed: false, priority: 'unassessed' },
      { id: 'C4', label: 'East assembly', observation: 'Synthetic person marker in the eastern assembly area; awaiting human confirmation.', position: { x: 6, y: 4 }, confirmed: false, priority: 'unassessed' },
    ],
    responders: [
      { id: 'alpha', name: 'Alpha', board: 'Arduino UNO R4 WiFi', position: { x: 0, y: 1 }, available: true },
      { id: 'bravo', name: 'Bravo', board: 'Arduino UNO Q', position: { x: 0, y: 3 }, available: true },
    ],
    blocked: [], tasks: [], plan: null, approvedRevision: null, showHint: true,
    notice: 'Confirm synthetic casualties and set operator priorities to begin.',
  };
}

export function shortestPath(start: Point, end: Point, blocked: string[]): Point[] | null {
  const closed = new Set(CORRIDORS.filter(corridor => blocked.includes(corridor.id)).flatMap(corridor => corridor.edges));
  const queue: Point[][] = [[start]];
  const seen = new Set([key(start)]);
  for (let index = 0; index < queue.length; index++) {
    const path = queue[index];
    const current = path[path.length - 1];
    if (key(current) === key(end)) return path;
    for (const next of [{ x: current.x + 1, y: current.y }, { x: current.x, y: current.y + 1 }, { x: current.x - 1, y: current.y }, { x: current.x, y: current.y - 1 }]) {
      if (next.x < 0 || next.x >= WIDTH || next.y < 0 || next.y >= HEIGHT || seen.has(key(next)) || closed.has(edge(current, next))) continue;
      seen.add(key(next)); queue.push([...path, next]);
    }
  }
  return null;
}

function occupies(task: Task): boolean { return task.status !== 'done' && task.status !== 'replaced'; }
function invalidate(state: SandboxState, notice: string): SandboxState {
  return { ...state, revision: state.revision + 1, plan: null, approvedRevision: null, notice };
}

export function recommend(state: SandboxState): Plan {
  const responders = state.responders.filter(responder => responder.available && !state.tasks.some(task => task.responderId === responder.id && occupies(task) && task.status !== 'blocked'));
  const eligible = state.casualties.filter(casualty => casualty.confirmed && casualty.priority !== 'unassessed' && !state.tasks.some(task => task.casualtyId === casualty.id && (task.status === 'done' || (occupies(task) && task.status !== 'blocked'))));
  let best: Assignment[] = [];
  let bestScore = [-1, -1, -1, -Infinity];
  const score = (assignments: Assignment[]) => [
    ...(['first', 'next', 'later'] as const).map(priority => assignments.filter(assignment => eligible.find(casualty => casualty.id === assignment.casualtyId)?.priority === priority).length),
    -assignments.reduce((distance, assignment) => distance + assignment.route.length - 1, 0),
  ];
  const better = (candidate: number[], previous: number[]) => {
    for (let index = 0; index < candidate.length; index++) {
      if (candidate[index] !== previous[index]) return candidate[index] > previous[index];
    }
    return false;
  };
  const enumerate = (index: number, assignments: Assignment[]) => {
    if (index === responders.length) {
      const candidateScore = score(assignments);
      if (better(candidateScore, bestScore)) { best = assignments; bestScore = candidateScore; }
      return;
    }
    const responder = responders[index];
    for (const casualty of eligible) {
      if (assignments.some(assignment => assignment.casualtyId === casualty.id)) continue;
      if (state.tasks.some(task => task.status === 'blocked' && task.casualtyId === casualty.id && task.responderId !== responder.id)) continue;
      const route = shortestPath(responder.position, casualty.position, state.blocked);
      if (route) enumerate(index + 1, [...assignments, { responderId: responder.id, casualtyId: casualty.id, route }]);
    }
    enumerate(index + 1, assignments);
  };
  enumerate(0, []);
  const warnings: string[] = [];
  if (!eligible.length) warnings.push('No eligible work: confirm a casualty and assign a priority. Completed and active work are excluded.');
  if (!responders.length) warnings.push('No available virtual responders. Busy responders cannot be reused.');
  for (const casualty of eligible) {
    if (responders.length && !responders.some(responder => shortestPath(responder.position, casualty.position, state.blocked))) warnings.push(`${casualty.id}: no feasible route from an available responder.`);
  }
  if (state.tasks.some(task => task.status === 'blocked')) warnings.push('Blocked tasks retain their responder until an approved replacement dispatch for that responder. No automatic rerouting.');
  return { revision: state.revision, assignments: best, warnings };
}

export function sandboxReducer(state: SandboxState, action: Action): SandboxState {
  if (action.type === 'reset') return createInitialState();
  if (action.type === 'dismissHint') return { ...state, showHint: false };
  if (action.type === 'confirm') {
    if (!state.casualties.some(casualty => casualty.id === action.id)) return state;
    return invalidate({ ...state, casualties: state.casualties.map(casualty => casualty.id === action.id ? { ...casualty, confirmed: !casualty.confirmed } : casualty) }, 'Confirmation changed. Previous recommendation and approval invalidated.');
  }
  if (action.type === 'priority') {
    if (!['unassessed', 'first', 'next', 'later'].includes(action.priority) || !state.casualties.some(casualty => casualty.id === action.id)) return state;
    return invalidate({ ...state, casualties: state.casualties.map(casualty => casualty.id === action.id ? { ...casualty, priority: action.priority } : casualty) }, 'Priority changed. Generate and authorize a new revision.');
  }
  if (action.type === 'availability') {
    if (!state.responders.some(responder => responder.id === action.id)) return state;
    return invalidate({ ...state, responders: state.responders.map(responder => responder.id === action.id ? { ...responder, available: !responder.available } : responder) }, 'Availability changed. Active tasks remain reserved; recommendation invalidated.');
  }
  if (action.type === 'corridor') {
    if (!CORRIDORS.some(corridor => corridor.id === action.id)) return state;
    const blocked = state.blocked.includes(action.id) ? state.blocked.filter(id => id !== action.id) : [...state.blocked, action.id];
    const closed = new Set(CORRIDORS.filter(corridor => blocked.includes(corridor.id)).flatMap(corridor => corridor.edges));
    const tasks = state.tasks.map(task => occupies(task) && task.route.some((point, index) => index > 0 && closed.has(edge(task.route[index - 1], point))) ? { ...task, status: 'blocked' as const } : task);
    return invalidate({ ...state, blocked, tasks }, 'Map changed. Affected tasks are held, not rerouted. Generate, approve, then dispatch replacements.');
  }
  if (action.type === 'recommend') return { ...state, plan: recommend(state), approvedRevision: null, notice: 'Advisory only. Review revision, approve it, then explicitly dispatch.' };
  if (action.type === 'approve') {
    if (!state.plan?.assignments.length || action.revision !== state.revision || state.plan.revision !== action.revision) return state;
    return { ...state, approvedRevision: action.revision, notice: `Revision ${action.revision} authorized. Nothing dispatched yet.` };
  }
  if (action.type === 'dispatch') {
    if (!state.plan?.assignments.length || action.revision !== state.revision || state.approvedRevision !== action.revision || state.plan.revision !== action.revision) return state;
    const assignments = state.plan.assignments;
    const tasks = state.tasks.map(task => task.status === 'blocked' && assignments.some(assignment => assignment.responderId === task.responderId) ? { ...task, status: 'replaced' as const } : task);
    return invalidate({ ...state, tasks: [...tasks, ...assignments.map((assignment, index) => ({ ...assignment, id: `R${state.revision}-${index + 1}`, revision: state.revision, status: 'dispatched' as const }))] }, 'Virtual assignments dispatched. Simulate receipt, ACK, and DONE individually.');
  }
  if (action.type === 'progress') {
    const task = state.tasks.find(candidate => candidate.id === action.taskId);
    const previous = { received: 'dispatched', acknowledged: 'received', done: 'acknowledged' };
    if (!task || previous[action.stage] !== task.status) return state;
    const responders = action.stage === 'done' ? state.responders.map(responder => responder.id === task.responderId ? { ...responder, position: { ...task.route[task.route.length - 1] } } : responder) : state.responders;
    return invalidate({ ...state, responders, tasks: state.tasks.map(candidate => candidate.id === task.id ? { ...candidate, status: action.stage } : candidate) }, `${task.id}: virtual ${action.stage === 'done' ? 'DONE (task closed, not a medical outcome)' : action.stage}.`);
  }
  return state;
}
