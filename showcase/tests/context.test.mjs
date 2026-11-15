import test from 'node:test';
import assert from 'node:assert/strict';
import { CAMERA_OBSERVATIONS, EMS_RESEARCH, RECORDED_CV, SYSTEM_STAGES } from '../src/lib/sandbox/context.ts';
import { createInitialState } from '../src/lib/sandbox/engine.ts';

test('synthetic camera fixtures map one-to-one to casualties, not recorded tracks', () => {
  assert.deepEqual(CAMERA_OBSERVATIONS.map(observation => observation.casualtyId), createInitialState().casualties.map(casualty => casualty.id));
  assert.equal(new Set(CAMERA_OBSERVATIONS.map(observation => observation.personId)).size, 4);
  for (const observation of CAMERA_OBSERVATIONS) {
    assert.match(observation.camera, /^SIM-CAM/);
    assert.match(observation.personId, /^SIM-P/);
    assert.match(observation.timestamp, /^T\+00:\d{2}$/);
    assert.notEqual(observation.personId, RECORDED_CV.track);
    if (observation.visibility !== 'Visible') {
      assert.equal(observation.posture, 'Not testable');
      assert.equal(observation.movement, 'Not testable');
    }
    assert.equal('confidence' in observation, false);
  }
});

test('recorded CV excerpt has explicit clip provenance and no casualty mapping', () => {
  assert.equal(RECORDED_CV.track, 't1');
  assert.equal(RECORDED_CV.firstLyingAtSeconds, 0);
  assert.equal(RECORDED_CV.firstMovingAtSeconds, 1);
  assert.equal(RECORDED_CV.firstStillAtSeconds, 2);
  assert.equal(RECORDED_CV.runAtUtc, '2026-11-14T06:50:11.433Z');
  assert.match(RECORDED_CV.source, /^cv\/eval\/signs\/.*\.json$/);
  assert.match(RECORDED_CV.sha256, /^[a-f0-9]{64}$/);
  assert.equal('casualtyId' in RECORDED_CV, false);
});

test('verified EMS research values round to the published metrics', () => {
  assert.equal(EMS_RESEARCH.sourceIncidents, 377606);
  assert.equal(EMS_RESEARCH.heldOutIncidents, 67798);
  assert.equal(EMS_RESEARCH.xgboostMaeSeconds.toFixed(2), '78.39');
  assert.equal(EMS_RESEARCH.temporalMaeSeconds.toFixed(2), '82.63');
  assert.equal(EMS_RESEARCH.severity8Incidents, 158);
});

test('full-system explanation keeps assessment, mathematical proposal and human approval before physical dispatch', () => {
  assert.deepEqual(SYSTEM_STAGES.map(([title]) => title), [
    'Camera observations', 'Human confirmation & assessment', 'OR-Tools optimization',
    'MLP/GNN advisory comparison', 'Human approval', 'Physical Arduino dispatch',
    'ACK / DONE', 'Dynamic route updates',
  ]);
});
