from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import restart_status as startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name);self.db=self.output/'zillow.sqlite3'
        self.started=datetime.now(timezone.utc)-timedelta(minutes=1)
        self.run={'pid':100,'started_at':self.started.isoformat(),'stopped_at':(self.started+timedelta(seconds=30)).isoformat(),'exit_code':1}
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('''CREATE TABLE captures(id INTEGER PRIMARY KEY,captured_at TEXT,final_url TEXT);
                CREATE TABLE crawl_errors(id INTEGER PRIMARY KEY,occurred_at TEXT,error TEXT);
                CREATE TABLE county_tax_captures(id INTEGER PRIMARY KEY,captured_at TEXT,url TEXT);
                CREATE TABLE county_tax_errors(id INTEGER PRIMARY KEY,occurred_at TEXT,error TEXT);''')
            db.execute('INSERT INTO captures VALUES(1,?,?)',((self.started-timedelta(hours=1)).isoformat(),'https://www.zillow.com/old'))
            db.execute('INSERT INTO crawl_errors VALUES(1,?,?)',((self.started-timedelta(hours=1)).isoformat(),'older HTTP 403'))
            db.commit()

    def describe(self,worker='zillow',active=False,run=None):
        return startup.describe(self.output,worker,self.db,run or self.run,{},active,active)

    def append(self,name,text):
        with (self.output/name).open('a') as stream:stream.write(text+'\n')

    def test_legacy_failed_attempt_exposes_exact_final_exception_and_exit_code(self):
        self.append('zillow-crawl.log','Traceback (most recent call last):\n  File "/local/script.py", line 1\nRuntimeError: Saved REL group is missing; refusing to switch groups')
        status=self.describe()
        self.assertEqual(status['phase'],'failed');self.assertEqual(status['exit_code'],1)
        self.assertEqual(status['error'],'RuntimeError: Saved REL group is missing; refusing to switch groups')
        self.assertEqual(status['elapsed_seconds'],30)
        self.assertEqual(status['saved_pages'],0)
        self.assertEqual(self.describe()['error'],status['error'])

    def test_restart_offsets_exclude_old_traceback_and_measure_actual_captures(self):
        self.append('zillow-crawl.log','RuntimeError: old attempt')
        token=startup.begin(self.output,'zillow',self.db)
        startup.result(self.output,'zillow',token,stage='launched',pid=200)
        state=startup.read(startup.attempt_path(self.output,'zillow'))
        run={'pid':200,'started_at':state['started_at']}
        self.append('zillow-runner.log','Running Zillow crawl through Release REL.app; resuming saved progress.')
        status=self.describe(active=True,run=run)
        self.assertEqual(status['phase'],'session');self.assertIsNone(status['error'])
        self.append('zillow-crawl.log','Applied saved geographic-area priority to 43210 pending inventory requests')
        status=self.describe(active=True,run=run)
        self.assertEqual(status['phase'],'ready');self.assertEqual(status['queue_total'],43210)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('INSERT INTO captures VALUES(2,?,?)',(datetime.now(timezone.utc).isoformat(),'https://www.zillow.com/new'));db.commit()
        status=self.describe(active=True,run=run)
        self.assertEqual(status['phase'],'running');self.assertEqual(status['saved_pages'],1)
        self.assertEqual(status['latest_url'],'https://www.zillow.com/new')

    def test_validation_failure_survives_reload_and_has_no_stale_pid_or_exit_code(self):
        token=startup.begin(self.output,'zillow',self.db)
        startup.result(self.output,'zillow',token,stage='failed',error='No saved crawler command',finished_at=datetime.now(timezone.utc).isoformat())
        status=self.describe()
        self.assertEqual(status['error'],'No saved crawler command')
        self.assertIsNone(status['pid']);self.assertIsNone(status['exit_code'])
        self.assertEqual(status['phase'],'failed')

    def test_resource_errors_are_scoped_to_this_attempt(self):
        self.append('zillow-crawl.log','Using persistent REL session: Session7')
        status=self.describe(active=True)
        self.assertIsNone(status['error'])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('INSERT INTO crawl_errors VALUES(2,?,?)',(datetime.now(timezone.utc).isoformat(),'HTTP 403 blocked'));db.commit()
        self.assertEqual(self.describe()['error'],'HTTP 403 blocked')
        self.assertEqual(self.describe(active=True)['phase'],'ready')

    def test_new_cli_start_ignores_old_dashboard_attempt(self):
        token=startup.begin(self.output,'zillow',self.db)
        startup.result(self.output,'zillow',token,stage='failed',error='Old restart failed')
        run={'pid':300,'started_at':(datetime.now(timezone.utc)+timedelta(seconds=3)).isoformat()}
        status=self.describe(active=True,run=run)
        self.assertIsNone(status['attempt_id']);self.assertIsNone(status['error'])
        self.assertEqual(status['pid'],300)

    def test_log_output_is_bounded_and_control_characters_removed(self):
        token=startup.begin(self.output,'tax',self.db)
        self.append('county-tax.log','\x1b[31mRuntimeError: bad\x00message\x1b[0m\n'*1000)
        status=self.describe('tax')
        self.assertLessEqual(len(status['log_lines']),12)
        self.assertTrue(all('\x1b' not in line and '\x00' not in line for line in status['log_lines']))
        self.assertLess(len(json.dumps(status)),40000)

    def test_rotated_log_is_read_from_new_inode(self):
        self.append('zillow-crawl.log','RuntimeError: old')
        token=startup.begin(self.output,'zillow',self.db)
        (self.output/'zillow-crawl.log').rename(self.output/'old.log')
        self.append('zillow-crawl.log','RuntimeError: new startup error')
        status=self.describe()
        self.assertEqual(status['error'],'RuntimeError: new startup error')

    def test_old_results_cannot_overwrite_new_attempt(self):
        old=startup.begin(self.output,'zillow',self.db);new=startup.begin(self.output,'zillow',self.db)
        startup.result(self.output,'zillow',old,error='stale response')
        state=startup.read(startup.attempt_path(self.output,'zillow'))
        self.assertEqual(state['id'],new);self.assertNotIn('error',state)


    def test_same_launcher_pid_keeps_attempt_during_slow_rel_setup(self):
        token=startup.begin(self.output,'zillow',self.db)
        startup.result(self.output,'zillow',token,stage='launched',pid=200)
        original=startup.read(startup.attempt_path(self.output,'zillow'))['started_at']
        run={'pid':200,'started_at':(datetime.fromisoformat(original)+timedelta(seconds=30)).isoformat()}
        status=self.describe(active=True,run=run)
        self.assertEqual(status['attempt_id'],token)
        self.assertEqual(status['started_at'],original)
