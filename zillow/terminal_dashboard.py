#!/usr/bin/env python3
"""Read-only terminal dashboard for the Zillow and county-tax crawlers (stdlib only)."""
import argparse
from collections import deque
from contextlib import closing
import curses
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
SOURCES = {
    'captures': ('ZILLOW', 'SAVED', 'captured_at', 'final_url', "''"),
    'crawl_errors': ('ZILLOW', 'ERROR', 'occurred_at', 'url', 'error'),
    'county_tax_captures': ('TAX', 'SAVED', 'captured_at', 'url', "''"),
    'county_tax_errors': ('TAX', 'ERROR', 'occurred_at', "'https://ttc.co.santa-cruz.ca.us/TaxBills/'", "'parcel ' || COALESCE(apn,'unknown') || ': ' || COALESCE(error,'')"),
}


def clean(value):
    """Never allow captured text to insert terminal controls or extra lines."""
    text = ANSI.sub('', str(value or ''))
    return ' '.join(display_text(text).split())


def display_text(value):
    return ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in ANSI.sub('',str(value or '')))


def stamp(value):
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone().strftime('%m-%d %H:%M:%S')
    except (ValueError,AttributeError): return clean(value)[:14] or '-- --:--:--'


@dataclass(frozen=True)
class Event:
    at: str
    worker: str
    kind: str
    detail: str

    def line(self):
        return f'{stamp(self.at):14} {self.worker:6} {self.kind:5} {clean(self.detail)}'


def read_json(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return {}


def locked(path):
    try:
        with path.open('r') as stream:
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return True
            fcntl.flock(stream, fcntl.LOCK_UN)
    except OSError: pass
    return False


def process_running(pid, script):
    try:
        result = subprocess.run(['ps','-p',str(int(pid)),'-o','stat=,command='],capture_output=True,text=True,timeout=1)
        status, _, command = result.stdout.strip().partition(' ')
        return bool(status and not status.startswith('Z') and script in command)
    except (ValueError, TypeError, OSError, subprocess.SubprocessError): return False


class LogTail:
    """Read appends without replaying unchanged tracebacks, even after truncation/rotation."""
    def __init__(self, path, worker, history_bytes=65536):
        self.path, self.worker = path, worker
        self.identity = None
        self.offset = 0
        self.partial = b''
        self.history_bytes = history_bytes

    def poll(self):
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno()); identity = (stat.st_dev, stat.st_ino)
                initial = self.identity is None
                if self.identity is None:
                    self.offset = max(0, stat.st_size-self.history_bytes)
                    if self.offset:
                        stream.seek(self.offset); stream.readline(); self.offset=stream.tell()
                elif identity != self.identity or stat.st_size < self.offset:
                    self.offset = 0; self.partial=b''
                self.identity=identity
                stream.seek(self.offset); data=stream.read(262144); self.offset=stream.tell()
        except OSError: return []
        lines=(self.partial+data).split(b'\n'); self.partial=lines.pop()
        # Protect memory from pathological unfinished log lines.
        self.partial=self.partial[-262144:]
        events=[]
        for raw in lines:
            text=clean(raw.decode('utf-8','replace'))
            # Resource errors are also in SQLite; LOG marks unstructured diagnostics,
            # including startup exceptions which never reached a resource handler.
            if re.search(r'\b(?:ERROR|CRITICAL|FATAL)\b|^(?:[\w.]+(?:Error|Exception)|Cannot start crawl):',text):
                events.append(Event('' if initial else datetime.now().astimezone().isoformat(),self.worker,'ERROR',f'[log:{self.path.name}] {text}'))
        return events


class Monitor:
    def __init__(self, output, database, history=60):
        self.output, self.database, self.history = output, database, history
        self.cursors = {}
        self.events = deque(maxlen=5000)
        self.stats = {}
        self.warning = ''
        self.worker_statuses = {}
        self.tails = [LogTail(output/name,worker) for name,worker in (
            ('zillow-crawl.log','ZILLOW'),('zillow-runner.log','ZILLOW'),('county-tax.log','TAX'))]

    def poll(self):
        incoming=[]
        try:
            with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.25)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,(worker,kind,stamp,url,message) in SOURCES.items():
                    if table not in tables: continue
                    if table not in self.cursors:
                        rows=db.execute(f'SELECT id,{stamp},{url},{message} FROM {table} ORDER BY id DESC LIMIT ?', (self.history,)).fetchall()[::-1]
                        self.cursors[table]=db.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0]
                    else:
                        rows=db.execute(f'SELECT id,{stamp},{url},{message} FROM {table} WHERE id>? ORDER BY id LIMIT 1000',(self.cursors[table],)).fetchall()
                        if rows: self.cursors[table]=rows[-1][0]
                    incoming.extend(Event(at or '',worker,kind,' | '.join(clean(x) for x in (url_value,detail) if x)) for _,at,url_value,detail in rows)
                for worker,table in [('ZILLOW','inventory_addresses'),('TAX','county_tax_targets')]:
                    counts=dict(db.execute(f'SELECT status,count(*) FROM {table} GROUP BY status')) if table in tables else {}
                    capture_table='captures' if worker=='ZILLOW' else 'county_tax_captures'
                    error_table='crawl_errors' if worker=='ZILLOW' else 'county_tax_errors'
                    capture=db.execute(f'SELECT COUNT(*),MAX(captured_at) FROM {capture_table}').fetchone() if capture_table in tables else (0,None)
                    errors=db.execute(f'SELECT COUNT(*) FROM {error_table}').fetchone()[0] if error_table in tables else 0
                    self.stats[worker]={'pending':sum(counts.get(s,0) for s in ('pending','resolving','running','history_complete')),
                        'done':counts.get('matched' if worker=='ZILLOW' else 'complete',0),
                        'captures':capture[0],'last':capture[1],'errors':errors}
            self.warning=''
        except (sqlite3.Error, OSError) as error:
            self.warning='Database unavailable: '+clean(error)
        for tail in self.tails: incoming.extend(tail.poll())
        # Historical startup traces sort with the initial feed. Live events remain
        # append-only, so late records and drained batches never displace unseen rows.
        incoming.sort(key=lambda event:event.at)
        self.events.extend(incoming)
        self.worker_statuses={who:self.status(who) for who in ('ZILLOW','TAX')}
        return incoming

    def status(self,worker):
        tax=worker=='TAX'
        run=read_json(self.output/('county-run.json' if tax else 'active-run.json'))
        progress=read_json(self.output/('county-progress.json' if tax else 'zillow-progress.json'))
        running=locked(self.output/('run.lock' if tax else 'zillow-run.lock'))
        if tax: running=running and process_running(run.get('pid'),'crawl_county_taxes.py')
        state='RUNNING' if running else 'MAINTENANCE' if run.get('maintenance') else 'STOPPED'
        if not running and run.get('status')=='starting' and process_running(run.get('pid'),'crawl_county_taxes.py' if tax else 'run.py'): state='STARTING'
        return state, progress.get('consecutive_failures',0), clean(progress.get('stop_reason') or run.get('stop_reason') or '')


def matching(event,worker,kind):
    return (worker=='ALL' or event.worker==worker) and (kind=='ALL' or event.kind==kind)


def draw(screen, monitor, worker, kind, paused, offset, horizontal):
    screen.erase(); height,width=screen.getmaxyx()
    def put(y,text,attr=0):
        if y>=height or width<2: return
        try: screen.addnstr(y,0,display_text(text),width-1,attr)
        except curses.error: pass
    put(0,'SANTA CRUZ CRAWLS  |  READ ONLY  |  '+datetime.now().astimezone().strftime('%H:%M:%S %Z'),curses.A_BOLD)
    for row,who in enumerate(('ZILLOW','TAX'),1):
        state,streak,reason=monitor.worker_statuses.get(who,('UNKNOWN',0,'')); s=monitor.stats.get(who,{})
        put(row*2-1,f"{who:6} {state:11}  done {s.get('done',0):,}  left {s.get('pending',0):,}  saved {s.get('captures',0):,}  errors {s.get('errors',0):,}  streak {streak}",curses.A_BOLD)
        put(row*2,f"Last save: {stamp(s['last']) if s.get('last') else 'none'}"+(f'  STOP: {reason}' if reason else ''))
    filtered=[event for event in monitor.events if matching(event,worker,kind)]
    feed_height=max(0,height-9); offset=min(offset,max(0,len(filtered)-feed_height))
    end=len(filtered)-offset; start=max(0,end-feed_height)
    put(5,f"FEED {worker}/{kind}  {'PAUSED' if paused else 'LIVE'}  scroll {offset}  horizontal {horizontal}  buffered {len(filtered):,}/5,000",curses.A_REVERSE)
    for y,event in enumerate(filtered[start:end],6):
        attr=curses.color_pair(1) if event.kind=='ERROR' and curses.has_colors() else 0
        put(y,event.line()[horizontal:],attr)
    put(height-3,monitor.warning or 'Long lines are clipped, never wrapped. Left/right scroll; --plain prints full lines.')
    put(height-2,'q quit | space pause | 1 all / 2 Zillow / 3 tax | e errors / s saves / a both')
    put(height-1,'Up/PgUp older | Down/PgDn newer | End live | Left/Right horizontal scroll')
    screen.refresh()
    return offset


def app(screen,monitor,interval):
    try: curses.curs_set(0)
    except curses.error: pass
    screen.keypad(True);screen.timeout(100)
    if curses.has_colors():
        curses.start_color();curses.use_default_colors();curses.init_pair(1,curses.COLOR_RED,-1)
    worker=kind='ALL';paused=False;offset=horizontal=0;due=0
    while True:
        if time.monotonic()>=due:
            incoming=monitor.poll()
            if offset and not paused: offset+=sum(matching(event,worker,kind) for event in incoming)
            due=time.monotonic()+interval
        if not paused: offset=draw(screen,monitor,worker,kind,False,offset,horizontal)
        key=screen.getch()
        if key in (ord('q'),27): return
        if key==ord(' '):
            paused=not paused
            if paused: draw(screen,monitor,worker,kind,True,offset,horizontal)
        elif key in (ord('1'),ord('2'),ord('3')): worker={ord('1'):'ALL',ord('2'):'ZILLOW',ord('3'):'TAX'}[key];offset=0;paused=False
        elif key in (ord('e'),ord('s'),ord('a')): kind={ord('e'):'ERROR',ord('s'):'SAVED',ord('a'):'ALL'}[key];offset=0;paused=False
        elif key in (curses.KEY_UP,curses.KEY_PPAGE): offset+=1 if key==curses.KEY_UP else max(1,screen.getmaxyx()[0]-9)
        elif key in (curses.KEY_DOWN,curses.KEY_NPAGE): offset=max(0,offset-(1 if key==curses.KEY_DOWN else max(1,screen.getmaxyx()[0]-9)))
        elif key==curses.KEY_END: offset=0;paused=False
        elif key==curses.KEY_RIGHT: horizontal+=20
        elif key==curses.KEY_LEFT: horizontal=max(0,horizontal-20)
        elif key==curses.KEY_RESIZE: paused=False
        # Pausing freezes rendering only. Polling continues to retain live events.


def default_output():
    local=Path(__file__).resolve().parent/'output'
    existing=Path.home()/'projects.next/rel-case-studies/zillow/output'
    return Path(os.environ['CRAWL_OUTPUT']) if os.environ.get('CRAWL_OUTPUT') else local if (local/'zillow.sqlite3').is_file() else existing if (existing/'zillow.sqlite3').is_file() else local


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=default_output())
    parser.add_argument('--database',type=Path,help='Defaults to OUTPUT/zillow.sqlite3; opened read-only')
    parser.add_argument('--interval',type=float,default=2,help='Poll interval in seconds (default 2)')
    parser.add_argument('--history',type=int,default=60,help='Initial recent records per event table (default 60)')
    parser.add_argument('--plain',action='store_true',help='Full one-line events suitable for piping to a log')
    parser.add_argument('--once',action='store_true',help='Print statuses and recent events, then exit')
    args=parser.parse_args(argv)
    if args.interval<0.2 or args.history<0 or args.history>5000: parser.error('interval must be >=0.2 and history must be between 0 and 5000')
    monitor=Monitor(args.output,args.database or args.output/'zillow.sqlite3',args.history)
    try:
        if args.plain or args.once or not sys.stdout.isatty() or not sys.stdin.isatty():
            first=True
            while True:
                events=monitor.poll()
                if first:
                    for worker in ('ZILLOW','TAX'):
                        status,streak,reason=monitor.status(worker)
                        print(f'# {worker} {status} streak={streak} {reason}',flush=True)
                    first=False
                for event in events: print(event.line(),flush=True)
                if monitor.warning: print('# '+monitor.warning,file=sys.stderr,flush=True)
                if args.once: return 1 if monitor.warning else 0
                time.sleep(args.interval)
        else: curses.wrapper(app,monitor,args.interval)
    except (KeyboardInterrupt,BrokenPipeError): return 0
    except curses.error as error:
        print('Terminal unavailable; run with --plain: '+clean(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
