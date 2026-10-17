"""Export a narrow, reproducible public dataset from the private capture database.

Never publishes raw captures, provider identifiers, owners, URLs, or browser state.
Annual tax is accepted only from an explicit historical tax amount. Assessment
and a generic property-tax-rate field are never used to invent payments.
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


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def tax_record(facts, captured_at):
    cutoff = datetime.fromisoformat(captured_at).timestamp() * 1000
    entries = [v for v in (facts.get('taxHistory') or []) if isinstance(v, dict)
               and positive(v.get('taxPaid')) and positive(v.get('time')) and v['time'] <= cutoff]
    if not entries:
        return None
    entry = max(entries, key=lambda v: v['time'])
    return {'annual_tax': round(entry['taxPaid'], 2),
            'tax_year': datetime.fromtimestamp(entry['time'] / 1000, timezone.utc).year,
            'tax_assessed_value': entry.get('value') if positive(entry.get('value')) else None,
            'tax_captured_at': captured_at}


def metrics(estimate, assessment, tax):
    return {
        'assessment_gap': round(estimate - assessment, 2) if positive(estimate) and positive(assessment) else None,
        'assessment_ratio': round(assessment / estimate, 6) if positive(estimate) and positive(assessment) else None,
        'effective_tax_rate': round(tax / estimate, 8) if positive(estimate) and positive(tax) else None,
        'benchmark_gap': round(estimate * .01 - tax, 2) if positive(estimate) and positive(tax) else None,
    }


def export(database, destination, county_inventory=None, county_taxes=None):
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    taxes = {}
    # Historic captures can contain tax history omitted in newer page responses.
    for row in db.execute("SELECT o.zpid,o.facts_json,c.captured_at FROM observations o JOIN captures c ON c.id=o.capture_id WHERE instr(o.facts_json,'\"taxHistory\"')>0"):
        record = tax_record(json.loads(row['facts_json']), row['captured_at'])
        previous = taxes.get(row['zpid'])
        if record and (not previous or (record['tax_year'], record['tax_captured_at']) > (previous['tax_year'], previous['tax_captured_at'])):
            taxes[row['zpid']] = record

    boundaries={}
    for row in db.execute("SELECT a.zpid,p.apn,p.feature_json FROM inventory_addresses a JOIN inventory_parcels p USING(address_key) WHERE a.zpid IS NOT NULL"):
        flag=json.loads(row['feature_json']).get('attributes',{}).get('SCCityLMT')
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
        tax = taxes.get(row['zpid']) or {}
        record = {
            'record_id': hashlib.sha256(('prop13-study:' + row['zpid']).encode()).hexdigest()[:14],
            'address': row['address'], 'city': row['city'], 'postal_code': row['postal_code'],
            'in_santa_cruz_city': None, 'municipality': None,
            'home_type': row['home_type'], 'bedrooms': row['bedrooms'], 'living_area': row['living_area'],
            'estimated_value': estimate, 'estimate_captured_at': row['captured_at'],
            'assessed_value': assessment, 'assessment_year': assessment_year,
            'annual_tax': tax.get('annual_tax'), 'tax_year': tax.get('tax_year'),
            'tax_captured_at': tax.get('tax_captured_at'), 'tax_assessed_value': tax.get('tax_assessed_value'),
        }
        record.update(metrics(estimate, assessment, record['annual_tax']))
        matching=boundaries.get(row['zpid'],[])
        flags={(county[a]['municipality']=='City of Santa Cruz' if county[a]['municipality'] else None) if a in county else flag for a,flag in matching}
        record['in_santa_cruz_city']=flags.pop() if len(flags)==1 else None
        municipalities={county[a]['municipality'] for a,_ in matching if a in county}
        record['municipality']=next(iter(municipalities)) if len(municipalities)==1 else 'City of Santa Cruz' if record['in_santa_cruz_city'] is True else None
        record['assessment_source']='Third-party property website' if assessment is not None else None
        record['tax_source']='Third-party property website' if record['annual_tax'] else None
        record['annual_paid']=None;record['fiscal_year']=None;record['county_captured_at']=None
        record['county_installments']=None;record['county_source_url']=None
        # Do not attribute one parcel's bill to a multi-parcel property.
        if len({apn for apn,_ in matching})==1:
            for apn,_ in matching: by_apn.setdefault(apn,[]).append(record)
        records.append(record)
    if county_taxes:
        taxdb=sqlite3.connect(county_taxes.resolve().as_uri()+'?mode=ro',uri=True);taxdb.row_factory=sqlite3.Row
        for target in taxdb.execute("SELECT * FROM targets WHERE EXISTS(SELECT 1 FROM bills b WHERE b.apn=targets.apn)"):
            apn=target['apn']
            bill=taxdb.execute('SELECT b.*,c.captured_at FROM bills b JOIN captures c ON c.id=b.capture_id WHERE b.apn=? ORDER BY (b.fiscal_start=2025) DESC,b.fiscal_start DESC LIMIT 1',(apn,)).fetchone()
            detail=taxdb.execute("SELECT c.text FROM listed_bills b JOIN captures c ON c.id=b.detail_capture_id WHERE b.apn=? AND b.fiscal_start=? AND b.bill_type='Annual Tax Bill' AND b.detail_status='complete'",(apn,bill['fiscal_start'])).fetchall()
            values={float(m[1].replace(',','')) for row in detail if (m:=re.search(r'TOTAL VALUE \$([\d,]+(?:\.\d{2})?) (?:LESS EXEMPTION|NET TAXABLE VALUE)',row['text']))}
            assessment=next(iter(values)) if len(values)==1 else None
            matches=by_apn.get(apn,[])
            if len(matches)>1: continue  # Shared-parcel allocations need review.
            if not matches:
                # County-only records are visible without inventing a market estimate.
                geography=county.get(apn,{})
                record={key:None for key in records[0]}
                record.update(record_id=hashlib.sha256(('county-parcel:'+apn).encode()).hexdigest()[:14],address=target['address'],city=geography.get('postal_city'),postal_code=geography.get('postal_code'),in_santa_cruz_city=(geography['municipality']=='City of Santa Cruz' if geography['municipality'] else None) if geography else None,municipality=geography.get('municipality'))
                records.append(record);matches=[record]
            for record in matches:
                record.update(annual_tax=float(bill['total_billed']),annual_paid=float(bill['amount_paid']),tax_year=bill['fiscal_start'],fiscal_year=f"{bill['fiscal_start']}–{str(bill['fiscal_end'])[-2:]}",tax_captured_at=bill['captured_at'],county_captured_at=bill['captured_at'],county_installments=json.loads(bill['installments_json']),county_source_url='https://ttc.co.santa-cruz.ca.us/TaxBills/',tax_source='Santa Cruz County Tax Collector',tax_assessed_value=assessment)
                if assessment is not None: record.update(assessed_value=assessment,assessment_year=bill['fiscal_start'],assessment_source='Santa Cruz County Tax Collector')
                record.update(metrics(record['estimated_value'],record['assessed_value'],record['annual_tax']))
        taxdb.close()
    coverage = dict(db.execute("SELECT status,count(*) FROM inventory_addresses WHERE address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential') GROUP BY status"))
    snapshot = db.execute('SELECT max(captured_at) FROM captures').fetchone()[0]
    db.close()
    metadata = {
        'title': 'Santa Cruz County property taxes', 'geographic_scope': 'Santa Cruz County', 'county_inventory':county_scope, 'snapshot_date': snapshot[:10],
        'source_category': 'Third-party property website; official county residential address inventory',
        'benchmark_rate': .01, 'coverage': coverage, **counts,
        'total_records':len(records), 'with_estimate': sum(positive(r['estimated_value']) for r in records),
        'with_assessment': sum(r['assessed_value'] is not None for r in records),
        'with_annual_tax': sum(r['annual_tax'] is not None for r in records),
        'with_assessment_comparison':sum(r['assessment_ratio'] is not None for r in records),
        'with_tax_comparison':sum(r['effective_tax_rate'] is not None for r in records),
        'tax_years': sorted({r['tax_year'] for r in records if r['tax_year']}),
        'caveat': 'Tax sources are labeled per record. County amounts billed and recorded paid are separate; third-party tax-history amounts do not establish payment. Estimates and tax years differ. Assessment years may be unavailable. This is a nonrandom observational sample, not a causal estimate of Proposition 13 or a calculation of tax owed.'
    }
    destination.mkdir(parents=True, exist_ok=True)
    data = {'metadata':metadata,'records':records}
    (destination/'data.json').write_text(json.dumps(data,separators=(',',':'),allow_nan=False)+'\n')
    with (destination/'data.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]),lineterminator='\n')
        writer.writeheader(); writer.writerows({key:json.dumps(value) if isinstance(value,(list,dict)) else value for key,value in record.items()} for record in records)
    print(json.dumps(metadata,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'public')
    parser.add_argument('--county-inventory',type=Path)
    parser.add_argument('--county-taxes',type=Path)
    args=parser.parse_args()
    export(args.database,args.output,args.county_inventory,args.county_taxes)
