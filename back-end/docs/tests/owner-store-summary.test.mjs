import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/management-summary'].get;
const validate = validator('OwnerStoreManagementSummary');
const input = op.responses['200'].content['application/json'].examples.summary.value;
test('매장 관리 요약: 빈/현황 응답 형식과 세션·승인 조건', () => {
  for (const e of Object.values(op.responses['200'].content['application/json'].examples)) assert.ok(validate(e.value));
  assert.deepEqual(op.security, [{ SessionCookie: [] }]);
  assert.match(op.responses['403'].description, /STORE_APPROVAL_REQUIRED/);
});
for (const field of ['pendingInvitationCount', 'activeWorkerCount', 'expiringWorkerCount']) {
  test(`매장 관리 요약: ${field} 음수·소수 거절`, () => {
    for (const n of [-1, 1.5]) assert.equal(validate({ ...input, [field]: n }), false);
  });
}
test('매장 관리 요약: 후속 공고 건수와 개인정보 추가 노출 거절', () => {
  assert.equal(validate({ ...input, recruitingJobCount: 3 }), false);
  assert.equal(validate({ ...input, emails: ['private@example.com'] }), false);
});
