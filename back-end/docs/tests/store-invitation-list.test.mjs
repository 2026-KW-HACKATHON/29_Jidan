import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/invitations'].get;
const page = validator('StoreInvitationPage');
const examples = op.responses['200'].content['application/json'].examples;
test('보낸 초대: 활성·지난·빈 목록 예시 허용 및 음수 건수 거절', () => {
  for (const example of Object.values(examples)) assert.ok(page(example.value), JSON.stringify(page.errors));
  const empty = examples.empty.value;
  assert.ok(page({ ...empty, page: 9 }));
  for (const field of ['activeCount', 'pastCount', 'totalItems', 'totalPages']) assert.equal(page({ ...empty, [field]: -1 }), false);
});
test('보낸 초대: 탭과 페이지 형식 검증 및 승인된 OWNER 조회 계약', () => {
  const view = parameterValidator(op.parameters.find(p => p.name === 'view'));
  assert.ok(view('ACTIVE')); assert.ok(view('PAST')); assert.equal(view('ALL'), false);
  const size = parameterValidator(op.parameters.find(p => p.name === 'size'));
  assert.ok(size(100)); assert.equal(size(101), false); assert.equal(size(0), false);
  assert.deepEqual(op.security, [{ SessionCookie: [] }]);
  assert.match(op.responses['403'].description, /STORE_APPROVAL_REQUIRED/);
});
