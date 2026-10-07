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
