import importlib.util
from pathlib import Path
import unittest
import sqlite3
import tempfile

path=Path(__file__).resolve().parents[1]/'scripts/crawl_county_taxes.py'
spec=importlib.util.spec_from_file_location('county_tax',path)
county=importlib.util.module_from_spec(spec);spec.loader.exec_module(county)


def bill(year,paid,amount='4,233.72',kind='Annual Tax Bill'):
    return f'''<table class="TaxBillContainer"><tr><td class="TaxBillHeader">{year} - {year+1} {kind}</td><td><a href="/Taxbills/TaxBillDetails?TaxBillID=1&amp;RevObjID=2">See Details</a></td></tr>
      <tr><td>First Installment</td><td>12/10/{year}</td><td>{paid}</td><td>${amount}</td></tr>
      <tr><td>Second Installment</td><td>4/10/{year+1}</td><td>{paid}</td><td>${amount}</td></tr>
      <tr><td>Both Installment</td><td></td><td></td><td>$8,467.44</td></tr></table>'''


class CountyTaxTests(unittest.TestCase):
    def setUp(self):
        self.header='<table class="paddedtable"><tr><td>Parcel/Account #</td><td>009-123-26</td><td>Situs Address</td><td>992 PROSPECT HEIGHTS</td></tr></table>'

    def test_counts_installments_once_and_keeps_billed_separate_from_paid(self):
        html=bill(2026,'Not Paid').replace('First Installment</td>', 'First Installment <form><input type="submit" value="Pay First Installment"></form></td>')
        result=county.parse_bills(self.header+html,'00912326')[0]
        self.assertEqual(result['total_billed'],'8467.44')
        self.assertEqual(result['amount_paid'],'0')
        self.assertFalse(result['fully_paid'])

    def test_payment_dates_support_closed_year_comparisons(self):
        result=county.parse_bills(self.header+bill(2025,'3/23/2026','4,140.58'),'009-123-26')[0]
        self.assertEqual(result['total_billed'],'8281.16')
        self.assertEqual(result['amount_paid'],'8281.16')
        self.assertTrue(result['fully_paid'])
        self.assertEqual(result['installments'][0]['paid_date'],'2026-03-23')

    def test_refuses_wrong_parcel_and_excludes_supplemental_bills(self):
        with self.assertRaisesRegex(ValueError,'different'):
            county.parse_bills(self.header+bill(2026,'Not Paid'),'00114135')
        result=county.parse_bills(self.header+bill(2026,'Not Paid')+bill(2026,'Not Paid',kind='Supplemental Tax Bill'),'00912326')
        self.assertEqual(len(result),1)

    def test_refuses_missing_or_duplicate_installments(self):
        with self.assertRaisesRegex(ValueError,'exactly two'):
            county.parse_bills(self.header+bill(2026,'Not Paid').replace('Second Installment','First Installment'),'00912326')

    def test_resume_preserves_completed_parcels_and_uses_only_latest_positive_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'inventory.sqlite3'
            db=sqlite3.connect(source)
            db.executescript('''CREATE TABLE observations(capture_id INTEGER,zpid TEXT,zestimate REAL);
              CREATE TABLE inventory_addresses(zpid TEXT,address_key TEXT,status TEXT,address TEXT);
              CREATE TABLE inventory_parcels(apn TEXT,address_key TEXT,classification TEXT);
              INSERT INTO observations VALUES(1,'a',1000000),(1,'b',900000),(2,'b',NULL);
              INSERT INTO inventory_addresses VALUES('a','aa','matched','1 A St'),('b','bb','matched','2 B St');
              INSERT INTO inventory_parcels VALUES('009-123-26','aa','residential'),('001-141-35','bb','residential');''')
            db.close()
            targets=county.init_database(Path(directory)/'county.sqlite3')
            county.seed_targets(targets,source)
            self.assertEqual(targets.execute('SELECT count(*) FROM targets').fetchone()[0],1)
            with targets: targets.execute("UPDATE targets SET status='complete'")
            county.seed_targets(targets,source)
            self.assertEqual(targets.execute('SELECT status FROM targets').fetchone()[0],'complete')
            targets.close()

    def test_bad_historical_year_is_flagged_without_discarding_valid_years(self):
        issues=[]
        malformed=bill(2008,'12/1/2008').replace('Second Installment','Unknown Installment')
        result=county.parse_bills(self.header+bill(2026,'Not Paid')+malformed,'00912326',issues)
        self.assertEqual([v['fiscal_start'] for v in result],[2026])
        self.assertEqual(issues[0]['fiscal_start'],2008)

    def test_duplicate_complete_bills_are_not_silently_overwritten(self):
        with self.assertRaisesRegex(ValueError,'Multiple complete annual'):
            county.parse_bills(self.header+bill(2026,'Not Paid')+bill(2026,'Not Paid'),'00912326')


class FullCaptureTests(unittest.TestCase):
    def test_all_bill_types_and_raw_installments_are_retained(self):
        entries=county.list_all_bills(bill(2026,'Not Paid')+bill(2025,'3/23/2026',kind='Supplemental Tax Bill'),'00912326')
        self.assertEqual(len(entries),2)
        self.assertEqual(entries[0]['unpaid_installments'],2)
        self.assertEqual(entries[1]['bill_type'],'Supplemental Tax Bill')
        self.assertEqual(entries[1]['installments'][0]['payment_status'],'paid')
        self.assertEqual(len(entries[0]['installments']),2)

    def test_defaulted_bill_is_preserved_without_discarding_other_years(self):
        html=bill(2025,'Past Due').replace('$4,233.72','Defaulted')
        entries=county.list_all_bills(html,'00912326')
        self.assertEqual(entries[0]['unpaid_installments'],2)
        self.assertEqual(entries[0]['installments'][0]['amount_text'],'Defaulted')
        issues=[]
        header='<table class="paddedtable"><tr><td>Parcel/Account # 009-123-26</td></tr></table>'
        parsed=county.parse_bills(header+html+bill(2026,'Not Paid'),'00912326',issues)
        self.assertEqual(len(parsed),1)
        self.assertEqual(issues[0]['fiscal_start'],2025)

    def test_rejects_payment_and_external_urls(self):
        for url in ['https://ttc.co.santa-cruz.ca.us/Taxbills/Pay?TaxBillID=1&RevObjID=2',
                    'https://example.com/Taxbills/TaxBillDetails?TaxBillID=1&RevObjID=2',
                    county.SITE+'/Taxbills/TaxBillDetails?TaxBillID=1&RevObjID=2&pay=1']:
            with self.assertRaises(ValueError): county.validate_detail_url(url)

    def test_migration_search_and_detail_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            db=county.init_database(Path(directory)/'test.sqlite3')
            html=bill(2026,'Not Paid')
            with db:
                db.execute("INSERT INTO targets(apn,zpid,address,status) VALUES('00912326','a','992 Prospect Heights','complete')")
                db.execute("INSERT INTO captures(apn,captured_at,url,html) VALUES(?,?,?,?)",('00912326','2026-10-17',county.SEARCH,html))
            county.prepare_full_capture(db)
            self.assertEqual(db.execute('SELECT status FROM targets').fetchone()[0],'history_complete')
            detail_html='<table><tr><td>NET TAXABLE VALUE</td><td>$694,272</td></tr><tr><td>Homeowners Exemption</td><td>$7,000</td></tr></table>'
            detail=county.save_capture(db,'00912326',county.SITE,detail_html,'bill_detail')
            self.assertIn('Homeowners',db.execute('SELECT text FROM captures WHERE id=?',(detail,)).fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM page_search WHERE page_search MATCH 'Exemption'").fetchone()[0],1)
            with db:
                db.execute("UPDATE listed_bills SET detail_status='complete',detail_capture_id=?",(detail,))
                db.execute("UPDATE targets SET status='complete'")
            # Fresh session-specific IDs must not invalidate an unchanged bill checkpoint.
            county.save_listed_bills(db,'00912326',html.replace('TaxBillID=1','TaxBillID=999'),1)
            self.assertEqual(db.execute('SELECT detail_capture_id FROM listed_bills').fetchone()[0],detail)
            county.prepare_full_capture(db)
            self.assertEqual(db.execute('SELECT status FROM targets').fetchone()[0],'complete')
            query_path=path.with_name('query_county_taxes.py');spec=importlib.util.spec_from_file_location('query',query_path)
            query=importlib.util.module_from_spec(spec);spec.loader.exec_module(query)
            found=query.lookup(db,apn='009-123-26',unpaid=True,search='Exemption')
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['unpaid_installments'],2)
            self.assertIn('$694,272',found[0]['detail_text'])
            db.close()


if __name__=='__main__': unittest.main()

class PriorityParcelTests(unittest.TestCase):
    def test_explicit_county_import_prioritizes_without_resetting_terminal_states(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            db=county.init_database(Path(directory)/'test.sqlite3')
            path=Path(directory)/'priority.json'
            path.write_text(json.dumps({'source':'https://sccgis.santacruzcountyca.gov/server/rest/services/giswebp/MapServer/25','parcels':[
                {'apn':'026-252-03','address':'1225 DOUGMAR DR'}, {'apn':'026-252-02','address':'1215 DOUGMAR DR'}]}))
            self.assertEqual(county.import_priority_parcels(db,path),['02625202','02625203'])
            with db: db.execute("UPDATE targets SET status='blocked' WHERE apn='02625202'")
            self.assertEqual(county.import_priority_parcels(db,path),['02625203'])
            self.assertEqual(db.execute("SELECT status FROM targets WHERE apn='02625202'").fetchone()[0],'blocked')
            self.assertEqual(db.execute('SELECT count(*) FROM targets').fetchone()[0],2)
            db.close()
