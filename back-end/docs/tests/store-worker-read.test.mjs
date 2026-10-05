import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/workers'].get;
const detail = spec.paths['/api/stores/{storeId}/workers/{workerId}'].get;
const member = validator('StoreWorkerAccess');
const page = validator('StoreWorkerAccessPage');
const examples = detail.responses['200'].content['application/json'].examples;
const regular = examples.regular.value;
const temporary = examples.temporary.value;
const ended = examples.ended.value;
test('근무자 조회: 정기·만료 예정 대타·종료와 빈 목록 허용', () => {
  for (const e of Object.values(examples)) assert.ok(member(e.value), JSON.stringify(member.errors));
  for (const e of Object.values(op.responses['200'].content['application/json'].examples)) assert.ok(page(e.value), JSON.stringify(page.errors));
});
test('근무자 조회: 여러 접근의 상태 집계 구조 및 무기한+임박 대타 허용', () => {
  assert.ok(member({ ...regular, accessGrants: [...regular.accessGrants, ...temporary.accessGrants] }));
  assert.equal(member({ ...regular, accessStatus: 'EXPIRING' }), false);
  assert.equal(member({ ...ended, accessStatus: 'ACTIVE', permissions: regular.permissions }), false);
  assert.equal(member({ ...temporary, accessStatus: 'ENDED', permissions: [] }), false);
});
for (const field of ['email', 'phoneNumber', 'birthDate', 'gender', 'careers', 'password']) {
  test(`근무자 조회: 불필요한 ${field} 노출 거절`, () => assert.equal(member({ ...regular, [field]: 'private' }), false));
}
test('근무자 조회: UUID·view/페이지 경계 및 소유 매장 회원 인가 계약', () => {
  assert.deepEqual(detail.security, [{ SessionCookie: [] }]);
  assert.equal(parameterValidator(detail.parameters[1])('other'), false);
  const view = parameterValidator(op.parameters.find(p => p.name === 'view'));
  for (const v of ['ACTIVE', 'ENDED', 'ALL']) assert.ok(view(v)); assert.equal(view('SUSPENDED'), false);
  const size = parameterValidator(op.parameters.find(p => p.name === 'size'));
  assert.ok(size(100)); assert.equal(size(101), false);
  assert.match(detail.responses['404'].description, /STORE_WORKER_NOT_FOUND/);
  assert.equal(member({ ...regular, permissions: [] }), false);
});
