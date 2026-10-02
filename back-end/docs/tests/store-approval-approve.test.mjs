import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/store-approve');
const schema = name => ajv.compile({ $ref: `https://jidan.example/store-approve#/components/schemas/${name}` });
const request = schema('StoreApprovalApproveRequest');
const result = schema('StoreApprovalResult');
const op = spec.paths['/api/admin/store-approval-requests/{requestId}/approve'].post;
const approved = op.responses['200'].content['application/json'].example;

test('승인 요청: password 단일 필드와 UUID 신청 ID 허용', () => {
  assert.ok(request({ password: '<admin-password>' }), JSON.stringify(request.errors));
  const validateId = ajv.compile(op.parameters[0].schema);
  assert.ok(validateId(approved.id));
  assert.equal(validateId('not-a-uuid'), false);
});
for (const [name, input] of [
  ['비밀번호 누락', {}], ['빈 비밀번호', { password: '' }], ['공백 비밀번호', { password: ' ' }],
  ['비밀번호 null', { password: null }],
  ['승인 상태 주입', { password: '<admin-password>', status: 'APPROVED' }],
  ['승인 시각 주입', { password: '<admin-password>', approvedAt: '2026-10-02T11:00:00Z' }],
  ['권한 주입', { password: '<admin-password>', permissions: ['MANAGE_STORE'] }],
  ['승인자 ID 주입', { password: '<admin-password>', approvedBy: approved.applicant.id }],
]) test(`승인 요청: ${name} 거절`, () => assert.equal(request(input), false));

test('승인 응답: APPROVED와 최초 승인 시각 필수, 비밀번호 반환 거절', () => {
  assert.ok(result(approved), JSON.stringify(result.errors));
  assert.equal(result({ ...approved, status: 'PENDING', approvedAt: null }), false);
  assert.equal(result({ ...approved, approvedAt: null }), false);
  assert.equal(result({ ...approved, approvedAt: 'invalid' }), false);
  assert.equal(result({ ...approved, password: 'must-not-be-returned' }), false);
});

test('승인 계약: body 관리자 인증과 없음·승인 불가·재시도 응답 명세', () => {
  assert.deepEqual(op.security, []);
  assert.equal(op.requestBody.required, true);
  assert.equal(op.responses['401'].$ref, '#/components/responses/AdminPasswordUnauthorized');
  assert.equal(op.responses['404'].content['application/json'].example.code, 'STORE_APPROVAL_REQUEST_NOT_FOUND');
  assert.equal(op.responses['409'].content['application/json'].example.code, 'STORE_APPROVAL_NOT_ALLOWED');
  assert.equal(op.parameters.some(p => p.name === 'Idempotency-Key' || p.$ref === '#/components/parameters/IdempotencyKey'), false);
});
