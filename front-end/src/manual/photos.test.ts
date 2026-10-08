import {expect,it,vi} from 'vitest'
import {createPhotoAttachment,nextPhotoTitle,reviewPhotos,validatePhoto} from './photos'
import {ManualError,type ManualService} from './service'
import {reviewFixture,manualStoreId,interviewFixture} from '../dev/manualFixtures'
const photo={mediaId:crypto.randomUUID(),caption:null,title:'사진 1'}
it('사진 MIME·빈 파일·10 MiB 초과를 거절하고 경계를 허용한다',()=>{
 for(const type of ['image/jpeg','image/png','image/webp'])expect(()=>validatePhoto(new Blob([new Uint8Array(10*1024*1024)],{type}))).not.toThrow()
 for(const blob of [new Blob([],{type:'image/png'}),new Blob(['a'],{type:'image/svg+xml'}),new Blob([new Uint8Array(10*1024*1024+1)],{type:'image/jpeg'})])expect(()=>validatePhoto(blob)).toThrow()
})
it('대상 삭제와 PROCESSING 상태·20장 초과를 업로드 전에 거절한다',()=>{
 expect(()=>reviewPhotos(reviewFixture,{target:'SECTION',sectionId:crypto.randomUUID()})).toThrow('MANUAL_REFERENCE_CONFLICT')
 const review={...reviewFixture,content:{...reviewFixture.content!,structurePhotos:Array.from({length:20},()=>({...photo,mediaId:crypto.randomUUID()}))}}
 expect(()=>createPhotoAttachment({} as ManualService,interviewFixture.id,review,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'}))).toThrow('PHOTO_LIMIT')
 expect(()=>reviewPhotos({...reviewFixture,status:'PROCESSING',confirmedAt:null,processing:{taskId:crypto.randomUUID(),kind:'CORRECTION',attempt:1},error:null},{target:'WORK_STRUCTURE',sectionId:null})).toThrow('MANUAL_STATE_CONFLICT')
})
it('사진 이름은 충돌 없이 부여하고 연결 응답 유실 시 업로드·이름·revision·키를 유지한다',async()=>{
 expect(nextPhotoTitle([photo,{...photo,title:'사진 3'}])).toBe('사진 2')
 const review={...reviewFixture,content:{...reviewFixture.content!,structurePhotos:[photo]}}
 const mediaId=crypto.randomUUID(),call=vi.fn().mockResolvedValueOnce({data:{id:mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockRejectedValueOnce(Error('lost')).mockResolvedValueOnce({data:review})
 const attach=createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,review,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'})),signal=new AbortController().signal
 await expect(attach(signal)).rejects.toThrow('lost');await attach(signal)
 expect(call.mock.calls.filter(c=>c[0]==='uploadManualMedia')).toHaveLength(1)
 expect(call.mock.calls[1][2]).toEqual({expectedRevision:review.revision,target:'WORK_STRUCTURE',sectionId:null,photos:[photo,{mediaId,caption:null,title:'사진 2'}]})
 expect(call.mock.calls[1][2]).toEqual(call.mock.calls[2][2]);expect(call.mock.calls[1][3].key).toBe(call.mock.calls[2][3].key)
})

it('같은 대상의 중복 media ID는 연결하지 않고 다른 section에서는 같은 사진을 허용한다',async()=>{
 const blob=new Blob(['photo'],{type:'image/png'}),signal=new AbortController().signal,review={...reviewFixture,content:{...reviewFixture.content!,structurePhotos:[photo],sections:[{id:crypto.randomUUID(),category:'COMMON_TASK' as const,shiftId:null,title:'다른 업무',steps:[],photos:[]}]}}
 const call=vi.fn().mockResolvedValue({data:{id:photo.mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}),service={storeId:manualStoreId,call} as ManualService
 await expect(createPhotoAttachment(service,interviewFixture.id,review,{target:'WORK_STRUCTURE',sectionId:null},blob)(signal)).rejects.toThrow('PHOTO_ALREADY_ATTACHED');expect(call).toHaveBeenCalledOnce()
 call.mockResolvedValueOnce({data:{id:photo.mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockResolvedValueOnce({data:review})
 await createPhotoAttachment(service,interviewFixture.id,review,{target:'SECTION',sectionId:review.content.sections[0].id},blob)(signal)
 expect(call.mock.calls.at(-1)?.[2]).toMatchObject({target:'SECTION',photos:[{mediaId:photo.mediaId,title:'사진 1',caption:null}]})
})
it('취소된 업로드 응답은 업무 연결로 이어지지 않는다',async()=>{
 const controller=new AbortController(),call=vi.fn(async()=>{controller.abort();return {data:{id:crypto.randomUUID(),purpose:'MANUAL_PHOTO',storeId:manualStoreId},retryAfterMs:2000}})
 await expect(createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,reviewFixture,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'}))(controller.signal)).rejects.toThrow();expect(call).toHaveBeenCalledOnce()
})

it('revision 충돌 뒤 명시적 재시도는 업로드를 유지하고 최신 사진과 새 키로 연결한다',async()=>{
 const mediaId=crypto.randomUUID(),latest={...reviewFixture,revision:reviewFixture.revision+1,content:{...reviewFixture.content!,structurePhotos:[photo]}}
 const call=vi.fn().mockResolvedValueOnce({data:{id:mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockRejectedValueOnce(new ManualError('REVISION_CONFLICT')).mockResolvedValueOnce({data:latest}).mockResolvedValueOnce({data:latest})
 const attach=createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,reviewFixture,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'})),signal=new AbortController().signal
 await expect(attach(signal)).rejects.toThrow('PHOTO_REVISION_CONFLICT')
 expect(call).toHaveBeenCalledTimes(2)
 await attach(signal)
 expect(call.mock.calls.map(c=>c[0])).toEqual(['uploadManualMedia','replaceManualInterviewReviewPhotos','getManualIntentReview','replaceManualInterviewReviewPhotos'])
 expect(call.mock.calls[3][2]).toMatchObject({expectedRevision:latest.revision,photos:[photo,{mediaId,title:'사진 2',caption:null}]})
 expect(call.mock.calls[3][3].key).not.toBe(call.mock.calls[1][3].key)
})
it.each(['deleted','processing','full'])('충돌 재조회 후 대상 %s 상태이면 업로드·연결을 반복하지 않는다',async state=>{
 const sectionId=crypto.randomUUID(),section={id:sectionId,category:'COMMON_TASK' as const,shiftId:null,title:'업무',steps:[],photos:[]}
 const review={...reviewFixture,content:{...reviewFixture.content!,sections:[section]}}
 const latest=state==='processing'?{...review,status:'PROCESSING'}:{...review,content:{...review.content,sections:state==='deleted'?[]:[{...section,photos:Array.from({length:20},()=>({...photo,mediaId:crypto.randomUUID()}))}]}}
 const call=vi.fn().mockResolvedValueOnce({data:{id:crypto.randomUUID(),purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockRejectedValueOnce(new ManualError('REVISION_CONFLICT')).mockResolvedValueOnce({data:latest})
 const attach=createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,review,{target:'SECTION',sectionId},new Blob(['p'],{type:'image/png'})),signal=new AbortController().signal
 await expect(attach(signal)).rejects.toThrow('PHOTO_REVISION_CONFLICT');await expect(attach(signal)).rejects.toThrow()
 expect(call).toHaveBeenCalledTimes(3)
})
it('충돌 뒤 재조회 실패는 다음 재시도에서 다시 조회하고 업로드를 반복하지 않는다',async()=>{
 const mediaId=crypto.randomUUID(),call=vi.fn().mockResolvedValueOnce({data:{id:mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockRejectedValueOnce(new ManualError('REVISION_CONFLICT')).mockRejectedValueOnce(Error('read lost')).mockResolvedValueOnce({data:reviewFixture}).mockResolvedValueOnce({data:reviewFixture})
 const attach=createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,reviewFixture,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'})),signal=new AbortController().signal
 await expect(attach(signal)).rejects.toThrow('PHOTO_REVISION_CONFLICT');await expect(attach(signal)).rejects.toThrow('read lost');await attach(signal)
 expect(call.mock.calls.filter(c=>c[0]==='uploadManualMedia')).toHaveLength(1)
 expect(call.mock.calls.filter(c=>c[0]==='getManualIntentReview')).toHaveLength(2)
})
it('충돌 재조회에서 같은 mediaId가 연결되어 있으면 중복 연결 없이 저장본을 반환한다',async()=>{
 const mediaId=photo.mediaId,latest={...reviewFixture,content:{...reviewFixture.content!,structurePhotos:[photo]}}
 const call=vi.fn().mockResolvedValueOnce({data:{id:mediaId,purpose:'MANUAL_PHOTO',storeId:manualStoreId}}).mockRejectedValueOnce(new ManualError('REVISION_CONFLICT')).mockResolvedValueOnce({data:latest})
 const attach=createPhotoAttachment({storeId:manualStoreId,call} as ManualService,interviewFixture.id,reviewFixture,{target:'WORK_STRUCTURE',sectionId:null},new Blob(['p'],{type:'image/png'})),signal=new AbortController().signal
 await expect(attach(signal)).rejects.toThrow('PHOTO_REVISION_CONFLICT');expect(await attach(signal)).toBe(latest)
 expect(call).toHaveBeenCalledTimes(3)
})
