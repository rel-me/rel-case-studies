import fcntl
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from terminal_dashboard import Monitor, LogTail, clean, Event, locked, matching


class TerminalDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name);self.path=self.output/'zillow.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''CREATE TABLE captures(id INTEGER PRIMARY KEY,captured_at TEXT,final_url TEXT);
                CREATE TABLE crawl_errors(id INTEGER PRIMARY KEY,occurred_at TEXT,url TEXT,error TEXT);
                CREATE TABLE county_tax_captures(id INTEGER PRIMARY KEY,captured_at TEXT,url TEXT);
                CREATE TABLE county_tax_errors(id INTEGER PRIMARY KEY,occurred_at TEXT,apn TEXT,error TEXT);
                CREATE TABLE inventory_addresses(status TEXT);
                CREATE TABLE county_tax_targets(status TEXT);''')
            db.execute("INSERT INTO captures VALUES(1,'2026-10-21T12:00:00Z','https://www.zillow.com/home/1')")
            db.execute("INSERT INTO crawl_errors VALUES(1,'2026-10-21T12:01:00Z','https://www.zillow.com/home/2',?)",('line 1\nline 2\x1b[31m RED',))
            db.execute("INSERT INTO county_tax_captures VALUES(1,'2026-10-21T12:02:00Z','https://county/detail/1')")
            db.execute("INSERT INTO county_tax_errors VALUES(1,'2026-10-21T12:03:00Z','12345678','one\ntwo')")
            db.executemany('INSERT INTO inventory_addresses VALUES(?)',[('pending',),('resolving',),('matched',),('failed',)])
            db.executemany('INSERT INTO county_tax_targets VALUES(?)',[('history_complete',),('complete',),('failed',)])
            db.commit()

    def test_initial_feed_and_status_are_read_only_and_include_both_crawlers(self):
        before=self.path.read_bytes();monitor=Monitor(self.output,self.path)
        events=monitor.poll()
        self.assertEqual([(e.worker,e.kind) for e in events],[('ZILLOW','SAVED'),('ZILLOW','ERROR'),('TAX','SAVED'),('TAX','ERROR')])
        self.assertTrue(all('\n' not in e.line() and '\x1b' not in e.line() for e in events))
        self.assertIn('12345678',events[-1].detail)
        self.assertEqual(monitor.stats['ZILLOW']['pending'],2)
        self.assertEqual(monitor.stats['TAX']['pending'],1)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(monitor.poll(),[])

    def test_batch_limit_drains_without_skipping_saved_urls(self):
        monitor=Monitor(self.output,self.path,history=0);self.assertEqual(monitor.poll(),[])
        with closing(sqlite3.connect(self.path)) as db:
            db.executemany('INSERT INTO captures VALUES(?,?,?)',[(i,'2026-10-21T13:00:00Z',f'https://www.zillow.com/{i}') for i in range(2,1004)])
            db.commit()
        first=monitor.poll();second=monitor.poll()
        self.assertEqual((len(first),len(second)),(1000,2))
        self.assertEqual(len({e.detail for e in first+second}),1002)
        self.assertEqual(monitor.poll(),[])

    def test_missing_database_is_not_created(self):
        missing=self.output/'missing.sqlite3';monitor=Monitor(self.output,missing)
        monitor.poll();self.assertFalse(missing.exists());self.assertIn('unavailable',monitor.warning)

    def test_startup_traceback_is_one_line_and_partial_lines_are_retained(self):
        path=self.output/'zillow-runner.log';path.write_text('Traceback (most recent call last):\n  File "x", line 1\nRuntimeError: failed\n')
        tail=LogTail(path,'ZILLOW');events=tail.poll()
        self.assertEqual(len(events),1);self.assertIn('RuntimeError: failed',events[0].detail)
        self.assertEqual(events[0].at,'');self.assertEqual(tail.poll(),[])
        with path.open('a') as log:log.write('ValueError: par')
        self.assertEqual(tail.poll(),[])
        with path.open('a') as log:log.write('tial\n')
        self.assertEqual(len(tail.poll()),1)
        path.rename(self.output/'old.log');path.write_text('ERROR new file\n')
        self.assertEqual(len(tail.poll()),1)
        path.write_text('ERROR x\n');self.assertEqual(len(tail.poll()),1)

    def test_lock_and_stop_reasons_are_independent(self):
        monitor=Monitor(self.output,self.path)
        (self.output/'zillow-progress.json').write_text('{"stop_reason":"HTTP 403","consecutive_failures":1}')
        with (self.output/'zillow-run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertTrue(locked(self.output/'zillow-run.lock'))
            self.assertEqual(monitor.status('ZILLOW')[0],'RUNNING')
            self.assertEqual(monitor.status('TAX')[0],'STOPPED')
        self.assertEqual(monitor.status('ZILLOW'),('STOPPED',1,'HTTP 403'))

    def test_filters_and_terminal_controls(self):
        event=Event('','ZILLOW','ERROR','bad\r\n\x1b[2J\x07string')
        self.assertTrue(matching(event,'ZILLOW','ERROR'))
        self.assertFalse(matching(event,'TAX','ALL'))
        self.assertFalse(matching(event,'ALL','SAVED'))
        self.assertEqual(clean('\x1b[31m one\n two\t\x00'),'one two')
        self.assertNotIn('\x1b',event.line())
