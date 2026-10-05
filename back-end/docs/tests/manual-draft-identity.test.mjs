import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';

for (const [suffix, method] of [['content', 'put'], ['acknowledgements', 'post'], ['publication', 'post']]) {
  const op = spec.paths[`/api/stores/{storeId}/manual/draft/${suffix}`][method];
  const request = op.requestBody.content['application/json'];
  const validate = validator(request.schema.$ref.split('/').at(-1));
  test(`${suffix}: 조회한 초안 UUID와 revision을 함께 필수로 제출`, () => {
    for (const { value } of Object.values(request.examples)) {
      assert.ok(validate(value), JSON.stringify(validate.errors));
      assert.equal(value.expectedVersionId, op.responses['200'].content['application/json'].example.versionId);
      for (const expectedVersionId of [undefined, null, '', 'not-a-uuid', 5]) {
        assert.equal(validate({ ...value, expectedVersionId }), false);
      }
      for (const expectedRevision of [undefined, null, 0, -1, 1.5]) {
        assert.equal(validate({ ...value, expectedRevision }), false);
      }
    }
  });
  // DB 상태 비교는 Schema로 수행할 수 없으므로 서버 구현 전까지 계약과 오류 예시를 검사합니다.
  test(`${suffix}: 초안 교체·동일 revision·초안 없음·멱등성의 서버 계약`, () => {
    for (const pattern of [/다른 초안에서 같은 revision 값이 재사용/,
      /현재 초안이 없거나 expectedVersionId와 다르면 revision이 같아도 409 MANUAL_VERSION_CONFLICT/,
      /ID가 일치한 뒤 revision이 다르면 409 REVISION_CONFLICT/,
      /포인터를 잠그고 ID 비교·revision 비교·상태 검증·변경을 같은 트랜잭션/,
      /게시·새 초안 생성도 같은 매뉴얼 잠금/,
      /최초 결과만 재현하고 현재 초안에 작업을 다시 적용하지/,
      /같은 key로 다른 버전을 보내면 409 IDEMPOTENCY_KEY_REUSED/,
      /이전 입력·확인 의사를 새 versionId에 자동 적용하지/]) assert.match(op.description, pattern);
    assert.match(op.responses['409'].description, /MANUAL_VERSION_CONFLICT/);
    const errors = op.responses['409'].content['application/json'].examples;
    for (const { value } of Object.values(errors)) assert.ok(validator('Error')(value));
    assert.equal(errors.versionConflict.value.code, 'MANUAL_VERSION_CONFLICT');
    assert.equal(errors.revisionConflict.value.code, 'REVISION_CONFLICT');
  });
}
