import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const root='/api/stores/{storeId}/manual/interviews/{sessionId}/intents/{intentId}/review';
test('이해 확인: 현재 revision과 명시적 true만 허용',()=>{const v=validator('ManualInterviewConfirm');assert.ok(v({expectedRevision:3,confirmed:true}));for(const x of [{expectedRevision:3,confirmed:false},{expectedRevision:3},{expectedRevision:0,confirmed:true},{expectedRevision:3,confirmed:true,coverage:'COVERED'}])assert.equal(v(x),false);});
test('정정: 원래 답변 덮어쓰기·depth 증가 없이 새 이력 보존',()=>{const op=spec.paths[root+'/corrections'].post;assert.ok(validator('ManualInterviewCorrection')(op.requestBody.content['application/json'].examples.default.value));assert.equal(op.responses['202'].content['application/json'].example.processing.kind,'CORRECTION');assert.match(op.description,/새 CORRECTION 턴/);assert.match(op.description,/depth를 증가시키거나/);});
test('인텐트별 선택적 검토는 다음 질문과 초안 생성의 조건이 아님',()=>{const op=spec.paths[root+'/confirmations'].post;const s=op.responses['200'].content['application/json'].example;assert.equal(s.status,'READY');assert.ok(s.confirmedAt);assert.match(op.description,/선행 조건이 아닙니다/);assert.match(op.description,/다음 질문을 생성하거나.*진행시키지/);assert.ok(validator('ManualIntentReview')(s));});

test('생성 전 참조 충돌은 인텐트 정정으로 복구하고 READY 초안 편집과 구분', () => {
  const correction = spec.paths[root + '/corrections'].post;
  const completion = spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}/completion'].post;
  const edit = spec.paths['/api/stores/{storeId}/manual/draft/content'].put;
  assert.match(completion.description, /참조 불일치는 409 MANUAL_REFERENCE_CONFLICT/);
  assert.match(correction.description, /생성 전에.*review\/corrections/);
  assert.match(correction.description, /재연결하거나.*삭제·재분류/);
  assert.match(correction.description, /READY.*전체 검토와 sessionRevision.*completion을 재요청/);
  assert.match(correction.description, /생성 전 참조 충돌의 복구 경로로 사용할 수 없습니다/);
  assert.doesNotMatch(correction.description, /또는 최종 초안 편집 전까지/);
  assert.match(edit.description, /READY 초안의 content 전체를 교체/);
  assert.match(edit.description, /진행\/생성\/오류 초안.*409 MANUAL_STATE_CONFLICT/);
});
