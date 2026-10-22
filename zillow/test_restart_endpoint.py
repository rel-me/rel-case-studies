from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.request import urlopen
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

    def test_zombie_worker_is_not_reported_as_alive(self):
        with patch.object(dashboard.subprocess,'run',return_value=Mock(stdout='Z')):
            self.assertFalse(dashboard._pid_alive(123))
        with patch.object(dashboard.subprocess,'run',return_value=Mock(stdout='S')):
            self.assertTrue(dashboard._pid_alive(123))
