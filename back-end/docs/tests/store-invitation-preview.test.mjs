import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/store-invitations/preview'].post;
const validate = validator('StoreInvitationTokenRequest');
const input = { token: 'a'.repeat(43) };
test('초대 토큰: 43/512자 base64url 경계 허용', () => {
  assert.ok(validate(input)); assert.ok(validate({ token: 'Z_-9'.repeat(128) }));
});
for (const [name, value] of [
  ['누락', {}], ['빈 값', { token: '' }], ['42자', { token: 'a'.repeat(42) }],
  ['513자', { token: 'a'.repeat(513) }], ['null', { token: null }], ['숫자', { token: 123 }],
  ['공백', { token: ' '.repeat(43) }], ['base64 padding', { token: 'a'.repeat(43)+'=' }],
  ['URL 입력', { token: 'https://example.com/#'+ 'a'.repeat(43) }],
  ['이메일 주입', { ...input, email: 'other@example.com' }], ['근무자 ID 주입', { ...input, workerId: 'other' }],
  ['매장 ID 주입', { ...input, storeId: 'other' }], ['접근 기간 주입', { ...input, accessExpiresAt: null }],
]) test(`초대 토큰: ${name} 거절`, () => assert.equal(validate(value), false));
test('초대 확인: 본문 토큰·조회 세션·바인딩 오류 및 두 만료 시각 구분', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [] }]); assert.equal(op.parameters, undefined);
  assert.equal(spec.components.schemas.StoreInvitationTokenRequest.properties.token.writeOnly, true);
  assert.equal(op.responses['403'].content['application/json'].example.code, 'INVITATION_EMAIL_MISMATCH');
  const value = op.responses['200'].content['application/json'].example;
  const response = validator('StoreInvitationPreview'); assert.ok(response(value));
  assert.ok(response({ ...value, accessExpiresAt: '2026-11-01T00:00:00Z' }));
  assert.equal(response({ ...value, token: input.token }), false);
  assert.ok(op.responses['410']);
});
