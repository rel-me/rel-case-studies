"""Download the city's published parcel API; all Zillow browsing stays in REL."""
import argparse
import csv
import fcntl
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

import records

SOURCE = 'https://vwgisportal2.santacruzca.gov/arcgis/rest/services/search/MapServer/1'
FIELDS = 'OBJECTID,APN,Address,UseCode,SCCityLMT,Zillow,NumberOfUnits'
# Explicit occupied residential categories from this layer, not vacant lots.
RESIDENTIAL = {'020','021','023','024','025','027','028','029','030','031','032',
               '033','034','041','042','043','044','045','046','060','061','062',
               '068','100','101','122'}
ALIASES = {'STREET':'ST','AVENUE':'AVE','ROAD':'RD','DRIVE':'DR','LANE':'LN',
           'COURT':'CT','PLACE':'PL','BOULEVARD':'BLVD','CIRCLE':'CIR','TERRACE':'TER',
           'APARTMENT':'UNIT','APT':'UNIT','SUITE':'UNIT','STE':'UNIT'}


def address_key(address):
    # Preserve unit identifiers, fractions, and number ranges.
    words = re.sub(r'[^A-Z0-9/\-]+', ' ', address.upper().replace('#', ' UNIT ')).split()
    return ' '.join(ALIASES.get(word, word) for word in words)


def lookup_url(value):
    p = urlsplit(value or '')
    if p.scheme not in {'http','https'} or p.hostname != 'www.zillow.com' or p.port or p.username:
        return None
    if not re.fullmatch(r'/homes/[^/]+_rb/', p.path):
        return None
    return urlunsplit(('https', 'www.zillow.com', p.path, '', ''))


def query(**params):
    with urlopen(SOURCE + '/query?' + urlencode(dict(f='json', **params)), timeout=60) as response:
        result = json.load(response)
    if 'error' in result:
        raise RuntimeError(f'GIS query failed: {result["error"]}')
    return result


def download(output):
    ids = query(where="SCCityLMT='Yes'", returnIdsOnly='true')['objectIds']
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('GIS returned empty or duplicate object IDs')
    features = []
    for start in range(0, len(ids), 500):
        batch = query(objectIds=','.join(map(str, ids[start:start+500])),
                      outFields=FIELDS, returnGeometry='true', outSR=4326)
        if batch.get('exceededTransferLimit'):
            raise ValueError('GIS truncated an object-ID batch')
        features.extend(batch['features'])
        print(f'GIS parcels: {len(features)}/{len(ids)}', flush=True)
    returned = [f['attributes']['OBJECTID'] for f in features]
    if len(returned) != len(set(returned)) or set(returned) != set(ids):
        raise ValueError('GIS snapshot missing or duplicating requested features')
    stamp = datetime.now(timezone.utc).isoformat()
    directory = output / 'inventory'
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / (stamp.replace(':', '-') + '.json')
    snapshot.write_text(json.dumps(dict(source=SOURCE, fetched_at=stamp, features=features)))
    return features, stamp, snapshot


def ingest(db, features, stamp, snapshot):
    with db:
        run_id = db.execute('INSERT INTO inventory_runs(source_url,fetched_at,feature_count,snapshot_path) VALUES(?,?,?,?)',
                            (SOURCE, stamp, len(features), str(snapshot))).lastrowid
        # Replace the current parcel snapshot while retaining lookup outcomes.
        db.execute('DELETE FROM inventory_parcels')
        for feature in features:
            a = feature['attributes']
            address = (a.get('Address') or '').strip()
            code = (a.get('UseCode') or '').split('-')[0]
            classification = ('outside_city' if a.get('SCCityLMT') != 'Yes' else
                              'missing_address' if not address else
                              'residential' if code in RESIDENTIAL else 'other_or_unknown_use')
            key = address_key(address) if address else None
            if classification == 'residential':
                link = lookup_url(a.get('Zillow'))
                db.execute('INSERT INTO inventory_addresses(address_key,address,lookup_url,status,reason) VALUES(?,?,?,?,?) '
                           'ON CONFLICT(address_key) DO UPDATE SET address=excluded.address,lookup_url=excluded.lookup_url',
                           (key, address, link, 'pending' if link else 'needs_review',
                            None if link else 'No supported Zillow lookup link in GIS'))
            db.execute('INSERT INTO inventory_parcels VALUES(?,?,?,?,?,?,?,?)',
                       (a['OBJECTID'], a.get('APN'), key, a.get('UseCode'), a.get('NumberOfUnits'),
                        classification, run_id, json.dumps(feature)))
    return summary(db)


def summary(db):
    return {'source': SOURCE, 'city_parcels': db.execute('SELECT count(*) FROM inventory_parcels').fetchone()[0],
            'parcel_classification': dict(db.execute('SELECT classification,count(*) FROM inventory_parcels GROUP BY classification')),
            'address_status': dict(db.execute("SELECT status,count(*) FROM inventory_addresses WHERE address_key IN "
                                             "(SELECT address_key FROM inventory_parcels WHERE classification='residential') GROUP BY status")),
            'coverage_note': 'City GIS parcel-address inventory; individual dwelling-unit completeness unverified'}


def export_addresses(db, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = db.execute("SELECT a.address,group_concat(DISTINCT p.apn) AS parcel_numbers, "
                      "group_concat(DISTINCT p.units) AS reported_unit_counts, "
                      "group_concat(DISTINCT p.use_code) AS use_codes,a.lookup_url "
                      "FROM inventory_addresses a JOIN inventory_parcels p USING(address_key) "
                      "WHERE p.classification='residential' GROUP BY a.address_key ORDER BY a.address_key")
    with path.open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([column[0] for column in rows.description])
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=records.ROOT / 'output')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        features, stamp, snapshot = download(args.output)
        db = records.database(args.output / 'zillow.sqlite3')
        try:
            result = ingest(db, features, stamp, snapshot)
            export_addresses(db, args.output / 'inventory/addresses.csv')
            print(json.dumps(result, indent=2))
        finally:
            db.close()



if __name__ == '__main__':
    main()
