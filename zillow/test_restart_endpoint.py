from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import dashboard


class RestartEndpointTests(unittest.TestCase):
    def test_fast_endpoint_exposes_startup_exception_without_resource_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);database=output/'zillow.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.executescript((Path(__file__).parent/'schema.sql').read_text())
            started=datetime.now(timezone.utc)-timedelta(seconds=30)
            (output/'active-run.json').write_text(json.dumps({'pid':0,'status':'stopped','exit_code':1,'started_at':started.isoformat(),'stopped_at':datetime.now(timezone.utc).isoformat(),'launcher_command':['python','run.py']}))
            (output/'zillow-crawl.log').write_text('RuntimeError: Saved REL group is missing; refusing to switch groups\n')
            server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.handler_for(database))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/restart-status',timeout=3) as response:
                    data=json.load(response)
                self.assertEqual(data['zillow']['phase'],'failed')
                self.assertIn('Saved REL group is missing',data['zillow']['error'])
                self.assertEqual(data['zillow']['exit_code'],1)
                self.assertFalse(data['tax']['available'])
            finally: server.shutdown();server.server_close();thread.join()

    def test_stop_routes_require_same_origin_and_dispatch_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'zillow.sqlite3';tax_run=Path(directory)/'county-run.json'
            server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.handler_for(database,tax_run_file=tax_run))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with patch.object(dashboard,'stop_worker',return_value={'status':'stopped'}) as stop:
                    for worker,run_path in [('zillow',Path(directory)/'active-run.json'),('tax',tax_run)]:
                        request=Request(base+'/api/stop/'+worker,data=b'{}',headers={'Origin':base,'Content-Type':'application/json'})
                        with urlopen(request,timeout=3) as response:self.assertEqual(response.status,202)
                        stop.assert_called_with(Path(directory),worker,run_path)
                    stop.reset_mock()
                    request=Request(base+'/api/stop/tax',data=b'{}',headers={'Origin':'https://example.com','Content-Type':'application/json'})
                    with self.assertRaises(HTTPError) as error:urlopen(request,timeout=3)
                    self.assertEqual(error.exception.code,403);stop.assert_not_called()
            finally:server.shutdown();server.server_close();thread.join()

    def test_zombie_worker_is_not_reported_as_alive(self):
        with patch.object(dashboard.subprocess,'run',return_value=Mock(stdout='Z')):
            self.assertFalse(dashboard._pid_alive(123))
        with patch.object(dashboard.subprocess,'run',return_value=Mock(stdout='S')):
            self.assertTrue(dashboard._pid_alive(123))


class WorkerIdentityTests(unittest.TestCase):
    def test_reused_pid_or_other_queue_does_not_count_as_this_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);run={'pid':123}
            for command in ['/System/Library/ColorSyncXPCAgent',
                            f'/usr/bin/python /repo/run.py --output {output}-other',
                            f'/usr/bin/python /repo/crawl_county_taxes.py --output {output}', '']:
                with self.subTest(command=command),patch.object(dashboard,'process_info',return_value=command):
                    self.assertFalse(dashboard._worker_alive(run,'zillow',output))
            with patch.object(dashboard,'process_info',return_value=f'/usr/bin/python /repo/run.py --output {output}'):
                self.assertTrue(dashboard._worker_alive(run,'zillow',output))
            with patch.object(dashboard,'process_info',return_value=f'/usr/bin/caffeinate -i python /repo/crawl_county_taxes.py --output {output}'):
                self.assertTrue(dashboard._worker_alive(run,'tax',output))

    def test_reused_pid_reports_failed_and_keeps_stop_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);database=output/'zillow.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.executescript((Path(__file__).parent/'schema.sql').read_text())
            (output/'active-run.json').write_text(json.dumps({'pid':123,'status':'stopped','exit_code':2,
                'started_at':'2026-10-23T23:06:31+00:00','stopped_at':'2026-10-23T23:43:00+00:00',
                'launcher_command':['python','/repo/run.py']}))
            reason='More than 10 consecutive failures; queue retained'
            (output/'zillow-progress.json').write_text(json.dumps({'consecutive_failures':11,'stop_reason':reason}))
            with patch.object(dashboard,'process_info',return_value='/System/Library/ColorSyncXPCAgent'):
                state=dashboard.restart_progress_data(database)['zillow']
            self.assertFalse(state['active']);self.assertFalse(state['can_stop'])
            self.assertTrue(state['can_restart']);self.assertEqual(state['phase'],'failed')
            self.assertEqual(state['error'],reason)
