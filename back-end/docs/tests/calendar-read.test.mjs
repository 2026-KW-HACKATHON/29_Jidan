import {test} from 'node:test';import assert from 'node:assert/strict';import {spec,validator,parameterValidator} from './helpers/owner-contract.mjs';
const path='/api/users/me/calendar/events';const op=spec.paths[path].get;
test('캘린더: 조회 연월 형식·경계',()=>{const v=parameterValidator(op.parameters.find(x=>x.name==='month'));assert.ok(v('2026-12'));for(const x of ['2026-00','2026-13','2026-1','2026-10-01'])assert.equal(v(x),false);});
test('캘린더: 빈 월과 날짜 간격 유지',()=>{const x=op.responses['200'].content['application/json'].example;assert.ok(validator('CalendarMonth')({...x,events:[]}));for(const e of x.events)assert.ok(Date.parse(e.startAt)<Date.parse(e.endAt));assert.match(op.description,/월말 심야/);assert.match(op.description,/경계만 닿으면 제외/);});
test('캘린더: 자동 대타는 수정 불가·공고 연결 필수',()=>{const x=op.responses['200'].content['application/json'].example.events[0];const v=validator('CalendarEvent');assert.ok(v(x));assert.equal(v({...x,editable:true}),false);assert.equal(v({...x,jobId:null}),false);assert.equal(v({...x,workerName:null}),false);});
test('캘린더: 본인 이력과 자료 접근 분리',()=>{assert.match(op.description,/다른 근무자 이름은 노출하지/);assert.match(op.description,/매뉴얼 접근을 복원하지/);});
