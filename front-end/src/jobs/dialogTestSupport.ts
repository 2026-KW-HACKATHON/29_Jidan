import { cleanup } from '@testing-library/react'
import { beforeEach,afterEach } from 'vitest'
/** jsdom lacks the native top layer; browser checks cover actual focus containment. */
export function mockDialog() {
  const original=Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
  beforeEach(()=>{
    Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value(this:HTMLDialogElement){this.setAttribute('open','')}})
    Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value(this:HTMLDialogElement){this.removeAttribute('open')}})
  })
  afterEach(()=>{cleanup();for(const key of ['showModal','close']){if(original[key])Object.defineProperty(HTMLDialogElement.prototype,key,original[key]);else Reflect.deleteProperty(HTMLDialogElement.prototype,key)}})
}
