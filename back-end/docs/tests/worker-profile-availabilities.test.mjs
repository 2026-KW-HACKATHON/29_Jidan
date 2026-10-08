import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/profile-availabilities');
const validate = ajv.compile({ $ref: 'https://jidan.example/profile-availabilities#/components/schemas/WorkerAvailabilitiesReplaceRequest' });
const op = spec.paths['/api/users/me/profile/availabilities'].put;
const input = op.requestBody.content['application/json'].examples.weekdays.value;
const time = slot => `${String(Math.floor(slot / 2)).padStart(2, '0')}:${slot % 2 ? '30' : '00'}`;
const slots = length => Array.from({ length }, (_, index) => ({
  days: [['MON', 'TUE', 'WED'][Math.floor(index / 48)]],
  startTime: time(index % 48), endTime: time((index + 1) % 48), endsNextDay: index % 48 === 47,
}));

test('가능 시간: 100개 인접 구간 상한 및 일요일 심야·자정 예시 허용', () => {
  for (const value of [...Object.values(op.requestBody.content['application/json'].examples).map(e => e.value), { availabilities: slots(100) }]) {
    assert.ok(validate(value), JSON.stringify(validate.errors));
  }
  assert.ok(validate({ availabilities: [{ days: ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'], startTime: '00:00', endTime: '00:30', endsNextDay: false }] }));
});
for (const [name, mutate] of [
  ['목록 누락', v => delete v.availabilities], ['빈 목록', v => v.availabilities = []],
  ['null 목록', v => v.availabilities = null], ['101개 구간', v => v.availabilities = slots(101)],
  ['요일 없음', v => v.availabilities[0].days = []], ['요일 중복', v => v.availabilities[0].days = ['MON', 'MON']],
  ['미정 요일', v => v.availabilities[0].days = ['HOLIDAY']], ['요일 숫자', v => v.availabilities[0].days = [1]],
  ['15분 시작', v => v.availabilities[0].startTime = '09:15'],
  ['15분 종료', v => v.availabilities[0].endTime = '14:15'],
  ['24시 종료', v => v.availabilities[0].endTime = '24:00'],
  ['시각 한 자리', v => v.availabilities[0].startTime = '9:00'],
  ['시작 시각 누락', v => delete v.availabilities[0].startTime],
  ['종료 시각 누락', v => delete v.availabilities[0].endTime],
  ['심야 표시 누락', v => delete v.availabilities[0].endsNextDay],
  ['문자열 심야 표시', v => v.availabilities[0].endsNextDay = 'false'],
  ['외부 시간 ID', v => v.availabilities[0].id = 'other'],
  ['기본 정보 주입', v => v.name = '김지민'], ['경력 정보 주입', v => v.careers = []],
  ['관리자 password 주입', v => v.password = 'secret'],
]) test(`가능 시간 교체: ${name} 거절`, () => {
  const value = structuredClone(input); mutate(value); assert.equal(validate(value), false);
});
test('가능 시간: 회원 세션/CSRF와 검증 실패 응답, 전체 프로필 반환', () => {
  assert.deepEqual(op.security, [{ SessionCookie: [], CsrfToken: [] }]);
  assert.equal(op.requestBody.required, true);
  assert.equal(op.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/WorkerProfile');
  assert.match(op.responses['403'].description, /CSRF_INVALID/);
  assert.ok(op.responses['422']);
});
