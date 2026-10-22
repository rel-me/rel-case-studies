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

export function selectRecords(records, mode = 'all', query = '', type = '', sort = 'gap', locationFilter = '') {
  if (!['all', 'assessment'].includes(mode)) throw new Error('Invalid comparison view');
  const q = query.trim().toLowerCase();
  const rows = records.filter(r => (r.assessed_value > 0 || r.estimated_value > 0)
    && (!locationFilter || (locationFilter==='city'?r.in_santa_cruz_city===true:locationFilter==='outside'?r.in_santa_cruz_city===false:r.in_santa_cruz_city==null))
    && (!type || r.home_type === type)
    && (!q || [r.address,r.city,r.postal_code].join(' ').toLowerCase().includes(q)));
  const order = sort.endsWith('-rate') ? 'rate'
    : sort.endsWith('-percent-gap') ? 'percent-gap'
    : sort.endsWith('-gap') ? 'gap' : sort;
  return rows.sort((a,b) => {
    const av = order === 'rate' ? assessedRatio(a)
      : order === 'gap' ? assessedDifference(a)
      : order === 'percent-gap' ? assessedDifferencePercent(a) : null;
    const bv = order === 'rate' ? assessedRatio(b)
      : order === 'gap' ? assessedDifference(b)
      : order === 'percent-gap' ? assessedDifferencePercent(b) : null;
    if(order==='rate'||order==='gap'||order==='percent-gap') {
      if(av==null||bv==null)return av==null?(bv==null?0:1):-1;
      return (order==='rate'?av-bv:bv-av)||a.record_id.localeCompare(b.record_id);
    }
    if(order==='estimate')return (b.estimated_value||0)-(a.estimated_value||0)||a.record_id.localeCompare(b.record_id);
    if(order==='address')return (a.address||'').localeCompare(b.address||'',undefined,{numeric:true})||a.record_id.localeCompare(b.record_id);
    throw new Error('Invalid sort order');
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
