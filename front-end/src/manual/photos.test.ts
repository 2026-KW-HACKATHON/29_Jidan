import {expect,it,vi} from 'vitest'
import {createPhotoAttachment,nextPhotoTitle,reviewPhotos,validatePhoto} from './photos'
import type {ManualService} from './service'
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
