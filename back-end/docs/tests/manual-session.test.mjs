import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const path='/api/stores/{storeId}/manual/interviews';const s=spec.paths[path+'/{sessionId}'].get.responses['200'].content['application/json'].example;const v=validator('ManualInterviewSession');
test('인터뷰 시작: 서버가 버전·주체 결정, 임의 provider 입력 거절',()=>{const x=validator('ManualInterviewStart');assert.ok(x({}));for(const field of ['ownerId','questionSetVersion','model','password'])assert.equal(x({[field]:'x'}),false);assert.ok(v(spec.paths[path].post.responses['201'].content['application/json'].example));});
test('세션: 진행 단계별 필수 데이터와 오류 비노출',()=>{assert.ok(v(s));for(const x of [{...s,questions:[]},{...s,review:{}},{...s,phase:'REVIEWING'},{...s,status:'ERROR',phase:'ERROR'},{...s,status:'COMPLETED',phase:'COMPLETED'},{...s,questionSetVersion:0},{...s,provider:'external'}])assert.equal(v(x),false);});
test('기본 질문과 추가 질문의 depth·묶음 ID 일치',()=>{const x=validator('ManualInterviewQuestion');const q=s.questions[0];assert.ok(x(q));assert.ok(x({...q,kind:'PROBE',depth:5,batchId:q.id}));for(const invalid of [{...q,depth:1},{...q,batchId:q.id},{...q,kind:'PROBE',depth:0,batchId:q.id},{...q,kind:'PROBE',depth:6,batchId:q.id},{...q,kind:'PROBE',depth:1}])assert.equal(x(invalid),false);});
test('사용자 정책: 미충족 항목은 유지하며 점주 확인으로 발행 허용',()=>{const op=spec.paths[path+'/{sessionId}'].get;assert.match(op.description,/점주 확인만으로 발행/);assert.match(op.description,/COVERED로 바꾸지/);});
