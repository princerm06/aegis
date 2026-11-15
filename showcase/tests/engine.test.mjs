import test from 'node:test';
import assert from 'node:assert/strict';
import { CORRIDORS, createInitialState, edge, recommend, sandboxReducer, shortestPath } from '../src/lib/sandbox/engine.ts';

const reduce = (state, ...actions) => actions.reduce(sandboxReducer, state);
const review = (state, id, priority = 'first') => reduce(state, { type: 'confirm', id }, { type: 'priority', id, priority });
const authorize = state => {
  state = sandboxReducer(state, { type: 'recommend' });
  return sandboxReducer(state, { type: 'approve', revision: state.revision });
};
const send = state => {
  state = authorize(state);
  return sandboxReducer(state, { type: 'dispatch', revision: state.revision });
};
const finish = (state, taskId) => reduce(state, ...['received', 'acknowledged', 'done'].map(stage => ({ type: 'progress', taskId, stage })));

test('initial states and reset produce independently owned nested objects', () => {
  const first = createInitialState();
  const second = createInitialState();
  first.casualties[0].position.x = 999;
  first.responders[0].available = false;
  first.blocked.push('east');
  assert.equal(second.casualties[0].position.x, 3);
  assert.equal(second.responders[0].available, true);
  assert.deepEqual(second.blocked, []);
  assert.deepEqual(sandboxReducer(first, { type: 'reset' }), second);
  assert.notEqual(sandboxReducer(first, { type: 'reset' }).casualties, second.casualties);
  assert.ok(second.casualties.every(casualty => !casualty.confirmed && casualty.priority === 'unassessed'));
});

test('hint dismissal is local reducer state and reset restores onboarding', () => {
  const initial = createInitialState();
  const dismissed = sandboxReducer(initial, { type: 'dismissHint' });
  assert.equal(dismissed.showHint, false);
  assert.equal(dismissed.revision, initial.revision);
  assert.equal(sandboxReducer(dismissed, { type: 'reset' }).showHint, true);
  assert.equal(createInitialState().showHint, true);
});

test('confirmation AND human priority are required', () => {
  let state = createInitialState();
  assert.equal(recommend(state).assignments.length, 0);
  state = sandboxReducer(state, { type: 'confirm', id: 'C1' });
  assert.equal(recommend(state).assignments.length, 0);
  state = sandboxReducer(state, { type: 'priority', id: 'C2', priority: 'first' });
  assert.equal(recommend(state).assignments.length, 0);
  state = sandboxReducer(state, { type: 'priority', id: 'C1', priority: 'next' });
  assert.deepEqual(recommend(state).assignments.map(assignment => assignment.casualtyId), ['C1']);
});

test('priority outranks distance and assignments are distinct', () => {
  let state = review(createInitialState(), 'C1', 'later');
  state = review(state, 'C2', 'first');
  state = review(state, 'C4', 'next');
  const plan = recommend(state);
  assert.deepEqual(new Set(plan.assignments.map(assignment => assignment.casualtyId)), new Set(['C2', 'C4']));
  assert.equal(new Set(plan.assignments.map(assignment => assignment.responderId)).size, 2);
  state = sandboxReducer(state, { type: 'availability', id: 'bravo' });
  assert.deepEqual(recommend(state).assignments.map(assignment => assignment.casualtyId), ['C2']);
});

test('distance breaks equal-priority assignment ties', () => {
  let state = review(createInitialState(), 'C1');
  state = review(state, 'C2');
  state = review(state, 'C3');
  const plan = recommend(state);
  assert.deepEqual(new Set(plan.assignments.map(assignment => assignment.casualtyId)), new Set(['C1', 'C3']));
  assert.equal(plan.assignments.reduce((sum, assignment) => sum + assignment.route.length - 1, 0), 7);
});

test('recommendation and approval never dispatch; explicit matching dispatch is required', () => {
  let state = review(createInitialState(), 'C1');
  state = sandboxReducer(state, { type: 'recommend' });
  assert.equal(state.tasks.length, 0);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision: state.revision }), state);
  assert.equal(sandboxReducer(state, { type: 'approve', revision: state.revision - 1 }), state);
  state = sandboxReducer(state, { type: 'approve', revision: state.revision });
  assert.equal(state.tasks.length, 0);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision: state.revision - 1 }), state);
  const approvedRevision = state.revision;
  state = sandboxReducer(state, { type: 'dispatch', revision: approvedRevision });
  assert.equal(state.tasks[0].status, 'dispatched');
  assert.equal(state.plan, null);
  assert.equal(state.approvedRevision, null);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision: approvedRevision }), state);
});

for (const change of [
  { type: 'confirm', id: 'C1' },
  { type: 'priority', id: 'C1', priority: 'later' },
  { type: 'availability', id: 'alpha' },
  { type: 'corridor', id: 'central' },
]) test(`${change.type} invalidates plan, approval, and stale revision`, () => {
  let state = authorize(review(createInitialState(), 'C1'));
  const revision = state.revision;
  state = sandboxReducer(state, change);
  assert.equal(state.plan, null);
  assert.equal(state.approvedRevision, null);
  assert.equal(state.revision, revision + 1);
  assert.equal(sandboxReducer(state, { type: 'approve', revision }), state);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision }), state);
  state = sandboxReducer(state, { type: 'recommend' });
  assert.equal(sandboxReducer(state, { type: 'approve', revision }), state);
});

test('full virtual lifecycle requires receipt, ACK, then DONE', () => {
  let state = send(review(createInitialState(), 'C1'));
  const task = state.tasks[0];
  assert.equal(sandboxReducer(state, { type: 'progress', taskId: task.id, stage: 'done' }), state);
  assert.equal(sandboxReducer(state, { type: 'progress', taskId: task.id, stage: 'acknowledged' }), state);
  state = sandboxReducer(state, { type: 'progress', taskId: task.id, stage: 'received' });
  assert.equal(state.tasks[0].status, 'received');
  assert.equal(sandboxReducer(state, { type: 'progress', taskId: task.id, stage: 'received' }), state);
  state = reduce(state, { type: 'progress', taskId: task.id, stage: 'acknowledged' }, { type: 'progress', taskId: task.id, stage: 'done' });
  assert.equal(state.tasks[0].status, 'done');
  assert.equal(sandboxReducer(state, { type: 'progress', taskId: task.id, stage: 'done' }), state);
  assert.deepEqual(state.responders.find(responder => responder.id === task.responderId).position, task.route.at(-1));
});

test('busy responders and active casualties cannot be assigned twice', () => {
  let state = review(createInitialState(), 'C1');
  state = review(state, 'C3');
  state = send(state);
  assert.equal(state.tasks.length, 2);
  state = review(state, 'C2');
  assert.equal(recommend(state).assignments.length, 0);
  assert.match(recommend(state).warnings.join(' '), /Busy responders/);
  const doneTask = state.tasks[0];
  state = finish(state, doneTask.id);
  const plan = recommend(state);
  assert.equal(plan.assignments.length, 1);
  assert.equal(plan.assignments[0].responderId, doneTask.responderId);
  assert.equal(plan.assignments[0].casualtyId, 'C2');
  const preserved = state.tasks[0];
  state = send(state);
  assert.deepEqual(state.tasks[0], preserved);
  assert.equal(state.tasks.filter(task => task.casualtyId === doneTask.casualtyId).length, 1);
});

test('BFS returns a real detour and unreachable east gate result', () => {
  const start = { x: 0, y: 1 };
  const end = { x: 3, y: 1 };
  const open = shortestPath(start, end, []);
  const detour = shortestPath(start, end, ['central']);
  assert.equal(open.length, 4);
  assert.equal(detour.length, 6);
  assert.notDeepEqual(detour, open);
  for (let index = 1; index < detour.length; index++) {
    assert.equal(Math.abs(detour[index].x - detour[index - 1].x) + Math.abs(detour[index].y - detour[index - 1].y), 1);
    assert.ok(!CORRIDORS[0].edges.includes(edge(detour[index - 1], detour[index])));
  }
  assert.equal(shortestPath(start, { x: 6, y: 1 }, ['east']), null);
  assert.deepEqual(shortestPath(start, start, ['east']), [start]);
});

test('route blockage holds in-flight task until NEW approved replacement dispatch', () => {
  let state = send(review(createInitialState(), 'C1'));
  const original = state.tasks[0];
  const oldRevision = original.revision;
  state = sandboxReducer(state, { type: 'corridor', id: 'central' });
  assert.equal(state.tasks[0].status, 'blocked');
  assert.equal(sandboxReducer(state, { type: 'progress', taskId: original.id, stage: 'received' }), state);
  state = sandboxReducer(state, { type: 'recommend' });
  assert.equal(state.tasks.length, 1);
  assert.equal(state.tasks[0].status, 'blocked');
  const replacement = state.plan.assignments[0];
  assert.equal(replacement.responderId, original.responderId);
  assert.notDeepEqual(replacement.route, original.route);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision: oldRevision }), state);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision: state.revision }), state);
  state = sandboxReducer(state, { type: 'approve', revision: state.revision });
  assert.equal(state.tasks[0].status, 'blocked');
  state = sandboxReducer(state, { type: 'dispatch', revision: state.revision });
  assert.equal(state.tasks[0].status, 'replaced');
  assert.equal(state.tasks[1].status, 'dispatched');
  assert.ok(state.tasks[1].revision > oldRevision);
});

test('unblocking does not implicitly resume or release a held assignment', () => {
  let state = send(review(createInitialState(), 'C2'));
  state = sandboxReducer(state, { type: 'corridor', id: 'east' });
  assert.equal(state.tasks[0].status, 'blocked');
  let plan = recommend(state);
  assert.equal(plan.assignments.length, 0);
  assert.match(plan.warnings.join(' '), /no feasible route/);
  state = sandboxReducer(state, { type: 'corridor', id: 'east' });
  assert.equal(state.tasks[0].status, 'blocked');
  plan = recommend(state);
  assert.equal(plan.assignments.length, 1);
  assert.equal(state.tasks.length, 1);
});

test('offline held responder cannot release or transfer its task implicitly', () => {
  let state = send(review(createInitialState(), 'C1'));
  const responderId = state.tasks[0].responderId;
  state = reduce(state, { type: 'corridor', id: 'central' }, { type: 'availability', id: responderId });
  assert.equal(recommend(state).assignments.length, 0);
  state = send(state);
  assert.equal(state.tasks.length, 1);
  assert.equal(state.tasks[0].status, 'blocked');
});

test('unaffected active and completed tasks remain unchanged by map edits', () => {
  let state = review(createInitialState(), 'C1');
  state = review(state, 'C3');
  state = send(state);
  const unaffected = state.tasks.find(task => task.casualtyId === 'C3');
  const affected = state.tasks.find(task => task.casualtyId === 'C1');
  state = sandboxReducer(state, { type: 'corridor', id: 'central' });
  assert.equal(state.tasks.find(task => task.id === unaffected.id).status, 'dispatched');
  assert.equal(state.tasks.find(task => task.id === affected.id).status, 'blocked');
  state = finish(state, unaffected.id);
  const completedTask = state.tasks.find(task => task.id === unaffected.id);
  state = send(state);
  assert.deepEqual(state.tasks.find(task => task.id === unaffected.id), completedTask);
});

test('unknown identifiers and invalid transitions are no-ops', () => {
  const state = createInitialState();
  for (const action of [
    { type: 'confirm', id: 'missing' }, { type: 'priority', id: 'missing', priority: 'first' },
    { type: 'priority', id: 'C1', priority: 'not-a-priority' }, { type: 'availability', id: 'missing' },
    { type: 'corridor', id: 'missing' }, { type: 'progress', taskId: 'missing', stage: 'done' },
    { type: 'approve', revision: 1 }, { type: 'dispatch', revision: 1 },
  ]) assert.equal(sandboxReducer(state, action), state);
});

test('task progression invalidates another approved recommendation', () => {
  let state = send(review(createInitialState(), 'C1'));
  const taskId = state.tasks[0].id;
  state = authorize(review(state, 'C2'));
  const revision = state.revision;
  assert.equal(state.approvedRevision, revision);
  state = sandboxReducer(state, { type: 'progress', taskId, stage: 'received' });
  assert.equal(state.plan, null);
  assert.equal(state.approvedRevision, null);
  assert.equal(sandboxReducer(state, { type: 'dispatch', revision }), state);
});
