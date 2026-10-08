import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spec,validator} from './helpers/owner-contract.mjs';
const R='/api/stores/{storeId}/manual/interviews/{sessionId}/intents/{intentId}/review';
const op=spec.paths[`${R}/media-writing`].post;
const accepted=op.responses['202'].content['application/json'].example;
const request=op.requestBody.content['application/json'].examples.default.value;
const id=request.mediaIds[0];
function ok(validate,value){assert.ok(validate(value),JSON.stringify(validate.errors));}
test('0.12.0: 사진·영상 기반 작성 operation과 버전',()=>{
  assert.equal(spec.info.version,'0.12.0');
  assert.equal(op.operationId,'writeManualInterviewSectionFromMedia');
  assert.equal(op.requestBody.content['application/json'].schema.$ref,'#/components/schemas/ManualMediaWritingRequest');
  assert.deepEqual(Object.keys(op.responses).sort(),['202','400','401','403','404','409','422','429','500','503']);
  assert.match(op.externalDocs.url,/777-2979/);
  assert.match(op.description,/MEDIA_WRITING/);assert.match(op.description,/retries/);assert.match(op.description,/첨부되거나 표시되지 않으며/);
  ok(validator('ManualIntentReview'),accepted);
  assert.equal(accepted.processing.kind,'MEDIA_WRITING');
  assert.deepEqual(accepted.content.sections[0].photos,[]);
});
test('작성 요청: expectedRevision·sectionId·mediaIds(1~10, 중복 없음)',()=>{
  const v=validator('ManualMediaWritingRequest');
  ok(v,request);
  ok(v,{...request,mediaIds:Array.from({length:10},(_,i)=>`71000000-0000-4000-8000-${String(i).padStart(12,'0')}`)});
  for(const bad of [{expectedRevision:3,sectionId:id},{...request,mediaIds:[]},{...request,mediaIds:[id,id]},
    {...request,mediaIds:Array.from({length:11},(_,i)=>`71000000-0000-4000-8000-${String(i).padStart(12,'0')}`)},
    {...request,mediaIds:['not-a-uuid']},{...request,expectedRevision:0},{...request,photos:[]}])
    assert.equal(v(bad),false,JSON.stringify(bad));
});
test('업로드 purpose MANUAL_VIDEO: 형식·100 MiB, 기존 사진·음성 한도 유지',()=>{
  const media=validator('ManualMedia');
  const base={id,storeId:id,createdAt:'2026-10-08T01:00:00Z'};
  const clip={...base,purpose:'MANUAL_VIDEO',mimeType:'video/mp4',sizeBytes:104857600};
  ok(media,clip);
  for(const mimeType of ['video/quicktime','video/webm'])ok(media,{...clip,mimeType});
  assert.equal(media({...clip,sizeBytes:104857601}),false);
  assert.equal(media({...clip,mimeType:'video/x-msvideo'}),false);
  assert.equal(media({...clip,posterMediaId:id}),false);
  assert.equal(media({...base,purpose:'MANUAL_PHOTO',mimeType:'image/jpeg',sizeBytes:10485761}),false);
  assert.equal(media({...base,purpose:'INTERVIEW_AUDIO',mimeType:'audio/wav',sizeBytes:20971521}),false);
  assert.deepEqual(spec.components.schemas.ManualUploadInput.properties.purpose.enum,['MANUAL_PHOTO','INTERVIEW_AUDIO','MANUAL_VIDEO']);
});
test('초안 정정 MEDIA 입력은 SECTION 대상과 mediaIds만, 기존 TEXT/VOICE 유지',()=>{
  const v=validator('ManualDraftCorrectionInput');
  const base={expectedVersionId:id,expectedRevision:2};
  ok(v,{...base,target:{kind:'SECTION',targetId:id},input:{method:'MEDIA',mediaIds:[id]}});
  ok(v,{...base,target:{kind:'MANUAL',targetId:null},input:{method:'TEXT',text:'고쳐 주세요.'}});
  ok(v,{...base,target:{kind:'SHIFT',targetId:id},input:{method:'VOICE',transcriptionId:id}});
  assert.equal(v({...base,target:{kind:'MANUAL',targetId:null},input:{method:'MEDIA',mediaIds:[id]}}),false);
  assert.equal(v({...base,target:{kind:'SHIFT',targetId:id},input:{method:'MEDIA',mediaIds:[id]}}),false);
  assert.equal(v({...base,target:{kind:'SECTION',targetId:id},input:{method:'MEDIA'}}),false);
  assert.equal(v({...base,target:{kind:'SECTION',targetId:id},input:{method:'MEDIA',mediaIds:[]}}),false);
});
test('사진 첨부 계약은 바뀌지 않음(kind·posterMediaId 없음)',()=>{
  const photo=spec.components.schemas.ManualPhotoAttachment;
  assert.deepEqual(Object.keys(photo.properties).sort(),['caption','mediaId','title']);
  assert.deepEqual(spec.components.schemas.ManualReviewProcessing.properties.kind.enum,['UNDERSTANDING','CORRECTION','MEDIA_WRITING']);
  assert.match(spec.paths[`${R}/retries`].post.description,/MEDIA_WRITING/);
  assert.match(spec.paths['/api/stores/{storeId}/manual/media'].post.description,/MANUAL_VIDEO/);
});
