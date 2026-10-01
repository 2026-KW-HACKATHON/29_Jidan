import { expect,it } from 'vitest'
import user from './assets/user.svg?raw'
import check from './assets/check.svg?raw'
it.each([[user,'16','18','#255CF5'],[check,'11','8','#28702F']])('아이콘 SVG는 유효한 원본 크기와 그릴 경로를 포함한다', (source,width,height,color)=>{const document=new DOMParser().parseFromString(source,'image/svg+xml');expect(document.querySelector('parsererror')).toBeNull();const svg=document.documentElement;expect(svg.tagName).toBe('svg');expect(svg.getAttribute('width')).toBe(width);expect(svg.getAttribute('height')).toBe(height);const path=document.querySelector('path');expect(path?.getAttribute('d')?.length).toBeGreaterThan(10);expect(path?.getAttribute('stroke')).toBe(color)})
