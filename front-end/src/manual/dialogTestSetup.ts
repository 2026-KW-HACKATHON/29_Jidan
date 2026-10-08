import {afterEach,beforeEach} from 'vitest'
const original=Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(()=>{
 Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value(this:HTMLDialogElement){this.setAttribute('open','')}})
 Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value(this:HTMLDialogElement){this.removeAttribute('open')}})
})
afterEach(()=>{for(const key of ['showModal','close']){if(original[key])Object.defineProperty(HTMLDialogElement.prototype,key,original[key]);else Reflect.deleteProperty(HTMLDialogElement.prototype,key)}})
