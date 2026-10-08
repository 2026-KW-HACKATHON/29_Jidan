import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const spec = parse(await readFile(new URL('../../openapi.yaml', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(spec, 'https://jidan.example/contract');
const validator = name => ajv.compile({ $ref: `https://jidan.example/contract#/components/schemas/${name}` });
const validateWorker = validator('WorkerRegistrationRequest');
const validateOwner = validator('OwnerRegistrationRequest');
const validateSession = validator('Session');
const workerOperation = spec.paths['/api/auth/registrations/workers'].post;
const ownerOperation = spec.paths['/api/auth/registrations/owners'].post;
const worker = workerOperation.requestBody.content['application/json'].examples.newWorker.value;
const experienced = workerOperation.requestBody.content['application/json'].examples.currentCareerNight.value;
const owner = ownerOperation.requestBody.content['application/json'].example;
const session = ownerOperation.responses['201'].content['application/json'].example;
const clone = value => structuredClone(value);

test('전체 요청/응답 예시가 참조된 JSON Schema를 만족', () => {
  for (const [path, item] of Object.entries(spec.paths)) {
    for (const [method, operation] of Object.entries(item)) {
      const media = [operation.requestBody, ...Object.values(operation.responses)];
      for (const entry of media) {
        const resolved = entry?.$ref ? spec.components.responses[entry.$ref.split('/').at(-1)] : entry;
        for (const body of Object.values(resolved?.content ?? {})) {
          const validate = ajv.compile({ ...body.schema, $id: undefined, $ref: body.schema.$ref?.replace('#/', 'https://jidan.example/contract#/') });
          const examples = 'example' in body ? [body.example] : Object.values(body.examples ?? {}).map(e => e.value);
          for (const example of examples) assert.ok(validate(example), `${method} ${path}: ${JSON.stringify(validate.errors)}`);
        }
      }
    }
  }
});

test('신입, 현재 근무 중 경력, 심야 시간 요청 예시 허용', () => {
  for (const example of [worker, experienced]) assert.ok(validateWorker(example), JSON.stringify(validateWorker.errors));
  const ended = clone(experienced);
  ended.careers[0].isCurrent = false;
  ended.careers[0].endMonth = '2025-02';
  assert.ok(validateWorker(ended));
});

const invalidWorkers = [
  ['공백 이름', w => w.name = '   '],
  ['누락 전화번호', w => delete w.phoneNumber],
  ['하이픈 전화번호', w => w.phoneNumber = '010-1234-5678'],
  ['문자 혼합 전화번호', w => w.phoneNumber = '0101234567a'],
  ['달력에 없는 생일', w => w.birthDate = '2001-02-30'],
  ['잘못된 성별', w => w.gender = 'INVALID'],
  ['신입 경력 기입', w => w.careers = clone(experienced.careers)],
  ['경력 있음인데 경력 없음', w => w.experienceLevel = 'EXPERIENCED'],
  ['근무 가능 시간 없음', w => w.availabilities = []],
  ['중복 요일', w => w.availabilities[0].days = ['MON', 'MON']],
  ['빈 요일', w => w.availabilities[0].days = []],
  ['잘못된 요일', w => w.availabilities[0].days = ['HOLIDAY']],
  ['30분 단위 아님', w => w.availabilities[0].startTime = '09:15'],
  ['24:00 시각', w => w.availabilities[0].endTime = '24:00'],
  ['심야 표시 누락', w => delete w.availabilities[0].endsNextDay],
  ['Google 이메일 직접 주입', w => w.email = 'other@example.com'],
  ['역할 직접 주입', w => w.role = 'OWNER'],
];
for (const [name, mutate] of invalidWorkers) test(`일반회원 유효성: ${name} 거절`, () => {
  const value = clone(worker); mutate(value); assert.equal(validateWorker(value), false);
});
for (const [name, mutate] of [
  ['현재 근무 중인데 종료일 제공', c => c.endMonth = '2025-02'],
  ['종료 경력인데 종료일 null', c => c.isCurrent = false],
  ['존재하지 않는 시작 월', c => c.startMonth = '2024-13'],
  ['빈 담당 업무', c => c.duties = '  '],
]) test(`경력 유효성: ${name} 거절`, () => {
  const value = clone(experienced); mutate(value.careers[0]); assert.equal(validateWorker(value), false);
});

test('점주 기본 정보 및 매장 신청 허용, 선택 상세주소 생략 허용', () => {
  assert.ok(validateOwner(owner), JSON.stringify(validateOwner.errors));
  const value = clone(owner); delete value.store.detailAddress; assert.ok(validateOwner(value));
});
for (const [name, mutate] of [
  ['사업자 번호 길이', o => o.store.businessRegistrationNumber = '123'],
  ['우편번호 숫자 타입', o => o.store.postalCode = 1897],
  ['승인 상태 주입', o => o.store.approvalStatus = 'APPROVED'],
  ['지역 판정 주입', o => o.store.region = '월계1동'],
  ['빈 주소', o => o.store.address = ' '],
  ['매장 연락처 누락', o => delete o.store.phoneNumber],
]) test(`점주 유효성: ${name} 거절`, () => {
  const value = clone(owner); mutate(value); assert.equal(validateOwner(value), false);
});

test('승인 대기 점주는 상태 조회만 가능하고 승인 후에만 운영 권한 허용', () => {
  assert.ok(validateSession(session), JSON.stringify(validateSession.errors));
  const invalid = clone(session);
  invalid.user.stores[0].permissions.push('INVITE_WORKERS');
  assert.equal(validateSession(invalid), false);
  const approved = clone(session);
  approved.user.stores[0].approvalStatus = 'APPROVED';
  approved.user.stores[0].permissions = ['READ_STORE_STATUS', 'MANAGE_STORE', 'INVITE_WORKERS', 'MANAGE_JOB_POSTINGS', 'MANAGE_MANUALS'];
  approved.nextAction = 'OWNER_HOME';
  assert.ok(validateSession(approved), JSON.stringify(validateSession.errors));
  approved.nextAction = 'WORKER_HOME';
  assert.equal(validateSession(approved), false);
});

test('가입 변경 요청은 쿠키와 CSRF, idempotency key를 함께 요구', () => {
  for (const op of [workerOperation, ownerOperation]) {
    assert.equal(op.parameters[0].$ref, '#/components/parameters/IdempotencyKey');
    for (const security of op.security) {
      assert.ok('CsrfToken' in security);
      assert.ok('RegistrationCookie' in security || 'SessionCookie' in security);
    }
  }
  assert.equal(spec.components.parameters.IdempotencyKey.required, true);
  assert.deepEqual(spec.paths['/api/auth/registration'].get.security, [{ RegistrationCookie: [] }]);
});
