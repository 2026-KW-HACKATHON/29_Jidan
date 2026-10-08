import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/draft/preview'].get;const x=op.responses['200'].content['application/json'].example;const v=validator('ManualWorkerPreview');
test('미리보기는 점주 전용, 게시본 상태와 혼동 불가',()=>{assert.ok(v(x));assert.equal(v({...x,preview:false}),false);assert.equal(v({...x,interviewTranscript:'secret'}),false);assert.match(op.description,/WORKER는 403/);assert.ok(op.responses['409']);assert.deepEqual(op.security,[{SessionCookie:[]}]);});
