import {test} from 'node:test';import assert from 'node:assert/strict';import {spec,validator} from './helpers/owner-contract.mjs';
const p='/api/users/me/favorite-stores';
test('관심 매장: 빈 목록과 개인정보 비노출',()=>{const x=spec.paths[p].get.responses['200'].content['application/json'].example;assert.ok(validator('FavoriteStorePage')({...x,items:[],totalItems:0}));assert.equal(validator('FavoriteStore')({...x.items[0],email:'owner@example.com'}),false);});
test('관심 매장: body 주체 주입 거절·반복 등록/해제 계약',()=>{assert.ok(validator('FavoriteStoreSave')({}));assert.equal(validator('FavoriteStoreSave')({workerId:'x'}),false);assert.match(spec.paths[p+'/{storeId}'].put.description,/최초 savedAt을 유지/);assert.match(spec.paths[p+'/{storeId}'].delete.description,/이미 없는 관계도 204/);});
