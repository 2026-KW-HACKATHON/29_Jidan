import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';

const root = '/api/stores/{storeId}/manual';
const create = spec.paths[root + '/draft/corrections'].post;
const read = spec.paths[root + '/draft/corrections/{correctionId}'].get;
const retry = spec.paths[root + '/draft/corrections/{correctionId}/retries'].post;
const request = create.requestBody.content['application/json'].examples.voice.value;
const cases = Object.fromEntries(Object.entries(read.responses['200'].content['application/json'].examples).map(([key, item]) => [key, item.value]));
const validateInput = validator('ManualDraftCorrectionInput');
const validateJob = validator('ManualDraftCorrection');
const validateRetry = validator('ManualDraftCorrectionRetry');

test('초안 정정: 음성·텍스트와 전체/근무조/섹션 대상 허용', () => {
  for (const { value } of Object.values(create.requestBody.content['application/json'].examples)) assert.ok(validateInput(value), JSON.stringify(validateInput.errors));
  for (const target of [{kind:'MANUAL',targetId:null}, request.target, {kind:'SECTION',targetId:request.target.targetId}]) assert.ok(validateInput({...request,target}));
});

for (const [label, patch] of [
  ['초안 ID 누락', {expectedVersionId:undefined}], ['초안 ID 오류', {expectedVersionId:'bad'}],
  ['revision 누락', {expectedRevision:undefined}], ['revision 0', {expectedRevision:0}],
  ['revision 소수', {expectedRevision:1.5}], ['대상 누락', {target:undefined}],
  ['전체 대상에 ID', {target:{kind:'MANUAL',targetId:request.target.targetId}}],
  ['항목 대상에 null', {target:{kind:'SECTION',targetId:null}}],
  ['지원하지 않는 대상', {target:{kind:'STEP',targetId:request.target.targetId}}],
  ['전사 ID 누락', {input:{method:'VOICE'}}], ['전사에 텍스트 혼합', {input:{...request.input,text:'변경'}}],
  ['공백 정정', {input:{method:'TEXT',text:'  '}}], ['서버 상태 주입', {status:'SUCCEEDED'}],
]) test(`초안 정정 입력 거절: ${label}`, () => assert.equal(validateInput({...request,...patch}),false));

test('정정 작업: 진행·성공·무변경·실패·불명확 예시와 결과 연결', () => {
  for (const sample of Object.values(cases)) {
    assert.ok(validateJob(sample), JSON.stringify(validateJob.errors));
    assert.equal(sample.versionId, request.expectedVersionId);
    assert.equal(sample.baseRevision, request.expectedRevision);
    assert.deepEqual(sample.target, request.target);
    if (sample.completedAt) assert.ok(Date.parse(sample.completedAt) >= Date.parse(sample.createdAt));
    if (sample.status === 'SUCCEEDED') assert.ok([sample.baseRevision,sample.baseRevision+1].includes(sample.resultRevision));
  }
  assert.equal(cases.unchanged.resultRevision,cases.unchanged.baseRevision);
});

test('정정 상태별 결과·오류·완료 시각과 내부 정보 노출 금지', () => {
  for (const sample of [
    {...cases.running,error:cases.failed.error}, {...cases.running,resultRevision:2},
    {...cases.running,completedAt:cases.succeeded.completedAt},
    {...cases.succeeded,resultRevision:null}, {...cases.succeeded,completedAt:null},
    {...cases.succeeded,error:cases.failed.error}, {...cases.failed,error:null},
    {...cases.failed,resultRevision:2}, {...cases.failed,completedAt:null},
    {...cases.failed,error:{...cases.failed.error,retryable:false}},
    {...cases.clarification,error:{...cases.clarification.error,retryable:true}},
    {...cases.failed,error:{...cases.failed.error,providerResponse:'secret'}},
    {...cases.running,attempt:0}, {...cases.running,transcript:'private'},
  ]) assert.equal(validateJob(sample),false);
});

test('재시도는 초안 식별자만 받고 원래 입력·대상 교체를 거절', () => {
  const body = retry.requestBody.content['application/json'].example;
  assert.ok(validateRetry(body));
  for (const patch of [{expectedVersionId:null},{expectedRevision:0},{input:request.input},{target:request.target},{attempt:2}]) assert.equal(validateRetry({...body,...patch}),false);
  const accepted = retry.responses['202'].content['application/json'].examples.running.value;
  assert.ok(validateJob(accepted));
  assert.equal(accepted.id,cases.running.id);
  assert.equal(accepted.attempt,cases.running.attempt+1);
});

test('기존 초안 응답과 처리 중·실패 시 저장 내용 보존 호환', () => {
  const validate = validator('ManualDraft');
  const draft = spec.paths[root+'/draft'].get.responses['200'].content['application/json'].example;
  for (const latestCorrection of [undefined,null,cases.running,cases.failed,cases.succeeded]) assert.ok(validate({...draft,latestCorrection}),JSON.stringify(validate.errors));
  assert.equal(validate({...draft,latestCorrection:cases.running,content:null}),false);
  for (const generationStatus of ['NOT_STARTED','RUNNING','ERROR']) {
    assert.equal(validate({...draft,generationStatus,content:null,latestCorrection:cases.running}),false);
    assert.ok(validate({...draft,generationStatus,content:null,latestCorrection:null}));
  }
  assert.equal(validate({...draft,latestCorrection:{id:cases.running.id}}),false);
});

// 아래는 서버 구현 전 계약 검증입니다. DB 잠금·AI 적용·실제 경쟁 상황을 실행한 테스트가 아닙니다.
test('전사 원문 확인 없이 제출하며 작업에 사용한 전사는 같은 매장 READY로 제한', () => {
  assert.match(spec.paths[root+'/transcriptions'].post.description,/원문 확인 없이 VOICE/);
  assert.match(create.description,/같은 매장의 READY 전사/);
  assert.match(create.description,/TRANSCRIPTION_NOT_READY/);
});
test('정정과 게시·편집·확인·초안 교체는 같은 잠금과 차단 오류를 사용', () => {
  for (const [suffix,method] of [['/draft/content','put'],['/draft/acknowledgements','post'],['/draft/publication','post'],['/interviews','post']]) {
    const operation = spec.paths[root+suffix][method];
    assert.match(operation.description,/latestCorrection.status=RUNNING/);
    assert.match(operation.description,/같은 매뉴얼 잠금/);
    assert.match(operation.responses['409'].description,/MANUAL_CORRECTION_IN_PROGRESS/);
  }
  for (const pattern of [/task ID와 attempt/,/versionId·baseRevision·latestCorrection.id·RUNNING/,/늦은\/중복 결과는 적용하지/,/영구 RUNNING을 방지/,/원자적으로 수행/,/초안당 RUNNING 작업은 하나/]) assert.match(create.description,pattern);
});
test('원본 보존·확인 초기화·참조 정합성과 모호한 정정 실패 계약', () => {
  for (const pattern of [/기존 근무조·섹션·단계 ID와 무관한 내용·사진을 보존/,/부족 항목 확인 초기화/,/MANUAL_REFERENCE_CONFLICT/,/CORRECTION_CLARIFICATION_REQUIRED/,/실패는 이전 content·issues·확인·revision을 그대로 보존/,/실질 변경이 없으면 확인·revision을 유지/,/명시적으로 다시 확인/]) assert.match(create.description,pattern);
});
test('멱등성과 재시도는 오래된 작업·교체된 초안·변경된 revision에 적용하지 않음', () => {
  for (const pattern of [/같은 key.*최초 결과만 재현/,/MANUAL_VERSION_CONFLICT/,/REVISION_CONFLICT/,/latestCorrection만 재시도/,/retryable=false/,/원본 음성이 정리되어도/,/새로운 내부 task ID/]) assert.match(retry.description,pattern);
  assert.deepEqual(create.security,[{SessionCookie:[],CsrfToken:[]}]);
  assert.deepEqual(retry.security,create.security);
  assert.deepEqual(read.security,[{SessionCookie:[]}]);
  assert.match(read.description,/현재 소유권·APPROVED/);
});

for (const [label, operation] of [['접수', create], ['재시도', retry]]) {
  test(`초안 정정 ${label}: 작업 예약 실패는 503 공통 오류 계약으로 반환`, () => {
    const response = operation.responses['503'];
    assert.ok(response, '설명에 명시된 작업 예약 실패 응답이 필요합니다.');
    assert.match(response.description, /rollback/);
    const { schema, example } = response.content['application/json'];
    assert.equal(schema.$ref, '#/components/schemas/Error');
    assert.equal(example.code, 'JOB_QUEUE_UNAVAILABLE');
    const validateError = validator('Error');
    assert.ok(validateError(example), JSON.stringify(validateError.errors));
    assert.deepEqual(example.fieldErrors, []);
    assert.equal(validateError({ ...example, fieldErrors: null }), false);
  });
}
