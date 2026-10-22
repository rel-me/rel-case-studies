// Synthetic records only: tests never read local capture exports.
const records=Array.from({length:80},(_,i)=>({
  record_id:(i+1).toString(16).padStart(14,'0'),
  address:`${i+1} ${i<40?'DOUGMAR':'EXAMPLE'} AVE`,
  city:'Santa Cruz',postal_code:'95062',home_type:'SINGLE_FAMILY',
  in_santa_cruz_city:i>=40,
  estimated_value:1000000+i*1000,assessed_value:200000+i*500,
  latitude:36.98+i*.0001,longitude:-122.03+i*.0001
}));
export default {metadata:{with_estimate:80,with_assessment:80,with_location:80},records};
