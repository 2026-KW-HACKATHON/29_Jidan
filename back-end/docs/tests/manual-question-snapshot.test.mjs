import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spec,validator} from './helpers/owner-contract.mjs';
const root='/api/stores/{storeId}/manual/interviews/{sessionId}';
const states=spec.paths[root].get.responses['200'].content['application/json'].examples;
const validate=validator('ManualInterviewSession');
const snapshot=states.processingWithGuidance.value.lastAnsweredQuestion;
test('평가 중·실패·답변 접수에 표시 질문 스냅샷 제공',()=>{
  const accepted=spec.paths[root+'/answers'].post.responses['202'].content['application/json'].examples.acceptedWithGuidance.value;
  for(const s of [states.processingWithGuidance.value,states.failedEvaluationWithGuidance.value,accepted]){
    assert.ok(validate(s),JSON.stringify(validate.errors));assert.deepEqual(s.questions,[]);
    assert.equal(s.lastAnsweredQuestion.intentId,s.currentIntentId);
    assert.equal(s.lastAnsweredQuestion.answered,true);
    assert.deepEqual(s.lastAnsweredQuestion.guidanceCards,snapshot.guidanceCards);
  }
  assert.equal(accepted.lastAnsweredQuestion.id,spec.paths[root+'/answers'].post.requestBody.content['application/json'].examples.default.value.questionId);
});
test('구 응답과 null 스냅샷 허용, 잘못된 phase·처리 종류에서는 스냅샷 거절',()=>{
  for(const {value:s} of Object.values(states)){
    assert.ok(validate({...s,lastAnsweredQuestion:null}),JSON.stringify(validate.errors));
    if(s.phase!=='PROCESSING'&&s.phase!=='ERROR'||s.processing?.kind!=='EVALUATION')assert.equal(validate({...s,lastAnsweredQuestion:snapshot}),false);
  }
  assert.ok(validate(states.processing.value));
});
test('미답변 질문·잘못된 카드·평가 주제 없는 스냅샷 거절',()=>{
  const s=states.processingWithGuidance.value;
  assert.equal(validate({...s,lastAnsweredQuestion:{...snapshot,answered:false}}),false);
  assert.equal(validate({...s,lastAnsweredQuestion:{...snapshot,guidanceCards:[{type:'BAD'}]}}),false);
  assert.equal(validate({...s,currentIntentId:null}),false);
});
