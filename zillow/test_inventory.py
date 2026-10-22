import json
import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import inventory
import records
import zillow

HOME = 'https://www.zillow.com/homedetails/530-High-St-Santa-Cruz-CA-95060/123_zpid/'
LOOKUP = 'https://www.zillow.com/homes/530-HIGH-ST-santa-cruz,-ca_rb/'


def feature(oid=1, address='530 High St', code='020-SINGLE RESIDENCE', city='Yes'):
    return {'attributes': {'OBJECTID': oid, 'APN': str(oid), 'Address': address,
                          'UseCode': code, 'SCCityLMT': city, 'Zillow': LOOKUP,
                          'NumberOfUnits': '1'}, 'geometry': {'rings': []}}


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = records.database(Path(self.tmp.name) / 'test.db')
        self.addCleanup(self.db.close)
        inventory.ingest(self.db, [feature()], '2026-09-15', 'snapshot.json')

    def test_county_import_adds_targets_idempotently_and_preserves_prior_results(self):
        source=Path(self.tmp.name)/'county.sqlite3'
        c=sqlite3.connect(source)
        c.executescript("""CREATE TABLE inventory_state(key TEXT,value TEXT);
          INSERT INTO inventory_state VALUES('complete','true');
          CREATE TABLE parcels(object_id INTEGER,apn TEXT,address TEXT,postal_city TEXT,postal_code TEXT,use_code TEXT,in_santa_cruz_city INTEGER,residential INTEGER,source_url TEXT);
          INSERT INTO parcels VALUES(7,'02625611','1540 DOUGMAR DR','SANTA CRUZ','95062','020',0,1,'https://county.example/parcels');
          INSERT INTO parcels VALUES(8,'1','530 High St','SANTA CRUZ','95060','020',1,1,'https://county.example/parcels');""");c.close()
        with self.db:self.db.execute("UPDATE inventory_addresses SET status='failed',reason='kept'")
        self.assertEqual(inventory.ingest_county(self.db,source)['added'],1)
        self.assertEqual(inventory.ingest_county(self.db,source)['added'],0)
        old=self.db.execute("SELECT status,reason FROM inventory_addresses WHERE address_key='530 HIGH ST'").fetchone()
        self.assertEqual(tuple(old),('failed','kept'))
        new=self.db.execute("SELECT * FROM inventory_addresses WHERE address_key='county:02625611'").fetchone()
        self.assertEqual(new['status'],'pending')
        self.assertIn('95062_rb/',new['lookup_url'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM inventory_parcels').fetchone()[0],2)

    def test_saved_priority_orders_pending_addresses_and_excludes_failed(self):
        inventory.ingest(self.db,[feature(1,'530 High St'),feature(2,'1 Far St'),feature(3,'2 Failed St')],'2026-10-18','priority-snapshot')
        with self.db:self.db.execute("UPDATE inventory_addresses SET status='failed' WHERE address_key='2 FAILED ST'")
        priority=Path(self.tmp.name)/'priority.json'
        priority.write_text(json.dumps({'address_keys':['2 FAILED ST','1 FAR ST','530 HIGH ST']}))
        seeds=zillow.inventory_seeds(self.db,priority)
        self.assertEqual([r.user_data['inventory_key'] for r in seeds],['1 FAR ST','530 HIGH ST'])

    def test_parcel_coverage_dedup_units_and_scope(self):
        features = [feature(), feature(2), feature(3, '530 High St #2'),
                    feature(4, code='150-GROCERY STORE'), feature(5, city='No')]
        result = inventory.ingest(self.db, features, '2026-09-16', 'next.json')
        self.assertEqual(result['address_status'], {'pending': 2})
        self.assertEqual(result['parcel_classification']['residential'], 3)
        self.assertEqual(inventory.address_key('530 High Street Apt 2'), '530 HIGH ST UNIT 2')
        self.assertNotEqual(inventory.address_key('530 High St'), inventory.address_key('530 High St #2'))
        self.assertEqual(len(zillow.inventory_seeds(self.db)), 2)

    def test_incomplete_download_rejected(self):
        with patch.object(inventory, 'query', side_effect=[{'objectIds': [1, 2]}, {'features': [feature()]}]):
            with self.assertRaisesRegex(ValueError, 'missing'):
                inventory.download(Path(self.tmp.name))

    async def test_county_match_saves_outside_city_only_with_verified_street_and_zip(self):
        self.test_county_import_adds_targets_idempotently_and_preserves_prior_results()
        url='https://www.zillow.com/homedetails/1540-Dougmar-Dr/123_zpid/'
        data={'facts':{'zpid':'123','address':{'streetAddress':'1540 DOUGMAR DRIVE','city':'Capitola','state':'CA','zipcode':'95062'}}}
        context=SimpleNamespace(request=SimpleNamespace(user_data={'inventory_key':'county:02625611'}),add_requests=AsyncMock())
        parsed=records.parse('<title>Home</title>',url)
        with self.db: capture=records.save(self.db,url,url,200,'<title>Home</title>',parsed)
        self.assertEqual(await zillow.match_inventory(context,self.db,capture,data,url),'123')
        self.assertEqual(self.db.execute("SELECT city FROM homes WHERE zpid='123'").fetchone()[0],'Capitola')
        data['facts']['address']['zipcode']='95060'
        self.assertIsNone(await zillow.match_inventory(context,self.db,capture,data,url))
        with self.assertRaisesRegex(ValueError,'verified Santa Cruz County'):
            records.save_home(self.db,capture,url,data['facts'],county_inventory_key='county:02625611')

    async def test_matching_and_unit_mismatch(self):
        context = SimpleNamespace(request=SimpleNamespace(user_data={'inventory_key': '530 HIGH ST'}),
                                  add_requests=AsyncMock())
        facts = {'zpid': '123', 'address': {'streetAddress': '530 High St #2', 'city': 'Santa Cruz', 'state': 'CA'}}
        data = {'facts': facts}
        self.assertIsNone(await zillow.match_inventory(context, self.db, 1, data, HOME))
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'ambiguous')
        facts['address']['streetAddress'] = '530 High Street'
        parsed = records.parse('<title>Home</title>', HOME)
        with self.db:
            capture = records.save(self.db, LOOKUP, HOME, 200, '<title>Home</title>', parsed)
        self.assertEqual(await zillow.match_inventory(context, self.db, capture, data, HOME), '123')
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'matched')
        inventory.ingest(self.db, [feature()], '2026-09-16', 'next.json')
        self.assertEqual(zillow.inventory_seeds(self.db), [])

    async def test_search_enqueues_only_exact_candidate(self):
        context = SimpleNamespace(request=SimpleNamespace(user_data={'inventory_key': '530 HIGH ST'}),
                                  add_requests=AsyncMock())
        card = {'detailUrl': HOME, 'hdpData': {'homeInfo': {'streetAddress': '530 High St',
                'city': 'Santa Cruz', 'state': 'CA', 'zpid': 123}}}
        await zillow.match_inventory(context, self.db, 1, {'structured': [card]}, LOOKUP)
        context.add_requests.assert_awaited_once()
        self.assertEqual(context.add_requests.call_args.args[0][0].url, HOME)
        self.assertEqual(self.db.execute('SELECT status FROM inventory_addresses').fetchone()[0], 'resolving')


if __name__ == '__main__':
    unittest.main()
