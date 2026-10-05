import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const list = spec.paths['/api/owners/me/stores'].get;
const detail = spec.paths['/api/stores/{storeId}'].get;
const validate = validator('OwnerStore');
const page = validator('OwnerStorePage');
const pending = list.responses['200'].content['application/json'].example.items[0];
const approved = detail.responses['200'].content['application/json'].example;

test('매장 조회: 승인 대기와 승인 완료 상태·권한 일치 허용', () => {
  for (const value of [pending, approved]) assert.ok(validate(value), JSON.stringify(validate.errors));
});
for (const [name, value] of [
  ['승인 대기에 승인 시각', { ...pending, approvedAt: approved.approvedAt }],
  ['승인 대기에 운영 권한', { ...pending, permissions: approved.permissions }],
  ['승인 완료인데 시각 없음', { ...approved, approvedAt: null }],
  ['승인 완료인데 운영 권한 부족', { ...approved, permissions: ['READ_STORE_STATUS'] }],
  ['중복 권한', { ...approved, permissions: Array(5).fill('READ_STORE_STATUS') }],
  ['미정 상태', { ...pending, approvalStatus: 'REJECTED' }],
  ['인증 정보 노출', { ...pending, password: 'secret' }],
]) test(`매장 조회: ${name} 거절`, () => assert.equal(validate(value), false));
test('매장 목록: 빈 결과·페이지 이후 빈 목록 허용 및 음수 건수 거절', () => {
  const empty = { items: [], page: 2, size: 20, totalItems: 0, totalPages: 0, asOf: '2026-10-05T01:00:00Z' };
  assert.ok(page(empty)); assert.equal(page({ ...empty, totalItems: -1 }), false);
});
test('매장 조회: 페이지/크기와 UUID 입력 경계 및 회원 세션 계약', () => {
  for (const operation of [list, detail]) assert.deepEqual(operation.security, [{ SessionCookie: [] }]);
  const size = parameterValidator(list.parameters.find(p => p.name === 'size'));
  const number = parameterValidator(list.parameters.find(p => p.name === 'page'));
  assert.ok(size(100)); for (const v of [0, 101, 1.5]) assert.equal(size(v), false);
  assert.ok(number(1)); for (const v of [0, -1, 1.5]) assert.equal(number(v), false);
  const id = parameterValidator(detail.parameters[0]); assert.ok(id(pending.id)); assert.equal(id('other'), false);
  assert.match(detail.responses['404'].description, /다른 점주/);
});
