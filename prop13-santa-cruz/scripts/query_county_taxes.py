#!/usr/bin/env python3
"""Read private county records. Unpaid is the captured status, not a delinquency finding."""
import argparse
import json
from pathlib import Path
import re
import sqlite3


def lookup(db, apn=None, unpaid=False, search=None, limit=50):
    prefixed=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='county_tax_listed_bills'").fetchone()
    prefix='county_tax_' if prefixed else ''
    clauses=[];params=[]
    if apn:
        clauses.append('b.apn=?');params.append(re.sub(r'[^0-9]','',apn))
    if unpaid: clauses.append('b.unpaid_installments>0')
    if search:
        clauses.append(f'b.apn IN (SELECT apn FROM {prefix}page_search WHERE {prefix}page_search MATCH ?)');params.append(search)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    rows=db.execute(f'''SELECT b.*,t.address,c.captured_at AS detail_captured_at,c.text AS detail_text,
        h.captured_at AS history_captured_at FROM {prefix}listed_bills b LEFT JOIN {prefix}targets t USING(apn)
        LEFT JOIN {prefix}captures c ON c.id=b.detail_capture_id LEFT JOIN {prefix}captures h ON h.id=b.history_capture_id'''
        +where+' ORDER BY b.apn,b.fiscal_start DESC,b.title LIMIT ?',params+[limit])
    result=[]
    for row in rows:
        item=dict(row)
        item['rows']=json.loads(item.pop('rows_json'))
        item['installments']=json.loads(item.pop('installments_json'))
        result.append(item)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--apn')
    parser.add_argument('--unpaid',action='store_true',help='Bills with installments explicitly marked Not Paid; may not yet be due')
    parser.add_argument('--search',help='SQLite full-text query across saved page text')
    parser.add_argument('--limit',type=int,default=50)
    parser.add_argument('--capture-id',type=int)
    parser.add_argument('--html-output',type=Path)
    args=parser.parse_args()
    if args.limit<1: parser.error('limit must be positive')
    if bool(args.capture_id)!=bool(args.html_output): parser.error('capture-id and html-output must be used together')
    db=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        if args.capture_id:
            prefix='county_tax_' if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='county_tax_captures'").fetchone() else ''
            row=db.execute(f'SELECT html FROM {prefix}captures WHERE id=?',(args.capture_id,)).fetchone()
            if not row: parser.error('Capture not found')
            with args.html_output.open('x') as output: output.write(row['html'])
            print(json.dumps({'capture_id':args.capture_id,'saved_to':str(args.html_output)}))
        else:
            print(json.dumps({'note':'Payment status is as captured; unpaid does not necessarily mean overdue.',
                'bills':lookup(db,args.apn,args.unpaid,args.search,args.limit)},indent=2))
    finally: db.close()


if __name__=='__main__': main()
