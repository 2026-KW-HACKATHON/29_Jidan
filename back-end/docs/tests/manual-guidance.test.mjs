import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spec,validator} from './helpers/owner-contract.mjs';
const examples=spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}'].get.responses['200'].content['application/json'].examples;
const card=examples.guidanceChecklist.value.questions[0].guidanceCards[0];
const list=examples.guidanceList.value.questions[0].guidanceCards[0];
const validate=validator('ManualGuidanceCard');
const question=validator('ManualInterviewQuestion');
function valid(value){assert.ok(validate(value),JSON.stringify(validate.errors));}
function invalid(value){assert.equal(validate(value),false,JSON.stringify(value));}
test('안내 카드가 없는 기존 질문과 신규 일반 목록·진행 목록 세션 허용',()=>{
  const session=validator('ManualInterviewSession');
  for(const name of ['collecting','guidanceList','guidanceChecklist']) assert.ok(session(examples[name].value),JSON.stringify(session.errors));
  const q=examples.collecting.value.questions[0];
  assert.ok(question({...q,guidance:null,guidanceCards:[]}));
  assert.equal(question({...q,guidance:'   '}),false);
  assert.equal(question({...q,guidanceCards:null}),false);
  assert.equal(question({...q,guidanceCards:Array(6).fill(list)}),false);
});
test('일반 목록은 상태·임의 필드를 거절하고 진행 목록은 모든 항목의 상태 필수',()=>{
  valid(list);valid(card);
  for(const status of [null,'CURRENT'])invalid({...list,items:[{...list.items[0],status}]});
  const {status,...missing}=card.items[0];invalid({...card,items:[missing]});
  for(const status of ['PENDING','CURRENT','COMPLETED','NEEDS_DETAIL'])valid({...card,items:[{...card.items[0],status}]});
  invalid({...card,items:[{...card.items[0],status:'CHECKED'}]});
  invalid({...card,type:'UNKNOWN'});invalid({...card,checked:true});
});
test('CURRENT는 0 또는 1개, 두 개는 거절',()=>{
  valid({...card,items:card.items.filter(i=>i.status!=='CURRENT')});
  invalid({...card,items:card.items.map(i=>({...i,status:'CURRENT'}))});
});
test('항목·카드 제목·UUID·배열 경계 검증',()=>{
  invalid({...list,items:[]});invalid({...list,items:Array(51).fill(list.items[0])});
  valid({...list,items:Array.from({length:50},(_,i)=>({...list.items[0],id:`71000000-0000-4000-8000-${String(i).padStart(12,'0')}`}))});
  for(const title of ['', ' ', 'a'.repeat(201)]) invalid({...list,title});
  invalid({...list,id:'not-a-uuid'});
  for(const label of ['', '\n', 'a'.repeat(201)])invalid({...list,items:[{...list.items[0],label}]});
  invalid({...list,items:[{...list.items[0],description:' '}]});
  invalid({...list,footer:'a'.repeat(1001)});
});

test('질문·사진 카드 합산 상한은 5개이며 구 응답은 그대로 허용',()=>{
  const photo=spec.components.schemas.ManualGuidancePhotos.examples[1];
  const q=examples.guidanceChecklist.value.questions[0];
  const cards=[card,list,...Array.from({length:3},(_,i)=>({...photo,id:`73000000-0000-4000-8000-${String(i).padStart(12,'0')}`}))];
  assert.ok(question({...q,guidanceCards:cards}),JSON.stringify(question.errors));
  assert.equal(question({...q,guidanceCards:[...cards,{...photo,id:'73000000-0000-4000-8000-000000000009'}]}),false);
  assert.ok(question(examples.collecting.value.questions[0]));
});
