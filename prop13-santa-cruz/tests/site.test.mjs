import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import worker from '../worker.js';
const BASE = '/case-studies/prop13-santa-cruz';
import {selectRecords,histogram,choosePair} from '../public/app.js';

const data=JSON.parse(fs.readFileSync(new URL('../public/data.json',import.meta.url)));

test('public data uses evidence-backed amounts and internally consistent metrics',()=>{
  const m=data.metadata;
  assert.equal(data.records.filter(r=>r.estimated_value>0).length,m.with_estimate);
  assert.equal(data.records.filter(r=>r.annual_tax!=null).length,m.with_annual_tax);
  assert.equal(data.records.filter(r=>r.assessed_value!=null).length,m.with_assessment);
  assert.equal(new Set(data.records.map(r=>r.record_id)).size,data.records.length);
  for(const r of data.records){
    assert.ok(r.estimated_value==null || r.estimated_value>0);
    if(r.estimated_value==null){assert.equal(r.benchmark_gap,null);assert.equal(r.assessment_ratio,null);}
    if(r.annual_tax!=null){assert.ok(r.tax_year);assert.ok(r.tax_captured_at);if(r.estimated_value>0)assert.ok(Math.abs(r.benchmark_gap-(r.estimated_value*.01-r.annual_tax))<.011);}
    else {assert.equal(r.benchmark_gap,null);assert.equal(r.effective_tax_rate,null);}
  }
});

test('public assets exclude provider branding, source identifiers and raw captures',()=>{
  for(const file of fs.readdirSync(new URL('../public',import.meta.url))){
    const source=fs.readFileSync(new URL('../public/'+file,import.meta.url),'utf8');
    assert.doesNotMatch(source,/zillow|zestimate|zpid|session_id|oxylabs|localhost:8787|127\.0\.0\.1:8787/i,file);
  }
});

test('rankings handle exclusions, search, type filters and negative gaps',()=>{
  const tax=selectRecords(data.records,'tax');
  assert.equal(tax.length,data.metadata.with_annual_tax);
  const comparable=tax.filter(r=>r.benchmark_gap!=null);assert.ok(comparable.every((r,i)=>!i||r.benchmark_gap<=comparable[i-1].benchmark_gap));
  const address=tax[0].address;
  assert.ok(selectRecords(data.records,'tax',address).some(r=>r.address===address));
  assert.equal(selectRecords(data.records,'tax','no such address 192837465').length,0);
  const rows=selectRecords(data.records,'assessment','','SINGLE_FAMILY','rate');
  assert.ok(rows.every(r=>r.home_type==='SINGLE_FAMILY'));
  assert.ok(rows.every((r,i)=>!i||r.assessment_ratio>=rows[i-1].assessment_ratio));
  assert.equal(histogram(data.records).reduce((a,b)=>a+b,0),data.metadata.with_assessment_comparison ?? data.metadata.with_assessment);
});

test('featured pair has same property category/year and estimates within 15 percent',()=>{
  const pair=choosePair(data.records);assert.ok(pair);
  assert.equal(pair[0].home_type,pair[1].home_type);
  assert.equal(pair[0].tax_year,pair[1].tax_year);
  assert.ok(Math.max(...pair.map(r=>r.estimated_value))/Math.min(...pair.map(r=>r.estimated_value))<=1.15);
});

test('worker serves only allowlisted public study files and guards its route',async()=>{
  let path;
  const env={ASSETS:{fetch:async r=>{path=new URL(r.url).pathname;return new Response('fixture');}}};
  const index=await worker.fetch(new Request('https://rel.me'+BASE),env);
  assert.equal(index.status,200);assert.equal(path,'/index.html');
  assert.match(index.headers.get('Content-Security-Policy'),/frame-ancestors 'none'/);
  for(const suffix of ['-other','/dashboard','/api/data','/output/database.sqlite3','/worker.js']){
    assert.equal((await worker.fetch(new Request('https://rel.me'+BASE+suffix),env)).status,404);
  }
  assert.equal((await worker.fetch(new Request('https://rel.me'+BASE,{method:'POST'}),env)).status,405);
});

 test('city-limit filtering uses boundary flags and retains missing-estimate records',()=>{
  const rows=[{record_id:'a',address:'City',in_santa_cruz_city:true,assessed_value:1,assessment_gap:10},{record_id:'b',address:'County',in_santa_cruz_city:false,assessed_value:2,assessment_gap:null},{record_id:'c',address:'Unknown',in_santa_cruz_city:null,assessed_value:3,assessment_gap:5}];
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','city').map(r=>r.record_id),['a']);
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','outside').map(r=>r.record_id),['b']);
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','unknown').map(r=>r.record_id),['c']);
  assert.deepEqual(selectRecords(rows,'assessment').map(r=>r.record_id),['a','c','b']);
});
