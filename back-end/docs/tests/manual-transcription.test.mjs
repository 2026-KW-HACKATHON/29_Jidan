import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const path='/api/stores/{storeId}/manual/transcriptions';const run=spec.paths[path].post.responses['202'].content['application/json'].example;const ready=spec.paths[path+'/{transcriptionId}'].get.responses['200'].content['application/json'].example;const v=validator('ManualTranscription');
test('전사 상태: 진행/성공/실패의 텍스트·오류·완료 시각 일치',()=>{assert.ok(v(run));assert.ok(v(ready));const error={...run,status:'ERROR',completedAt:ready.completedAt,error:{code:'TRANSCRIPTION_FAILED',message:'다시 시도해 주세요.',retryable:true}};assert.ok(v(error));for(const x of [{...run,text:'진행 중'},{...ready,text:' '},{...ready,completedAt:null},{...ready,error:error.error},{...error,error:null},{...error,text:'전사 성공'},{...error,error:{...error.error,providerResponse:'secret'}}])assert.equal(v(x),false);});
test('전사 입력: 임의 텍스트/계정/다른 인증 정보 주입 금지',()=>{const v=validator('ManualTranscriptionInput');assert.ok(v({mediaId:run.mediaId}));for(const x of [{mediaId:'bad'},{mediaId:run.mediaId,text:'대체'},{mediaId:run.mediaId,password:'x'},{mediaId:run.mediaId,ownerId:run.id}])assert.equal(v(x),false);});
test('전사는 답변과 독립되며 실패 재시도는 새 key 사용',()=>{const op=spec.paths[path].post;assert.match(op.description,/자동 제출하지/);assert.match(op.description,/새 Idempotency-Key/);assert.deepEqual(op.security,[{SessionCookie:[],CsrfToken:[]}]);});
