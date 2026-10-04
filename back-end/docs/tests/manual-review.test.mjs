import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const root='/api/stores/{storeId}/manual/interviews/{sessionId}';
test('이해 확인: 현재 revision과 명시적 true만 허용',()=>{const v=validator('ManualInterviewConfirm');assert.ok(v({expectedRevision:3,confirmed:true}));for(const x of [{expectedRevision:3,confirmed:false},{expectedRevision:3},{expectedRevision:0,confirmed:true},{expectedRevision:3,confirmed:true,coverage:'COVERED'}])assert.equal(v(x),false);});
test('정정: 원래 답변 덮어쓰기·depth 증가 없이 새 이력 보존',()=>{const op=spec.paths[root+'/corrections'].post;assert.ok(validator('ManualInterviewCorrection')(op.requestBody.content['application/json'].examples.default.value));assert.equal(op.responses['202'].content['application/json'].example.processing.kind,'CORRECTION');assert.match(op.description,/새 CORRECTION 턴/);assert.match(op.description,/depth를 증가시키거나/);});
test('수집 후 선택적 검토는 다음 질문과 초안 생성의 조건이 아님',()=>{const op=spec.paths[root+'/confirmations'].post;const s=op.responses['202'].content['application/json'].example;assert.equal(s.phase,'READY_TO_GENERATE');assert.ok(s.intents[0].confirmedAt);assert.match(op.description,/선행 조건이 아닙니다/);assert.match(op.description,/다음 질문을 생성하거나.*진행시키지/);assert.ok(validator('ManualInterviewSession')(s));});
