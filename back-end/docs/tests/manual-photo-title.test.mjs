import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validator } from './helpers/owner-contract.mjs';
const validate = validator('ManualPhotoAttachment');
const photo = {mediaId:'09dbccbd-75e2-4709-9a80-b9a9ddeef10a',title:'재고 배치',caption:'먼저 입고된 제품을 앞쪽에 진열해요.'};
test('사진 이름과 설명을 독립적으로 보존하며 설명 없이도 첨부',()=>{
 assert.ok(validate(photo)); assert.ok(validate({...photo,caption:null}));
 assert.ok(validate({...photo,title:'가'.repeat(100),caption:'가'.repeat(300)}));
 for(const title of [undefined,null,'','   ','가'.repeat(101)]) assert.equal(validate({...photo,title}),false);
 assert.equal(validate({...photo,caption:'가'.repeat(301)}),false);
});

// 기본 이름의 생성·유지는 프론트 구현에서 검증하며 여기서는 전송 계약을 확인합니다.
test('MVP 사진 연결은 기본 이름과 null 설명으로 사용자 입력 없이 가능',()=>{
 assert.ok(validate({mediaId:photo.mediaId,title:'사진 1',caption:null}));
 assert.ok(validate({mediaId:photo.mediaId,title:'사진 12',caption:null}));
 assert.equal(validate({mediaId:photo.mediaId,caption:null}),false);
});
