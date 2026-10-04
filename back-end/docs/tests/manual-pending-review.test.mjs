import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const root = '/api/stores/{storeId}/manual/interviews/{sessionId}';
const examples = spec.paths[root].get.responses['200'].content['application/json'].examples;
const next = examples.nextIntentPendingReview.value;
const last = examples.readyToGeneratePendingReview.value;
const generate = spec.paths[root + '/completion'].post.responses['202'].content['application/json'].examples.pendingReview.value;
const validateSession = validator('ManualInterviewSession');
const validateIntent = validator('ManualInterviewIntent');

test('상한 부족: 검수중 항목 확인 없이 다음 인텐트 질문으로 이동', () => {
  assert.ok(validateSession(next), JSON.stringify(validateSession.errors));
  const skipped = next.intents[0];
  assert.equal(skipped.coverage, 'NEEDS_DETAIL');
  assert.equal(skipped.depth, 5);
  assert.ok(skipped.finishedAt);
  assert.equal(skipped.confirmedAt, null);
  assert.equal(next.currentIntentId, next.intents[1].id);
  assert.equal(next.phase, 'PROCESSING');
  assert.equal(next.review, null);
  assert.equal(next.processing.kind, 'INITIAL_QUESTION');
});
test('마지막 상한 부족: 주제별 확인 없이 초안 생성 준비와 생성 가능', () => {
  for (const value of [last, generate]) {
    assert.ok(validateSession(value), JSON.stringify(validateSession.errors));
    assert.equal(value.intents[0].confirmedAt, null);
    assert.equal(value.intents[0].coverage, 'NEEDS_DETAIL');
    assert.equal(value.currentIntentId, null);
    assert.equal(value.review, null);
  }
  assert.equal(last.phase, 'READY_TO_GENERATE');
  assert.equal(last.processing, null);
  assert.equal(generate.phase, 'GENERATING');
});
test('검수중 항목: depth 5·진행 종료 시각·중간 확인 없음의 경계', () => {
  const intent = last.intents[0];
  assert.ok(validateIntent(intent));
  for (const invalid of [
    { ...intent, depth: 4 },
    { ...intent, depth: 6 },
    { ...intent, finishedAt: null },
    { ...intent, confirmedAt: intent.finishedAt },
  ]) assert.equal(validateIntent(invalid), false);
});
test('초안 생성은 진행이 끝나지 않은 항목이 있으면 거절', () => {
  const intent = last.intents[0];
  for (const coverage of ['PENDING', 'COVERED']) {
    const waiting = { ...intent, coverage, finishedAt: null, confirmedAt: null };
    for (const sample of [last, generate]) assert.equal(validateSession({ ...sample, intents: [waiting] }), false);
  }
});
test('평가 실패는 상한에 도달해도 검수중 진행으로 간주하지 않음', () => {
  const intent = { ...last.intents[0], coverage: 'PENDING', finishedAt: null, confirmedAt: null };
  const failed = {
    ...last,
    status: 'ERROR', phase: 'ERROR', intents: [intent], currentIntentId: intent.id,
    processing: { taskId: next.processing.taskId, kind: 'EVALUATION', attempt: 3 },
    error: { code: 'AI_PROCESSING_FAILED', message: '저장된 답변으로 다시 평가합니다.', retryable: true },
  };
  assert.ok(validateSession(failed), JSON.stringify(validateSession.errors));
  assert.equal(failed.intents[0].coverage, 'PENDING');
  assert.equal(failed.intents[0].finishedAt, null);
});
test('최종 검수중 항목 확인은 발행 시 수행하고 질문 진행에는 불필요', () => {
  const publish = spec.paths['/api/stores/{storeId}/manual/draft/publication'].post;
  assert.match(publish.description, /현재 OPEN인 항목 ID를 정확히 모두/);
  assert.match(publish.description, /MANUAL_REVIEW_REQUIRED/);
  assert.equal(publish.requestBody.content['application/json'].examples.default.value.confirmed, true);
});
