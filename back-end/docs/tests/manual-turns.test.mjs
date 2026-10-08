import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/interviews/{sessionId}/turns'].get;const q=op.responses['200'].content['application/json'].example.items[0];const v=validator('ManualInterviewTurn');
test('대화 턴: AI 질문과 점주 답변/정정 구분',()=>{assert.ok(v(q));const a={...q,kind:'ANSWER',speaker:'OWNER',questionKind:null,inputMethod:'VOICE',replyToQuestionId:q.id};assert.ok(v(a));assert.ok(v({...a,kind:'CORRECTION',replyToQuestionId:null}));for(const x of [{...q,inputMethod:'VOICE'},{...q,speaker:'OWNER'},{...a,replyToQuestionId:null},{...a,questionKind:'BASE'},{...q,providerResponse:'secret'},{...q,content:'  '}])assert.equal(v(x),false);});
test('대화 페이지: 빈 목록과 크기/페이지 경계',()=>{const x=op.responses['200'].content['application/json'].example;assert.ok(validator('ManualInterviewTurnPage')({...x,items:[],totalItems:0,totalPages:0}));const size=parameterValidator(op.parameters.find(p=>p.name==='size'));assert.ok(size(100));for(const value of [0,101,1.5])assert.equal(size(value),false);});
