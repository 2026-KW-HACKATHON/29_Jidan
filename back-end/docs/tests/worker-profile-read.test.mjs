import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/profile-read');
const validate = ajv.compile({ $ref: 'https://jidan.example/profile-read#/components/schemas/WorkerProfile' });
const op = spec.paths['/api/users/me/profile'].get;
const profile = op.responses['200'].content['application/json'].example;

test('내 프로필: 신입과 기존 경력 회원의 전체 데이터 허용', () => {
  assert.ok(validate(profile), JSON.stringify(validate.errors));
  const careers = spec.paths['/api/auth/registrations/workers'].post.requestBody.content['application/json'].examples.currentCareerNight.value;
  assert.ok(validate({ ...profile, ...careers }), JSON.stringify(validate.errors));
});
for (const [name, mutate] of [
  ['누락 이메일', p => delete p.identity.email],
  ['미검증 이메일', p => p.identity.emailVerified = false],
  ['잘못된 이메일', p => p.identity.email = 'invalid'],
  ['점주 역할', p => p.role = 'OWNER'],
  ['잘못된 회원 ID', p => p.id = '123'],
  ['잘못된 변경 시각', p => p.updatedAt = 'invalid'],
  ['누락 기본 정보', p => delete p.birthDate],
  ['누락 경력 목록', p => delete p.careers],
  ['누락 가능 시간', p => delete p.availabilities],
  ['인증 비밀 노출', p => p.password = 'secret'],
]) test(`내 프로필 응답: ${name} 거절`, () => {
  const value = structuredClone(profile); mutate(value); assert.equal(validate(value), false);
});
test('내 프로필: 가입 세션과 외부 회원 ID 입력을 허용하지 않는 계약', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [] }]);
  assert.equal(op.requestBody, undefined);
  assert.equal(op.parameters, undefined);
  assert.match(op.responses['401'].description, /REGISTRATION_REQUIRED/);
  assert.match(op.responses['403'].description, /ACCOUNT_SUSPENDED/);
});
