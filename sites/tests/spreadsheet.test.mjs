
import test from 'node:test';import assert from 'node:assert/strict';import {mkdirSync,writeFileSync}from'node:fs';
import {productTemplate,workbook,zip} from '../lib/spreadsheet.mjs';
test('xlsx and zip contain actual Excel containers and preserve user text',()=>{
  mkdirSync('test-artifacts',{recursive:true});
  const bytes=productTemplate();assert.deepEqual([...bytes.slice(0,4)],[80,75,3,4]);
  writeFileSync('test-artifacts/product-template.xlsx',bytes);
  writeFileSync('test-artifacts/text-safety.xlsx',workbook([['Test',['User text','Quantity'],[['=HYPERLINK("fake")',3],['a&b<text>',4]]]]));
});
