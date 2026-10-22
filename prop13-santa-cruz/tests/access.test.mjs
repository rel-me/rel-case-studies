import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createWorker} from '../lib/worker.js';
import snapshot from './fixtures/snapshot.mjs';
const worker=createWorker(snapshot);
import {createApi} from '../lib/api.js';

const api=createApi(snapshot),BASE='https://rel.me/case-studies/prop13-santa-cruz';
const allowed={STUDY_RATE_LIMITER:{limit:async()=>({success:true})}};

test('bulk files are absent from assets and every former download route is blocked',async()=>{
  for(const file of ['data.json','data.csv'])assert.equal(fs.existsSync(new URL('../public/'+file,import.meta.url)),false);
  for(const path of ['/data.json','/data.csv','/data/data.json','/data/data.csv','/api/export','/api/data','/worker.js','/lib/api.js']){
    const response=await worker.fetch(new Request(BASE+path),allowed);assert.equal(response.status,404,path);
  }
  const html=fs.readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
  assert.doesNotMatch(html,/\bdownload\s*[=>]|Download CSV|Download filtered|Data and metadata \(JSON\)/);
  const client=fs.readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
  assert.doesNotMatch(client,/data\.json|data\.csv|data\.records|downloadView/);
});

test('record pages are capped on the server with non-overlapping pagination',async()=>{
  const first=api('/api/records',new URLSearchParams({view:'assessment',page:'1'}));
  const second=api('/api/records',new URLSearchParams({view:'assessment',page:'2'}));
  assert.equal(first.records.length,25);assert.equal(second.records.length,25);
  assert.equal(first.page_size,25);assert.equal(first.records.some(r=>second.records.some(s=>s.record_id===r.record_id)),false);
  const dougmar=api('/api/records',new URLSearchParams({view:'assessment',q:'dougmar',location:'outside'}));
  assert.ok(dougmar.total>25);assert.equal(dougmar.records.length,25);
  for(const query of ['limit=1000','limit=all','limit=0','page=-1','page=Infinity','limit=25&limit=1000']){
    const response=await worker.fetch(new Request(BASE+'/api/records?'+query),allowed);assert.equal(response.status,400,query);
  }
});

test('summary contains coverage metadata but no bill or payment data',()=>{
  const result=api('/api/summary',new URLSearchParams());assert.equal(result.records,undefined);assert.equal(result.tax_chart,undefined);assert.equal(result.histogram,undefined);
  assert.equal(Object.keys(result.metadata).some(key=>/paid|installment|annual_tax|payment|bill/i.test(key)),false);
});

test('map serves bounded aggregates or at most 25 sparse property points',()=>{
  const params=new URLSearchParams({view:'assessment',bbox:'-122.4,36.7,-121.5,37.4',zoom:'10'});
  const broad=api('/api/map',params);assert.equal(broad.points.length,0);assert.ok(broad.clusters.length<=64);assert.equal(broad.clusters.reduce((sum,c)=>sum+c.count,0),broad.visible);
  assert.ok(broad.clusters.every(c=>!('address' in c)&&!('record_id' in c)));
  params.set('zoom','19');const close=api('/api/map',params);assert.equal(close.points.length,25);assert.equal(close.truncated,true);
  assert.deepEqual(Object.keys(close.points[0]).sort(),['band','latitude','longitude','record_id']);
});

test('single-record details, no-store responses and request throttling are enforced',async()=>{
  const id=snapshot.records[0].record_id;
  const record=await worker.fetch(new Request(BASE+'/api/record?id='+id),allowed);assert.equal(record.status,200);assert.equal((await record.json()).record_id,id);assert.match(record.headers.get('Cache-Control'),/no-store/);
  const denied={STUDY_RATE_LIMITER:{limit:async()=>({success:false})}};
  const response=await worker.fetch(new Request(BASE+'/api/records'),denied);assert.equal(response.status,429);assert.equal(response.headers.get('Retry-After'),'60');
  assert.equal((await worker.fetch(new Request(BASE+'/api/records'),{})).status,503);
  assert.equal((await worker.fetch(new Request(BASE+'/api/record?id=bad'),allowed)).status,400);
});


test('property browsing exposes only the two requested values',()=>{
  const records=[
    {record_id:'aaaaaaaaaaaaaa',address:'1 Example',estimated_value:1000000,assessed_value:200000,annual_tax:3000,annual_paid:3000,assessment_gap:800000,benchmark_gap:7000,assessment_ratio:.2,effective_tax_rate:.003,latitude:36.98,longitude:-122.03},
    {record_id:'bbbbbbbbbbbbbb',address:'2 Example',estimated_value:900000,assessed_value:300000,annual_tax:null,annual_paid:null,assessment_gap:600000,benchmark_gap:null,assessment_ratio:1/3,effective_tax_rate:null,latitude:36.981,longitude:-122.031},
    {record_id:'cccccccccccccc',address:'3 Example',estimated_value:800000,assessed_value:null,annual_tax:5000,annual_paid:null,assessment_gap:null,benchmark_gap:3000,assessment_ratio:null,effective_tax_rate:.00625,latitude:36.982,longitude:-122.032}
  ];
  const combined=createApi({metadata:{},records});
  const page=combined('/api/records',new URLSearchParams({view:'all',sort:'assessment-gap'}));
  assert.equal(page.total,3);assert.equal(new Set(page.records.map(r=>r.record_id)).size,3);
  assert.deepEqual(page.records.map(r=>r.address),['1 Example','2 Example','3 Example']);
  assert.equal(page.records[0].estimated_value,1000000);assert.equal(page.records[0].assessed_value,200000);
  assert.equal(Object.keys(page.records[0]).some(key=>/paid|installment|annual_tax|payment|bill/i.test(key)),false);
  const params=new URLSearchParams({view:'all',bbox:'-122.04,36.97,-122.02,36.99',zoom:'19',color:'assessment'});
  const assessmentMap=combined('/api/map',params);
  assert.equal(assessmentMap.points.length,3);
  assert.equal(assessmentMap.points.find(r=>r.record_id===records[1].record_id).band,'mid');
  assert.equal(assessmentMap.points.find(r=>r.record_id===records[2].record_id).band,'missing');
  const detail=combined('/api/record',new URLSearchParams({id:assessmentMap.points[0].record_id}));
  assert.equal(detail.assessed_value,200000);assert.equal(detail.estimated_value,1000000);
  assert.equal(Object.keys(detail).some(key=>/paid|installment|annual_tax|payment|bill/i.test(key)),false);
  const byPercent=combined('/api/records',new URLSearchParams({view:'all',sort:'assessment-percent-gap'}));
  assert.deepEqual(byPercent.records.map(r=>r.address),['1 Example','2 Example','3 Example']);
});
