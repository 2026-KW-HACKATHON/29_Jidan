import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spec,validator} from './helpers/owner-contract.mjs';
const validate=validator('ManualGuidanceCard');
const samples=spec.components.schemas.ManualGuidancePhotos.examples;
const states=spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}'].get.responses['200'].content['application/json'].examples;
test('사진 추천은 준비 전 null, 근무 구조와 업무 section 연결 대상 허용',()=>{
  const session=validator('ManualInterviewSession');assert.ok(session(states.guidancePhotosPending.value),JSON.stringify(session.errors));
  for(const sample of [...samples,{...samples[0],attachmentTarget:null}])assert.ok(validate(sample),JSON.stringify(validate.errors));
});
test('사진 연결 종류와 sectionId 조합 및 필수 대상 검증',()=>{
  const card=samples[0],target=card.attachmentTarget;
  const {attachmentTarget,...missing}=card;assert.equal(validate(missing),false);
  for(const invalid of [{...target,target:'OTHER'},{...target,sectionId:target.intentId},{...target,target:'SECTION'},{...target,target:'SECTION',sectionId:'invalid'},{...target,intentId:'invalid'},{target:'WORK_STRUCTURE',sectionId:null},{...target,storeId:target.intentId}])assert.equal(validate({...card,attachmentTarget:invalid}),false);
});
test('사진 추천에는 체크 상태·클라이언트 URL을 허용하지 않으며 일반 목록에 첨부 대상 금지',()=>{
  const card=samples[0];
  for(const extra of [{status:'CURRENT'},{url:'https://example.org/photo.jpg'}])assert.equal(validate({...card,items:[{...card.items[0],...extra}]}),false);
  assert.equal(validate({...card,type:'LIST'}),false);
});

test('실제 다음 질문은 이전 완료 주제의 SECTION 사진 추천을 운반할 수 있음',()=>{
  const session=validator('ManualInterviewSession');
  const value=structuredClone(states.collecting.value);
  const question=value.questions[0];
  const priorIntentId='74000000-0000-4000-8000-000000000001';
  value.intents.unshift({...value.intents[0],id:priorIntentId,key:'PRIOR_PHOTO_TOPIC',coverage:'COVERED',finishedAt:value.startedAt});
  const photo=structuredClone(samples[1]);
  photo.attachmentTarget.intentId=priorIntentId;
  question.guidanceCards=[photo];
  assert.notEqual(question.intentId,photo.attachmentTarget.intentId);
  assert.equal(photo.attachmentTarget.target,'SECTION');
  assert.ok(session(value),JSON.stringify(session.errors));
  // 참조의 실제 DB 귀속·READY 검증은 D06 HTTP 검사 책임이다.
});
test('질문 없는 최종 상태는 추천 카드 우회 필드 없이 기존 수동 첨부 계약 사용',()=>{
  const session=validator('ManualInterviewSession');
  const terminal=Object.values(states).map(x=>x.value).filter(x=>['READY_TO_GENERATE','GENERATING','COMPLETED'].includes(x.phase));
  assert.ok(terminal.some(x=>x.phase==='READY_TO_GENERATE'));
  for(const value of terminal){
    assert.ok(session(value),JSON.stringify(session.errors));
    assert.deepEqual(value.questions,[]);
    assert.equal(session({...value,questions:[states.collecting.value.questions[0]]}),false);
    for(const field of ['guidanceCards','photoGuidanceCards'])assert.equal(session({...value,[field]:[samples[1]]}),false);
  }
  const review=validator('ManualInterviewReview');
  const content={intentId:samples[1].attachmentTarget.intentId,summary:'저장된 이해 요약',shifts:[],sections:[],needsDetail:false};
  assert.ok(review(content),JSON.stringify(review.errors));
  const wrapper=validator('ManualIntentReview');
  const value={intentId:content.intentId,revision:1,status:'READY',content,confirmedAt:null,processing:null,error:null};
  assert.ok(wrapper(value),JSON.stringify(wrapper.errors));
  for(const field of ['guidanceCards','photoGuidanceCards']){
    assert.equal(review({...content,[field]:[samples[1]]}),false);
    assert.equal(wrapper({...value,[field]:[samples[1]]}),false);
  }
  assert.ok(spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}/intents/{intentId}/review/photos'].put);
});
