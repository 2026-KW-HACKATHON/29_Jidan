import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/invitations/{invitationId}/resend'].post;
test('초대 재전송: 원래 기한·접근 기간 유지와 큐 응답 형식', () => {
  const result = op.responses['200'].content['application/json'].example;
  const before = spec.paths['/api/stores/{storeId}/invitations'].post.responses['201'].content['application/json'].example.invitation;
  assert.ok(validator('StoreInvitationDelivery')(result));
  for (const field of ['id', 'email', 'createdAt', 'expiresAt', 'accessExpiresAt']) assert.equal(result.invitation[field], before[field]);
  assert.notEqual(result.invitation.lastSentAt, before.lastSentAt);
  assert.equal(result.deliveryStatus, 'QUEUED');
  assert.equal('token' in result, false);
});
test('초대 재전송: UUID key 필수·body 없음·상태/만료/발송 실패 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody, undefined);
  for (const p of op.parameters) { const v = parameterValidator(p); assert.equal(v('wrong'), false); }
  assert.equal(spec.components.parameters.InvitationResendKey.required, true);
  for (const status of ['409', '410', '503']) assert.ok(op.responses[status]);
});
