import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const root='/api/stores/{storeId}/manual/interviews/{sessionId}';
test('이해 확인: 현재 revision과 명시적 true만 허용',()=>{const v=validator('ManualInterviewConfirm');assert.ok(v({expectedRevision:3,confirmed:true}));for(const x of [{expectedRevision:3,confirmed:false},{expectedRevision:3},{expectedRevision:0,confirmed:true},{expectedRevision:3,confirmed:true,coverage:'COVERED'}])assert.equal(v(x),false);});
test('정정: 원래 답변 덮어쓰기·depth 증가 없이 새 이력 보존',()=>{const op=spec.paths[root+'/corrections'].post;assert.ok(validator('ManualInterviewCorrection')(op.requestBody.content['application/json'].examples.default.value));assert.equal(op.responses['202'].content['application/json'].example.processing.kind,'CORRECTION');assert.match(op.description,/새 CORRECTION 턴/);assert.match(op.description,/depth를 증가시키거나/);});
test('주제별 확인은 정보가 충분한 정상 항목에만 적용',()=>{const op=spec.paths[root+'/confirmations'].post;const s=op.responses['202'].content['application/json'].example;assert.equal(s.intents[0].coverage,'COVERED');assert.ok(s.intents[0].confirmedAt);assert.match(op.description,/NEEDS_DETAIL은 검수중/);assert.match(op.description,/즉시 다음 인텐트/);assert.ok(validator('ManualInterviewSession')(s));});
