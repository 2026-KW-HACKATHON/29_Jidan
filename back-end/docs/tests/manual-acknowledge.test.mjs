import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const op=spec.paths['/api/stores/{storeId}/manual/draft/acknowledgements'].post;const b=op.requestBody.content['application/json'].examples.default.value;const v=validator('ManualIssueAcknowledgement');
test('사용자 정책: 부족 항목은 보완 답변/메모 없이 확인 가능',()=>{assert.ok(v(b));assert.ok(v({...b,note:'추가 안내는 점주에게 문의'}));const issue=op.responses['200'].content['application/json'].example.issues[0];assert.equal(issue.status,'ACKNOWLEDGED');assert.equal(issue.ownerNote,null);assert.ok(issue.acknowledgedAt);assert.ok(validator('ManualReviewIssue')(issue));});
for(const [name,x] of [['빈 항목',{...b,issueIds:[]}],['중복 항목',{...b,issueIds:[...b.issueIds,...b.issueIds]}],['동의 false',{...b,confirmed:false}],['공백 메모',{...b,note:' '}],['주체 주입',{...b,ownerId:b.issueIds[0]}]])test(`부족 항목 확인: ${name} 거절`,()=>assert.equal(v(x),false));
test('부족 항목은 확인 시각만 달라지며 내용 부족 판단을 지우지 않음',()=>{const x=op.responses['200'].content['application/json'].example.issues[0];const v=validator('ManualReviewIssue');assert.equal(v({...x,acknowledgedAt:null}),false);assert.ok(v({...x,status:'OPEN',ownerNote:null,acknowledgedAt:null}));assert.equal(v({...x,status:'OPEN'}),false);assert.match(op.description,/coverage를 COVERED로 바꾸지/);});
