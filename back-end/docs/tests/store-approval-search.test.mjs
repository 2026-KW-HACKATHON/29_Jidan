import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/store-search');
const validate = name => ajv.compile({ $ref: `https://jidan.example/store-search#/components/schemas/${name}` });
const search = validate('StoreApprovalSearchRequest');
const page = validate('StoreApprovalPage');
const item = validate('StoreApprovalRequest');
const op = spec.paths['/api/admin/store-approval-requests/search'].post;
const pending = op.responses['200'].content['application/json'].examples.pending.value.items[0];

test('관리자 조회: 전체 조회 기본값과 최대 페이지 크기 허용', () => {
  for (const input of [{ password: '<admin-password>' }, { password: '<admin-password>', page: 0 }, { password: '<admin-password>', status: 'APPROVED', page: 2, size: 100 }]) {
    assert.ok(search(input), JSON.stringify(search.errors));
  }
});
for (const [name, input] of [
  ['비밀번호 누락', {}], ['빈 비밀번호', { password: '' }], ['공백 비밀번호', { password: '   ' }],
  ['비밀번호 숫자 타입', { password: 1234 }], ['비밀번호 과대 길이', { password: 'a'.repeat(1025) }],
  ['미정 상태', { password: '<admin-password>', status: 'REJECTED' }],
  ['음수 페이지', { password: '<admin-password>', page: -1 }],
  ['소수 페이지', { password: '<admin-password>', page: 1.5 }], ['0 크기', { password: '<admin-password>', size: 0 }],
  ['최대 크기 초과', { password: '<admin-password>', size: 101 }],
  ['임의 관리자 역할 주입', { password: '<admin-password>', role: 'ADMIN' }],
]) test(`관리자 조회 입력: ${name} 거절`, () => assert.equal(search(input), false));

test('승인 신청: PENDING/null 및 APPROVED/승인 시각만 허용', () => {
  assert.ok(item(pending), JSON.stringify(item.errors));
  const approved = { ...pending, status: 'APPROVED', approvedAt: '2026-10-02T11:00:00Z' };
  assert.ok(item(approved), JSON.stringify(item.errors));
  assert.equal(item({ ...pending, approvedAt: '2026-10-02T11:00:00Z' }), false);
  assert.equal(item({ ...approved, approvedAt: null }), false);
  assert.equal(item({ ...approved, approvedAt: 'invalid' }), false);
  assert.equal(item({ ...pending, password: 'must-not-be-returned' }), false);
});

test('신청 페이지: 빈 결과와 마지막 페이지 이후의 빈 결과 허용', () => {
  for (const value of [
    { items: [], page: 1, size: 20, totalItems: 0, totalPages: 0 },
    { items: [], page: 3, size: 20, totalItems: 1, totalPages: 1 },
  ]) assert.ok(page(value), JSON.stringify(page.errors));
  assert.equal(page({ items: [], page: 1, size: 20, totalItems: -1, totalPages: 0 }), false);
});

test('관리자 인증: body password 필수 및 기존 세션 인증 제외, 응답 비밀번호 제외', () => {
  assert.deepEqual(op.security, []);
  assert.equal(op.requestBody.required, true);
  assert.ok(spec.components.schemas.StoreApprovalSearchRequest.required.includes('password'));
  assert.equal(spec.components.schemas.AdminPassword.writeOnly, true);
  assert.equal(spec.components.schemas.AdminPassword.format, 'password');
  assert.equal(op.responses['401'].$ref, '#/components/responses/AdminPasswordUnauthorized');
  assert.equal('password' in spec.components.schemas.StoreApprovalRequest.properties, false);
  assert.equal('password' in spec.components.schemas.StoreApprovalApplicant.properties, false);
  assert.equal('password' in spec.components.schemas.StoreApprovalStore.properties, false);
});
