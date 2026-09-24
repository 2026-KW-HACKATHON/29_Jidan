import { useEffect, useRef, useState } from 'react'
import { PickerDialog } from '../ui/PickerDialog'
import { Button } from '../ui/Button'
import { searchAddress, type AddressSearch, type AddressResult } from './postcode'

export function AddressPicker({ onClose, onSelect, search = searchAddress }: { onClose: () => void; onSelect: (result: AddressResult) => void; search?: AddressSearch }) {
  const container = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const select = useRef(onSelect)
  useEffect(() => { select.current = onSelect }, [onSelect])
  useEffect(() => {
    const controller = new AbortController()
    const element = container.current!
    void search(element, result => select.current(result), controller.signal).catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => { controller.abort(); element.replaceChildren() }
  }, [search, attempt])
  return <PickerDialog title="주소 검색" onClose={onClose}>
    {failed && <div role="alert"><p>주소 검색에 연결하지 못했어요.</p><Button onClick={() => { setFailed(false); setAttempt(value => value + 1) }}>다시 시도</Button></div>}
    <div ref={container} style={{ height: 'min(480px, 60dvh)', width: '100%' }} aria-label="주소 검색 결과" />
  </PickerDialog>
}
