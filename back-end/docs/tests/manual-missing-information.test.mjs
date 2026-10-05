import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator } from './helpers/owner-contract.mjs';
const draft=spec.components.schemas.ManualDraft.examples[0];
const gap=draft.content.missingInformation[0];
test('종료 시간 미확정 초안과 게시본은 공개 부족 항목을 유지',()=>{
 assert.ok(validator('ManualDraft')(draft));
 assert.ok(validator('PublishedManual')(spec.components.schemas.PublishedManual.examples[0]));
 assert.equal(draft.issues[0].id,gap.id);
 const x=structuredClone(draft.content);delete x.missingInformation;
 assert.equal(validator('ManualContent')(x),false);
 assert.equal(validator('ManualContent')({...draft.content,missingInformation:[]}),false);
});
test('정보가 전혀 없는 인텐트도 미확정 사유가 있으면 생성·확인 가능',()=>{
 const x={shifts:[],sections:[],missingInformation:[{...gap,target:'MANUAL',targetId:null,field:'shifts'},{...gap,id:'75f9cba6-bfd0-40cd-9844-f9cb860db21c',target:'MANUAL',targetId:null,field:'sections'}]};
 assert.ok(validator('ManualContent')(x));
 assert.equal(validator('ManualContent')({...x,missingInformation:[]}),false);
});
test('미확정 대상·필드 조합과 빈 설명 거절',()=>{
 const v=validator('ManualMissingInformation');assert.ok(v(gap));
 for(const x of [{...gap,target:'MANUAL'},{...gap,targetId:null},{...gap,field:'steps'},{...gap,description:' '},{...gap,acknowledged:true}])assert.equal(v(x),false);
});
test('모든 미확정 분기는 판별 필드 누락과 다른 대상의 필드 거절',()=>{
 const v=validator('ManualMissingInformation');
 for(const target of [
  {target:'MANUAL',targetId:null,field:'shifts'},
  {target:'SHIFT',targetId:gap.targetId,field:'endTime'},
  {target:'SECTION',targetId:gap.targetId,field:'steps'},
 ]){
  const value={...gap,...target};assert.ok(v(value));
  for(const field of ['target','targetId','field']){
   const missing={...value};delete missing[field];assert.equal(v(missing),false);
  }
  assert.equal(v({...value,field:target.field==='steps'?'endTime':'steps'}),false);
 }
});
test('미확정 항목은 최종 발행 동의로도 확인하며 내부 issues 주입은 거절',()=>{
 const v=validator('ManualPublishInput');const x={expectedVersionId:draft.versionId,expectedRevision:draft.revision,confirmed:true,acknowledgedIssueIds:[gap.id]};
 assert.ok(v(x));assert.equal(v({...x,issues:[]}),false);
});
