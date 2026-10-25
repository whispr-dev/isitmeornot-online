import test from 'node:test';
import assert from 'node:assert/strict';
import {POPULAR,parseUrls,resultText,csvReport} from '../site/format.js';
test('bulk input trims blank lines and deduplicates without splitting query commas',()=>{
 assert.deepEqual(parseUrls(' example.com\r\n\nhttps://example.com/?q=a,b\nexample.com '),['example.com','https://example.com/?q=a,b']);
});
test('text export preserves the actual status and timing',()=>{
 const text=resultText({url:'https://example.com/',label:'Access limited',status:403,elapsed_ms:42,detail:'Automated access may be blocked.',checked_at:'2026-10-25T15:00:00+00:00'});
 assert.match(text,/HTTP 403/);assert.match(text,/42 ms/);assert.match(text,/Access limited/);assert.doesNotMatch(text,/down/i);
});
test('CSV quotes values and prevents spreadsheet formulas in invalid input',()=>{
 const csv=csvReport([{url:'=1+1',label:'Blocked target',detail:'one,"two"\nthree'}]);
 assert.match(csv,/"'=1\+1"/);assert.match(csv,/one,""two""\nthree/);assert.match(csv,/\r\n/);
});
test('popular shortcuts are a small explicit set of public HTTPS URLs',()=>{
 assert.equal(POPULAR.length,6);for(const value of POPULAR){const url=new URL(value);assert.equal(url.protocol,'https:');assert.equal(url.username,'');}
});
