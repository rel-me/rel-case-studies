from pathlib import Path
import sqlite3
import tempfile
import unittest
from crawl_areas import area_rank, address_areas
from dashboard import area_status_data

class AreaTests(unittest.TestCase):
    def test_boundaries_override_postal_area_and_live_oak_is_conservative(self):
        self.assertEqual(area_rank(dict(in_santa_cruz_city=1,postal_code='95062',postal_city='SOQUEL')),0)
        self.assertEqual(area_rank(dict(in_santa_cruz_city=0,municipality='City of Capitola',postal_code='95062')),2)
        self.assertEqual(area_rank(dict(in_santa_cruz_city=0,municipality='n/a',postal_code='95062-1234')),1)
        self.assertEqual(area_rank(dict(postal_code='95062')),10)
        self.assertEqual(area_rank(dict(postal_city='SOQUEL')),3)
        self.assertEqual(area_rank(dict(postal_city='SANTA CRUZ',in_santa_cruz_city=0)),4)

    def test_remaining_counts_deduplicate_addresses_and_exclude_terminal_work(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/'crawl.sqlite3'; county=Path(directory)/'county.sqlite3'
            with sqlite3.connect(county) as db:
                db.executescript('CREATE TABLE parcels(apn TEXT,municipality TEXT,postal_city TEXT,postal_code TEXT,in_santa_cruz_city INTEGER,residential INTEGER);')
                db.executemany('INSERT INTO parcels VALUES(?,?,?,?,?,1)',[
                    ('00000001','City of Santa Cruz','SANTA CRUZ','95062',1),
                    ('00000002','n/a','SANTA CRUZ','95062',0)])
            with sqlite3.connect(dbpath) as db:
                db.executescript('''CREATE TABLE inventory_parcels(apn TEXT,address_key TEXT,classification TEXT);
                    CREATE TABLE inventory_addresses(address_key TEXT,status TEXT,lookup_url TEXT);
                    CREATE TABLE county_tax_targets(apn TEXT,status TEXT);''')
                db.executemany('INSERT INTO inventory_parcels VALUES(?,?,\'residential\')',[
                    ('00000001','shared'),('00000002','shared'),('00000002','live'),('00000002','failed')])
                db.executemany('INSERT INTO inventory_addresses VALUES(?,?,\'url\')',[
                    ('shared','resolving'),('live','pending'),('failed','failed')])
                db.executemany('INSERT INTO county_tax_targets VALUES(?,?)',[
                    ('00000001','complete'),('00000002','history_complete'),('99999999','failed'),('88888888','pending')])
            rows=area_status_data(dbpath,dbpath,county)['rows']
            self.assertEqual((rows[0]['zillow_remaining'],rows[0]['tax_remaining']),(1,0))
            self.assertEqual((rows[1]['zillow_remaining'],rows[1]['tax_remaining']),(1,1))
            self.assertEqual(rows[-1]['tax_remaining'],1)
            self.assertEqual(sum(r['tax_remaining'] for r in rows),2)
