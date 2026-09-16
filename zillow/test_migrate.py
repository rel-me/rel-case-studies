import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migrate
import records

URL = 'https://www.zillow.com/homedetails/123-Test-St-Santa-Cruz-CA-95060/123_zpid/'


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.path = self.output / 'zillow.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        # The actual pre-migration schema, independent of the new schema.
        self.db.executescript('''
        CREATE TABLE captures(id INTEGER PRIMARY KEY, requested_url TEXT,final_url TEXT,
          captured_at TEXT,http_status INTEGER,title TEXT,canonical_url TEXT,sha256 TEXT,
          html TEXT,text TEXT,metadata_json TEXT,structured_json TEXT);
        CREATE TABLE homes(zpid TEXT PRIMARY KEY,url TEXT,address TEXT,city TEXT,state TEXT,
          postal_code TEXT,latitude REAL,longitude REAL,home_type TEXT,bedrooms REAL,
          bathrooms REAL,living_area REAL,lot_area REAL,year_built INTEGER);
        CREATE TABLE observations(capture_id INTEGER REFERENCES captures(id),
          zpid TEXT REFERENCES homes(zpid),zestimate REAL,rent_zestimate REAL,asking_price REAL,
          home_status TEXT,zestimate_status TEXT,facts_json TEXT,PRIMARY KEY(capture_id,zpid));
        CREATE VIEW latest_homes AS SELECT * FROM homes;
        ''')
        self.facts = dict(zpid=123, address=dict(streetAddress='123 Test St', city='Santa Cruz', state='CA'),
                          bathrooms=2.5, lotAreaValue=.25, lotAreaUnits='Acres', price=0,
                          zestimate=850000, homeStatus='OTHER')
        html = '<span data-testid="price">Price Unknown</span><div data-testid="bed-bath-sqft-facts">3 beds 2 baths 1,234 sqft</div>'
        self.db.execute('INSERT INTO captures(id,html,captured_at) VALUES(1,?,?)',(html,'2026-09-15'))
        self.db.execute('INSERT INTO homes(zpid,url) VALUES(?,?)',('123',URL))
        self.db.execute('INSERT INTO observations VALUES(1,?,?,?,?,?,?,?)',
                        ('123',850000,None,0,'OTHER','available',json.dumps(self.facts)))
        self.db.commit()

    def test_backfill_backup_and_repeat(self):
        result = migrate.run(self.output)
        self.assertTrue(Path(result['backup']).exists())
        self.db.row_factory = sqlite3.Row
        row = self.db.execute('SELECT * FROM latest_homes').fetchone()
        self.assertIsNone(row['price'])
        self.assertEqual(row['zestimate'],850000)
        self.assertEqual(row['lot_area_units'],'Acres')
        self.assertEqual(row['lot_area'],.25)
        self.assertEqual((row['bathrooms'],row['rendered_bathrooms'],row['bathrooms_conflict']),(2.5,2,1))
        self.assertEqual(row['bathrooms_source'],'property_json')
        self.assertEqual(migrate.run(self.output)['status'],'already migrated')

    def test_running_crawler_refuses_migration(self):
        with (self.output / 'run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'Crawler is running'):
                migrate.run(self.output)
        self.assertIn('asking_price',{r[1] for r in self.db.execute('PRAGMA table_info(observations)')})
        self.assertEqual(list(self.output.glob('*.bak')),[])

    def test_failed_backfill_rolls_back_schema_and_data(self):
        with patch.object(records,'save_home',side_effect=RuntimeError('invalid record')):
            with self.assertRaisesRegex(RuntimeError,'invalid record'):
                migrate.run(self.output)
        self.assertIn('asking_price',{r[1] for r in self.db.execute('PRAGMA table_info(observations)')})
        self.assertEqual(self.db.execute('SELECT asking_price FROM observations').fetchone()[0],0)

    def test_new_captures_preserve_evidence(self):
        path = self.output / 'fresh.db'
        with records.database(path) as db:
            html = '<script type="application/json">'+json.dumps(self.facts)+'</script><div data-testid="bed-bath-sqft-facts">3 beds 2 baths 1,234 sqft</div>'
            data = records.parse(html,URL)
            capture = records.save(db,URL,URL,200,html,data)
            records.save_home(db,capture,URL,data['facts'])
            row = db.execute('SELECT * FROM latest_homes').fetchone()
            self.assertIsNone(row['price'])
            self.assertEqual(row['bathrooms_conflict'],1)
            self.assertEqual(row['lot_area_units'],'Acres')
        self.assertEqual(records.price(dict(price=900000,homeStatus='RECENTLY_SOLD')),900000)
        self.assertEqual(records.price(dict(price=1350,homeStatus='FOR_RENT')),1350)
        self.assertIsNone(records.price(dict(price=100,rendered_fields={'price':'Price Unknown'})))
