export const AREAS = [
  { id: 'santa-cruz-city', label: 'Santa Cruz city', selected: true },
  { id: 'live-oak', label: 'Live Oak', selected: true },
  { id: 'capitola', label: 'Capitola', selected: true },
  { id: 'soquel', label: 'Soquel', selected: true },
  { id: 'other-santa-cruz', label: 'Other Santa Cruz' },
  { id: 'aptos', label: 'Aptos / La Selva Beach' },
  { id: 'scotts-valley', label: 'Scotts Valley' },
  { id: 'san-lorenzo-valley', label: 'San Lorenzo Valley' },
  { id: 'watsonville', label: 'Watsonville / Freedom / Corralitos' },
  { id: 'north-coast', label: 'North coast / Bonny Doon' },
  { id: 'other', label: 'Other / unknown' }
];

export function propertyArea(record) {
  const city = (record.city || '').split(',')[0].trim().toLowerCase();
  const municipality = (record.municipality || '').trim().toLowerCase();
  const zip = String(record.postal_code || '').slice(0, 5);
  if (record.in_santa_cruz_city === true || municipality === 'city of santa cruz') return 'santa-cruz-city';
  if (municipality === 'city of capitola' || ['capitola', 'captiola'].includes(city) || zip === '95010') return 'capitola';
  if (city === 'soquel' || zip === '95073') return 'soquel';
  if (city === 'live oak' || (zip === '95062' && record.in_santa_cruz_city === false &&
      ['', 'n/a', 'unincorporated'].includes(municipality))) return 'live-oak';
  if (municipality === 'city of scotts valley' ||
      ['scotts valley', 'scott valley', 'scotts valleu', 'scots valley'].includes(city)) return 'scotts-valley';
  if (municipality === 'city of watsonville' ||
      ['watsonville', 'watsonmville', 'watosnville', 'freedom', 'corralitos'].includes(city)) return 'watsonville';
  if (['santa cruz', 'santa cruz ca', 'santsa cruz', 'paradise park'].includes(city)) return 'other-santa-cruz';
  if (['aptos', 'la selva bch', 'la selva beach'].includes(city)) return 'aptos';
  if (['felton', 'felton ca', 'mt hermon', 'ben lomond', 'brookdale', 'boulder creek'].includes(city)) return 'san-lorenzo-valley';
  if (['davenport', 'bonny doon'].includes(city)) return 'north-coast';
  return 'other';
}
