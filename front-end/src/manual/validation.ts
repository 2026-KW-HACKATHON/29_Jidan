/** Response boundary checks the selected OpenAPI keywords; server still owns cross-resource rules. */
export type Schema = { $ref?: string; type?: string | string[]; const?: unknown; enum?: unknown[]; properties?: Record<string, Schema>; required?: string[]; items?: Schema; minItems?: number; maxItems?: number; minLength?: number; maxLength?: number; minimum?: number; maximum?: number; pattern?: string; format?: string; additionalProperties?: boolean; uniqueItems?: boolean; oneOf?: Schema[]; anyOf?: Schema[]; allOf?: Schema[]; if?: Schema; then?: Schema; else?: Schema; not?: Schema; contains?: Schema; minContains?: number; maxContains?: number }
export function matches(schema: Schema, value: unknown, definitions: Record<string, Schema>): boolean {
 if(schema.$ref) return matches(definitions[schema.$ref.split('/').at(-1)!],value,definitions)
 const test=(s:Schema)=>matches(s,value,definitions)
 if(schema.oneOf && schema.oneOf.filter(test).length!==1)return false
 if(schema.anyOf && !schema.anyOf.some(test))return false
 if(schema.allOf && !schema.allOf.every(test))return false
 if(schema.not && test(schema.not))return false
 if(schema.if && !test(test(schema.if)?schema.then??{}:schema.else??{}))return false
 if('const' in schema && value!==schema.const)return false
 if(schema.enum && !schema.enum.includes(value))return false
 if(schema.type){const types=Array.isArray(schema.type)?schema.type:[schema.type]; if(!types.some(t=>t==='null'?value===null:t==='array'?Array.isArray(value):t==='object'?!!value && typeof value==='object' && !Array.isArray(value):t==='integer'?Number.isInteger(value):typeof value===t))return false}
 if(typeof value==='string'){
  if(value.length<(schema.minLength??0)||value.length>(schema.maxLength??Infinity))return false
  if(schema.pattern && !new RegExp(schema.pattern).test(value))return false
  if(schema.format==='uuid' && !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(value))return false
  if(schema.format==='date-time' && !/^\d{4}-\d{2}-\d{2}T/.test(value))return false
 }
 if(typeof value==='number' && (!Number.isFinite(value)||value<(schema.minimum??-Infinity)||value>(schema.maximum??Infinity)))return false
 if(Array.isArray(value)){
  if(value.length<(schema.minItems??0)||value.length>(schema.maxItems??Infinity))return false
  if(schema.items && !value.every(v=>matches(schema.items!,v,definitions)))return false
  if(schema.contains){const count=value.filter(v=>matches(schema.contains!,v,definitions)).length;if(count<(schema.minContains??1)||count>(schema.maxContains??Infinity))return false}
  if(schema.uniqueItems && new Set(value.map(v=>JSON.stringify(v))).size!==value.length)return false
 }
 if(value && typeof value==='object'&&!Array.isArray(value)){
  const object=value as Record<string,unknown>
  if(schema.required?.some(k=>!(k in object)))return false
  if(schema.additionalProperties===false && Object.keys(object).some(k=>!(k in (schema.properties??{}))))return false
  if(schema.properties && !Object.entries(schema.properties).every(([k,v])=>!(k in object)||matches(v,object[k],definitions)))return false
 }
 return true
}
