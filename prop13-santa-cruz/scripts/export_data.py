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
        'assessment_gap': round(estimate - assessment, 2) if positive(assessment) else None,
        'assessment_ratio': round(assessment / estimate, 6) if positive(assessment) else None,
        'effective_tax_rate': round(tax / estimate, 8) if positive(tax) else None,
        'benchmark_gap': round(estimate * .01 - tax, 2) if positive(tax) else None,
    }


def export(database, destination):
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    taxes = {}
    # Historic captures can contain tax history omitted in newer page responses.
    for row in db.execute("SELECT o.zpid,o.facts_json,c.captured_at FROM observations o JOIN captures c ON c.id=o.capture_id WHERE instr(o.facts_json,'\"taxHistory\"')>0"):
        record = tax_record(json.loads(row['facts_json']), row['captured_at'])
        previous = taxes.get(row['zpid'])
        if record and (not previous or (record['tax_year'], record['tax_captured_at']) > (previous['tax_year'], previous['tax_captured_at'])):
            taxes[row['zpid']] = record

    records = []
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
            'home_type': row['home_type'], 'bedrooms': row['bedrooms'], 'living_area': row['living_area'],
            'estimated_value': estimate, 'estimate_captured_at': row['captured_at'],
            'assessed_value': assessment, 'assessment_year': assessment_year,
            'annual_tax': tax.get('annual_tax'), 'tax_year': tax.get('tax_year'),
            'tax_captured_at': tax.get('tax_captured_at'), 'tax_assessed_value': tax.get('tax_assessed_value'),
        }
        record.update(metrics(estimate, assessment, record['annual_tax']))
        records.append(record)
    coverage = dict(db.execute("SELECT status,count(*) FROM inventory_addresses WHERE address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential') GROUP BY status"))
    snapshot = db.execute('SELECT max(captured_at) FROM captures').fetchone()[0]
    db.close()
    metadata = {
        'title': 'Property-tax inequality in Santa Cruz', 'snapshot_date': snapshot[:10],
        'source_category': 'Third-party property website; official county residential address inventory',
        'benchmark_rate': .01, 'coverage': coverage, **counts,
        'with_estimate': len(records),
        'with_assessment': sum(r['assessed_value'] is not None for r in records),
        'with_annual_tax': sum(r['annual_tax'] is not None for r in records),
        'tax_years': sorted({r['tax_year'] for r in records if r['tax_year']}),
        'caveat': 'Reported annual tax is a third-party tax-history amount, not verified proof of payment. Estimates and tax years differ. Assessment years may be unavailable. This is a nonrandom observational sample, not a causal estimate of Proposition 13 or a calculation of tax owed.'
    }
    destination.mkdir(parents=True, exist_ok=True)
    data = {'metadata':metadata,'records':records}
    (destination/'data.json').write_text(json.dumps(data,separators=(',',':'),allow_nan=False)+'\n')
    with (destination/'data.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)
    print(json.dumps(metadata,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'public')
    args=parser.parse_args()
    export(args.database,args.output)
