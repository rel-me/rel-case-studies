from pathlib import Path
import fcntl
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

import dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'crawl.sqlite3'
        db = sqlite3.connect(self.path)
        db.executescript(Path(__file__).with_name('schema.sql').read_text())
        db.execute("INSERT INTO inventory_runs(id,source_url,fetched_at,feature_count,snapshot_path) "
                   "VALUES(1,'source','now',3,'snapshot')")
        for capture, zpid, estimate, price in [(1, '1', 800000, 750000), (2, '2', None, 3000), (3, '1', 900000, 950000)]:
            db.execute('INSERT INTO captures(id,requested_url,final_url,captured_at,sha256,html,text,metadata_json,structured_json) '
                       'VALUES(?,?,?,?,?,\'\',\'\',\'[]\',\'[]\')',
                       (capture, 'https://www.zillow.com/', 'https://www.zillow.com/', f'2026-10-06T00:00:0{capture}+00:00', 'hash'))
            db.execute('INSERT OR IGNORE INTO homes(zpid,url,address,postal_code) VALUES(?,?,?,?)',
                       (zpid, 'https://www.zillow.com/', 'Walnut Ave' if zpid == '1' else 'Ocean St', '95060'))
            db.execute('INSERT INTO observations(capture_id,zpid,zestimate,price,home_status,zestimate_status,facts_json) '
                       'VALUES(?,?,?,?,?,\'available\',\'{}\')',
                       (capture, zpid, estimate, price, 'FOR_SALE' if zpid == '1' else 'FOR_RENT'))
        for key, status, kind in [('1', 'matched', 'residential'), ('2', 'pending', 'residential'), ('3', 'pending', 'other')]:
            db.execute('INSERT INTO inventory_addresses(address_key,address,status,zpid) VALUES(?,?,?,?)',
                       (key,key,status,key if kind == 'residential' else None))
            apn = 'A' + key if kind == 'residential' else None
            db.execute('INSERT INTO inventory_parcels(object_id,apn,address_key,classification,run_id,feature_json) VALUES(?,?,?, ?,1,\'{}\')',
                       (int(key), apn, key, kind))
        db.commit()
        db.close()

    def test_latest_values_and_residential_coverage_are_consistent(self):
        data = dashboard.dashboard_data(self.path, {})
        self.assertEqual(data['summary']['homes'], 2)
        self.assertEqual(data['summary']['with_zestimate'], 1)
        self.assertEqual(data['summary']['captures'], 3)
        self.assertEqual(data['summary']['inventory_total'], 2)
        self.assertEqual(data['summary']['reviewed'], 1)
        self.assertEqual(data['homes'][0]['price'], 950000)
        self.assertIsNone(data['homes'][0]['assessed_value'])
        self.assertIsNone(data['homes'][1]['zestimate'])

    def test_assessed_values_come_from_unambiguous_annual_county_details(self):
        tax_path = Path(self.tmp.name) / 'county-tax.sqlite3'
        tax = sqlite3.connect(tax_path)
        tax.executescript('''CREATE TABLE listed_bills(apn TEXT,bill_type TEXT,fiscal_start INTEGER,detail_status TEXT,detail_capture_id INTEGER);
            CREATE TABLE captures(id INTEGER PRIMARY KEY,text TEXT);''')
        city = sqlite3.connect(self.path)
        city.execute("UPDATE inventory_addresses SET status='matched' WHERE address_key='2'")
        city.execute("INSERT INTO inventory_parcels(object_id,apn,address_key,classification,run_id,feature_json) VALUES(4,'A3','2','residential',1,'{}')")
        city.commit(); city.close()
        tax.executemany('INSERT INTO captures VALUES(?,?)', [
            (1,'TOTAL VALUE $900,000 LESS EXEMPTION $7,000'),
            (2,'TOTAL VALUE $1,000,000 LESS EXEMPTION $7,000'),
            (3,'TOTAL VALUE $100,000 LESS EXEMPTION $7,000'),
            (4,'TOTAL VALUE $200,000 LESS EXEMPTION $7,000'),
        ])
        tax.executemany('INSERT INTO listed_bills VALUES(?,?,?,?,?)', [
            ('A1','Annual Tax Bill',2025,'complete',1),
            ('A1','Annual Tax Bill',2026,'complete',2),
            ('A2','Annual Tax Bill',2025,'complete',3),
            ('A3','Annual Tax Bill',2025,'complete',4),
        ])
        tax.commit(); tax.close()
        data = dashboard.dashboard_data(self.path, {}, tax_database_path=tax_path)
        values = {home['zpid']: (home['assessed_value'], home['assessment_year']) for home in data['homes']}
        self.assertEqual(values['1'], (900000, 2025))
        self.assertEqual(values['2'], (None, None))
        detail = dashboard.property_data(self.path, '1', tax_path)
        self.assertEqual(detail['property']['assessed_value'], 900000)

    def test_assessments_and_status_work_when_tax_tables_share_the_zillow_database(self):
        db=sqlite3.connect(self.path)
        db.executescript('''CREATE TABLE county_tax_targets(apn TEXT PRIMARY KEY,zpid TEXT NOT NULL,address TEXT,status TEXT NOT NULL);
            CREATE TABLE county_tax_captures(id INTEGER PRIMARY KEY,apn TEXT,captured_at TEXT,url TEXT,html TEXT,kind TEXT,text TEXT,data_json TEXT,sha256 TEXT);
            CREATE TABLE county_tax_bills(apn TEXT,fiscal_start INTEGER,fiscal_end INTEGER,total_billed TEXT,amount_paid TEXT,fully_paid INTEGER,installments_json TEXT,details_url TEXT,capture_id INTEGER);
            CREATE TABLE county_tax_errors(id INTEGER PRIMARY KEY,apn TEXT,occurred_at TEXT,error TEXT,terminal INTEGER);
            CREATE TABLE county_tax_listed_bills(apn TEXT,bill_key TEXT,bill_type TEXT,fiscal_start INTEGER,detail_status TEXT,detail_capture_id INTEGER);''')
        db.execute("UPDATE inventory_addresses SET status='matched' WHERE address_key='2'")
        db.execute("INSERT INTO county_tax_targets VALUES('A1','1','Walnut Ave','complete')")
        db.execute("INSERT INTO county_tax_targets VALUES('A2','2','Ocean St','pending')")
        db.execute("INSERT INTO county_tax_captures VALUES(1,'A1','2026-10-01','u','TOTAL VALUE $400,000 LESS EXEMPTION','bill_detail','TOTAL VALUE $400,000 LESS EXEMPTION','{}','hash')")
        db.execute("INSERT INTO county_tax_listed_bills VALUES('A1','a','Annual Tax Bill',2025,'complete',1)")
        db.commit();db.close()
        data=dashboard.dashboard_data(self.path,{},tax_database_path=self.path)
        values={home['zpid']:home['assessed_value'] for home in data['homes']}
        self.assertEqual(values['1'],400000)
        self.assertIsNone(values['2'])
        status=dashboard.tax_status_data(self.path)
        self.assertEqual(status['targets'],{'complete':1,'pending':1})
        self.assertEqual(status['details'],{'complete':1})
        self.assertEqual(status['captures']['bill_detail']['count'],1)

    def test_restart_uses_saved_launcher_and_preserves_failed_resources(self):
        output = self.path.parent
        command = ['/python', '/saved/run.py', '--profile', 'Zillow', '--skip-failed']
        (output / 'active-run.json').write_text(json.dumps({'launcher_command': command, 'profile': 'Zillow'}))
        (output / 'zillow-progress.json').write_text(json.dumps({'consecutive_failures': 11, 'stop_reason': 'Failure limit'}))
        with patch.object(dashboard.subprocess, 'Popen', return_value=Mock(pid=12345)) as launch:
            result = dashboard.restart_crawler(self.path, 'zillow')
        self.assertEqual(result['status'], 'starting')
        self.assertEqual(launch.call_args.args[0], command)
        self.assertEqual(json.loads((output / 'zillow-progress.json').read_text())['consecutive_failures'], 0)
        self.assertEqual(json.loads((output / 'continuation-history.jsonl').read_text())['previous_progress']['consecutive_failures'], 11)
        self.assertEqual(json.loads((output / 'active-run.json').read_text())['pid'], 12345)
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM captures').fetchone()[0], 3)
        db.close()

    def test_restart_rejects_occupied_queue_and_maintenance(self):
        output = self.path.parent
        run = output / 'active-run.json'
        run.write_text(json.dumps({'maintenance': 'Migration'}))
        with self.assertRaisesRegex(ValueError, 'maintenance'):
            dashboard.restart_crawler(self.path, 'zillow')
        run.write_text('{}')
        with (output / 'zillow-run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'already in use'):
                dashboard.restart_crawler(self.path, 'zillow')
        self.assertFalse((output / 'zillow-progress.json').exists())

    def test_restart_launch_failure_restores_failure_streak(self):
        output = self.path.parent
        (output / 'active-run.json').write_text(json.dumps({'launcher_command': ['/python', '/saved/run.py', '--profile', 'Zillow']}))
        progress = output / 'zillow-progress.json'
        previous = {'consecutive_failures': 11, 'stop_reason': 'Failure limit'}
        progress.write_text(json.dumps(previous))
        with patch.object(dashboard.subprocess, 'Popen', side_effect=OSError('No executable')):
            with self.assertRaises(OSError):
                dashboard.restart_crawler(self.path, 'zillow')
        diagnostic=json.loads((self.path.parent/'zillow-restart-status.json').read_text())
        self.assertIn('No executable',diagnostic['error'])
        self.assertEqual(diagnostic['stage'],'failed')
        self.assertEqual(json.loads(progress.read_text()), previous)

    def test_worker_status_uses_independent_zillow_lock(self):
        output = self.path.parent
        with (output / 'run.lock').open('a') as county_lock:
            fcntl.flock(county_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(dashboard.worker_running(output))
            with (output / 'zillow-run.lock').open('a') as zillow_lock:
                fcntl.flock(zillow_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(dashboard.worker_running(output))
            self.assertFalse(dashboard.worker_running(output))

    def test_crawl_status_preserves_zillow_stop_and_profile(self):
        output = self.path.parent
        (output / 'active-run.json').write_text(json.dumps({'profile': 'Zillow', 'status': 'stopped'}))
        (output / 'zillow-progress.json').write_text(json.dumps({
            'consecutive_failures': 1, 'stop_reason': 'Access restriction; queue retained'}))
        data = dashboard.crawl_status_data(self.path)
        self.assertEqual(data['zillow']['status'], 'Stopped')
        self.assertEqual(data['zillow']['profile'], 'Zillow')
        self.assertEqual(data['zillow']['consecutive_failures'], 1)
        self.assertEqual(data['zillow']['stop_reason'], 'Access restriction; queue retained')
        self.assertFalse(data['tax']['available'])

    def test_difference_fields_and_sorts_use_all_matching_properties(self):
        tax_path = Path(self.tmp.name) / 'county-tax.sqlite3'
        tax = sqlite3.connect(tax_path)
        tax.executescript('''CREATE TABLE listed_bills(apn TEXT,bill_type TEXT,fiscal_start INTEGER,detail_status TEXT,detail_capture_id INTEGER);
            CREATE TABLE captures(id INTEGER PRIMARY KEY,text TEXT);''')
        tax.executemany('INSERT INTO captures VALUES(?,?)', [
            (1, 'TOTAL VALUE $400,000 LESS EXEMPTION $7,000'),
            (2, 'TOTAL VALUE $100,000 LESS EXEMPTION $7,000'),
        ])
        tax.executemany('INSERT INTO listed_bills VALUES(?,?,?,?,?)', [
            ('A1','Annual Tax Bill',2025,'complete',1),
            ('A2','Annual Tax Bill',2025,'complete',2),
        ])
        tax.commit(); tax.close()
        city = sqlite3.connect(self.path)
        city.execute("UPDATE observations SET zestimate=500000 WHERE capture_id=2")
        city.execute("UPDATE inventory_addresses SET status='matched' WHERE address_key='2'")
        city.commit(); city.close()
        percent = dashboard.dashboard_data(self.path, {'sort': ['percentage-difference']}, tax_database_path=tax_path)
        amount = dashboard.dashboard_data(self.path, {'sort': ['value-difference']}, tax_database_path=tax_path)
        self.assertEqual([home['zpid'] for home in percent['homes']], ['2', '1'])
        self.assertEqual([home['zpid'] for home in amount['homes']], ['1', '2'])
        self.assertAlmostEqual(percent['homes'][0]['percentage_difference'], 80.0)
        self.assertEqual(amount['homes'][0]['value_difference'], 500000)
        self.assertEqual(dashboard.dashboard_data(self.path, {'sort': ['percentage-difference-asc']}, tax_database_path=tax_path)['homes'][0]['zpid'], '1')
        self.assertEqual(dashboard.dashboard_data(self.path, {'sort': ['value-difference-asc']}, tax_database_path=tax_path)['homes'][0]['zpid'], '2')

    def test_search_and_status_are_parameterized_literal_filters(self):
        data = dashboard.dashboard_data(self.path, {'q': ['Walnut'], 'status': ['FOR_SALE']})
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['homes'][0]['zpid'], '1')
        for query in ['%', "' OR 1=1 --"]:
            self.assertEqual(dashboard.dashboard_data(self.path, {'q': [query]})['total'], 0)

    def test_county_inventory_expands_denominator_without_writing_city_database(self):
        city = sqlite3.connect(self.path)
        city.execute("INSERT INTO inventory_addresses(address_key,address,status) VALUES('WALNUT AVE','Walnut Ave','matched')")
        city.execute("INSERT INTO inventory_parcels(object_id,address_key,classification,run_id,feature_json) VALUES(4,'WALNUT AVE','residential',1,'{}')")
        city.commit(); city.close()
        county_path = Path(self.tmp.name) / 'county.sqlite3'
        county = sqlite3.connect(county_path)
        county.execute('CREATE TABLE parcels(address TEXT,residential INTEGER)')
        county.execute('CREATE TABLE inventory_state(key TEXT PRIMARY KEY,value TEXT)')
        county.execute("INSERT INTO inventory_state VALUES('complete','1')")
        county.executemany('INSERT INTO parcels VALUES(?,1)', [
            ('Walnut Avenue',), ('Ocean Street',), ('Dougmar Drive',),
        ])
        county.commit(); county.close()
        data = dashboard.dashboard_data(self.path, {}, county_path)
        self.assertEqual(data['summary']['inventory_total'], 3)
        self.assertEqual(data['summary']['reviewed'], 1)
        self.assertEqual(data['summary']['pending'], 2)
        city = sqlite3.connect(self.path)
        self.assertEqual(city.execute('SELECT count(*) FROM inventory_addresses').fetchone()[0], 4)
        city.close()

    def test_unknown_sort_is_rejected_and_large_page_is_bounded(self):
        with self.assertRaises(ValueError):
            dashboard.dashboard_data(self.path, {'sort': ['address; DROP TABLE homes']})
        self.assertEqual(dashboard.dashboard_data(self.path, {'page': ['999999']})['page'], 1)

    def test_missing_database_is_not_created(self):
        path = Path(self.tmp.name) / 'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            dashboard.dashboard_data(path, {})
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
