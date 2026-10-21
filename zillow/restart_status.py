"""Bounded, private startup diagnostics; separate from crawler safety checkpoints."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
LOGS={'zillow':('zillow-runner.log','zillow-crawl.log'),'tax':('county-tax.log',)}


def clean(value):
    return ' '.join(ANSI.sub('',str(value or '')).replace('\x00','').split())[:2500]


def read(path):
    try:
        state=json.loads(path.read_text())
        return state if isinstance(state,dict) else {}
    except (OSError,ValueError): return {}


def write(path,state):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try: temp.write_text(json.dumps(state,indent=2)+'\n');temp.replace(path)
    finally: temp.unlink(missing_ok=True)


def seconds(value):
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,AttributeError): return 0


def attempt_path(output,worker): return output/(worker+'-restart-status.json')


def capture_data(database,worker,baseline=0,started_at=None):
    table='captures' if worker=='zillow' else 'county_tax_captures'
    url='final_url' if worker=='zillow' else 'url'
    error_table='crawl_errors' if worker=='zillow' else 'county_tax_errors'
    empty={'latest_id':0,'saved':0,'latest_url':None,'latest_capture':None,'resource_error':None}
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.3)) as db:
            maximum=db.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0]
            if started_at:
                where='id>?' if baseline is not None else 'captured_at>=?'
                value=baseline if baseline is not None else started_at
                saved=db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}',(value,)).fetchone()[0]
                last=db.execute(f'SELECT {url},captured_at FROM {table} WHERE {where} ORDER BY id DESC LIMIT 1',(value,)).fetchone()
                error=db.execute(f'SELECT error FROM {error_table} WHERE occurred_at>=? ORDER BY id DESC LIMIT 1',(started_at,)).fetchone()
            else: saved=0;last=error=None
            return dict(latest_id=maximum,saved=saved,latest_url=last[0] if last else None,latest_capture=last[1] if last else None,resource_error=clean(error[0]) if error else None)
    except (OSError,sqlite3.Error): return empty


def begin(output,worker,database):
    state={'id':uuid.uuid4().hex,'started_at':datetime.now(timezone.utc).isoformat(),
           'stage':'checking','message':'Checking saved command, profile, queue and worker lock.',
           'baseline':capture_data(database,worker)['latest_id'],'logs':{}}
    for name in LOGS[worker]:
        try:
            stat=(output/name).stat();state['logs'][name]={'offset':stat.st_size,'inode':stat.st_ino}
        except OSError: state['logs'][name]={'offset':0,'inode':None}
    write(attempt_path(output,worker),state)
    return state['id']


def result(output,worker,attempt_id,**fields):
    path=attempt_path(output,worker);state=read(path)
    if state.get('id')==attempt_id: state.update(fields);write(path,state)


def log_lines(output,worker,attempt,start):
    lines=[]
    for name in LOGS[worker]:
        try:
            with (output/name).open('rb') as stream:
                stat=(output/name).stat()
                if stat.st_mtime<seconds(start): continue
                checkpoint=attempt.get('logs',{}).get(name,{})
                offset=checkpoint.get('offset',0) if checkpoint.get('inode')==stat.st_ino else 0
                if stat.st_size<offset: offset=0
                stream.seek(max(offset,stat.st_size-131072))
                data=stream.read(131072).decode('utf-8','replace')
                # Legacy run JSON has no offsets. The final exception is retained;
                # older whole-run output is bounded and never returned wholesale.
                for line in data.splitlines():
                    line=clean(line)
                    if line: lines.append(line)
        except OSError: pass
    return lines


def describe(output,worker,database,run,progress,alive,queue_locked):
    attempt=read(attempt_path(output,worker))
    # CLI/terminal restarts also get diagnostics, without inheriting old sidecars.
    if run.get('pid')!=attempt.get('pid') and seconds(run.get('started_at'))>seconds(attempt.get('started_at'))+2: attempt={}
    started=attempt.get('started_at') or run.get('started_at')
    if not started: return {'available':False}
    captures=capture_data(database,worker,attempt.get('baseline'),started)
    lines=log_lines(output,worker,attempt,started)
    errors=[line for line in lines if re.search(r'^(?:[\w.]+(?:Error|Exception)|Cannot start crawl):',line)]
    error=attempt.get('error') or progress.get('stop_reason') or captures['resource_error'] or (errors[-1] if errors else None)
    active=alive or queue_locked
    same_run=not attempt or attempt.get('pid')==run.get('pid')
    code=run.get('exit_code') if not active and same_run else None
    # Recoverable resource errors remain useful diagnostics after a clean exit,
    # but are not the final reason that a completed/interrupted worker stopped.
    explicit_failure=attempt.get('error') or progress.get('stop_reason')
    failed=not active and bool(explicit_failure or code not in (None,0,130)
                              or (code is None and (error or not captures['saved'])))
    stage='launching';message='Starting saved launcher and waiting for REL readiness.'
    queue_total=None
    if any('Running Zillow crawl through' in line for line in lines):
        stage='session';message='REL is ready. Restoring the configured browser session.'
    if any('county_inventory' in line for line in lines):
        stage='queue';message='Loading county inventory and applying the saved queue priority. This can take a minute.'
    for line in lines:
        match=re.search(r'Applied saved geographic-area priority to (\d+)|"prioritized_pending":\s*(\d+)',line)
        if match: queue_total=int(match[1] or match[2]);stage='ready';message=f'Queue restored: {queue_total:,} pending requests prioritized. Waiting for the first saved page.'
    if any('Crawled ' in line or 'Using persistent REL session:' in line for line in lines):
        stage='ready';message='Worker is processing the queue. Waiting for the first saved page.'
    if captures['saved'] and active: stage='running';message=f"Capturing pages. {captures['saved']:,} pages saved since this start."
    if not active:
        stage='failed' if failed else 'stopped'
        message='Restart failed.' if failed else 'Worker stopped.'
        if not error and failed: error=f'Worker exited with code {code} before saving a page.' if code is not None else 'Worker is no longer running; no page was saved. See the startup log.'
    if attempt.get('stage')=='checking' and not active and not error and seconds(started)>datetime.now().timestamp()-10:
        stage='checking';message=attempt['message'];failed=False;error=None
    finished=attempt.get('finished_at') or run.get('stopped_at')
    last_log=0
    for name in LOGS[worker]:
        try: last_log=max(last_log,(output/name).stat().st_mtime)
        except OSError: pass
    end=datetime.now(timezone.utc).timestamp() if active or stage=='checking' else seconds(finished) or max(seconds(started),last_log)
    useful=[line for line in lines if re.search(r'Error:|Exception:|ERROR|Cannot start|Using persistent|Running Zillow|Applied saved|queue_priority|county_inventory|search_form|Addresses verified|Crawled \d',line)]
    return {'available':True,'attempt_id':attempt.get('id'),'phase':stage,'message':message,
            'active':active,'started_at':started,'elapsed_seconds':max(0,int(end-seconds(started))),
            'pid':attempt.get('pid') if attempt else run.get('pid'),'exit_code':code,
            'saved_pages':captures['saved'],'latest_url':captures['latest_url'],
            'last_capture':captures['latest_capture'],'queue_total':queue_total,
            'error':clean(error) if error else None,'log_lines':useful[-12:] or lines[-8:]}
