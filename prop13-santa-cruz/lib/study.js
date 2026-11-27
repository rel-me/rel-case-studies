export function assessedRatio(record) {
  return Number.isFinite(record.assessed_value) && record.assessed_value > 0
    && Number.isFinite(record.estimated_value) && record.estimated_value > 0
    ? record.assessed_value / record.estimated_value : null;
}

export function assessedDifference(record) {
  return Number.isFinite(record.assessed_value) && record.assessed_value > 0
    && Number.isFinite(record.estimated_value) && record.estimated_value > 0
    ? record.estimated_value - record.assessed_value : null;
}

export function assessedDifferencePercent(record) {
  const difference = assessedDifference(record);
  return difference == null ? null : difference / record.estimated_value;
}

export function selectRecords(records, mode = 'all', query = '', type = '', sort = 'gap', locationFilter = '', direction = '') {
  if (!['all', 'assessment'].includes(mode)) throw new Error('Invalid comparison view');
  const q = query.trim().toLowerCase();
  const rows = records.filter(r => (r.assessed_value > 0 || r.estimated_value > 0)
    && (!locationFilter || (locationFilter==='city'?r.in_santa_cruz_city===true:locationFilter==='outside'?r.in_santa_cruz_city===false:r.in_santa_cruz_city==null))
    && (!type || r.home_type === type)
    && (!q || [r.address,r.city,r.postal_code].join(' ').toLowerCase().includes(q)));
  const order = sort.endsWith('-rate') ? 'rate'
    : sort.endsWith('-percent-gap') ? 'percent-gap'
    : sort.endsWith('-gap') ? 'gap' : sort;
  const value = new Map([
    ['rate', assessedRatio],
    ['gap', assessedDifference],
    ['percent-gap', assessedDifferencePercent],
    ['estimate', r => r.estimated_value > 0 ? r.estimated_value : null],
    ['assessment', r => r.assessed_value > 0 ? r.assessed_value : null],
    ['address', r => r.address || null]
  ]).get(order);
  if(!value)throw new Error('Invalid sort order');
  const ascending = direction ? direction === 'asc' : order === 'rate' || order === 'address';
  return rows.sort((a,b) => {
    const av=value(a),bv=value(b);
    if(av==null&&bv!=null)return 1;
    if(bv==null&&av!=null)return -1;
    const comparison=av==null?0:order==='address'?av.localeCompare(bv,undefined,{numeric:true}):av-bv;
    return (ascending?comparison:-comparison)||a.record_id.localeCompare(b.record_id);
  });
}

export function hasLocation(r) {
  return Number.isFinite(r.latitude) && Number.isFinite(r.longitude) && r.latitude>=36.7 && r.latitude<=37.4 && r.longitude>=-122.4 && r.longitude<=-121.5;
}

export function mapBand(record) {
  const ratio=assessedRatio(record);
  if(ratio==null)return 'missing';
  return ratio<.25?'low':ratio<.5?'mid':ratio<1?'high':'full';
}
