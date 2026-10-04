import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const root = '/api/stores/{storeId}/manual/interviews/{sessionId}';
const states = spec.paths[root].get.responses['200'].content['application/json'].examples;
const validate = validator('ManualInterviewSession');
test('현재 질문은 정확히 한 개: 두 질문 대기 또는 답변 완료 질문 노출 거절', () => {
  const collecting = states.collecting.value;
  assert.ok(validate(collecting));
  const question = collecting.questions[0];
  assert.equal(validate({ ...collecting, questions: [question, { ...question, id: collecting.id }] }), false);
  assert.equal(validate({ ...collecting, questions: [{ ...question, answered: true }] }), false);
});
test('기본/추가 질문 각각의 답변 하나에 즉시 Jev 평가 예약', () => {
  const accepted = spec.paths[root + '/answers'].post.responses['202'].content['application/json'].examples;
  for (const item of Object.values(accepted)) {
    assert.ok(validate(item.value));
    assert.equal(item.value.phase, 'PROCESSING');
    assert.equal(item.value.processing.kind, 'EVALUATION');
    assert.deepEqual(item.value.questions, []);
  }
});
test('충분 판단 성공: 점주 확인 없이 다음 필수 질문 생성', () => {
  const next = states.nextQuestionAfterSufficientAnswer.value;
  assert.ok(validate(next), JSON.stringify(validate.errors));
  assert.equal(next.intents[0].coverage, 'COVERED');
  assert.ok(next.intents[0].finishedAt);
  assert.equal(next.intents[0].confirmedAt, null);
  assert.notEqual(next.currentIntentId, next.intents[0].id);
  assert.equal(next.processing.kind, 'INITIAL_QUESTION');
});
test('부족 판단: 같은 인텐트의 추가 질문 하나만 제시', () => {
  const followup = states.followupQuestion.value;
  assert.ok(validate(followup));
  assert.equal(followup.questions.length, 1);
  assert.equal(followup.questions[0].kind, 'PROBE');
  assert.equal(followup.questions[0].intentId, followup.currentIntentId);
  assert.equal(followup.intents[0].coverage, 'PENDING');
});
test('마지막 충분 판단도 중간 점주 확인 없이 초안 생성 가능', () => {
  const ready = structuredClone(states.readyToGeneratePendingReview.value);
  ready.intents[0] = { ...ready.intents[0], coverage: 'COVERED', depth: 0, confirmedAt: null };
  assert.ok(validate(ready), JSON.stringify(validate.errors));
  const generating = spec.paths[root + '/completion'].post.responses['202'].content['application/json'].examples.allCovered.value;
  assert.ok(validate(generating));
  assert.ok(generating.intents.every(intent => intent.confirmedAt === null));
});
test('확인·정정·사진 API는 유지하며 수집 후 선택 도구로 분리', () => {
  for (const route of ['/confirmations', '/corrections', '/photos']) {
    assert.ok(spec.paths[root + route]);
    const operation = Object.values(spec.paths[root + route])[0];
    assert.match(operation.description, /READY_TO_GENERATE/);
  }
  assert.match(spec.paths[root + '/confirmations'].post.description, /선행 조건이 아닙니다/);
  assert.match(spec.paths[root + '/photos'].put.description, /선행 조건이 아닙니다/);
});
