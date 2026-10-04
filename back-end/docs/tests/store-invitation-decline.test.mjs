import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/store-invitations/decline'].post;
test('초대 거절: DECLINED와 최초 시각만 반환하며 자료 권한 제외', () => {
  const validate = validator('StoreInvitationDeclineResult');
  const result = op.responses['200'].content['application/json'].example;
  assert.ok(validate(result));
  assert.equal(validate({ ...result, status: 'ACCEPTED' }), false);
  assert.equal(validate({ ...result, declinedAt: null }), false);
  assert.equal(validate({ ...result, accessGrant: {} }), false);
  assert.equal(validate({ ...result, token: 'secret' }), false);
});
test('초대 거절: 회원 세션과 CSRF·token 단독 본문 및 완료/만료 오류 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody.content['application/json'].schema.$ref, '#/components/schemas/StoreInvitationTokenRequest');
  assert.ok(op.responses['409']); assert.ok(op.responses['410']);
});
