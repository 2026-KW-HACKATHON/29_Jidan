import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}/answers'].post;const body=op.requestBody.content['application/json'].examples.default.value;const v=validator('ManualInterviewAnswer');
test('답변: 텍스트/완료 전사 참조와 선택 사진 허용',()=>{assert.ok(v(body));assert.ok(v(op.requestBody.content['application/json'].examples.voice.value));assert.ok(v({...body,photoIds:[]}));});
for(const [label,mutate] of [['공백',x=>x.input.text=' '],['본문 상한',x=>x.input.text='a'.repeat(10001)],['음성에 텍스트 혼합',x=>x.input={method:'VOICE',transcriptionId:x.questionId,text:'x'}],['전사 누락',x=>x.input={method:'VOICE'}],['사진 중복',x=>x.photoIds=[x.questionId,x.questionId]],['revision 0',x=>x.expectedRevision=0],['서버 평가 주입',x=>x.coverage='COVERED'],['depth 주입',x=>x.depth=6]])test(`답변: ${label} 거절`,()=>{const x=structuredClone(body);mutate(x);assert.equal(v(x),false);});
test('부분 묶음은 COLLECTING, 모든 답변 후 EVALUATION',()=>{const examples=op.responses['202'].content['application/json'].examples;assert.equal(examples.remainingQuestions.value.phase,'COLLECTING');assert.ok(examples.remainingQuestions.value.questions.some(q=>!q.answered));assert.equal(examples.evaluating.value.processing.kind,'EVALUATION');assert.match(op.description,/전부 답변/);});
