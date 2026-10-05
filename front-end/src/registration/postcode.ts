export type AddressResult = { postcode: string; address: string }
type PostcodeData = { zonecode: string; address: string; roadAddress: string; jibunAddress: string; userSelectedType: string }
type PostcodeConstructor = new (options: { width: string; height: string; minWidth: number; onresize: () => void; oncomplete: (data: PostcodeData) => void }) => { embed: (container: HTMLElement) => void }
declare global { interface Window { kakao?: { Postcode?: PostcodeConstructor } } }
const SCRIPT = 'https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js'
let loading: Promise<PostcodeConstructor> | undefined
function loadPostcode(): Promise<PostcodeConstructor> {
  if (window.kakao?.Postcode) return Promise.resolve(window.kakao.Postcode)
  if (loading) return loading
  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const timer = setTimeout(() => finish(new Error('ADDRESS_TIMEOUT')), 10000)
    function finish(error?: Error) {
      clearTimeout(timer); script.onload = null; script.onerror = null
      if (error) { script.remove(); loading = undefined; reject(error) }
      else if (window.kakao?.Postcode) resolve(window.kakao.Postcode)
      else { script.remove(); loading = undefined; reject(new Error('ADDRESS_UNAVAILABLE')) }
    }
    script.src = SCRIPT; script.async = true
    script.onload = () => finish()
    script.onerror = () => finish(new Error('ADDRESS_UNAVAILABLE'))
    document.head.append(script)
  })
  return loading
}
export type AddressSearch = (container: HTMLElement, onSelect: (address: AddressResult) => void, signal: AbortSignal) => Promise<void>
export const searchAddress: AddressSearch = async (container, onSelect, signal) => {
  const Postcode = await loadPostcode()
  if (signal.aborted) return
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); container.replaceChildren(); reject(new Error('ADDRESS_FRAME_TIMEOUT')) }, 10000)
    function cleanup() { clearTimeout(timer); signal.removeEventListener('abort', abort) }
    function abort() { cleanup(); resolve() }
    signal.addEventListener('abort', abort, { once: true })
    try {
      new Postcode({ width: '100%', height: '100%', minWidth: 200,
        onresize: () => { cleanup(); resolve() },
        oncomplete: data => {
          if (signal.aborted) return
          const address = (data.userSelectedType === 'R' ? data.roadAddress : data.jibunAddress) || data.address
          if (/^\d{5}$/.test(data.zonecode) && address?.trim()) onSelect({ postcode: data.zonecode, address })
        },
      }).embed(container)
    } catch (error) { cleanup(); reject(error) }
  })
}
