import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
export const spec = parse(await readFile(new URL('../../../openapi.yaml', import.meta.url), 'utf8'));
export function validator(name) {
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  ajv.addSchema(spec, 'https://jidan.example/owner-contract');
  return ajv.compile({ $ref: `https://jidan.example/owner-contract#/components/schemas/${name}` });
}
export function parameterValidator(parameter) {
  const value = parameter.$ref ? spec.components.parameters[parameter.$ref.split('/').at(-1)] : parameter;
  const ajv = new Ajv2020({ strict: false }); addFormats(ajv);
  return ajv.compile(value.schema);
}
