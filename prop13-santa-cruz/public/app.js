const BASE = '/case-studies/prop13-santa-cruz';
const PAGE_SIZE = 25;
const number = new Intl.NumberFormat('en-US');
const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 2 });
const money = v => v == null ? 'Not available' : currency.format(v);
const share = v => v == null ? 'Not available' : percent.format(v);
const label = v => v ? v.replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : 'Type not recorded';
const date = v => v ? new Intl.DateTimeFormat('en-US', { year:'numeric', month:'short', day:'numeric', timeZone:'UTC' }).format(new Date(v)) : 'Not available';

export function selectRecords(records, mode, query = '', type = '', sort = 'gap', locationFilter = '') {
  const q = query.trim().toLowerCase();
  const rows = records.filter(r => (mode === 'tax' ? r.annual_tax > 0 : r.assessed_value > 0)
    && (!locationFilter || (locationFilter==='city'?r.in_santa_cruz_city===true:locationFilter==='outside'?r.in_santa_cruz_city===false:r.in_santa_cruz_city==null))
    && (!type || r.home_type === type)
    && (!q || [r.address,r.city,r.postal_code].join(' ').toLowerCase().includes(q)));
  const gap = mode === 'tax' ? 'benchmark_gap' : 'assessment_gap';
  const ratio = mode === 'tax' ? 'effective_tax_rate' : 'assessment_ratio';
  return rows.sort((a,b) => {
    const key=sort==='rate'?ratio:sort==='estimate'?'estimated_value':sort==='address'?null:gap;
    if(key && (a[key]==null || b[key]==null)) return a[key]==null?(b[key]==null?0:1):-1;
    let difference;
    if (sort === 'rate') difference = a[ratio] - b[ratio];
    else if (sort === 'estimate') difference = b.estimated_value - a.estimated_value;
    else if (sort === 'address') difference = (a.address || '').localeCompare(b.address || '', undefined, { numeric:true });
    else difference = b[gap] - a[gap];
    return difference || a.record_id.localeCompare(b.record_id);
  });
}

export function histogram(records) {
  const bins = Array(16).fill(0);
  for (const r of records) if (r.assessment_ratio != null && r.assessment_ratio >= 0) {
    bins[Math.min(15,Math.floor(r.assessment_ratio*10))]++;
  }
  return bins;
}

export function choosePair(records) {
  const rows=records.filter(r => r.annual_tax > 0);
  let best=null, maximum=0;
  for (let i=0;i<rows.length;i++) for (let j=i+1;j<rows.length;j++) {
    const a=rows[i],b=rows[j];
    if (!a.home_type || a.home_type!==b.home_type || a.tax_year!==b.tax_year) continue;
    if (Math.max(a.estimated_value,b.estimated_value)/Math.min(a.estimated_value,b.estimated_value)>1.15) continue;
    const ratio=Math.max(a.annual_tax,b.annual_tax)/Math.min(a.annual_tax,b.annual_tax);
    if(ratio>maximum) { maximum=ratio; best=[a,b].sort((x,y)=>x.annual_tax-y.annual_tax); }
  }
  return best;
}

const $=id=>document.getElementById(id);
function element(tag,text,className) { const el=document.createElement(tag); if(text!=null)el.textContent=text; if(className)el.className=className; return el; }
function svg(tag,attrs={},text) { const el=document.createElementNS('http://www.w3.org/2000/svg',tag); for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value); if(text!=null)el.textContent=text; return el; }
let data, mode='tax', page=1, filtered=[];

function openRecord(r) {
  $('detail-title').textContent=r.address || 'Property record';
  $('detail-subtitle').textContent=[r.city,r.postal_code,r.in_santa_cruz_city===true?'Santa Cruz city limits':r.in_santa_cruz_city===false?'Outside Santa Cruz city':'Boundary unknown',label(r.home_type)].filter(Boolean).join(' · ');
  const fields=[['Location',r.in_santa_cruz_city===true?'Santa Cruz city limits':r.in_santa_cruz_city===false?'Outside Santa Cruz city':'Boundary unknown'],['Municipality',r.municipality==='n/a'?'Unincorporated county':r.municipality || 'Not recorded'],['Third-party estimated value',money(r.estimated_value)],['Estimate captured',date(r.estimate_captured_at)],
    ['Assessed value',money(r.assessed_value)],['Assessment source',r.assessment_source || 'Not recorded'],['Assessment year',r.assessment_year || 'Not provided by source'],
    ['Annual tax billed / reported',money(r.annual_tax)],['Tax source',r.tax_source || 'Not recorded'],['Fiscal year',r.fiscal_year || 'Not recorded'],['County recorded paid',money(r.annual_paid)],['Tax history year',r.tax_year || 'Not available'],
    ['Tax history captured',date(r.tax_captured_at)],['Assessment in tax-year record',money(r.tax_assessed_value)],
    ['Annual tax / estimated value',share(r.effective_tax_rate)],['1% of estimated value',money(r.estimated_value==null?null:r.estimated_value*.01)],
    ['Annual benchmark gap',money(r.benchmark_gap)],['Estimated minus assessed value',money(r.assessment_gap)],
    ['Assessed / estimated value',share(r.assessment_ratio)],['Study record ID',r.record_id]];
  $('detail-fields').replaceChildren();
  for(const [key,value] of fields) { const group=element('div'); group.append(element('dt',key),element('dd',String(value))); $('detail-fields').append(group); }
  $('detail').showModal();
}

function renderDistribution() {
  const rows=data.records.filter(r=>r.assessment_ratio!=null), bins=histogram(rows);
  const below=rows.filter(r=>r.assessment_ratio<.5).length;
  const max=Math.max(...bins,1), w=600,h=300,left=45,right=15,top=28,bottom=40,plotW=w-left-right,plotH=h-top-bottom;
  const chart=svg('svg',{viewBox:`0 0 ${w} ${h}`,'aria-hidden':'true'});
  for(let i=0;i<=4;i++) {
    const y=top+plotH-i*plotH/4;
    chart.append(svg('line',{x1:left,y1:y,x2:w-right,y2:y,stroke:'#d5d8d0','stroke-width':1}));
    chart.append(svg('text',{x:left-9,y:y+3,'text-anchor':'end','font-size':9,fill:'#626d6a'},number.format(Math.round(max*i/4))));
  }
  bins.forEach((count,i)=>{
    const bh=count/max*plotH,x=left+i*plotW/16;
    const bar=svg('rect',{x:x+2,y:top+plotH-bh,width:plotW/16-4,height:bh,fill:'#61788b'});
    bar.append(svg('title',{},`${i===15?'150% and above':`${i*10}–${(i+1)*10}%`}: ${number.format(count)} homes`));chart.append(bar);
  });
  const reference=left+10*plotW/16;
  chart.append(svg('line',{x1:reference,y1:top-10,x2:reference,y2:top+plotH,stroke:'#202e30','stroke-dasharray':'3 3','stroke-width':1}));
  chart.append(svg('text',{x:reference+5,y:top-14,'font-size':9,fill:'#202e30'},'Assessment = estimate'));
  for(const i of [0,5,10,15])chart.append(svg('text',{x:left+i*plotW/16,y:h-16,'text-anchor':'middle','font-size':10,fill:'#626d6a'},i===15?'150%+':`${i*10}%`));
  $('distribution').replaceChildren(chart);
  $('distribution').setAttribute('aria-label',`${number.format(rows.length)} properties grouped by assessed value divided by estimated value. ${number.format(below)} (${share(below/rows.length)}) are assessed below half the estimate. The rightmost bar includes all ratios of 150% and above.`);
  const key=$('distribution').nextElementSibling;
  key.firstElementChild.textContent='Assessment / estimate · Last band includes 150% and above';
}

function renderTaxChart() {
  const rows=selectRecords(data.records,'tax').filter(r=>r.benchmark_gap!=null).slice(0,10);
  const max=Math.max(...rows.flatMap(r=>[r.annual_tax,r.estimated_value*.01]),1);
  $('tax-chart').replaceChildren();
  for(const r of rows) {
    const row=element('div',null,'tax-chart-row'),address=element('button',r.address);
    address.addEventListener('click',()=>openRecord(r));address.append(element('small',`Tax ${r.tax_year} · estimate ${date(r.estimate_captured_at)}`));
    const bars=element('div',null,'bars');
    for(const [className,value] of [['benchmark',r.estimated_value*.01],['reported',r.annual_tax]]) {
      const bar=element('span',null,className);bar.style.width=`${value/max*100}%`;bars.append(bar);
    }
    bars.setAttribute('aria-label',`Reported annual tax ${money(r.annual_tax)}; 1% reference ${money(r.estimated_value==null?null:r.estimated_value*.01)}`);bars.setAttribute('role','img');
    const gap=element('div',money(r.benchmark_gap),'tax-chart-amount');gap.append(element('small','annual difference'));
    row.append(address,bars,gap);$('tax-chart').append(row);
  }
  $('tax-chart-note').textContent=`The 10 largest benchmark gaps among ${data.metadata.with_tax_comparison ?? data.metadata.with_annual_tax} records with both amounts. Tax history year ${data.metadata.tax_years.join(', ')}; estimates captured on each record’s date. This subset is not representative of the county.`;
}

function saveFilters() {
  const u=new URL(location.href);u.search='';
  if(mode!=='tax')u.searchParams.set('view',mode);
  for(const [key,value] of [['location',$('location').value],['q',$('search').value],['type',$('home-type').value],['sort',$('sort').value]])if(value && value!=='gap')u.searchParams.set(key,value);
  history.replaceState(null,'',u);
}

function renderRows() {
  filtered=selectRecords(data.records,mode,$('search').value,$('home-type').value,$('sort').value,$('location').value);
  const pages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));page=Math.min(page,pages);
  const total=mode==='tax'?data.metadata.with_annual_tax:data.metadata.with_assessment;
  $('result-count').textContent=`${number.format(filtered.length)} of ${number.format(total)} records with available values`;
  $('records').replaceChildren();
  for(const [index,r] of filtered.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE).entries()) {
    const row=element('tr'), first=element('td'),cell=element('div',null,'property-cell'),text=element('div'),button=element('button',r.address || 'Address unavailable');
    button.addEventListener('click',()=>openRecord(r));text.append(button,element('small',[r.city,r.postal_code,r.in_santa_cruz_city===true?'Santa Cruz city limits':r.in_santa_cruz_city===false?'Outside Santa Cruz city':'Boundary unknown',label(r.home_type)].filter(Boolean).join(' · ')));cell.append(element('span',String((page-1)*PAGE_SIZE+index+1).padStart(2,'0'),'rank'),text);first.append(cell);
    const estimate=element('td',money(r.estimated_value),'numeric');
    const amount=element('td',money(mode==='tax'?r.annual_tax:r.assessed_value),'numeric');
    amount.append(element('small',mode==='tax'?r.fiscal_year?`${r.fiscal_year} · County bill`:`Tax year ${r.tax_year}`:r.assessment_year?`Assessment ${r.assessment_year}`:'Assessment year unavailable'));
    const ratio=element('td',share(mode==='tax'?r.effective_tax_rate:r.assessment_ratio),'numeric');
    const gap=element('td',money(mode==='tax'?r.benchmark_gap:r.assessment_gap),'numeric gap-cell');
    row.append(first,estimate,amount,ratio,gap);$('records').append(row);
  }
  $('empty').hidden=filtered.length!==0;$('previous').disabled=page===1;$('next').disabled=page===pages;
  $('page-label').textContent=`Page ${page} of ${number.format(pages)} · ${PAGE_SIZE} records per page`;
  $('download-view').disabled=!filtered.length;saveFilters();
}

function setMode(next,initialSort='gap') {
  mode=next;page=1;
  for(const tab of document.querySelectorAll('[role=tab]')){const selected=tab.dataset.mode===mode;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;}
  $('data-panel').setAttribute('aria-labelledby',mode==='tax'?'tax-tab':'assessment-tab');
  const tax=mode==='tax';
  $('mode-note').textContent=tax?`${data.metadata.with_annual_tax} records · Tax year ${data.metadata.tax_years.join(', ')} · Difference = 1% of estimated value − annual tax amount. Records without estimates have no calculated difference.`:`${number.format(data.metadata.with_assessment)} records · Difference = estimated value − assessed value. Records without estimates have no calculated difference.`;
  $('amount-heading').textContent=tax?'Annual tax billed / reported':'Assessed value';
  $('ratio-heading').textContent=tax?'Tax / estimate':'Assessed / estimate';
  $('gap-heading').textContent=tax?'Difference from 1% reference':'Value − assessment';
  $('table-caption').textContent=tax?'Reported annual tax compared with a 1% reference on estimated value':'Assessed values compared with third-party home-value estimates';
  $('sort').replaceChildren();
  for(const [value,text] of [['gap',tax?'Largest annual benchmark gap':'Largest assessment gap'],['rate',tax?'Lowest tax / estimate ratio':'Lowest assessed / estimate share'],['estimate','Highest estimated value'],['address','Address A–Z']]){const option=element('option',text);option.value=value;$('sort').append(option);}
  $('sort').value=['gap','rate','estimate','address'].includes(initialSort)?initialSort:'gap';renderRows();
}

function downloadView() {
  const keys=['record_id','address','in_santa_cruz_city','municipality','tax_source','annual_paid','fiscal_year','city','postal_code','home_type','estimated_value','estimate_captured_at','assessed_value','assessment_year','annual_tax','tax_year','tax_captured_at','assessment_gap','assessment_ratio','effective_tax_rate','benchmark_gap'];
  const quote=v=>{let value=v==null?'':String(v);if(typeof v==='string' && /^[=+\-@\t\r]/.test(value))value="'"+value;return '"'+value.replaceAll('"','""')+'"';};
  const csv=[keys.join(','),...filtered.map(r=>keys.map(k=>quote(r[k])).join(','))].join('\r\n');
  const u=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=element('a');a.href=u;a.download=`santa-cruz-${mode}-filtered.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}

async function init() {
  try {
    const response=await fetch(BASE+'/data.json');if(!response.ok)throw new Error(`HTTP ${response.status}`);
    data=await response.json();const m=data.metadata;
    $('estimate-count').textContent=number.format(m.with_estimate);$('assessment-count').textContent=number.format(m.with_assessment);$('tax-count').textContent=number.format(m.with_annual_tax);
    $('snapshot').textContent=date(m.snapshot_date);
    $('tax-tab-count').textContent=m.with_annual_tax;$('assessment-tab-count').textContent=number.format(m.with_assessment);
    const coverage=m.coverage,total=Object.values(coverage).reduce((a,b)=>a+b,0);
    $('coverage-detail').textContent=`The original city residential inventory contains ${number.format(total)} addresses. The county inventory contains ${number.format(m.county_inventory?.inventory_parcels||0)} parcels${m.county_inventory?.inventory_complete?'':' collected so far'}. ${number.format(m.matched_properties)} were matched to property records; ${number.format(m.with_estimate)} have a usable value estimate and ${number.format(m.with_assessment)} also have an assessment. ${m.with_annual_tax} records have annual tax amounts; ${m.total_records-m.with_estimate} records currently lack estimates. ${number.format(m.without_estimate)} matched records without estimates are excluded from the public comparisons.`;
    $('coverage-outcomes').textContent=`Other inventory outcomes: ${number.format(coverage.ambiguous||0)} ambiguous matches, ${number.format(coverage.not_found||0)} not found, ${number.format(coverage.needs_review||0)} needing review, ${number.format(coverage.failed||0)} failed and ${number.format(coverage.skipped||0)} skipped. These records do not appear in the rankings.`;
    const types=[...new Set(data.records.map(r=>r.home_type).filter(Boolean))].sort();
    for(const type of types){const option=element('option',label(type));option.value=type;$('home-type').append(option);}
    const params=new URLSearchParams(location.search);if(['city','outside','unknown'].includes(params.get('location')))$('location').value=params.get('location');$('search').value=params.get('q')||'';if(types.includes(params.get('type')))$('home-type').value=params.get('type');
    renderDistribution();renderTaxChart();setMode(params.get('view')==='assessment'?'assessment':'tax',params.get('sort')||'gap');
    for(const tab of document.querySelectorAll('[role=tab]')) {
      tab.addEventListener('click',()=>setMode(tab.dataset.mode));
      tab.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'tax':e.key==='End'?'assessment':mode==='tax'?'assessment':'tax';setMode(next);$(next+'-tab').focus();}});
    }
    $('search').addEventListener('input',()=>{page=1;renderRows();});
    for(const id of ['home-type','sort','location'])$(id).addEventListener('change',()=>{page=1;renderRows();});
    $('previous').addEventListener('click',()=>{page--;renderRows();});$('next').addEventListener('click',()=>{page++;renderRows();});
    $('download-view').addEventListener('click',downloadView);$('close-detail').addEventListener('click',()=>$('detail').close());
    $('detail').addEventListener('click',e=>{if(e.target===$('detail')){const r=$('detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('detail').close();}});
  } catch(error) {
    $('load-error').hidden=false;$('result-count').textContent='Data unavailable';console.error('Could not load study data',error);
  }
}
if(typeof document!=='undefined')init();
