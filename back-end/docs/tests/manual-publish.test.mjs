import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/draft/publication'].post;const body=op.requestBody.content['application/json'].examples.default.value;const v=validator('ManualPublishInput');
test('게시: 최신 revision의 최종 확인과 부족 항목 명시적 선택',()=>{assert.ok(v(body));assert.ok(v(op.requestBody.content['application/json'].examples.confirmMissingInformation.value));for(const x of [{...body,confirmed:false},{...body,acknowledgedIssueIds:undefined},{...body,acknowledgedIssueIds:['bad']},{...body,status:'PUBLISHED'},{...body,expectedRevision:0}])assert.equal(v(x),false);});
test('게시 결과: 불변 게시 상태·점주 확인·내용 및 UTC 시각',()=>{const x=op.responses['200'].content['application/json'].example;const v=validator('PublishedManual');assert.ok(v(x));for(const invalid of [{...x,status:'DRAFT'},{...x,ownerConfirmed:false},{...x,publishedAt:null},{...x,issues:[]},{...x,interviewTurns:[]}])assert.equal(v(invalid),false);});
test('발행은 부족 항목 확인과 게시 포인터를 원자적으로 교체',()=>{assert.match(op.description,/MANUAL_REVIEW_REQUIRED/);assert.match(op.description,/currentPublishedVersionId 교체/);assert.match(op.description,/전체 rollback/);assert.match(op.description,/추가 답변.*필수가 아닙니다/);assert.deepEqual(op.security,[{SessionCookie:[],CsrfToken:[]}]);});
