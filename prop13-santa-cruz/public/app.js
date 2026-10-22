const BASE = '/case-studies/prop13-santa-cruz';
const PAGE_SIZE = 25;
const number = new Intl.NumberFormat('en-US');
const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const money = v => v == null ? 'Not available' : currency.format(v);
const difference = r => Number.isFinite(r.estimated_value) && r.estimated_value > 0 && Number.isFinite(r.assessed_value) && r.assessed_value > 0 ? r.estimated_value-r.assessed_value : null;
const differencePercent = r => difference(r)==null ? null : difference(r)/r.estimated_value;
const percent = v => v==null ? 'Not available' : `${new Intl.NumberFormat('en-US',{style:'percent',maximumFractionDigits:1}).format(v)}`;
const label = v => v ? v.replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : 'Type not recorded';
const date = v => v ? new Intl.DateTimeFormat('en-US', { year:'numeric', month:'short', day:'numeric', timeZone:'UTC' }).format(new Date(v)) : 'Not available';

const $=id=>document.getElementById(id);
function element(tag,text,className) { const el=document.createElement(tag); if(text!=null)el.textContent=text; if(className)el.className=className; return el; }
let data, page=1, filtered=[], layout='list';
let propertyMap, propertyMarkers, serverClusters, mapSelection='', mapTimer, mapAbort, rowAbort, searchTimer, pageInfo={};

function fitMap() {
  if(propertyMap && pageInfo.bounds)propertyMap.fitBounds(pageInfo.bounds,{padding:[35,35],maxZoom:17,animate:false});
}
function filterParams(){
  return new URLSearchParams({view:'all',location:$('location').value,q:$('search').value,type:$('home-type').value,sort:$('sort').value});
}
async function api(path,params=new URLSearchParams(),signal){
  const response=await fetch(BASE+'/api/'+path+'?'+params,{signal,cache:'no-store'});
  if(!response.ok)throw new Error(response.status===429?'Too many requests. Please wait a minute before trying again.':'Could not load records. Please try again.');
  return response.json();
}
function mapPopup(r){
  const popup=element('div',null,'map-record');
  popup.append(element('strong',r.address||'Property record'),element('p',r.city||''));
  for(const text of [
    `Third-party estimate: ${money(r.estimated_value)}`,
    `Assessed value for tax purposes: ${money(r.assessed_value)}`,
    `Value difference: ${money(difference(r))}`,
    `Percentage difference: ${percent(differencePercent(r))}`
  ])popup.append(element('p',text));
  const button=element('button','View property details');button.addEventListener('click',()=>openRecord(r));popup.append(button);return popup;
}
async function refreshMap(){
  if(layout!=='map'||!propertyMap)return;
  mapAbort?.abort();mapAbort=new AbortController();const signal=mapAbort.signal;
  const b=propertyMap.getBounds(),params=filterParams();
  params.set('bbox',[Math.max(-180,b.getWest()),Math.max(-90,b.getSouth()),Math.min(180,b.getEast()),Math.min(90,b.getNorth())].join(','));params.set('zoom',Math.min(19,Math.max(0,Math.round(propertyMap.getZoom()))));
  try{
    const result=await api('map',params,signal);if(signal.aborted||layout!=='map')return;
    propertyMarkers.clearLayers();serverClusters.clearLayers();
    const L=window.L;
    for(const group of result.clusters){
      const marker=L.marker([group.latitude,group.longitude],{title:`${group.count} records; zoom in`,alt:`${group.count} records; zoom in`,icon:L.divIcon({html:String(group.count),className:'map-cluster',iconSize:[42,42]})});
      marker.on('click',()=>propertyMap.fitBounds(group.bounds,{padding:[35,35],maxZoom:19}));serverClusters.addLayer(marker);
    }
    for(const point of result.points){
      const marker=L.marker([point.latitude,point.longitude],{title:'View property record',alt:'View property record',icon:L.divIcon({className:'property-pin pin-'+point.band,html:'',iconSize:[14,14]})});
      marker.on('click',async()=>{try{const r=await api('record',new URLSearchParams({id:point.record_id}));marker.bindPopup(mapPopup(r)).openPopup();}catch(error){$('map-error').textContent=error.message;$('map-error').hidden=false;}});propertyMarkers.addLayer(marker);
    }
    $('map-status').textContent=`${number.format(result.mapped)} matching locations · ${number.format(result.unmapped)} locations unavailable. ${result.clusters.length?`${number.format(result.visible)} records in this area, shown as aggregate groups. Zoom in for properties.`:`${result.points.length} of ${number.format(result.visible)} records in this area shown.`}${result.truncated?' Zoom in further or use List to browse more.':''}`;
    $('map-error').hidden=true;
  }catch(error){if(error.name!=='AbortError'){$('map-error').textContent=error.message;$('map-error').hidden=false;}}
}
function renderMap() {
  $('fit-map').disabled=!pageInfo.bounds;
  if(!window.L?.markerClusterGroup){$('map-error').textContent='Map could not load. Use List to explore the records.';$('map-error').hidden=false;return;}
  const L=window.L;
  if(!propertyMap){
    propertyMap=L.map('property-map',{scrollWheelZoom:false}).setView([37.03,-121.98],10);
    const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,updateWhenIdle:true,keepBuffer:1,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
    tiles.on('tileerror',()=>{$('map-error').textContent='Background map unavailable. Use List if needed.';$('map-error').hidden=false;});tiles.addTo(propertyMap);
    propertyMarkers=L.markerClusterGroup({animate:false,showCoverageOnHover:false,maxClusterRadius:45,iconCreateFunction:cluster=>L.divIcon({html:String(cluster.getChildCount()),className:'map-cluster',iconSize:[42,42]})}).addTo(propertyMap);
    serverClusters=L.featureGroup().addTo(propertyMap);
    propertyMap.on('moveend',()=>{clearTimeout(mapTimer);mapTimer=setTimeout(refreshMap,250);});
  }
  propertyMap.invalidateSize({pan:false});
  const key=filterParams().toString();if(key!==mapSelection){mapSelection=key;fitMap();}
  $('map-legend').replaceChildren(element('strong','Assessed value relative to third-party estimate'));
  for(const [band,text] of [['low','Below 25%'],['mid','25–<50%'],['high','50–<100%'],['full','100% or more'],['missing','Comparison unavailable']]){const item=element('span');item.append(element('i',null,'legend-dot pin-'+band),document.createTextNode(text));$('map-legend').append(item);}
  clearTimeout(mapTimer);mapTimer=setTimeout(refreshMap,250);
}

function setLayout(next) {
  layout=next==='map'?'map':'list';
  $('list-view').setAttribute('aria-pressed',String(layout==='list'));$('map-view').setAttribute('aria-pressed',String(layout==='map'));
  $('list-panel').hidden=layout!=='list';$('map-panel').hidden=layout!=='map';
  if(layout==='map')renderMap();else{mapAbort?.abort();clearTimeout(mapTimer);}saveFilters();
}

function openRecord(r) {
  $('detail-title').textContent=r.address || 'Property record';
  $('detail-subtitle').textContent=[r.city,r.postal_code,r.in_santa_cruz_city===true?'Santa Cruz city limits':r.in_santa_cruz_city===false?'Outside Santa Cruz city':'Boundary unknown',label(r.home_type)].filter(Boolean).join(' · ');
  const fields=[['Third-party estimate',money(r.estimated_value)],['Assessed value for tax purposes',money(r.assessed_value)],['Value difference (estimate minus assessed)',money(difference(r))],['Percentage difference (share of estimate)',percent(differencePercent(r))]];
  $('detail-fields').replaceChildren();
  for(const [key,value] of fields) { const group=element('div'); group.append(element('dt',key),element('dd',String(value))); $('detail-fields').append(group); }
  $('detail').showModal();
}

function saveFilters() {
  const u=new URL(location.href);u.search='';
  if(layout==='map')u.searchParams.set('layout','map');
  for(const [key,value] of [['location',$('location').value],['q',$('search').value],['type',$('home-type').value],['sort',$('sort').value]])if(value && value!=='gap')u.searchParams.set(key,value);
  history.replaceState(null,'',u);
}

async function renderRows() {
  rowAbort?.abort();rowAbort=new AbortController();const signal=rowAbort.signal;
  const params=filterParams();params.set('page',page);params.set('limit',PAGE_SIZE);
  $('result-count').textContent='Loading records…';$('previous').disabled=true;$('next').disabled=true;
  let result;try{result=await api('records',params,signal);}catch(error){if(error.name!=='AbortError'){$('load-error').textContent=error.message;$('load-error').hidden=false;$('result-count').textContent='Records unavailable';}return;}
  if(signal.aborted)return;pageInfo=result;filtered=result.records;page=result.page;
  $('load-error').hidden=true;
  const pages=Math.max(1,Math.ceil(result.total/PAGE_SIZE));
  const total=data.metadata.total_records;
  $('result-count').textContent=`${number.format(result.total)} of ${number.format(total)} property records`;
  $('records').replaceChildren();
  for(const [index,r] of filtered.entries()) {
    const row=element('tr'), first=element('td'),cell=element('div',null,'property-cell'),text=element('div'),button=element('button',r.address || 'Address unavailable');
    button.addEventListener('click',()=>openRecord(r));text.append(button,element('small',[r.city,r.postal_code,r.in_santa_cruz_city===true?'Santa Cruz city limits':r.in_santa_cruz_city===false?'Outside Santa Cruz city':'Boundary unknown',label(r.home_type)].filter(Boolean).join(' · ')));cell.append(element('span',String((page-1)*PAGE_SIZE+index+1).padStart(2,'0'),'rank'),text);first.append(cell);
    row.append(first,element('td',money(r.estimated_value),'numeric'),element('td',money(r.assessed_value),'numeric'),element('td',money(difference(r)),'numeric'),element('td',percent(differencePercent(r)),'numeric'));$('records').append(row);
  }
  $('empty').hidden=filtered.length!==0;$('previous').disabled=page===1;$('next').disabled=page===pages;
  $('page-label').textContent=`Page ${page} of ${number.format(pages)} · ${PAGE_SIZE} records per page`;
  saveFilters();if(layout==='map')renderMap();
}

async function configureRecords(initialSort='assessment-gap') {
  page=1;
  $('mode-note').textContent='Compare each property’s third-party estimate with its county assessed value for tax purposes.';
  $('table-caption').textContent='Third-party estimates and assessed values for tax purposes';
  $('sort').replaceChildren();
  const options=[['assessment-gap','Largest value difference'],['assessment-percent-gap','Largest percentage difference'],['estimate','Highest third-party estimate'],['address','Address A–Z']];
  for(const [value,text] of options){const option=element('option',text);option.value=value;$('sort').append(option);}
  $('sort').value=options.some(([value])=>value===initialSort)?initialSort:'assessment-gap';await renderRows();
}

async function init() {
  try {
    data=await api('summary');const m=data.metadata;
    $('estimate-count').textContent=number.format(m.with_estimate);$('assessment-count').textContent=number.format(m.with_assessment);
    $('snapshot').textContent=date(m.snapshot_date);
    const coverage=m.coverage,total=Object.values(coverage).reduce((a,b)=>a+b,0);
    $('coverage-detail').textContent=`The original city residential inventory contains ${number.format(total)} addresses. The county inventory contains ${number.format(m.county_inventory?.inventory_parcels||0)} parcels${m.county_inventory?.inventory_complete?'':' collected so far'}. ${number.format(m.matched_properties)} were matched to property records; ${number.format(m.with_estimate)} have a usable value estimate and ${number.format(m.with_assessment)} have an assessed value. ${m.total_records-m.with_estimate} records currently lack estimates. ${number.format(m.without_estimate)} matched records without estimates remain listed with a blank estimate.`;
    $('coverage-outcomes').textContent=`Other inventory outcomes: ${number.format(coverage.ambiguous||0)} ambiguous matches, ${number.format(coverage.not_found||0)} not found, ${number.format(coverage.needs_review||0)} needing review, ${number.format(coverage.failed||0)} failed and ${number.format(coverage.skipped||0)} skipped. These records do not appear in the rankings.`;
    const types=data.home_types;
    for(const type of types){const option=element('option',label(type));option.value=type;$('home-type').append(option);}
    const params=new URLSearchParams(location.search);if(['city','outside','unknown'].includes(params.get('location')))$('location').value=params.get('location');$('search').value=params.get('q')||'';if(types.includes(params.get('type')))$('home-type').value=params.get('type');
    const legacySort=params.get('sort');
    const initialSort=legacySort==='rate'?'assessment-percent-gap':legacySort==='assessment-rate'?'assessment-percent-gap':legacySort?.startsWith('tax-')?'assessment-gap':legacySort||'assessment-gap';
    await configureRecords(initialSort);
    $('list-view').addEventListener('click',()=>setLayout('list'));$('map-view').addEventListener('click',()=>setLayout('map'));$('fit-map').addEventListener('click',fitMap);
    setLayout(params.get('layout'));
    $('search').addEventListener('input',()=>{page=1;clearTimeout(searchTimer);searchTimer=setTimeout(renderRows,300);});
    for(const id of ['home-type','sort','location'])$(id).addEventListener('change',()=>{page=1;renderRows();});
    $('previous').addEventListener('click',()=>{page--;renderRows();});$('next').addEventListener('click',()=>{page++;renderRows();});
    $('close-detail').addEventListener('click',()=>$('detail').close());
    $('detail').addEventListener('click',e=>{if(e.target===$('detail')){const r=$('detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('detail').close();}});
  } catch(error) {
    $('load-error').hidden=false;$('result-count').textContent='Data unavailable';console.error('Could not load study data',error);
  }
}
if(typeof document!=='undefined')init();
