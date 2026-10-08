import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const draft = spec.paths['/api/stores/{storeId}/manual/draft'].get.responses['200'].content['application/json'].example;
const content = validator('ManualContent');
test('매뉴얼: 심야 근무 구조와 초안 예시 허용', () => assert.ok(validator('ManualDraft')(draft)));
for (const [label, mutate] of [
 ['빈 근무조', x => x.shifts = []], ['빈 섹션', x => x.sections = []],
 ['24시 입력', x => x.shifts[0].startTime = '24:00'], ['빈 이름', x => x.shifts[0].name = '  '],
 ['빈 지시', x => x.sections[0].steps[0].instruction = '\n'],
 ['조별 업무 참조 누락', x => x.sections[0].category = 'SHIFT_TASK'],
 ['공통 업무에 조 참조', x => x.sections[0].shiftId = x.shifts[0].id],
 ['근무자 완료 상태 주입', x => x.sections[0].steps[0].completed = true],
 ['사진 설명 상한', x => x.sections[0].photos = [{mediaId:x.shifts[0].id,caption:'a'.repeat(301)}]],
]) test(`매뉴얼 구조: ${label} 거절`, () => { const x=structuredClone(draft.content); mutate(x); assert.equal(content(x),false); });
test('생성 중/오류의 초안 본문은 null, READY 본문은 필수', () => {
 const v=validator('ManualDraft'); for(const status of ['RUNNING','ERROR','NOT_STARTED']) {assert.ok(v({...draft,generationStatus:status,content:null})); assert.equal(v({...draft,generationStatus:status}),false);}
 assert.equal(v({...draft,content:null}),false);
});
