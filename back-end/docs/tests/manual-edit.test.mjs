import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/draft/content'].put;const input=op.requestBody.content['application/json'].examples.default.value;const v=validator('ManualContentUpdate');
test('초안 전체 편집: 심야 근무와 순서 유지 및 시스템 필드 주입 거절',()=>{assert.ok(v(input));for(const field of ['versionId','issues','status','publishedAt','ownerId'])assert.equal(v({...input,[field]:'x'}),false);for(const revision of [0,-1,1.1])assert.equal(v({...input,expectedRevision:revision}),false);});
test('편집의 같은 버전 참조·시간 길이·사진 중복은 서버 검증 계약',()=>{assert.match(op.description,/0 < duration <= 24/);assert.match(op.description,/같은 버전의 shiftId/);assert.match(op.description,/사진 mediaId 중복/);assert.match(op.description,/실질 변경이 없으면/);assert.match(op.description,/확인을 초기화/);});
test('섹션: 조별 업무에 해당 조 참조 허용, 단계 상한과 누락 거절',()=>{const content=structuredClone(input.content);content.sections[0].category='SHIFT_TASK';content.sections[0].shiftId=content.shifts[0].id;assert.ok(v({...input,content}));content.sections[0].steps=[];assert.equal(v({...input,content}),false);});
