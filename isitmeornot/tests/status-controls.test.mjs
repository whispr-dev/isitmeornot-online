import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
test('status form posts only to its own API and copies returned results as text',async()=>{
 class Element {
  constructor(tag='div'){this.tag=tag;this.textContent='';this.value='';this.disabled=false;this.handlers={};this.dataset={};this.children=[];this.classList={add(){},remove(){},toggle(){}};}
  addEventListener(name,fn){this.handlers[name]=fn;}
  setAttribute(){}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  showModal(){} close(){} focus(){}
  set innerHTML(_){throw Error('HTML injection sink used');}
 }
 const html=fs.readFileSync(new URL('../site/index.html',import.meta.url),'utf8');
 const ids=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
 const services=[...html.matchAll(/data-service="([^"]+)"/g)].map(match=>{const button=new Element('button');button.dataset.service=match[1];return button;});
 globalThis.document={getElementById(id){assert(ids.has(id),'Missing element '+id);return ids.get(id);},querySelectorAll(){return services;},createElement(tag){return new Element(tag);}};
 globalThis.window={isSecureContext:true};
 let copied='';Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{async writeText(value){copied=value;}}}});
 const calls=[];
 globalThis.fetch=async(url,options)=>{
  calls.push([url,options]);const input=JSON.parse(options.body).urls[0];
  return {ok:true,async json(){return {results:[{input,url:input,status:403,state:'restricted',label:'Access limited',elapsed_ms:42,detail:'Access was restricted.',redirects:0,checked_at:'2026-10-25T15:00:00Z'}]};}};
 };
 await import('../site/app.js');
 ids.get('urls').value='https://example.com/';
 ids.get('check-form').handlers.submit({preventDefault(){}});
 for(let i=0;i<50&&ids.get('check-button').disabled;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(ids.get('check-button').disabled,false);
 assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/status');assert.equal(calls[0][1].credentials,'omit');assert.equal(calls[0][1].method,'POST');
 ids.get('copy-all-results').handlers.click();assert.match(copied,/HTTP 403/);assert.match(copied,/42 ms/);assert.match(copied,/Access limited/);
 const card=ids.get('results').children[0];
 const meta=card.children[1];const urlButton=meta.children.find(node=>node.textContent==='Copy URL ↗');urlButton.handlers.click();assert.equal(copied,'https://example.com/');
 ids.get('urls').value=Array.from({length:21},(_,i)=>`https://example.com/${i}`).join('\n');
 ids.get('check-form').handlers.submit({preventDefault(){}});assert.equal(calls.length,1);assert.match(ids.get('message').textContent,/between 1 and 20/);
});
