import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

script=Path(__file__).resolve().parents[1]/'scripts/merge_county_tax_database.py'
spec=importlib.util.spec_from_file_location('merge_county_tax_database',script)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)


class CountyTaxMigrationTests(unittest.TestCase):
    def test_migration_namespaces_tax_tables_and_preserves_capture_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'tax.sqlite3';destination=root/'zillow.sqlite3'
            tax=sqlite3.connect(source)
            tax.executescript('''
              CREATE TABLE targets(apn TEXT PRIMARY KEY,zpid TEXT NOT NULL,address TEXT,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,captured_at TEXT,error TEXT);
              CREATE TABLE captures(id INTEGER PRIMARY KEY,apn TEXT NOT NULL,captured_at TEXT NOT NULL,url TEXT NOT NULL,html TEXT NOT NULL,kind TEXT DEFAULT 'history',text TEXT,data_json TEXT,sha256 TEXT);
              CREATE TABLE bills(apn TEXT NOT NULL,fiscal_start INTEGER NOT NULL,fiscal_end INTEGER NOT NULL,total_billed TEXT NOT NULL,amount_paid TEXT NOT NULL,fully_paid INTEGER NOT NULL,installments_json TEXT NOT NULL,details_url TEXT,capture_id INTEGER NOT NULL,PRIMARY KEY(apn,fiscal_start));
              CREATE TABLE errors(id INTEGER PRIMARY KEY,apn TEXT,occurred_at TEXT,error TEXT,terminal INTEGER);
              CREATE TABLE parse_issues(apn TEXT,fiscal_start INTEGER,reason TEXT,capture_id INTEGER);
              CREATE TABLE listed_bills(apn TEXT NOT NULL,bill_key TEXT NOT NULL,title TEXT,bill_type TEXT,fiscal_start INTEGER,rows_json TEXT,installments_json TEXT,unpaid_installments INTEGER,source_url TEXT,history_capture_id INTEGER,data_hash TEXT,detail_status TEXT,detail_capture_id INTEGER,detail_error TEXT,PRIMARY KEY(apn,bill_key));
              CREATE VIRTUAL TABLE page_search USING fts5(apn,kind,url UNINDEXED,text);
              CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE priority_parcels(apn TEXT PRIMARY KEY,source_url TEXT,imported_at TEXT);
              INSERT INTO targets(apn,zpid,address,status) VALUES('00123456','77','1 Test St','complete');
              INSERT INTO captures(id,apn,captured_at,url,html,kind,text,data_json,sha256) VALUES(3,'00123456','2026-10-17T00:00:00Z','https://example.test','raw html','history','parcel text','{}','abc');
              INSERT INTO page_search(rowid,apn,kind,url,text) VALUES(3,'00123456','history','https://example.test','parcel text');
              INSERT INTO listed_bills(apn,bill_key,title,bill_type,fiscal_start,history_capture_id,detail_status) VALUES('00123456','bill-a','Annual Tax Bill','Annual Tax Bill',2025,3,'pending');
              INSERT INTO settings VALUES('full-bill-pages-v1','1');
            ''');tax.commit();tax.close()
            db=sqlite3.connect(destination)
            db.execute('CREATE TABLE captures(id INTEGER PRIMARY KEY, requested_url TEXT)')
            db.execute("INSERT INTO captures VALUES(1,'zillow capture')")
            db.commit();db.close()

            counts=migration.merge(source,destination)
            self.assertEqual(counts['targets'],1)
            db=sqlite3.connect(destination)
            self.assertEqual(db.execute('SELECT requested_url FROM captures').fetchone()[0],'zillow capture')
            self.assertEqual(db.execute('SELECT html FROM county_tax_captures WHERE id=3').fetchone()[0],'raw html')
            self.assertEqual(db.execute('SELECT detail_status FROM county_tax_listed_bills').fetchone()[0],'pending')
            self.assertEqual(db.execute("SELECT count(*) FROM county_tax_page_search WHERE county_tax_page_search MATCH 'parcel'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM county_tax_migration').fetchone()[0],1)
            db.close()
            self.assertTrue(source.is_file(),'migration keeps the source database as a rollback copy')


if __name__=='__main__': unittest.main()
