import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/store-invitations/accept'].post;
const acceptance = validator('StoreInvitationAcceptance');
const grant = validator('StoreAccessGrant');
const examples = op.responses['200'].content['application/json'].examples;
const input = examples.unlimited.value;
test('초대 수락: 무기한/기한 지정 REGULAR 결과 허용', () => {
  for (const e of Object.values(examples)) assert.ok(acceptance(e.value), JSON.stringify(acceptance.errors));
  const expiring = { ...input, accessGrant: { ...input.accessGrant, status: 'EXPIRING', validUntil: '2026-10-05T15:00:00Z' } };
  assert.ok(acceptance(expiring));
});
for (const [name, mutate] of [
  ['TEMPORARY 수락 결과', v => { v.accessGrant.type = 'TEMPORARY'; v.accessGrant.validUntil = '2026-10-26T15:00:00Z'; }],
  ['종료된 권한 수락 결과', v => { v.accessGrant.status = 'REVOKED'; v.accessGrant.revokedAt = '2026-10-05T03:00:00Z'; v.accessGrant.permissions = []; }],
  ['활성 권한의 종료 시각', v => v.accessGrant.revokedAt = '2026-10-05T03:00:00Z'],
  ['활성 권한 부족', v => v.accessGrant.permissions = ['READ_MANUALS']],
  ['만료 예정에 무기한', v => v.accessGrant.status = 'EXPIRING'],
  ['잘못된 접근 종료', v => v.accessGrant.validUntil = 'invalid'],
  ['반환 토큰 주입', v => v.token = 'secret'],
]) test(`초대 수락 응답: ${name} 거절`, () => {
  const v = structuredClone(input); mutate(v); assert.equal(acceptance(v), false);
});
test('자료 접근: 기한 지정 정기·대타의 만료/수동 종료와 빈 권한 허용', () => {
  for (const kind of ['REGULAR', 'TEMPORARY']) {
    const ended = { ...input.accessGrant, type: kind, validUntil: '2026-10-01T01:00:00Z', status: 'EXPIRED', permissions: [] };
    assert.ok(grant(ended));
    assert.ok(grant({ ...ended, status: 'REVOKED', revokedAt: '2026-10-05T01:00:00Z' }));
    assert.equal(grant({ ...ended, permissions: ['READ_MANUALS'] }), false);
  }
  assert.equal(grant({ ...input.accessGrant, type: 'TEMPORARY' }), false);
});
test('초대 수락: body token만 허용하며 세션과 CSRF를 함께 요구', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody.content['application/json'].schema.$ref, '#/components/schemas/StoreInvitationTokenRequest');
  assert.ok(op.responses['409']); assert.ok(op.responses['410']);
});
