import { useState } from 'react'
import { fireEvent,render,screen } from '@testing-library/react'
import { it,expect } from 'vitest'
import { Tabs } from './Tabs'
function Sample(){const [selected,setSelected]=useState(0);return <Tabs labels={['활성 초대 1','지난 초대']} selected={selected} onSelect={setSelected} panelId="sample" label="초대 상태"/>}
it('키보드로 순환 선택하고 포커스를 유지한다',()=>{render(<Sample/>);fireEvent.keyDown(screen.getByRole('tab',{name:'활성 초대 1'}),{key:'ArrowLeft'});const past=screen.getByRole('tab',{name:'지난 초대'});expect(past).toHaveFocus();expect(past).toHaveAttribute('aria-selected','true');fireEvent.keyDown(past,{key:'ArrowRight'});expect(screen.getByRole('tab',{name:'활성 초대 1'})).toHaveFocus()})
