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

    def test_pending_priority_uses_latest_estimates_and_preserves_terminal_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp)/'inventory.sqlite3'
            source = sqlite3.connect(inventory)
            source.executescript('''CREATE TABLE observations(zpid TEXT,capture_id INTEGER,zestimate REAL);
                CREATE TABLE inventory_addresses(address_key TEXT,zpid TEXT,address TEXT,status TEXT);
                CREATE TABLE inventory_parcels(apn TEXT,address_key TEXT,classification TEXT);''')
            for i,estimate in [(1,800000),(2,None),(3,900000),(4,700000),(5,600000)]:
                source.execute('INSERT INTO observations VALUES(?,?,?)',(str(i),1,estimate))
                source.execute('INSERT INTO inventory_addresses VALUES(?,?,?,?)',(str(i),str(i),str(i),'matched'))
                source.execute('INSERT INTO inventory_parcels VALUES(?,?,?)',(f'0000000{i}',str(i),'residential'))
            # An older estimate must not prioritize a home whose latest estimate is missing.
            source.execute("INSERT INTO observations VALUES('3',2,NULL)")
            source.commit();source.close()
            db=sqlite3.connect(':memory:')
            db.execute('CREATE TABLE county_tax_targets(apn TEXT,address TEXT,status TEXT)')
            db.executemany('INSERT INTO county_tax_targets VALUES(?,?,?)',[
                ('00000001','Z estimated','pending'),('00000002','A Dougmar','pending'),
                ('00000003','B missing estimate','pending'),('00000004','C failed','failed'),
                ('00000005','D complete','complete')])
            ordered,priority=county.ordered_pending_targets(db,inventory,['00000002'])
            self.assertEqual(ordered,['00000002','00000001','00000003'])
            self.assertEqual(priority,['00000002','00000001'])
            county_path=Path(tmp)/'county.sqlite3'
            with sqlite3.connect(county_path) as geo:
                geo.execute('CREATE TABLE parcels(apn TEXT,municipality TEXT,postal_city TEXT,postal_code TEXT,in_santa_cruz_city INTEGER,residential INTEGER)')
                geo.executemany('INSERT INTO parcels VALUES(?,?,?,?,?,1)',[
                    ('00000001','n/a','SOQUEL','95073',0),
                    ('00000002','n/a','SANTA CRUZ','95062',0),
                    ('00000003','City of Santa Cruz','SANTA CRUZ','95060',1)])
            ordered,priority=county.ordered_pending_targets(db,inventory,['00000002'],county_path)
            # A city parcel without an estimate precedes outside-city estimates and Dougmar.
            self.assertEqual(ordered,['00000003','00000002','00000001'])
            self.assertEqual(priority,ordered)
            self.assertEqual(dict(db.execute('SELECT apn,status FROM county_tax_targets'))['00000004'],'failed')
            db.close()

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
            self.assertEqual(targets.execute('SELECT count(*) FROM county_tax_targets').fetchone()[0],1)
            with targets: targets.execute("UPDATE county_tax_targets SET status='complete'")
            county.seed_targets(targets,source)
            self.assertEqual(targets.execute('SELECT status FROM county_tax_targets').fetchone()[0],'complete')
            targets.close()

    def test_tax_tables_can_share_the_zillow_file_without_capture_table_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            main=Path(directory)/'zillow.sqlite3';db=sqlite3.connect(main)
            db.executescript('''CREATE TABLE observations(capture_id INTEGER,zpid TEXT,zestimate REAL);
              CREATE TABLE inventory_addresses(zpid TEXT,address_key TEXT,status TEXT,address TEXT);
              CREATE TABLE inventory_parcels(apn TEXT,address_key TEXT,classification TEXT);
              CREATE TABLE captures(id INTEGER PRIMARY KEY,title TEXT);
              INSERT INTO captures VALUES(1,'Zillow capture');
              INSERT INTO observations VALUES(1,'a',1000000);
              INSERT INTO inventory_addresses VALUES('a','aa','matched','1 A St');
              INSERT INTO inventory_parcels VALUES('009-123-26','aa','residential');''');db.commit();db.close()
            combined=county.init_database(main)
            county.seed_targets(combined,main)
            county.save_capture(combined,'00912326','https://ttc.co.santa-cruz.ca.us/TaxBills/','county HTML','history')
            self.assertEqual(combined.execute('SELECT count(*) FROM county_tax_targets').fetchone()[0],1)
            self.assertEqual(combined.execute('SELECT title FROM captures WHERE id=1').fetchone()[0],'Zillow capture')
            self.assertEqual(combined.execute('SELECT html FROM county_tax_captures WHERE id=1').fetchone()[0],'county HTML')
            combined.close()

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

    def test_only_latest_annual_bill_detail_is_selected(self):
        entries=county.list_all_bills(
            bill(2024,'1/10/2025')+
            bill(2025,'Not Paid',kind='Supplemental Tax Bill')+
            bill(2025,'Not Paid'), '00912326')
        selected=county.latest_annual_bill_keys(entries)
        self.assertEqual(len(selected),1)
        self.assertEqual(next(e['title'] for e in entries if e['key'] in selected),
                         '2025 - 2026 Annual Tax Bill')

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
                db.execute("INSERT INTO county_tax_targets(apn,zpid,address,status) VALUES('00912326','a','992 Prospect Heights','complete')")
                db.execute("INSERT INTO county_tax_captures(apn,captured_at,url,html) VALUES(?,?,?,?)",('00912326','2026-10-17',county.SEARCH,html))
            county.prepare_full_capture(db)
            self.assertEqual(db.execute('SELECT status FROM county_tax_targets').fetchone()[0],'history_complete')
            detail_html='<table><tr><td>NET TAXABLE VALUE</td><td>$694,272</td></tr><tr><td>Homeowners Exemption</td><td>$7,000</td></tr></table>'
            detail=county.save_capture(db,'00912326',county.SITE,detail_html,'bill_detail')
            self.assertIn('Homeowners',db.execute('SELECT text FROM county_tax_captures WHERE id=?',(detail,)).fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM county_tax_page_search WHERE county_tax_page_search MATCH 'Exemption'").fetchone()[0],1)
            with db:
                db.execute("UPDATE county_tax_listed_bills SET detail_status='complete',detail_capture_id=?",(detail,))
                db.execute("UPDATE county_tax_targets SET status='complete'")
            # Fresh session-specific IDs must not invalidate an unchanged bill checkpoint.
            county.save_listed_bills(db,'00912326',html.replace('TaxBillID=1','TaxBillID=999'),1)
            self.assertEqual(db.execute('SELECT detail_capture_id FROM county_tax_listed_bills').fetchone()[0],detail)
            county.prepare_full_capture(db)
            self.assertEqual(db.execute('SELECT status FROM county_tax_targets').fetchone()[0],'complete')
            query_path=path.with_name('query_county_taxes.py');spec=importlib.util.spec_from_file_location('query',query_path)
            query=importlib.util.module_from_spec(spec);spec.loader.exec_module(query)
            found=query.lookup(db,apn='009-123-26',unpaid=True,search='Exemption')
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['unpaid_installments'],2)
            self.assertIn('$694,272',found[0]['detail_text'])
            db.close()


class PriorityParcelTests(unittest.TestCase):
    def test_explicit_county_import_prioritizes_without_resetting_terminal_states(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            db=county.init_database(Path(directory)/'test.sqlite3')
            path=Path(directory)/'priority.json'
            path.write_text(json.dumps({'source':'https://sccgis.santacruzcountyca.gov/server/rest/services/giswebp/MapServer/25','parcels':[
                {'apn':'026-252-03','address':'1225 DOUGMAR DR'}, {'apn':'026-252-02','address':'1215 DOUGMAR DR'}]}))
            self.assertEqual(county.import_priority_parcels(db,path),['02625202','02625203'])
            with db: db.execute("UPDATE county_tax_targets SET status='blocked' WHERE apn='02625202'")
            self.assertEqual(county.import_priority_parcels(db,path),['02625203'])
            self.assertEqual(db.execute("SELECT status FROM county_tax_targets WHERE apn='02625202'").fetchone()[0],'blocked')
            self.assertEqual(db.execute('SELECT count(*) FROM county_tax_targets').fetchone()[0],2)
            db.close()

class CountyInventoryTests(unittest.TestCase):
    def test_boundary_uses_county_field_instead_of_postal_city(self):
        import json
        inventory_path=path.with_name('crawl_county_inventory.py');spec=importlib.util.spec_from_file_location('inventory',inventory_path)
        inventory=importlib.util.module_from_spec(spec);spec.loader.exec_module(inventory)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'inventory.sqlite3';db=inventory.initialize(p)
            features=[{'attributes':{'OBJECTID':1,'APNNODASH':'02625202','SITEADD':'1215 DOUGMAR DR','SITCITY':'SANTA CRUZ','SITZIP':'95062','ASRUSECD':'020','USECDDESC':'SINGLE RESIDENCE','CITYLIMITS':'n/a'}},
                {'attributes':{'OBJECTID':2,'APNNODASH':'00114135','SITEADD':'530 HIGH ST','SITCITY':'SANTA CRUZ','SITZIP':'95060','ASRUSECD':'020','USECDDESC':'SINGLE RESIDENCE','CITYLIMITS':'City of Santa Cruz'}}]
            html='<pre>'+json.dumps({'features':features})+'</pre>'
            inventory.save_batch(db,0,inventory.SOURCE,html,[1,2])
            self.assertEqual(db.execute('SELECT in_santa_cruz_city FROM parcels ORDER BY object_id').fetchall(),[(0,),(1,)])
            target=county.init_database(Path(directory)/'tax.sqlite3')
            with self.assertRaisesRegex(ValueError,'incomplete'): county.seed_county_targets(target,p)
            with db: db.execute("INSERT INTO inventory_state VALUES('complete','1')")
            county.seed_county_targets(target,p)
            self.assertEqual(target.execute('SELECT count(*) FROM county_tax_targets').fetchone()[0],2)
            with self.assertRaisesRegex(ValueError,'IDs'): inventory.save_batch(db,1,inventory.SOURCE,html,[1,3])
            target.close();db.close()


if __name__=='__main__': unittest.main()

class SearchFormReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_returns_directly_to_form_without_reload(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SITE+'/Taxbills/TaxBillDetails?TaxBillID=1&RevObjID=2')
        page.locator.return_value.wait_for=AsyncMock()
        async def back(**kwargs):
            page.url=county.SEARCH
            return Mock(status=200)
        page.go_back=AsyncMock(side_effect=back);page.goto=AsyncMock();activate=AsyncMock()
        self.assertEqual(await county.restore_search_form(page,activate),'history_return')
        page.go_back.assert_awaited_once();page.goto.assert_not_awaited();activate.assert_awaited_once()

    async def test_result_is_left_before_detail_without_replaying_post(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SITE+'/Taxbills/TaxBills')
        page.locator.return_value.wait_for=AsyncMock()
        async def back(**kwargs):
            page.url=county.SEARCH
            return Mock(status=200)
        page.go_back=AsyncMock(side_effect=back);page.goto=AsyncMock()
        self.assertEqual(await county.restore_search_form(page,AsyncMock()),'history_return')
        page.go_back.assert_awaited_once();page.goto.assert_not_awaited()

    async def test_missing_history_uses_one_bounded_form_reload(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SITE+'/Taxbills/TaxBillDetails')
        page.locator.return_value.wait_for=AsyncMock()
        page.go_back=AsyncMock(return_value=None);page.goto=AsyncMock(return_value=Mock(status=200))
        self.assertEqual(await county.restore_search_form(page,AsyncMock()),'form_reload')
        page.go_back.assert_awaited_once();page.goto.assert_awaited_once_with(county.SEARCH,timeout=45000)

    async def test_access_denial_does_not_reload_or_continue(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SITE+'/Taxbills/TaxBillDetails')
        page.go_back=AsyncMock(return_value=Mock(status=403));page.goto=AsyncMock()
        with self.assertRaises(county.CountyNavigationError):
            await county.restore_search_form(page,AsyncMock())
        page.goto.assert_not_awaited()


class ModernHistoryCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_modern_group_uses_one_form_load_without_legacy_history_call(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SITE+'/Taxbills/TaxBills')
        page.locator.return_value.wait_for=AsyncMock()
        page.go_back=AsyncMock();page.goto=AsyncMock(return_value=Mock(status=200));activate=AsyncMock()
        self.assertEqual(await county.restore_search_form(page,activate,history_supported=False),'form_reload')
        page.goto.assert_awaited_once_with(county.SEARCH,timeout=45000)
        page.go_back.assert_not_awaited();activate.assert_not_awaited()


class RetrySearchFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_reloads_even_when_cached_url_claims_search_form(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SEARCH)
        page.locator.return_value.wait_for=AsyncMock()
        page.goto=AsyncMock(return_value=Mock(status=200));page.go_back=AsyncMock();activate=AsyncMock()
        result=await county.restore_search_form(page,activate,force_reload=True)
        self.assertEqual(result,'form_reload')
        page.goto.assert_awaited_once_with(county.SEARCH,timeout=45000)
        page.locator.return_value.wait_for.assert_awaited_once_with(timeout=15000)
        page.go_back.assert_not_awaited();activate.assert_not_awaited()

    async def test_retry_leaves_results_without_history_replay(self):
        from unittest.mock import AsyncMock, Mock
        for supported in (True,False):
            with self.subTest(history_supported=supported):
                page=Mock(url=county.SITE+'/Taxbills/TaxBills')
                page.locator.return_value.wait_for=AsyncMock()
                page.goto=AsyncMock(return_value=Mock(status=200));page.go_back=AsyncMock();activate=AsyncMock()
                await county.restore_search_form(page,activate,supported,force_reload=True)
                page.goto.assert_awaited_once_with(county.SEARCH,timeout=45000)
                page.go_back.assert_not_awaited();activate.assert_not_awaited()

    async def test_retry_navigation_access_denial_is_not_swallowed(self):
        from unittest.mock import AsyncMock, Mock
        for status in (401,403,429):
            with self.subTest(status=status):
                page=Mock(url=county.SEARCH)
                page.goto=AsyncMock(return_value=Mock(status=status))
                page.locator.return_value.wait_for=AsyncMock()
                with self.assertRaises(county.CountyNavigationError) as error:
                    await county.restore_search_form(page,AsyncMock(),force_reload=True)
                self.assertEqual(error.exception.status_code,status)
                page.locator.return_value.wait_for.assert_not_awaited()
                page.goto.assert_awaited_once()

    async def test_retry_timeout_propagates_without_an_extra_attempt(self):
        from unittest.mock import AsyncMock, Mock
        page=Mock(url=county.SEARCH)
        page.goto=AsyncMock(return_value=Mock(status=200))
        page.locator.return_value.wait_for=AsyncMock(side_effect=TimeoutError('search field missing'))
        with self.assertRaises(TimeoutError):
            await county.restore_search_form(page,AsyncMock(),force_reload=True)
        page.goto.assert_awaited_once();page.locator.return_value.wait_for.assert_awaited_once()
