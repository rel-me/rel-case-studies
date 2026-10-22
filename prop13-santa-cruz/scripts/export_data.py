"""Export a narrow public dataset of county assessments and third-party estimates.

Never publishes bill amounts, payment status, raw captures, provider identifiers,
owners, URLs, or browser state.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import re


PUBLIC_RECORD_FIELDS = (
    'record_id', 'address', 'city', 'postal_code', 'in_santa_cruz_city', 'municipality',
    'home_type', 'bedrooms', 'living_area', 'estimated_value', 'estimate_captured_at',
    'assessed_value', 'assessment_year', 'latitude', 'longitude', 'coordinate_source',
    'assessment_source',
)


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def address_key(address):
    aliases={'STREET':'ST','AVENUE':'AVE','ROAD':'RD','DRIVE':'DR','LANE':'LN',
             'COURT':'CT','PLACE':'PL','BOULEVARD':'BLVD','CIRCLE':'CIR','TERRACE':'TER',
             'APARTMENT':'UNIT','APT':'UNIT','SUITE':'UNIT','STE':'UNIT'}
    words=re.sub(r'[^A-Z0-9/\\-]+',' ',(address or '').upper().replace('#',' UNIT ')).split()
    return ' '.join(aliases.get(word,word) for word in words)


def coordinates(latitude, longitude, source="Third-party property website"):
    valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (latitude, longitude))
    if not valid or not 36.7 <= latitude <= 37.4 or not -122.4 <= longitude <= -121.5:
        return {'latitude': None, 'longitude': None, 'coordinate_source': None}
    return {'latitude': round(latitude, 6), 'longitude': round(longitude, 6),
            'coordinate_source': source}


def parcel_coordinates(feature):
    # A recorded parcel's bounding-box center is approximate, not a rooftop location.
    points = [point for ring in feature.get('geometry', {}).get('rings', []) for point in ring
              if isinstance(point, list) and len(point) >= 2
              and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point[:2])]
    if not points:
        return coordinates(None, None)
    longitude = (min(p[0] for p in points) + max(p[0] for p in points)) / 2
    latitude = (min(p[1] for p in points) + max(p[1] for p in points)) / 2
    return coordinates(latitude, longitude, 'Santa Cruz city parcel GIS (approximate center)')


def export(database, destination, county_inventory=None, county_taxes=None, supplement_database=None):
    public_assets = Path(__file__).resolve().parents[1] / 'public'
    if destination.resolve().is_relative_to(public_assets.resolve()):
        raise ValueError('Refusing to export a bulk dataset into public assets')
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    boundaries={};parcel_locations={}
    for row in db.execute("SELECT a.zpid,p.apn,p.feature_json FROM inventory_addresses a JOIN inventory_parcels p USING(address_key) WHERE a.zpid IS NOT NULL"):
        feature=json.loads(row['feature_json']);flag=feature.get('attributes',{}).get('SCCityLMT')
        point=parcel_coordinates(feature)
        if point['latitude'] is not None: parcel_locations.setdefault(row['zpid'],{})[(point['latitude'],point['longitude'])]=point
        boundaries.setdefault(row['zpid'],[]).append((re.sub(r'[^0-9]','',row['apn'] or ''),True if flag=='Yes' else False if flag=='No' else None))
    county={};county_scope={}
    if county_inventory:
        inventory_db=sqlite3.connect(county_inventory.resolve().as_uri()+'?mode=ro',uri=True);inventory_db.row_factory=sqlite3.Row
        county={r['apn']:dict(r) for r in inventory_db.execute('SELECT * FROM parcels')}
        county_scope={'inventory_parcels':inventory_db.execute('SELECT count(*) FROM parcels').fetchone()[0],
            'residential_parcels':inventory_db.execute('SELECT count(*) FROM parcels WHERE residential=1').fetchone()[0],
            'inventory_complete':bool(inventory_db.execute("SELECT 1 FROM inventory_state WHERE key='complete'").fetchone())}
        inventory_db.close()
    records = []
    by_apn={}
    query = '''WITH latest AS (SELECT zpid,max(capture_id) AS capture_id FROM observations GROUP BY zpid)
        SELECT h.*,o.zestimate,o.facts_json,c.captured_at FROM latest l
        JOIN homes h ON h.zpid=l.zpid JOIN observations o ON o.zpid=l.zpid AND o.capture_id=l.capture_id
        JOIN captures c ON c.id=l.capture_id ORDER BY h.zpid'''
    counts = {'matched_properties': 0, 'without_estimate': 0, 'without_assessment': 0}
    for row in db.execute(query):
        counts['matched_properties'] += 1
        estimate = row['zestimate']
        if not positive(estimate):
            counts['without_estimate'] += 1
            continue
        facts = json.loads(row['facts_json'])
        assessment = facts.get('taxAssessedValue')
        assessment = assessment if positive(assessment) else None
        assessment_year = None
        if assessment is None:
            history = [v for v in (facts.get('taxHistory') or []) if isinstance(v, dict) and positive(v.get('value')) and positive(v.get('time')) and v['time'] <= datetime.fromisoformat(row['captured_at']).timestamp()*1000]
            if history:
                latest = max(history, key=lambda v:v['time'])
                assessment = latest['value']
                assessment_year = datetime.fromtimestamp(latest['time']/1000, timezone.utc).year
        if assessment is None: counts['without_assessment'] += 1
        record = {
            'record_id': hashlib.sha256(('prop13-study:' + row['zpid']).encode()).hexdigest()[:14],
            'address': row['address'], 'city': row['city'], 'postal_code': row['postal_code'],
            'in_santa_cruz_city': None, 'municipality': None,
            'home_type': row['home_type'], 'bedrooms': row['bedrooms'], 'living_area': row['living_area'],
            'estimated_value': estimate, 'estimate_captured_at': row['captured_at'],
            'assessed_value': assessment, 'assessment_year': assessment_year,
        }
        record.update(coordinates(row['latitude'] if 'latitude' in row.keys() else None,
                                  row['longitude'] if 'longitude' in row.keys() else None))
        if record['latitude'] is None and len(parcel_locations.get(row['zpid'],{}))==1:
            record.update(next(iter(parcel_locations[row['zpid']].values())))
        matching=boundaries.get(row['zpid'],[])
        flags={(county[a]['municipality']=='City of Santa Cruz' if county[a]['municipality'] else None) if a in county else flag for a,flag in matching}
        record['in_santa_cruz_city']=flags.pop() if len(flags)==1 else None
        municipalities={county[a]['municipality'] for a,_ in matching if a in county}
        record['municipality']=next(iter(municipalities)) if len(municipalities)==1 else 'City of Santa Cruz' if record['in_santa_cruz_city'] is True else None
        record['assessment_source']='Third-party property website' if assessment is not None else None
        # Do not attribute one parcel's bill to a multi-parcel property.
        if len({apn for apn,_ in matching})==1:
            for apn,_ in matching: by_apn.setdefault(apn,[]).append(record)
        records.append(record)
    county_records_by_apn={}
    tax_snapshot=None
    if county_taxes:
        if supplement_database is None:
            candidate=Path(county_taxes).parent/'dougmar-zillow'/'zillow.sqlite3'
            if candidate.is_file(): supplement_database=candidate
        if supplement_database and Path(supplement_database).is_file():
            supplemental=sqlite3.connect(Path(supplement_database).resolve().as_uri()+'?mode=ro',uri=True)
            supplemental.row_factory=sqlite3.Row
            by_address={};duplicates=set()
            for row in supplemental.execute('SELECT * FROM latest_homes WHERE zestimate IS NOT NULL'):
                key=address_key(row['address'])
                if not key or key in duplicates: continue
                if key in by_address:
                    by_address.pop(key,None);duplicates.add(key);continue
                by_address[key]=dict(row)
            supplemental.close()
            existing_ids={record['record_id'] for record in records}
            county_by_address={}
            for apn,parcel in county.items():
                key=address_key(parcel.get('address'))
                if key: county_by_address.setdefault(key,[]).append((apn,parcel))
            for key,home in by_address.items():
                matches=county_by_address.get(key,[])
                if len(matches)!=1: continue
                apn,parcel=matches[0]
                if apn in by_apn: continue
                record_id=hashlib.sha256(('prop13-study:'+str(home['zpid'])).encode()).hexdigest()[:14]
                if record_id in existing_ids: continue
                record={
                    'record_id':record_id,'address':parcel.get('address') or home.get('address'),
                    'city':parcel.get('postal_city') or home.get('city'),
                    'postal_code':parcel.get('postal_code') or home.get('postal_code'),
                    'in_santa_cruz_city':(bool(parcel['in_santa_cruz_city']) if parcel.get('in_santa_cruz_city') is not None else None),
                    'municipality':parcel.get('municipality') if parcel.get('municipality') not in ('n/a','N/A','') else None,
                    'home_type':home.get('home_type'),'bedrooms':None,'living_area':None,
                    'estimated_value':home.get('zestimate'),'estimate_captured_at':home.get('captured_at'),
                    'assessed_value':None,'assessment_year':None}
                record.update(coordinates(home.get('latitude'),home.get('longitude')))
                record['assessment_source']='Third-party property website'
                records.append(record);existing_ids.add(record_id);county_records_by_apn[apn]=[record]
    if county_taxes:
        taxdb=sqlite3.connect(county_taxes.resolve().as_uri()+'?mode=ro',uri=True);taxdb.row_factory=sqlite3.Row
        table_names={row[0] for row in taxdb.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        prefix='county_tax_' if 'county_tax_targets' in table_names else ''
        for target in taxdb.execute(f"SELECT * FROM {prefix}targets WHERE EXISTS(SELECT 1 FROM {prefix}bills b WHERE b.apn={prefix}targets.apn)"):
            apn=target['apn']
            bill=taxdb.execute(f'SELECT b.*,c.captured_at FROM {prefix}bills b JOIN {prefix}captures c ON c.id=b.capture_id WHERE b.apn=? ORDER BY (b.fiscal_start=2025) DESC,b.fiscal_start DESC LIMIT 1',(apn,)).fetchone()
            detail=taxdb.execute(f"SELECT c.text FROM {prefix}listed_bills b JOIN {prefix}captures c ON c.id=b.detail_capture_id WHERE b.apn=? AND b.fiscal_start=? AND b.bill_type='Annual Tax Bill' AND b.detail_status='complete'",(apn,bill['fiscal_start'])).fetchall()
            values={float(m[1].replace(',','')) for row in detail if (m:=re.search(r'TOTAL VALUE \$([\d,]+(?:\.\d{2})?) (?:LESS EXEMPTION|NET TAXABLE VALUE)',row['text']))}
            assessment=next(iter(values)) if len(values)==1 else None
            matches=by_apn.get(apn,[]) or county_records_by_apn.get(apn,[])
            if len(matches)>1: continue  # Shared-parcel allocations need review.
            if not matches:
                # County-only records are visible without inventing a market estimate.
                geography=county.get(apn,{})
                record=dict.fromkeys(PUBLIC_RECORD_FIELDS)
                record.update(record_id=hashlib.sha256(('county-parcel:'+apn).encode()).hexdigest()[:14],address=target['address'],city=geography.get('postal_city'),postal_code=geography.get('postal_code'),in_santa_cruz_city=(geography['municipality']=='City of Santa Cruz' if geography['municipality'] else None) if geography else None,municipality=geography.get('municipality'))
                records.append(record);matches=[record]
            for record in matches:
                if assessment is not None: record.update(assessed_value=assessment,assessment_year=bill['fiscal_start'],assessment_source='Santa Cruz County Tax Collector')
        tax_snapshot=taxdb.execute(f'SELECT max(captured_at) FROM {prefix}captures').fetchone()[0]
        taxdb.close()
    coverage = dict(db.execute("SELECT status,count(*) FROM inventory_addresses WHERE address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential') GROUP BY status"))
    snapshot = db.execute('SELECT max(captured_at) FROM captures').fetchone()[0] or tax_snapshot
    db.close()
    metadata = {
        'title': 'Property Tax Disparities: A Santa Cruz County Study', 'geographic_scope': 'Santa Cruz County', 'county_inventory':county_scope, 'snapshot_date': snapshot[:10] if snapshot else None,
        'source_category': 'Third-party property website; official county residential address inventory',
        'coverage': coverage, **counts,
        'total_records':len(records), 'with_location':sum(r.get('latitude') is not None for r in records), 'with_estimate': sum(positive(r['estimated_value']) for r in records),
        'with_assessment': sum(r['assessed_value'] is not None for r in records),
        'with_assessment_comparison':sum(positive(r['assessed_value']) and positive(r['estimated_value']) for r in records),
        'caveat': 'This is a nonrandom observational sample. Estimates are not appraisals, assessment and estimate dates may differ, and differences cannot be attributed entirely to Proposition 13.'
    }
    destination.mkdir(parents=True, exist_ok=True)
    data = {'metadata':metadata,'records':records}
    (destination/'data.json').write_text(json.dumps(data,separators=(',',':'),allow_nan=False)+'\n')
    with (destination/'data.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=PUBLIC_RECORD_FIELDS,lineterminator='\n')
        writer.writeheader(); writer.writerows({key:json.dumps(value) if isinstance(value,(list,dict)) else value for key,value in record.items()} for record in records)
    print(json.dumps(metadata,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    parser.add_argument('--county-inventory',type=Path)
    parser.add_argument('--county-taxes',type=Path)
    parser.add_argument('--supplement-database',type=Path,
                        help='Optional separate Zillow crawl database matched to county parcels by exact address')
    args=parser.parse_args()
    export(args.database,args.output,args.county_inventory,args.county_taxes,args.supplement_database)
