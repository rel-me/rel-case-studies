import {selectRecords, hasLocation, mapBand} from './study.js';

export const PAGE_LIMIT=25;
const integer=(value,min,max,name)=>{
  if(!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value)<min || Number(value)>max) throw new Error('Invalid '+name);
  return Number(value);
};
function filters(params){
  const mode=params.get('view')||'all', location=params.get('location')||'', sort=params.get('sort')||'gap';
  if(!['all','assessment'].includes(mode)||!['','city','outside','unknown'].includes(location)||!['gap','rate','estimate','address','assessment-gap','assessment-rate','assessment-percent-gap','percent-gap'].includes(sort))throw new Error('Invalid filter');
  const q=params.get('q')||'',type=params.get('type')||'';
  if(q.length>100||type.length>60)throw new Error('Filter too long');
  return {mode,q,type,sort,location};
}
function bounds(rows){
  if(!rows.length)return null;
  return [[Math.min(...rows.map(r=>r.latitude)),Math.min(...rows.map(r=>r.longitude))],[Math.max(...rows.map(r=>r.latitude)),Math.max(...rows.map(r=>r.longitude))]];
}
const PUBLIC_FIELDS=['record_id','address','city','postal_code','in_santa_cruz_city','municipality','home_type','estimated_value','assessed_value','latitude','longitude'];
function publicRecord(record){return Object.fromEntries(PUBLIC_FIELDS.filter(key=>Object.hasOwn(record,key)).map(key=>[key,record[key]]));}
export function createApi(snapshot){
  const records=snapshot.records, byId=new Map(records.map(r=>[r.record_id,r]));
  const summary={metadata:snapshot.metadata,home_types:[...new Set(records.map(r=>r.home_type).filter(Boolean))].sort()};
  return function api(path,params){
    for(const key of params.keys())if(params.getAll(key).length>1)throw new Error('Duplicate parameter');
    if(path==='/api/summary')return summary;
    if(path==='/api/record'){
      const id=params.get('id');if(!/^[a-f0-9]{14}$/.test(id||''))throw new Error('Invalid record ID');
      const record=byId.get(id);return record?publicRecord(record):null;
    }
    const f=filters(params);
    const selected=selectRecords(records,f.mode,f.q,f.type,f.sort,f.location),located=selected.filter(hasLocation);
    if(path==='/api/records'){
      const limit=integer(params.get('limit')||String(PAGE_LIMIT),1,PAGE_LIMIT,'page size');
      const page=Math.min(integer(params.get('page')||'1',1,10000,'page'),Math.max(1,Math.ceil(selected.length/limit)));
      return {records:selected.slice((page-1)*limit,page*limit).map(publicRecord),page,page_size:limit,total:selected.length,mapped:located.length,unmapped:selected.length-located.length,bounds:bounds(located)};
    }
    if(path==='/api/map'){
      const zoom=integer(params.get('zoom')||'10',0,19,'zoom');
      const bbox=(params.get('bbox')||'').split(',').map(Number);
      if(bbox.length!==4||bbox.some(v=>!Number.isFinite(v))||bbox[0]>=bbox[2]||bbox[1]>=bbox[3]||bbox[0]<-180||bbox[2]>180||bbox[1]<-90||bbox[3]>90)throw new Error('Invalid map bounds');
      const [west,south,east,north]=bbox;
      const visible=located.filter(r=>r.longitude>=west&&r.longitude<=east&&r.latitude>=south&&r.latitude<=north);
      if(visible.length<=PAGE_LIMIT||zoom>=17){
        return {points:visible.slice(0,PAGE_LIMIT).map(r=>({record_id:r.record_id,latitude:r.latitude,longitude:r.longitude,band:mapBand(r)})),clusters:[],visible:visible.length,mapped:located.length,unmapped:selected.length-located.length,truncated:visible.length>PAGE_LIMIT};
      }
      const cells=new Map();
      for(const r of visible){
        const x=Math.min(7,Math.floor((r.longitude-west)/(east-west)*8)),y=Math.min(7,Math.floor((r.latitude-south)/(north-south)*8));
        const key=x+':'+y;if(!cells.has(key))cells.set(key,[]);cells.get(key).push(r);
      }
      return {points:[],clusters:[...cells.values()].map(rows=>({count:rows.length,latitude:rows.reduce((sum,r)=>sum+r.latitude,0)/rows.length,longitude:rows.reduce((sum,r)=>sum+r.longitude,0)/rows.length,bounds:bounds(rows)})),visible:visible.length,mapped:located.length,unmapped:selected.length-located.length,truncated:false};
    }
    return undefined;
  };
}
