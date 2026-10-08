import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, parameterValidator, validator } from './helpers/owner-contract.mjs';

function checkPage(schema) {
  const validate = parameterValidator({ schema });
  assert.equal(schema.minimum, 0);
  for (const value of [0, 1, 100]) assert.ok(validate(value));
  for (const value of [-1, 0.5, '0', null]) assert.equal(validate(value), false);
}

test('전체 목록 query는 page=0 기본값과 동일한 정수 경계 사용', () => {
  assert.doesNotMatch(JSON.stringify(spec), /page=1|1부터 시작(?:하는 페이지|\. 생략 시 1)/);
  let count = 0;
  for (const item of Object.values(spec.paths)) {
    for (const operation of Object.values(item)) {
      for (const parameter of operation.parameters ?? []) {
        if (parameter.name !== 'page') continue;
        checkPage(parameter.schema);
        assert.equal(parameter.schema.default, 0);
        count++;
      }
    }
  }
  assert.equal(count, 15);
});

test('관리자 검색 body와 모든 목록 응답 schema도 page=0 허용', () => {
  let count = 0;
  for (const schema of Object.values(spec.components.schemas)) {
    if (!schema.properties?.page) continue;
    checkPage(schema.properties.page);
    count++;
  }
  assert.equal(count, 16);
  const search = validator('StoreApprovalSearchRequest');
  assert.ok(search({ password: '<admin-password>' }));
  assert.ok(search({ password: '<admin-password>', page: 0 }));
  assert.equal(spec.components.schemas.StoreApprovalSearchRequest.properties.page.default, 0);
});
