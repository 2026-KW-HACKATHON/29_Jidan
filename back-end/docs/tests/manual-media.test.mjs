import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec, validator, parameterValidator } from './helpers/owner-contract.mjs';

const v=validator('ManualMedia');const p=spec.paths['/api/stores/{storeId}/manual/media'].post;const photo=p.responses['201'].content['application/json'].example;
test('사진·음성: MIME과 목적 및 크기 상한 일치',()=>{assert.ok(v(photo));assert.ok(v({...photo,sizeBytes:10*1024*1024}));assert.equal(v({...photo,sizeBytes:10*1024*1024+1}),false);assert.equal(v({...photo,sizeBytes:0}),false);assert.equal(v({...photo,mimeType:'audio/wav'}),false);assert.ok(v({...photo,purpose:'INTERVIEW_AUDIO',mimeType:'audio/wav',sizeBytes:20*1024*1024}));assert.equal(v({...photo,purpose:'INTERVIEW_AUDIO',mimeType:'image/jpeg'}),false);});
test('업로드: multipart와 크기/형식 오류 및 개인정보 비노출',()=>{assert.ok(p.requestBody.content['multipart/form-data']);assert.ok(p.responses['413']);assert.ok(p.responses['415']);for(const field of ['url','objectKey','signedUrl','ownerId'])assert.equal(v({...photo,[field]:'secret'}),false);});
test('사진 조회: 현재 게시본 참조와 매장 접근 권한 재검사 명시',()=>{const op=spec.paths['/api/stores/{storeId}/manual/media/{mediaId}/content'].get;assert.match(op.description,/현재 게시본/);assert.match(op.description,/no-store/);assert.equal(op.security[0].SessionCookie.length,0);});
test('파일 삭제: 참조 중인 파일은 보존하고 삭제 재요청은 204',()=>{const op=spec.paths['/api/stores/{storeId}/manual/media/{mediaId}'].delete;assert.ok(op.responses['204']);assert.match(op.description,/MEDIA_IN_USE/);assert.deepEqual(op.security,[{SessionCookie:[],CsrfToken:[]}]);});
