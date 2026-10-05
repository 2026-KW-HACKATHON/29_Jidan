import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';

const base = '/api/stores/{storeId}/job-postings/{jobId}';
const examples = spec.paths[base].get.responses['200'].content['application/json'].examples;
const response = spec.paths['/api/users/me/work-requests/{requestId}/response'].post;
const withdrawal = spec.paths[`${base}/work-requests/{requestId}/confirmation-withdrawal`].post;
const closure = spec.paths[`${base}/closure`].post;

test('공고 생애주기 예시: 수락 시 마감 시각, 확정 철회 시 초기화 및 revision 증가', () => {
  const { recruiting: { value: before }, accepted: { value: closed }, confirmationWithdrawn: { value: reopened } } = examples;
  const acceptedRequest = response.responses['200'].content['application/json'].example;
  const validate = validator('JobPosting');
  for (const job of [before, closed, reopened]) assert.ok(validate(job), JSON.stringify(validate.errors));
  assert.equal(before.id, acceptedRequest.jobId);
  assert.equal(closed.id, before.id);
  assert.equal(reopened.id, before.id);
  assert.equal(before.status, 'RECRUITING');
  assert.equal(before.closedAt, null);
  assert.equal(closed.status, 'CLOSED');
  assert.equal(closed.closedAt, acceptedRequest.respondedAt);
  assert.equal(reopened.status, 'RECRUITING');
  assert.equal(reopened.closedAt, null);
  assert.ok(before.revision < closed.revision && closed.revision < reopened.revision);
  // 이전 마감 시각이 남은 재개 응답과 시각 없는 마감 응답은 모두 거절합니다.
  assert.equal(validate({ ...reopened, closedAt: closed.closedAt }), false);
  assert.equal(validate({ ...closed, closedAt: null }), false);
});

test('수락·거절 계약: 수락만 자동 마감하고 확정 공고 오류 판정 순서 명시', () => {
  for (const re of [/같은 트랜잭션/, /RECRUITING→CLOSED/, /closedAt=respondedAt/, /revision을 증가/, /DECLINE.*RECRUITING\/closedAt=null/, /확정 여부를 일반 CLOSED 검사보다 먼저/]) {
    assert.match(response.description, re);
  }
  assert.doesNotMatch(response.description, /별도 점주 마감 전까지 RECRUITING/);
});

test('확정 철회 계약: 시작 경계·이력 보존·원자적 모집 재개·재시도', () => {
  for (const re of [/startAt과 같은 시각부터는 409/, /같은 트랜잭션.*CLOSED→RECRUITING/, /closedAt=null/, /마감·철회 시각.*이력/, /신규 지원/, /같은 key 재시도는 최초 200/, /다른 key 재철회는 409/, /최초 revokedAt을 유지/]) {
    assert.match(withdrawal.description, re);
  }
  assert.match(spec.paths['/api/job-postings'].get.description, /확정 철회.*다시 탐색 대상/);
});

test('마감 재요청 계약: 시작 전·진행 중·완료 후 이력 보존, stale revision 거절', () => {
  for (const re of [/CLOSED.*시작 전·진행 중/, /완료 후 모두.*200/, /최초 closedAt과 revision/, /CONFIRMED\/COMPLETED/, /새 key는 먼저 expectedRevision/, /첫 성공/, /동일 Idempotency-Key 재시도는 최초 결과/]) {
    assert.match(closure.description, re);
  }
  const conflict = closure.responses['409'];
  assert.match(conflict.description, /JOB_REVISION_CONFLICT/);
  assert.equal(conflict.content['application/json'].example.code, 'WORK_REQUEST_WITHDRAWAL_REQUIRED');
  assert.doesNotMatch(JSON.stringify(closure), /CONFIRMATION_WITHDRAWAL_REQUIRED|JOB_IN_PROGRESS/);
});
