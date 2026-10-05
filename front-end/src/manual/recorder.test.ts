import {afterEach,expect,it,vi} from 'vitest'
import {recordVoice} from './recorder'
const stopTrack=vi.fn()
class FakeRecorder{
 static isTypeSupported(t:string){return t.startsWith('audio/webm')}
 state='inactive';ondataavailable:((e:{data:Blob})=>void)|null=null;onstop:(()=>void)|null=null;onerror:(()=>void)|null=null
 start(){this.state='recording'}stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['sound'])});this.onstop?.()}
}
function setup(){vi.stubGlobal('MediaRecorder',FakeRecorder);Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:vi.fn().mockResolvedValue({getTracks:()=>[{stop:stopTrack}]})}})}
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();vi.restoreAllMocks();stopTrack.mockClear()})
it('취소는 마이크와 타이머를 해제한다',async()=>{setup();vi.useFakeTimers();const controller=new AbortController();const recorder=await recordVoice(controller.signal,vi.fn());const assertion=expect(recorder.done).rejects.toThrow();controller.abort();await assertion;expect(stopTrack).toHaveBeenCalledOnce();expect(vi.getTimerCount()).toBe(0)})
it('권한 응답이 늦게 와도 이탈한 화면의 마이크를 즉시 해제한다',async()=>{setup();let grant!:(value:unknown)=>void;navigator.mediaDevices.getUserMedia=vi.fn(()=>new Promise(resolve=>grant=resolve)) as typeof navigator.mediaDevices.getUserMedia;const controller=new AbortController();const pending=recordVoice(controller.signal,vi.fn());controller.abort();grant({getTracks:()=>[{stop:stopTrack}]});await expect(pending).rejects.toThrow();expect(stopTrack).toHaveBeenCalledOnce()})
it('120초에 자동 종료하며 한 번만 완료하고 정확한 경계를 보존한다',async()=>{setup();vi.useFakeTimers();let now=0;vi.spyOn(performance,'now').mockImplementation(()=>now);const tick=vi.fn(),recorder=await recordVoice(new AbortController().signal,tick);now=120000;vi.advanceTimersByTime(100);const result=await recorder.done;recorder.finish();expect(result.duration).toBe(120);expect(result.blob.size).toBeGreaterThan(0);expect(stopTrack).toHaveBeenCalledOnce();expect(vi.getTimerCount()).toBe(0)})
it('권한 거부와 미지원은 녹음 실패로 명확히 반환한다',async()=>{setup();navigator.mediaDevices.getUserMedia=vi.fn().mockRejectedValue(new DOMException('denied'));await expect(recordVoice(new AbortController().signal,vi.fn())).rejects.toThrow('MIC_DENIED');vi.stubGlobal('MediaRecorder',undefined);await expect(recordVoice(new AbortController().signal,vi.fn())).rejects.toThrow('MIC_UNSUPPORTED')})
