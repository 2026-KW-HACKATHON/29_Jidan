import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/profile-careers');
const validate = ajv.compile({ $ref: 'https://jidan.example/profile-careers#/components/schemas/WorkerCareersReplaceRequest' });
const op = spec.paths['/api/users/me/profile/careers'].put;
const input = op.requestBody.content['application/json'].examples.currentCareer.value;
const career = input.careers[0];

test('근무 정보: 신입 전환, 선택 매장명 생략, 같은 달 종료, 20건 경계 허용', () => {
  const { storeName, ...withoutStore } = career;
  for (const value of [
    { experienceLevel: 'NEW', careers: [] }, { ...input, careers: [withoutStore] },
    { ...input, careers: [{ ...withoutStore, isCurrent: false, endMonth: '2024-03' }] },
    { ...input, careers: Array.from({ length: 20 }, () => structuredClone(career)) },
  ]) assert.ok(validate(value), JSON.stringify(validate.errors));
});
for (const [name, mutate] of [
  ['신입에 경력 존재', v => v.experienceLevel = 'NEW'],
  ['경력인데 빈 목록', v => v.careers = []], ['21건', v => v.careers = Array(21).fill(career)],
  ['경력 유형 누락', v => delete v.experienceLevel], ['경력 목록 누락', v => delete v.careers],
  ['미정 경력 유형', v => v.experienceLevel = 'UNKNOWN'], ['null 목록', v => v.careers = null],
  ['현재 근무에 종료 연월', v => v.careers[0].endMonth = '2025-02'],
  ['종료 경력에 null', v => v.careers[0].isCurrent = false],
  ['현재 근무 여부 누락', v => delete v.careers[0].isCurrent],
  ['종료일 필드 누락', v => delete v.careers[0].endMonth],
  ['잘못된 연월', v => v.careers[0].startMonth = '2024-13'],
  ['잘못된 연월 자릿수', v => v.careers[0].startMonth = '2024-3'],
  ['미정 업종', v => v.careers[0].industry = 'UNKNOWN'],
  ['빈 담당 업무', v => v.careers[0].duties = '  '],
  ['업무 301자', v => v.careers[0].duties = '가'.repeat(301)],
  ['빈 선택 매장명', v => v.careers[0].storeName = ''],
  ['매장명 101자', v => v.careers[0].storeName = '가'.repeat(101)],
  ['외부 경력 ID', v => v.careers[0].id = 'other'],
  ['기본 정보 함께 변경', v => v.name = '김지민'],
  ['가능 시간 함께 변경', v => v.availabilities = []],
  ['관리자 password 주입', v => v.password = 'secret'],
]) test(`근무 정보 교체: ${name} 거절`, () => {
  const value = structuredClone(input); mutate(value); assert.equal(validate(value), false);
});
test('근무 정보: CSRF와 회원 세션을 함께 요구하고 검증 실패 계약 제공', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody.required, true);
  assert.equal(op.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/WorkerProfile');
  assert.ok(op.responses['422']);
});
