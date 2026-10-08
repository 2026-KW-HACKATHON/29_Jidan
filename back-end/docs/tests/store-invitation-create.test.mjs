import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/invitations'].post;
const create = validator('StoreInvitationCreateRequest');
const invitation = validator('StoreInvitation');
const pending = op.responses['201'].content['application/json'].example.invitation;
test('초대 생성: 미지정/null/명시 접근 종료 시각 허용', () => {
  for (const v of [{ email: 'jisu@example.com' }, { email: 'jisu@example.com', accessExpiresAt: null }, { email: 'jisu@example.com', accessExpiresAt: '2026-11-01T00:00:00+09:00' }]) assert.ok(create(v), JSON.stringify(create.errors));
});
for (const [name, value] of [
  ['이메일 누락', {}], ['이메일 null', { email: null }], ['이메일 오류', { email: 'invalid' }],
  ['이메일 공백', { email: 'jisu @example.com' }], ['이메일 초과 길이', { email: 'a'.repeat(245)+'@example.com' }],
  ['기간 날짜만', { email: 'jisu@example.com', accessExpiresAt: '2026-11-01' }],
  ['역할 주입', { email: 'jisu@example.com', role: 'OWNER' }], ['소유자 주입', { email: 'jisu@example.com', ownerId: 'other' }],
  ['링크 기간 변경', { email: 'jisu@example.com', expiresAt: '2030-01-01T00:00:00Z' }],
  ['password 주입', { email: 'jisu@example.com', password: 'secret' }],
]) test(`초대 생성: ${name} 거절`, () => assert.equal(create(value), false));
test('초대: PENDING/완료 상태의 시각과 수락자 일치 및 비밀 응답 제외', () => {
  assert.ok(invitation(pending));
  const accepted = { ...pending, status: 'ACCEPTED', acceptedAt: '2026-10-05T02:00:00Z', acceptedBy: { workerId: '7390d3c1-dc0d-4b1c-814d-b68a1df5c1b6', name: '김지수' } };
  assert.ok(invitation(accepted)); assert.equal(invitation({ ...accepted, acceptedBy: null }), false);
  assert.equal(invitation({ ...pending, acceptedAt: accepted.acceptedAt }), false);
  assert.equal(invitation({ ...pending, status: 'CANCELLED' }), false);
  assert.equal(invitation({ ...pending, status: 'DECLINED' }), false);
  for (const field of ['token', 'tokenHash', 'invitationUrl', 'password']) assert.equal(invitation({ ...pending, [field]: 'secret' }), false);
});
test('초대 생성: 쿠키/CSRF, 멱등 key, 승인/중복/발송 큐 실패 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  const key = parameterValidator(op.parameters[1]); assert.equal(key('wrong'), false);
  assert.match(op.responses['403'].description, /STORE_APPROVAL_REQUIRED/);
  assert.match(op.responses['409'].description, /INVITATION_ALREADY_PENDING/);
  assert.equal(op.responses['503'].content['application/json'].example.code, 'DELIVERY_UNAVAILABLE');
  assert.equal(op.responses['201'].content['application/json'].example.deliveryStatus, 'QUEUED');
});
