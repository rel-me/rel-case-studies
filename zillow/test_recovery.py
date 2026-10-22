import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

import records
import zillow


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=records.database(Path(self.tmp.name)/'test.db')
        self.addCleanup(self.db.close)

    def test_explicit_recovery_excludes_challenges_and_deduplicates(self):
        for n,reason in [(1,'Timed out waiting for embedded Chromium'),(2,'REQUEST_CANCELLED'),(3,'Access challenge'),(4,'SESSION_NOT_FOUND')]:
            url=f'https://www.zillow.com/homes/{n}-TEST-ST_rb/'
            self.db.execute("INSERT INTO inventory_addresses(address_key,address,lookup_url,status,reason) VALUES(?,?,?,'failed',?)",(str(n),str(n),url,reason))
            self.db.execute('INSERT INTO crawl_errors(url,occurred_at,attempt,terminal,error) VALUES(?,\'now\',2,1,?)',(url,reason))
        a=zillow.failed_seeds(self.db);b=zillow.failed_seeds(self.db)
        self.assertEqual(len(a),2)
        self.assertEqual([r.unique_key for r in a],[r.unique_key for r in b])
        self.assertTrue(all(r.retry_count==0 for r in a))
        self.db.execute("UPDATE inventory_addresses SET status='matched' WHERE address_key='1'")
        self.assertEqual(len(zillow.failed_seeds(self.db)),1)

    def test_explicit_http_recovery_is_limited_to_configured_statuses(self):
        for status in (403, 429, 404, 401):
            url=f'https://www.zillow.com/homes/{status}-TEST-ST_rb/'
            reason=f'Client error status code returned (status code: {status}).'
            self.db.execute("INSERT INTO inventory_addresses(address_key,address,lookup_url,status,reason) VALUES(?,?,?,'failed',?)",
                            (str(status),str(status),url,reason))
            self.db.execute('INSERT INTO crawl_errors(url,occurred_at,attempt,terminal,error) VALUES(?,\'now\',1,1,?)',
                            (url,reason))
        seeds=zillow.failed_seeds(self.db,zillow.RotationRules())
        self.assertEqual({r.user_data['inventory_key'] for r in seeds},set())
        self.assertEqual([r.unique_key for r in seeds],
                         [r.unique_key for r in zillow.failed_seeds(self.db,zillow.RotationRules())])

    async def test_timeout_waits_before_reclaim_then_records_failure_and_continues(self):
        class Crawler:
            router=SimpleNamespace(default_handler=lambda fn:fn)
            reason=None
            def error_handler(self,fn):self.retry=fn
            def failed_request_handler(self,fn):self.failed=fn
            def stop(self,reason):self.reason=reason
        crawler=Crawler()
        zillow.register_handlers(crawler,self.db,retry_delay=5)
        request=SimpleNamespace(url='https://www.zillow.com/',retry_count=1,user_data={},forefront=False)
        context=SimpleNamespace(request=request,log=SimpleNamespace(warning=lambda x:None))
        with patch.object(zillow.asyncio,'sleep',new_callable=AsyncMock) as sleep:
            await crawler.retry(context,TimeoutError('Timed out waiting for Chromium'))
            sleep.assert_awaited_once_with(5)
        self.assertTrue(request.forefront)
        await crawler.failed(context,RuntimeError('REQUEST_CANCELLED'))
        self.assertIsNone(crawler.reason)
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT attempt,terminal FROM crawl_errors')],[(1,0),(2,1)])

    async def test_access_denial_stops_without_retrying_or_scheduling_recovery(self):
        from crawlee.errors import HttpClientStatusCodeError
        class Crawler:
            router=SimpleNamespace(default_handler=lambda fn:fn)
            reason=None
            def error_handler(self,fn):self.retry=fn
            def failed_request_handler(self,fn):self.failed=fn
            def stop(self,reason):self.reason=reason
        crawler=Crawler()
        recovery=SimpleNamespace(rules=zillow.RotationRules(),schedule=lambda *args: self.fail('403 must not schedule recovery'))
        zillow.register_handlers(crawler,self.db,recovery=recovery)
        request=SimpleNamespace(url='https://www.zillow.com/homes/1-TEST-ST_rb/',retry_count=0,user_data={},forefront=False)
        context=SimpleNamespace(request=request,log=SimpleNamespace(warning=lambda x:None))
        await crawler.failed(context,HttpClientStatusCodeError('denied',403))
        self.assertIn('403',crawler.reason)
