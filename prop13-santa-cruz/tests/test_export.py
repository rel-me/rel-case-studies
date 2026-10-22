import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'scripts/export_data.py'
spec=importlib.util.spec_from_file_location('export_data',path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class TaxEvidenceTests(unittest.TestCase):
    def test_bulk_exports_cannot_target_the_deployed_asset_directory(self):
        with self.assertRaisesRegex(ValueError, 'public assets'):
            module.export(Path('unused.sqlite3'), path.parents[1] / 'public')

    def test_coordinates_require_a_valid_pair_and_keep_source(self):
        self.assertEqual(module.coordinates(None, -122)['latitude'], None)
        self.assertEqual(module.coordinates(91, -122)['longitude'], None)
        self.assertEqual(module.coordinates(True, -122)['latitude'], None)
        self.assertIsNone(module.coordinates(44.315987, -98.43372)['latitude'])
        parcel = module.parcel_coordinates({'geometry': {'rings': [[[-122.03,36.98],[-122.02,36.99],[-122.03,36.98]]]}})
        self.assertEqual(parcel['latitude'], 36.985)
        self.assertIn('parcel GIS', parcel['coordinate_source'])
        point = module.coordinates(36.98123456, -122.03123456)
        self.assertEqual(point['latitude'], 36.981235)
        self.assertEqual(point['coordinate_source'], 'Third-party property website')

class CountyExportTests(unittest.TestCase):
    def empty_source(self, root):
        import sqlite3
        from contextlib import closing
        source=root/'source.sqlite3'
        with closing(sqlite3.connect(source)) as db:
            db.executescript((path.parents[2]/'zillow/schema.sql').read_text())
        return source

    def test_empty_export_keeps_public_schema_without_inventing_a_snapshot_date(self):
        import csv,io,json,tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=self.empty_source(root);destination=root/'export'
            with redirect_stdout(io.StringIO()): module.export(source,destination)
            data=json.loads((destination/'data.json').read_text())
            self.assertEqual(data['records'],[])
            self.assertIsNone(data['metadata']['snapshot_date'])
            self.assertEqual(data['metadata']['total_records'],0)
            with (destination/'data.csv').open(newline='') as stream:
                rows=list(csv.reader(stream))
            self.assertEqual(rows,[list(module.PUBLIC_RECORD_FIELDS)])

    def test_county_only_assessment_exports_before_any_zillow_capture(self):
        import csv,io,json,tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=self.empty_source(root);destination=root/'export'
            spec=importlib.util.spec_from_file_location('county_empty_export',path.with_name('crawl_county_taxes.py'))
            county=importlib.util.module_from_spec(spec);spec.loader.exec_module(county)
            tax=county.init_database(source)
            try:
                tax.execute("INSERT INTO county_tax_targets(apn,zpid,address) VALUES('02625202','','1215 DOUGMAR DR')")
                capture=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>history</p>','history')
                tax.execute('INSERT INTO county_tax_bills VALUES(?,?,?,?,?,?,?,?,?)',('02625202',2025,2026,'10000.00','5000.00',0,'[]',None,capture))
                detail=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>TOTAL VALUE $700,000 LESS EXEMPTION</p>','bill_detail')
                tax.execute("INSERT INTO county_tax_listed_bills(apn,bill_key,bill_type,fiscal_start,detail_status,detail_capture_id) VALUES('02625202','test','Annual Tax Bill',2025,'complete',?)",(detail,))
                tax.commit()
                expected_date=tax.execute('SELECT max(captured_at) FROM county_tax_captures').fetchone()[0][:10]
            finally: tax.close()
            with redirect_stdout(io.StringIO()): module.export(source,destination,county_taxes=source)
            data=json.loads((destination/'data.json').read_text());record=data['records'][0]
            self.assertEqual(record['address'],'1215 DOUGMAR DR')
            self.assertIsNone(record['estimated_value'])
            self.assertEqual(record['assessed_value'],700000)
            self.assertEqual(record['assessment_source'],'Santa Cruz County Tax Collector')
            self.assertEqual(data['metadata']['snapshot_date'],expected_date)
            self.assertEqual(data['metadata']['with_estimate'],0)
            self.assertEqual(data['metadata']['with_assessment'],1)
            self.assertEqual(set(record),set(module.PUBLIC_RECORD_FIELDS))
            self.assertFalse({'apn','zpid','annual_tax','annual_paid','source_url'} & record.keys())
            with (destination/'data.csv').open(newline='') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),1)

    def test_public_export_shows_assessment_without_bill_or_payment_fields(self):
        import sqlite3,tempfile,json,io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.sqlite3';db=sqlite3.connect(source)
            db.executescript('''CREATE TABLE homes(zpid TEXT,address TEXT,city TEXT,postal_code TEXT,home_type TEXT,bedrooms REAL,living_area REAL);
              CREATE TABLE observations(capture_id INTEGER,zpid TEXT,zestimate REAL,facts_json TEXT);
              CREATE TABLE captures(id INTEGER,captured_at TEXT);
              CREATE TABLE inventory_addresses(address_key TEXT,zpid TEXT,status TEXT);
              CREATE TABLE inventory_parcels(address_key TEXT,apn TEXT,feature_json TEXT,classification TEXT);
              INSERT INTO homes VALUES('one','1 City St','Santa Cruz','95060','SINGLE_FAMILY',3,1000);
              INSERT INTO captures VALUES(1,'2026-10-16T00:00:00+00:00');
              INSERT INTO inventory_addresses VALUES('a','one','matched');''')
            db.execute('INSERT INTO observations VALUES(?,?,?,?)',(1,'one',1000000,json.dumps({'taxAssessedValue':200000})))
            db.execute('INSERT INTO inventory_parcels VALUES(?,?,?,?)',('a','00114135',json.dumps({'attributes':{'SCCityLMT':'Yes'}}),'residential'));db.commit();db.close()
            spec=importlib.util.spec_from_file_location('county',path.with_name('crawl_county_taxes.py'));county=importlib.util.module_from_spec(spec);spec.loader.exec_module(county)
            taxpath=source;tax=county.init_database(taxpath)
            tax.execute("INSERT INTO county_tax_targets(apn,zpid,address) VALUES('02625202','','1215 DOUGMAR DR')")
            capture=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>history</p>','history')
            tax.execute('INSERT INTO county_tax_bills VALUES(?,?,?,?,?,?,?,?,?)',('02625202',2025,2026,'10000.00','5000.00',0,'[]',None,capture))
            detail=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>TOTAL VALUE $700,000 LESS EXEMPTION</p>','bill_detail')
            tax.execute("INSERT INTO county_tax_listed_bills(apn,bill_key,bill_type,fiscal_start,detail_status,detail_capture_id) VALUES('02625202','test','Annual Tax Bill',2025,'complete',?)",(detail,));tax.commit();tax.close()
            county_path=root/'county.sqlite3';county_db=sqlite3.connect(county_path)
            county_db.execute('CREATE TABLE parcels(apn TEXT,address TEXT,postal_city TEXT,postal_code TEXT,municipality TEXT,in_santa_cruz_city INTEGER,residential INTEGER)')
            county_db.execute('CREATE TABLE inventory_state(key TEXT,value TEXT)')
            county_db.execute("INSERT INTO parcels VALUES('02625202','1215 DOUGMAR DR','SANTA CRUZ','95062-2914','n/a',0,1)")
            county_db.execute("INSERT INTO inventory_state VALUES('complete','true')")
            county_db.commit();county_db.close()
            supplement_path=root/'dougmar-zillow'/'zillow.sqlite3';supplement_path.parent.mkdir()
            supplement=sqlite3.connect(supplement_path)
            supplement.executescript('''CREATE TABLE homes(zpid TEXT,address TEXT,city TEXT,state TEXT,postal_code TEXT,latitude REAL,longitude REAL,home_type TEXT);
              CREATE TABLE captures(id INTEGER,captured_at TEXT);
              CREATE TABLE observations(capture_id INTEGER,zpid TEXT,zestimate REAL);
              INSERT INTO homes VALUES('z1','1215 Dougmar Dr','Santa Cruz','CA','95062',36.97,-122.02,'SINGLE_FAMILY');
              INSERT INTO captures VALUES(1,'2026-10-17T00:00:00+00:00');
              INSERT INTO observations VALUES(1,'z1',1400000);
              CREATE VIEW latest_homes AS SELECT h.*,o.zestimate,c.captured_at FROM homes h JOIN observations o USING(zpid) JOIN captures c ON c.id=o.capture_id;''')
            supplement.commit();supplement.close()
            # The default county export discovers the private Dougmar supplement without changing
            # the read-only source database or adding bill fields to the public snapshot.
            with redirect_stdout(io.StringIO()): module.export(source,root/'public',county_inventory=county_path,county_taxes=taxpath)
            data=json.loads((root/'public/data.json').read_text());record=next(r for r in data['records'] if r['address']=='1215 DOUGMAR DR')
            self.assertEqual(record['estimated_value'],1400000);self.assertFalse(record['in_santa_cruz_city'])
            self.assertEqual(record['assessed_value'],700000)
            self.assertEqual(record['assessment_source'],'Santa Cruz County Tax Collector')
            self.assertEqual(record['latitude'],36.97)
            self.assertFalse({'apn','zpid','owner','source_url'} & record.keys())
            self.assertFalse({'annual_tax','annual_paid','fully_paid','installments','county_installments','fiscal_year','payment_status'} & record.keys())
            self.assertEqual(data['metadata']['with_estimate'],2);self.assertEqual(len(data['records']),2)


if __name__=='__main__': unittest.main()
