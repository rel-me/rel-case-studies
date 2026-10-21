import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'scripts/export_data.py'
spec=importlib.util.spec_from_file_location('export_data',path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class TaxEvidenceTests(unittest.TestCase):
    def test_assessment_and_generic_rate_never_become_a_payment(self):
        self.assertIsNone(module.tax_record({'taxAssessedValue':200000,'propertyTaxRate':1.1},'2026-10-16T00:00:00+00:00'))

    def test_latest_positive_tax_ignores_null_and_future_entries(self):
        facts={'taxHistory':[
            {'time':1791149429711,'taxPaid':None,'value':100000},
            {'time':1759613429711,'taxPaid':2000,'value':98000},
            {'time':1728077429711,'taxPaid':1800,'value':96000},
            {'time':1893456000000,'taxPaid':9000,'value':1000000}]}
        record=module.tax_record(facts,'2026-10-16T00:00:00+00:00')
        self.assertEqual(record['annual_tax'],2000)
        self.assertEqual(record['tax_year'],2025)
        self.assertEqual(record['tax_assessed_value'],98000)

    def test_rates_and_benchmarks_have_consistent_units(self):
        result=module.metrics(1000000,200000,3000)
        self.assertEqual(result['assessment_gap'],800000)
        self.assertEqual(result['assessment_ratio'],.2)
        self.assertEqual(result['effective_tax_rate'],.003)
        self.assertEqual(result['benchmark_gap'],7000)
        self.assertIsNone(module.metrics(1000000,None,None)['benchmark_gap'])
        self.assertLess(module.metrics(1000000,1200000,15000)['benchmark_gap'],0)
        self.assertTrue(all(v is None for v in module.metrics(None,200000,3000).values()))


class CountyExportTests(unittest.TestCase):
    def test_county_only_record_keeps_payment_provenance_without_inventing_estimate(self):
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
            taxpath=root/'tax.sqlite3';tax=county.init_database(taxpath)
            tax.execute("INSERT INTO targets(apn,zpid,address) VALUES('02625202','','1215 DOUGMAR DR')")
            capture=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>history</p>','history')
            tax.execute('INSERT INTO bills VALUES(?,?,?,?,?,?,?,?,?)',('02625202',2025,2026,'10000.00','5000.00',0,'[]',None,capture))
            detail=county.save_capture(tax,'02625202','https://ttc.co.santa-cruz.ca.us/TaxBills/','<p>TOTAL VALUE $700,000 LESS EXEMPTION</p>','bill_detail')
            tax.execute("INSERT INTO listed_bills(apn,bill_key,bill_type,fiscal_start,detail_status,detail_capture_id) VALUES('02625202','test','Annual Tax Bill',2025,'complete',?)",(detail,));tax.commit();tax.close()
            with redirect_stdout(io.StringIO()): module.export(source,root/'public',county_taxes=taxpath)
            data=json.loads((root/'public/data.json').read_text());record=next(r for r in data['records'] if r['address']=='1215 DOUGMAR DR')
            self.assertIsNone(record['estimated_value']);self.assertIsNone(record['benchmark_gap']);self.assertIsNone(record['in_santa_cruz_city'])
            self.assertEqual(record['assessed_value'],700000);self.assertEqual(record['annual_paid'],5000);self.assertEqual(record['fiscal_year'],'2025–26')
            self.assertEqual(data['metadata']['with_estimate'],1);self.assertEqual(len(data['records']),2)


if __name__=='__main__': unittest.main()
