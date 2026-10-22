"""Build a private, approximate near-city ordering from saved official GIS fields."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from crawl_areas import AREAS, area_rank, parcel_areas, address_areas


def rank(address, parcel):
    index = area_rank(parcel)
    return index, AREAS[index]


def build(database, county_inventory, output):
    with closing(sqlite3.connect(county_inventory.resolve().as_uri()+'?mode=ro',uri=True)) as county:
        if not county.execute("SELECT 1 FROM inventory_state WHERE key='complete'").fetchone():
            raise ValueError('County GIS inventory must be complete')
    parcels = parcel_areas(county_inventory)
    db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        keys = address_areas(db, parcels)
        pending=db.execute("SELECT address_key,address FROM inventory_addresses WHERE status IN ('pending','resolving') AND lookup_url IS NOT NULL AND address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential')").fetchall()
        ordered=sorted(pending,key=lambda r:(keys.get(r['address_key'],10),0 if 'DOUGMAR' in r['address'].upper() else 1,r['address_key']))
        counts=Counter(AREAS[keys.get(r['address_key'],10)] for r in ordered)
        result={'created_at':datetime.now(timezone.utc).isoformat(),
            'method':'Santa Cruz city limits, Live Oak (unincorporated ZIP 95062 approximation), Capitola, Soquel, then other postal areas. Dougmar first within its area. Not parcel-distance measurements.',
            'county_inventory':str(county_inventory.resolve()),'area_counts':dict(counts),
            'address_keys':[row['address_key'] for row in ordered]}
        temp=output.with_suffix('.tmp');temp.write_text(json.dumps(result,indent=2)+'\n');temp.replace(output)
        return {'pending':len(ordered),'area_counts':dict(counts),'first_addresses':[r['address'] for r in ordered[:8]]}
    finally: db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--county-inventory',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.database,args.county_inventory,args.output),indent=2))
