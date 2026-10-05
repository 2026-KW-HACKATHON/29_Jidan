import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores'].post;
const validate = validator('StoreRegistrationInput');
const input = op.requestBody.content['application/json'].examples.store.value;
test('매장 추가: 가입 입력 재사용 및 선택 상세주소 생략 허용', () => {
  assert.ok(validate(input)); const { detailAddress, ...withoutDetail } = input; assert.ok(validate(withoutDetail));
  assert.equal(op.requestBody.content['application/json'].schema.$ref, '#/components/schemas/StoreRegistrationInput');
});
for (const [field, value] of Object.entries({ ownerId: 'other', id: 'other', approvalStatus: 'APPROVED', permissions: ['MANAGE_STORE'], approvedAt: '2026-10-05T01:00:00Z', password: 'secret' })) {
  test(`매장 추가: ${field} 주입 거절`, () => assert.equal(validate({ ...input, [field]: value }), false));
}
test('매장 추가: 필수 주소/사업자 번호와 UUID key 및 회원 세션/CSRF 계약', () => {
  const noAddress = { ...input }; delete noAddress.address; assert.equal(validate(noAddress), false);
  assert.equal(validate({ ...input, businessRegistrationNumber: '123-45-67890' }), false);
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  const key = parameterValidator(op.parameters[0]); assert.ok(key('8215b01a-ed7b-4c4a-950f-c6e1480bd563')); assert.equal(key(''), false);
  assert.equal(spec.components.parameters.OwnerStoreCreationKey.required, true);
  assert.equal(op.responses['201'].content['application/json'].example.approvalStatus, 'PENDING');
  assert.match(op.responses['409'].description, /IDEMPOTENCY_KEY_REUSED/);
});
