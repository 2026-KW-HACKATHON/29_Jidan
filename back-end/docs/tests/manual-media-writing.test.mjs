import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spec,validator} from './helpers/owner-contract.mjs';
const R='/api/stores/{storeId}/manual/interviews/{sessionId}/intents/{intentId}/review';
const op=spec.paths[`${R}/media-writing`].post;
const accepted=op.responses['202'].content['application/json'].example;
const [photo,video]=accepted.content.sections[0].photos;
const attachment=validator('ManualPhotoAttachment');
function ok(validate,value){assert.ok(validate(value),JSON.stringify(validate.errors));}
test('0.12.0: 사진·영상 기반 작성 operation과 버전',()=>{
  assert.equal(spec.info.version,'0.12.0');
  assert.equal(op.operationId,'writeManualInterviewSectionFromMedia');
  assert.equal(op.requestBody.content['application/json'].schema.$ref,'#/components/schemas/ManualMediaWritingRequest');
  assert.deepEqual(Object.keys(op.responses).sort(),['202','400','401','403','404','409','422','429','500','503']);
  assert.match(op.externalDocs.url,/777-2979/);
  assert.match(op.description,/MEDIA_WRITING/);assert.match(op.description,/retries/);
  ok(validator('ManualIntentReview'),accepted);
  assert.equal(accepted.processing.kind,'MEDIA_WRITING');
});
test('작성 요청은 expectedRevision과 sectionId만 받음',()=>{
  const v=validator('ManualMediaWritingRequest');
  ok(v,{expectedRevision:3,sectionId:photo.mediaId});
  for(const bad of [{expectedRevision:3},{sectionId:photo.mediaId},{expectedRevision:0,sectionId:photo.mediaId},
    {expectedRevision:3,sectionId:'not-a-uuid'},{expectedRevision:3,sectionId:photo.mediaId,mediaIds:[]}])
    assert.equal(v(bad),false,JSON.stringify(bad));
});
test('사진 항목은 0.11.0 모양 그대로, 영상 항목은 kind=VIDEO와 대표 프레임 필수',()=>{
  ok(attachment,photo);assert.equal('kind' in photo,false);
  ok(attachment,video);assert.equal(video.kind,'VIDEO');
  ok(attachment,{...photo,kind:'PHOTO'});
  const {posterMediaId,...noPoster}=video;
  assert.equal(attachment(noPoster),false);
  assert.equal(attachment({...photo,posterMediaId}),false);
  assert.equal(attachment({...photo,kind:'PHOTO',posterMediaId}),false);
  assert.equal(attachment({...video,kind:'AUDIO'}),false);
  assert.equal(attachment({...video,posterMediaId:'x'}),false);
});
test('업로드 purpose MANUAL_VIDEO: 형식·100 MiB·대표 프레임',()=>{
  const media=validator('ManualMedia');
  const base={id:photo.mediaId,storeId:photo.mediaId,createdAt:'2026-10-08T01:00:00Z'};
  const clip={...base,purpose:'MANUAL_VIDEO',mimeType:'video/mp4',sizeBytes:104857600,posterMediaId:video.posterMediaId};
  ok(media,clip);
  for(const mimeType of ['video/quicktime','video/webm'])ok(media,{...clip,mimeType});
  assert.equal(media({...clip,sizeBytes:104857601}),false);
  assert.equal(media({...clip,mimeType:'video/x-msvideo'}),false);
  const {posterMediaId,...withoutPoster}=clip;assert.equal(media(withoutPoster),false);
  ok(media,{...base,purpose:'MANUAL_PHOTO',mimeType:'image/jpeg',sizeBytes:10});
  assert.equal(media({...base,purpose:'MANUAL_PHOTO',mimeType:'image/jpeg',sizeBytes:10,posterMediaId}),false);
  assert.equal(media({...base,purpose:'INTERVIEW_AUDIO',mimeType:'audio/wav',sizeBytes:20971521}),false);
  assert.deepEqual(spec.components.schemas.ManualUploadInput.properties.purpose.enum,['MANUAL_PHOTO','INTERVIEW_AUDIO','MANUAL_VIDEO']);
});
test('초안 정정 MEDIA 입력은 SECTION 대상만, 기존 TEXT/VOICE 유지',()=>{
  const v=validator('ManualDraftCorrectionInput');
  const base={expectedVersionId:photo.mediaId,expectedRevision:2};
  ok(v,{...base,target:{kind:'SECTION',targetId:photo.mediaId},input:{method:'MEDIA'}});
  ok(v,{...base,target:{kind:'MANUAL',targetId:null},input:{method:'TEXT',text:'고쳐 주세요.'}});
  ok(v,{...base,target:{kind:'SHIFT',targetId:photo.mediaId},input:{method:'VOICE',transcriptionId:photo.mediaId}});
  assert.equal(v({...base,target:{kind:'MANUAL',targetId:null},input:{method:'MEDIA'}}),false);
  assert.equal(v({...base,target:{kind:'SHIFT',targetId:photo.mediaId},input:{method:'MEDIA'}}),false);
  assert.equal(v({...base,target:{kind:'SECTION',targetId:photo.mediaId},input:{method:'MEDIA',mediaIds:[]}}),false);
});
test('검토 처리 종류와 기존 사진 교체·재시도 문서',()=>{
  assert.deepEqual(spec.components.schemas.ManualReviewProcessing.properties.kind.enum,['UNDERSTANDING','CORRECTION','MEDIA_WRITING']);
  assert.match(spec.paths[`${R}/photos`].put.description,/MANUAL_VIDEO/);
  assert.match(spec.paths[`${R}/photos`].put.description,/777-3464/);
  assert.match(spec.paths[`${R}/retries`].post.description,/MEDIA_WRITING/);
  assert.match(spec.paths['/api/stores/{storeId}/manual/media'].post.description,/MANUAL_VIDEO/);
});
