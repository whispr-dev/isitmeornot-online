import {POPULAR,parseUrls,resultText,csvReport} from './format.js';
const byId=id=>document.getElementById(id);
let running=false;
let rows=[];
let toastTimer;
const allowedStates=new Set(['up','restricted','redirect','http_error','unreachable','timeout','dns_error','tls_error','blocked','checker_error','queued','checking']);
function notify(text) {
  byId('toast').textContent=text;byId('toast').classList.add('visible');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>byId('toast').classList.remove('visible'),3000);
}
async function copy(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const previous=document.activeElement;
      const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();
      const ok=document.execCommand('copy');input.remove();previous?.focus();if(!ok)throw new Error('Unavailable');
    }
    notify('Copied to clipboard');
  } catch (_) {notify('Copy unavailable. Select the result text to copy it manually.');}
}
function message(text,error=false) {byId('message').textContent=text;byId('message').classList.toggle('error',error);}
function count() {byId('url-count').textContent=`${parseUrls(byId('urls').value).length} / 20`;}
function element(tag,className,text) {const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function render() {
  const list=byId('results');list.replaceChildren();
  for(const row of rows) {
    const card=element('article','result');
    const head=element('div','result-head');
    const target=element('span','result-url',row.url||row.input);target.title=row.url||row.input;
    const state=allowedStates.has(row.state)?row.state:'checker_error';
    head.append(target,element('span',`result-badge ${state}`,row.label));
    const meta=element('div','result-meta');
    meta.append(element('span','',row.status?`HTTP ${row.status}`:'HTTP —'),element('span','',Number.isFinite(row.elapsed_ms)?`${row.elapsed_ms} ms`:'Time —'));
    if(row.redirects) meta.append(element('span','',`${row.redirects} redirect${row.redirects===1?'':'s'}`));
    const urlCopy=element('button','text-button','Copy URL ↗');urlCopy.type='button';urlCopy.setAttribute('aria-label',`Copy URL ${row.url||row.input}`);urlCopy.addEventListener('click',()=>copy(row.url||row.input));
    const resultCopy=element('button','text-button copy-result','Copy result ↗');resultCopy.type='button';resultCopy.disabled=state==='queued'||state==='checking';resultCopy.addEventListener('click',()=>copy(resultText(row)));
    meta.append(urlCopy,resultCopy);
    card.append(head,meta);
    if(row.detail)card.append(element('p','result-detail',row.detail));
    if(row.final_url && row.final_url!==row.url)card.append(element('p','result-detail','Final URL: '+row.final_url));
    list.append(card);
  }
  const done=rows.some(row=>!['queued','checking'].includes(row.state));
  byId('copy-all-results').disabled=!done;
  byId('export-csv').disabled=!done;
}
async function checkOne(value) {
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);
  try {
    const response=await fetch('/api/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:[value]}),cache:'no-store',credentials:'omit',signal:controller.signal});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error||`Checker returned HTTP ${response.status}`);
    if(!Array.isArray(data.results)||!data.results[0])throw new Error('The checker returned no result.');
    return data.results[0];
  } catch(error) {
    return {input:value,url:value,status:null,elapsed_ms:null,state:'checker_error',label:'Check failed',redirects:0,detail:error.name==='AbortError'?'The checker did not reply in time. Try again.':error.message,checked_at:new Date().toISOString()};
  } finally {clearTimeout(timer);}
}
async function run(values) {
  if(running)return;
  if(!values.length||values.length>20){message('Enter between 1 and 20 URLs, one per line.',true);return;}
  if(values.some(value=>value.length>2048)){message('Each URL must be at most 2048 characters.',true);return;}
  running=true;byId('check-button').disabled=true;byId('check-popular').disabled=true;
  for(const button of document.querySelectorAll('[data-service]'))button.disabled=true;
  rows=values.map(input=>({input,url:input,state:'queued',label:'Queued',status:null,elapsed_ms:null,redirects:0}));render();
  let next=0;let finished=0;
  message(`Checking 0 / ${values.length} from London…`);
  async function worker(){
    while(next<values.length){
      const index=next++;rows[index]={...rows[index],state:'checking',label:'Checking…'};render();
      rows[index]=await checkOne(values[index]);finished++;render();message(`Checking ${finished} / ${values.length} from London…`);
    }
  }
  try {
    await Promise.all(Array.from({length:Math.min(3,values.length)},worker));
    const healthy=rows.filter(row=>row.state==='up').length;
    message(`Checked ${rows.length}: ${healthy} available · ${rows.length-healthy} need a closer look.`);
  } finally {
    running=false;byId('check-button').disabled=false;byId('check-popular').disabled=false;
    for(const button of document.querySelectorAll('[data-service]'))button.disabled=false;
  }
}
byId('urls').addEventListener('input',count);
byId('check-form').addEventListener('submit',event=>{event.preventDefault();run(parseUrls(byId('urls').value));});
for(const button of document.querySelectorAll('[data-service]'))button.addEventListener('click',()=>{byId('urls').value=button.dataset.service;count();run([button.dataset.service]);});
byId('check-popular').addEventListener('click',()=>{byId('urls').value=POPULAR.join('\n');count();run([...POPULAR]);});
byId('copy-all-results').addEventListener('click',()=>copy(rows.filter(row=>!['queued','checking'].includes(row.state)).map(resultText).join('\n\n')));
byId('export-csv').addEventListener('click',()=>{
  const data=csvReport(rows.filter(row=>!['queued','checking'].includes(row.state)));
  const url=URL.createObjectURL(new Blob(['\uFEFF'+data],{type:'text/csv;charset=utf-8'}));
  const anchor=document.createElement('a');anchor.href=url;anchor.download='isitmeornot-results.csv';document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
const dialog=byId('privacy');
byId('privacy-open').addEventListener('click',()=>dialog.showModal());
byId('privacy-close').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
count();
