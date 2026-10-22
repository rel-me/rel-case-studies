import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createWorker} from '../lib/worker.js';
import snapshot from './fixtures/snapshot.mjs';
const worker=createWorker(snapshot);
const BASE = '/case-studies/prop13-santa-cruz';
import {selectRecords,assessedDifference,assessedDifferencePercent,assessedRatio,hasLocation,mapBand} from '../lib/study.js';

const data=snapshot;

test('public data contains only assessed and estimated property values, not bill data',()=>{
  const m=data.metadata;
  assert.equal(data.records.filter(r=>r.estimated_value>0).length,m.with_estimate);
  assert.equal(data.records.filter(r=>r.assessed_value!=null).length,m.with_assessment);
  assert.equal(new Set(data.records.map(r=>r.record_id)).size,data.records.length);
  for(const r of data.records){
    assert.ok(r.estimated_value==null || r.estimated_value>0);
    assert.ok(r.assessed_value==null || r.assessed_value>0);
    assert.equal(assessedDifference(r)==null,!(r.assessed_value>0&&r.estimated_value>0));
    assert.equal(assessedDifferencePercent(r)==null,!(r.assessed_value>0&&r.estimated_value>0));
    assert.equal(assessedRatio(r)==null,!(r.assessed_value>0&&r.estimated_value>0));
    assert.equal(Object.keys(r).some(key=>/paid|installment|annual_tax|payment|bill/i.test(key)),false);
  }
  assert.equal(Object.keys(m).some(key=>/paid|installment|annual_tax|payment|bill/i.test(key)),false);
});

test('public assets exclude provider branding, source identifiers and raw captures',()=>{
  for(const file of fs.readdirSync(new URL('../public',import.meta.url),{recursive:true}).filter(file=>fs.statSync(new URL('../public/'+file,import.meta.url)).isFile())){
    const source=fs.readFileSync(new URL('../public/'+file,import.meta.url),'utf8');
    assert.doesNotMatch(source,/zillow|zestimate|zpid|session_id|oxylabs|localhost:8787|127\.0\.0\.1:8787/i,file);
  }
});

test('public comparison displays both source values and sortable differences',()=>{
  const html=fs.readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
  const app=fs.readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
  assert.match(html,/<th[^>]*>Third-party estimate<\/th>/);
  assert.match(html,/<th[^>]*>Assessed value for tax purposes<\/th>/);
  assert.match(html,/<th[^>]*>Value difference/);
  assert.match(html,/<th[^>]*>Percentage difference/);
  assert.match(app,/assessment-gap/);
  assert.match(app,/assessment-percent-gap/);
  assert.match(app,/estimate minus assessed/);
  assert.match(app,/share of estimate/);
  assert.doesNotMatch(html,/County recorded paid|Annual tax billed|Annual tax comparisons/);
  assert.doesNotMatch(app,/annual_paid|annual_tax|installment|payment status/i);
});

test('assessment comparisons retain address and property filters',()=>{
  const rows=selectRecords(data.records,'assessment');
  const comparable=rows.filter(r=>assessedDifference(r)!=null);assert.ok(comparable.every((r,i)=>!i||assessedDifference(r)<=assessedDifference(comparable[i-1])));
  const address=rows[0].address;
  assert.ok(selectRecords(data.records,'assessment',address).some(r=>r.address===address));
  assert.equal(selectRecords(data.records,'assessment','no such address 192837465').length,0);
  const singleFamily=selectRecords(data.records,'assessment','','SINGLE_FAMILY','rate');
  assert.ok(singleFamily.every(r=>r.home_type==='SINGLE_FAMILY'));
  const rateSorted=singleFamily.filter(r=>assessedRatio(r)!=null);
  assert.ok(rateSorted.every((r,i)=>!i||assessedRatio(r)>=assessedRatio(rateSorted[i-1])));
  const percentSorted=selectRecords(data.records,'assessment','','','assessment-percent-gap').filter(r=>assessedDifferencePercent(r)!=null);
  assert.ok(percentSorted.every((r,i)=>!i||assessedDifferencePercent(r)<=assessedDifferencePercent(percentSorted[i-1])));
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
  const rows=[{record_id:'a',address:'City',in_santa_cruz_city:true,estimated_value:11,assessed_value:1},{record_id:'b',address:'County',in_santa_cruz_city:false,assessed_value:2},{record_id:'c',address:'Unknown',in_santa_cruz_city:null,estimated_value:8,assessed_value:3}];
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','city').map(r=>r.record_id),['a']);
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','outside').map(r=>r.record_id),['b']);
  assert.deepEqual(selectRecords(rows,'assessment','','','gap','unknown').map(r=>r.record_id),['c']);
  assert.deepEqual(selectRecords(rows,'assessment').map(r=>r.record_id),['a','c','b']);
});

test('map excludes absent or invalid locations and classifies the same two values',()=>{
  assert.equal(hasLocation({latitude:null,longitude:null}),false);
  assert.equal(hasLocation({latitude:36.98,longitude:-122.03}),true);
  assert.equal(hasLocation({latitude:136,longitude:-122}),false);
  assert.equal(hasLocation({latitude:44.315987,longitude:-98.43372}),false);
  assert.equal(hasLocation({latitude:36,longitude:Infinity}),false);
  assert.equal(mapBand({assessed_value:null,estimated_value:100000},'assessment'),'missing');
  assert.equal(mapBand({assessed_value:250000,estimated_value:1000000}),'mid');
  assert.equal(mapBand({assessed_value:1000000,estimated_value:1000000}),'full');
  assert.equal(data.records.filter(hasLocation).length,data.metadata.with_location);
});
