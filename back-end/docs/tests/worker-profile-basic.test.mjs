import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/profile-basic');
const validate = ajv.compile({ $ref: 'https://jidan.example/profile-basic#/components/schemas/WorkerBasicProfileUpdateRequest' });
const op = spec.paths['/api/users/me/profile/basic'].patch;

test('기본 정보: 각 필드 단독 변경 및 이름 길이 경계 허용', () => {
  for (const value of [{ name: '가' }, { name: '가'.repeat(50) }, { name: '김 지민' }, { phoneNumber: '01012345678' }, { birthDate: '2000-02-29' }, { gender: 'MALE' }]) {
    assert.ok(validate(value), JSON.stringify(validate.errors));
  }
});
for (const [name, value] of [
  ['빈 객체', {}], ['null body', null], ['배열 body', []], ['빈 이름', { name: '' }],
  ['공백 이름', { name: '   ' }], ['이름 51자', { name: '가'.repeat(51) }],
  ['하이픈 전화번호', { phoneNumber: '010-1234-5678' }], ['숫자 전화번호', { phoneNumber: 1012345678 }],
  ['짧은 전화번호', { phoneNumber: '0101234567' }], ['불가능한 날짜', { birthDate: '2001-02-29' }],
  ['생일 시각 입력', { birthDate: '2000-02-29T00:00:00Z' }], ['미정 성별', { gender: 'UNKNOWN' }],
]) test(`기본 정보: ${name} 거절`, () => assert.equal(validate(value), false));
for (const field of ['name', 'phoneNumber', 'birthDate', 'gender']) {
  test(`기본 정보: ${field} null 거절`, () => assert.equal(validate({ [field]: null }), false));
}
for (const [field, value] of Object.entries({ email: 'other@example.com', identity: {}, id: '7390d3c1-dc0d-4b1c-814d-b68a1df5c1b6', userId: 'other', role: 'OWNER', provider: 'GOOGLE', password: 'secret', careers: [], availabilities: [], updatedAt: '2026-10-03T01:00:00Z' })) {
  test(`기본 정보: ${field} 변경 주입 거절`, () => assert.equal(validate({ name: '김지민', [field]: value }), false));
}
test('기본 정보: 회원 세션과 CSRF를 동시에 요구하고 전체 프로필 반환', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody.required, true);
  assert.equal(op.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/WorkerProfile');
  assert.match(op.responses['403'].description, /CSRF_INVALID/);
  assert.ok(op.responses['422']);
});
