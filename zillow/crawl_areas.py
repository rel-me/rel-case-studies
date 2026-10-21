"""Shared private crawl geography. Postal groupings are approximate, not distances."""
import re
from contextlib import closing
import sqlite3

AREAS = (
    'Santa Cruz city limits', 'Live Oak area (approx.)', 'Capitola', 'Soquel area',
    'Other Santa Cruz postal area', 'Aptos / La Selva Beach', 'Scotts Valley area',
    'San Lorenzo Valley', 'Watsonville / Freedom / Corralitos', 'North coast / Bonny Doon',
    'Other / unknown',
)
NOTE = ('Santa Cruz uses official city limits; Capitola uses city limits with a postal-name fallback. Live Oak is approximated by '
        'unincorporated ZIP 95062; other areas use postal names. Counts are queued Zillow '
        'addresses and tax parcels, including work in progress; saved terminal failures are excluded.')


def apn_key(value):
    return re.sub(r'\D', '', value or '')


def area_rank(parcel):
    parcel = parcel or {}
    city = (parcel.get('postal_city') or '').strip().upper().split(',')[0]
    municipality = (parcel.get('municipality') or '').strip().upper()
    if parcel.get('in_santa_cruz_city') == 1 or municipality == 'CITY OF SANTA CRUZ': return 0
    if municipality == 'CITY OF CAPITOLA': return 2
    if municipality in ('', 'N/A') and parcel.get('in_santa_cruz_city') == 0 and (parcel.get('postal_code') or '').startswith('95062'): return 1
    if city in ('CAPITOLA', 'CAPTIOLA'): return 2
    if city == 'SOQUEL': return 3
    if city in ('SANTA CRUZ', 'SANTA CRUZ CA', 'SANTSA CRUZ', 'PARADISE PARK'): return 4
    if city in ('APTOS', 'LA SELVA BCH', 'LA SELVA BEACH'): return 5
    if municipality == 'CITY OF SCOTTS VALLEY' or city in ('SCOTTS VALLEY','SCOTT VALLEY','SCOTTS VALLEU','SCOTS VALLEY'): return 6
    if city in ('FELTON','FELTON CA','MT HERMON','BEN LOMOND','BROOKDALE','BOULDER CREEK'): return 7
    if municipality == 'CITY OF WATSONVILLE' or city in ('WATSONVILLE','WATSONMVILLE','WATOSNVILLE','FREEDOM','CORRALITOS'): return 8
    if city in ('DAVENPORT','BONNY DOON'): return 9
    return 10


def parcel_areas(path):
    if not path: return {}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {apn_key(r['apn']): area_rank(dict(r)) for r in db.execute(
            'SELECT apn,municipality,postal_city,postal_code,in_santa_cruz_city FROM parcels WHERE residential=1')}


def address_areas(db, parcels):
    """One queue item per address; assign multi-parcel addresses to their first area."""
    result = {}
    for key, apn in db.execute("SELECT address_key,apn FROM inventory_parcels WHERE classification='residential'"):
        rank = parcels.get(apn_key(apn), 10)
        result[key] = min(result.get(key, 10), rank)
    return result
