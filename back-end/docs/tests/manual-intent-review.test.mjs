import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';
const session='/api/stores/{storeId}/manual/interviews/{sessionId}';
const root=session+'/intents/{intentId}/review';
const examples=spec.paths[root].get.responses['200'].content['application/json'].examples;
const validate=validator('ManualIntentReview');
test('다음 질문 수집과 이전 인텐트 요약 조회를 동시에 표현',()=>{
 const progress=spec.paths[session].get.responses['200'].content['application/json'].examples.nextQuestionAfterSufficientAnswer.value;
 assert.ok(validator('ManualInterviewSession')(progress));assert.ok(validate(examples.ready.value));
 assert.notEqual(progress.currentIntentId,examples.ready.value.intentId);
 assert.equal(progress.intents[0].id,examples.ready.value.intentId);
 assert.equal('review' in progress,false);
 const list=spec.paths[session+'/reviews'].get.responses['200'].content['application/json'].example;
 assert.ok(validator('ManualIntentReviewList')(list));assert.ok(validator('ManualIntentReviewList')({...list,items:[]}));
});
test('최초 생성·정정·확인·실패 상태의 본문 보존과 필수 데이터',()=>{
 for(const {value} of Object.values(examples))assert.ok(validate(value),JSON.stringify(validate.errors));
 const ready=examples.ready.value;
 for(const x of [{...ready,content:null},{...ready,status:'PROCESSING'},{...ready,status:'ERROR'},{...ready,revision:0},{...examples.correcting.value,confirmedAt:'2026-10-05T01:40:00Z'},{...examples.failed.value,error:null}])assert.equal(validate(x),false);
 assert.deepEqual(examples.correcting.value.content,ready.content);
 assert.deepEqual(examples.failed.value.content,ready.content);
 assert.equal(examples.correcting.value.confirmedAt,null);
});
test('검토 실패 재시도는 같은 작업과 입력으로 새 attempt를 사용',()=>{
 const retried=spec.paths[root+'/retries'].post.responses['202'].content['application/json'].example;
 assert.ok(validate(retried));assert.equal(retried.processing.taskId,examples.failed.value.processing.taskId);
 assert.equal(retried.processing.attempt,examples.failed.value.processing.attempt+1);
 assert.deepEqual(retried.content,examples.failed.value.content);
 assert.equal(retried.intentId,examples.failed.value.intentId);
});
test('검토 대상은 필수 path UUID이며 요청 body로 바꿀 수 없음',()=>{
 for(const suffix of ['/corrections','/confirmations','/photos']){
  assert.equal(spec.paths[session+suffix],undefined);
  const op=Object.values(spec.paths[root+suffix])[0];
  const param=op.parameters.find(x=>x.name==='intentId');assert.equal(param.required,true);
  assert.ok(parameterValidator(param)(examples.ready.value.intentId));assert.equal(parameterValidator(param)('bad'),false);
  const media=op.requestBody.content['application/json'];const schema=media.schema.$ref.split('/').at(-1);
  const body=media.examples.default.value;assert.equal(validator(schema)({...body,intentId:examples.ready.value.intentId}),false);
  for(const status of ['401','403','404','409','422'])assert.ok(op.responses[status]);
 }
});
test('생성은 검토 snapshot을 필수로 받으며 중복·잘못된 revision을 거절',()=>{
 const op=spec.paths[session+'/completion'].post;const body=op.requestBody.content['application/json'].examples.default.value;
 const v=validator('ManualDraftGenerationInput');assert.ok(v(body));
 for(const x of [{expectedRevision:body.expectedRevision},{...body,reviewRevisions:[]},{...body,reviewRevisions:[...body.reviewRevisions,...body.reviewRevisions]},{...body,reviewRevisions:[{...body.reviewRevisions[0],revision:0}]},{...body,confirmed:true}])assert.equal(v(x),false);
 assert.match(op.responses['409'].description,/REVIEW_NOT_READY/);
 assert.match(op.description,/생성이 먼저 접수되면.*409/);
 assert.match(op.description,/정정이 먼저 접수되면 생성은 거절/);
});
