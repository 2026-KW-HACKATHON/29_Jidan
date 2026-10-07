import {readFileSync,writeFileSync} from 'node:fs'
// Supply the deployed /api/swagger/openapi.json snapshot. No credentials are read.
const spec=JSON.parse(readFileSync(process.argv[2],'utf8')), used=new Set(), operations={}
const refName=s=>s?.$ref?.split('/').at(-1)
function refs(value){if(Array.isArray(value))value.forEach(refs);else if(value&&typeof value==='object'){const name=refName(value);if(name&&!used.has(name)){used.add(name);refs(spec.components.schemas[name])}Object.values(value).forEach(refs)}}
for(const [path,methods] of Object.entries(spec.paths))for(const [method,op] of Object.entries(methods)){
 if(!op.operationId||/\/auth\/|\/admin\/|\/manual(?:\/|$)/.test(path))continue
 const input=Object.values(op.requestBody?.content??{})[0]?.schema
 const response=Object.entries(op.responses).find(([status,value])=>status.startsWith('2')&&value.content?.['application/json'])?.[1].content['application/json'].schema
 operations[op.operationId]={method:method.toUpperCase(),path:path.slice(4),input:input??{},output:response??null,parameters:(op.parameters??[]).map(p=>p.$ref?spec.components.parameters[refName(p)]:p).filter(p=>['path','query'].includes(p.in))};refs(input);refs(response)
}
function type(s){
 if(!s)return 'undefined';if(s.$ref)return refName(s);if('const'in s)return JSON.stringify(s.const);if(s.enum)return s.enum.map(x=>JSON.stringify(x)).join(' | ')
 if(Array.isArray(s.type))return s.type.map(t=>type({...s,type:t})).join(' | ')
 let base=s.type==='object'||s.properties?'{ '+Object.entries(s.properties??{}).map(([n,v])=>`${JSON.stringify(n)}${s.required?.includes(n)?'':'?'}: ${type(v)}`).join('; ')+' }':s.type==='array'?`Array<${type(s.items)}>`:({string:'string',number:'number',integer:'number',boolean:'boolean',null:'null'}[s.type]??'unknown')
 for(const [key,join]of [['allOf',' & '],['oneOf',' | '],['anyOf',' | ']])if(s[key])base=(base==='unknown'?'':base+' & ')+`(${s[key].map(type).join(join)})`
 return base
}
const header=`// Generated from deployed OpenAPI ${spec.info.version}. Regenerate with scripts/generate-api-contract.mjs.\n`
let types=header+[...used].sort().map(n=>`export type ${n} = ${type(spec.components.schemas[n])}\n`).join('')
types+='export type Operations = {\n'+Object.entries(operations).map(([n,o])=>` ${n}: {input: ${Object.keys(o.input).length?type(o.input):'undefined'}; output: ${o.output?type(o.output):'void'}}`).join('\n')+'\n}\n'
const keys=new Set(['$ref','type','const','enum','properties','required','items','minItems','maxItems','minLength','maxLength','minimum','maximum','pattern','format','additionalProperties','uniqueItems','oneOf','anyOf','allOf','if','then','else','not','contains'])
function strip(x){if(Array.isArray(x))return x.map(strip);if(x&&typeof x==='object')return Object.fromEntries(Object.entries(x).filter(([k])=>keys.has(k)).map(([k,v])=>[k,k==='properties'?Object.fromEntries(Object.entries(v).map(([n,p])=>[n,strip(p)])):strip(v)]));return x}
const schemas=Object.fromEntries([...used].sort().map(n=>[n,strip(spec.components.schemas[n])]))
for(const op of Object.values(operations)){op.input=strip(op.input);op.output=strip(op.output);op.parameters=op.parameters.map(p=>({name:p.name,in:p.in,required:!!p.required,schema:strip(p.schema)}))}
writeFileSync(new URL('../src/api/types.generated.ts',import.meta.url),types)
writeFileSync(new URL('../src/api/contract.generated.ts',import.meta.url),header+'import type {Schema} from "../manual/validation"\nexport const schemas: Record<string,Schema> = '+JSON.stringify(schemas)+'\nexport const operations = '+JSON.stringify(operations)+' as const\n')
console.log(`${Object.keys(operations).length} operations, ${used.size} schemas`)
