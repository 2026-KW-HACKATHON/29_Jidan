import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/invitations/{invitationId}/cancel'].post;
test('초대 취소: CANCELLED와 최초 취소 시각을 가진 응답 및 다른 완료 시각 거절', () => {
  const validate = validator('StoreInvitation');
  const result = op.responses['200'].content['application/json'].example;
  assert.ok(validate(result)); assert.equal(result.status, 'CANCELLED');
  for (const status of ['CANCELED', 'cancelled']) assert.equal(validate({ ...result, status }), false);
  assert.equal(validate({ ...result, canceledAt: null }), false);
  assert.equal(validate({ ...result, acceptedAt: result.canceledAt }), false);
  assert.equal(validate({ ...result, declinedAt: result.canceledAt }), false);
});
test('점주 초대·일반회원 초대함·근무 요청은 같은 취소 상태 철자를 사용', () => {
  for (const name of ['StoreInvitation', 'ReceivedInvitation', 'WorkRequest']) {
    const statuses = spec.components.schemas[name].properties.status.enum;
    assert.ok(statuses.includes('CANCELLED'), name);
    assert.equal(statuses.includes('CANCELED'), false, name);
  }
});
test('초대 취소: body 없는 회원 변경 요청과 수락/만료 오류 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]); assert.equal(op.requestBody, undefined);
  assert.ok(op.responses['409']); assert.ok(op.responses['410']);
  assert.match(op.description, /이미 CANCELLED/);
});
