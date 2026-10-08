import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const root='/api/stores/{storeId}/manual';const list=spec.paths[root+'/published'].get;const detail=spec.paths[root+'/published/sections/{sectionId}'].get;
test('근무자 목록: 빈 필터 결과 허용 및 초안/비공개 정보 주입 거절',()=>{const x=list.responses['200'].content['application/json'].examples.withCommonTask.value;const v=validator('PublishedManualList');assert.ok(v(x));assert.ok(v({...x,sections:[]}));for(const invalid of [{...x,ownerConfirmed:false},{...x,interviewTurns:[]},{...x,issues:[]},{...x,shifts:[]}])assert.equal(v(invalid),false);});
test('게시본 목록/상세/사진은 현재 접근과 버전 검사',()=>{assert.match(list.description,/종료 시각과 같아도/);assert.match(detail.description,/MANUAL_VERSION_CHANGED/);assert.ok(detail.responses['409']);const p=detail.parameters.find(p=>p.name==='expectedVersionId');assert.ok(parameterValidator(p)(detail.responses['200'].content['application/json'].example.versionId));assert.equal(parameterValidator(p)('bad'),false);assert.ok(spec.paths[root+'/media/{mediaId}/content'].get.responses['409']);});
test('필터: 전체/공통/근무조와 미정 값 경계',()=>{const v=parameterValidator(list.parameters.find(p=>p.name==='filter'));for(const x of ['ALL','COMMON','SHIFT'])assert.ok(v(x));for(const x of ['OWNER','NIGHT',''])assert.equal(v(x),false);assert.match(list.parameters.find(p=>p.name==='shiftId').description,/SHIFT일 때 필수/);});
test('섹션 상세는 게시 버전의 단계/사진이며 점주 대화 노출 없음',()=>{const x=detail.responses['200'].content['application/json'].example;const v=validator('PublishedManualSectionDetail');assert.ok(v(x));assert.equal(v({...x,conversation:[]}),false);assert.deepEqual(detail.security,[{SessionCookie:[]}]);});
