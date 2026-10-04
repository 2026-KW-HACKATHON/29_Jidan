import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const root = '/api/stores/{storeId}/manual';
const states = spec.paths[root + '/interviews/{sessionId}'].get.responses['200'].content['application/json'].examples;
function examples(operation) {
  return [operation.requestBody, ...Object.values(operation.responses)]
    .flatMap(entry => Object.values(entry?.content ?? {}))
    .flatMap(media => media.example ? [media.example] : Object.values(media.examples ?? {}).map(x => x.value));
}
function walk(value, visit) {
  if (!value || typeof value !== 'object') return;
  visit(value);
  for (const child of Object.values(value)) walk(child, visit);
}
function uniqueIds(items) {
  const ids = items.map(item => item.id);
  assert.equal(new Set(ids).size, ids.length);
}
test('세션 조회 예시: 모든 공개 phase와 실패/완료 경계 제공', () => {
  const phases = new Set();
  const validate = validator('ManualInterviewSession');
  for (const { value } of Object.values(states)) {
    assert.ok(validate(value), JSON.stringify(validate.errors));
    phases.add(value.phase);
    const intents = new Map(value.intents.map(intent => [intent.id, intent]));
    uniqueIds(value.intents);
    uniqueIds(value.questions);
    for (const question of value.questions) {
      assert.ok(intents.has(question.intentId));
      assert.equal(question.intentId, value.currentIntentId);
      assert.equal(question.depth, intents.get(question.intentId).depth);
    }
    if (value.review) { assert.ok(intents.has(value.review.intentId)); assert.equal(value.phase, 'READY_TO_GENERATE'); }
    if (['READY_TO_GENERATE', 'GENERATING', 'COMPLETED'].includes(value.phase)) {
      assert.ok(value.intents.every(intent => intent.finishedAt && (intent.coverage === 'COVERED' ? true : intent.coverage === 'NEEDS_DETAIL' && intent.depth === 5 && intent.confirmedAt === null)));
    }
    if (value.completedAt) assert.ok(Date.parse(value.startedAt) <= Date.parse(value.completedAt));
  }
  assert.deepEqual([...phases].sort(), ['COLLECTING', 'COMPLETED', 'ERROR', 'GENERATING', 'PROCESSING', 'READY_TO_GENERATE']);
});
test('전체 매뉴얼 예시: 근무조 참조·시간 길이·ID·사진 순서 일관성', () => {
  for (const [path, item] of Object.entries(spec.paths)) {
    if (!path.startsWith(root)) continue;
    for (const operation of Object.values(item)) {
      for (const sample of examples(operation)) walk(sample, value => {
        if (Array.isArray(value.shifts) && Array.isArray(value.sections)) {
          const shiftIds = new Set(value.shifts.map(shift => shift.id));
          uniqueIds(value.shifts);
          uniqueIds(value.sections);
          for (const section of value.sections) {
            if (section.category === 'SHIFT_TASK') assert.ok(shiftIds.has(section.shiftId), path);
            else assert.equal(section.shiftId, null, path);
          }
          for (const shift of value.shifts) {
            if ([shift.startTime, shift.endTime, shift.endsNextDay].includes(null)) continue;
            const minutes = text => Number(text.slice(0, 2)) * 60 + Number(text.slice(3));
            const duration = minutes(shift.endTime) - minutes(shift.startTime) + (shift.endsNextDay ? 1440 : 0);
            assert.ok(duration > 0 && duration <= 1440, path);
          }
        }
        if (Array.isArray(value.steps)) uniqueIds(value.steps);
        if (Array.isArray(value.photos)) assert.equal(new Set(value.photos.map(photo => photo.mediaId)).size, value.photos.length, path);
      });
    }
  }
});
test('추가 질문은 하나만 제시하고 해당 답변은 바로 평가', () => {
  const operation = spec.paths[root + '/interviews/{sessionId}/answers'].post;
  const basicId = operation.requestBody.content['application/json'].examples.default.value.questionId;
  const followup = spec.paths[root + '/interviews/{sessionId}'].get.responses['200'].content['application/json'].examples.followupQuestion.value;
  const request = operation.requestBody.content['application/json'].examples.followupAnswer.value;
  assert.equal(followup.questions.length, 1);
  const question = followup.questions[0];
  assert.notEqual(question.id, basicId);
  assert.notEqual(question.batchId, basicId);
  assert.equal(question.id, request.questionId);
  assert.equal(question.answered, false);
  const accepted = operation.responses['202'].content['application/json'].examples.evaluatingFollowup.value;
  assert.equal(accepted.revision, request.expectedRevision + 1);
  assert.equal(accepted.processing.kind, 'EVALUATION');
  assert.deepEqual(accepted.questions, []);
});
