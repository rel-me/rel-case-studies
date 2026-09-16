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

    async def test_timeout_waits_before_reclaim_and_still_stops_at_budget(self):
        class Crawler:
            router=SimpleNamespace(default_handler=lambda fn:fn)
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
        self.assertIn('REQUEST_CANCELLED',crawler.reason)
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT attempt,terminal FROM crawl_errors')],[(1,0),(2,1)])
