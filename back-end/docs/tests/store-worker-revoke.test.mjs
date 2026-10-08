import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/workers/{workerId}/access'].delete;
test('근무자 접근 종료: 쿠키/CSRF와 UUID·204 빈 응답 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody, undefined);
  assert.equal(op.responses['204'].content, undefined);
  for (const p of op.parameters) assert.equal(parameterValidator(p)('other'), false);
  assert.match(op.responses['404'].description, /STORE_WORKER_NOT_FOUND/);
});
test('근무자 접근 종료: REVOKED는 시각 필수·자료 권한 없음 및 기존 종료 이력 허용', () => {
  const member = spec.paths['/api/stores/{storeId}/workers/{workerId}'].get.responses['200'].content['application/json'].examples.ended.value;
  const grant = member.accessGrants[0];
  const validate = validator('StoreAccessGrant');
  assert.ok(validate(grant));
  assert.equal(validate({ ...grant, revokedAt: null }), false);
  assert.equal(validate({ ...grant, permissions: ['READ_MANUALS'] }), false);
});
