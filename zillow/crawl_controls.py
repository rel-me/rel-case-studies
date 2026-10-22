"""Start or gracefully restart saved crawlers without changing their safety checkpoints."""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import time
import threading
import uuid


_children=[]
_children_lock=threading.Lock()


def reap_children():
    with _children_lock:
        _children[:]=[process for process in _children if process.poll() is None]


class ControlError(RuntimeError):
    pass


WORKERS = {
    'ZILLOW': ('active-run.json','zillow-run.lock','zillow-progress.json','run.py','Zillow','zillow-runner.log'),
    'TAX': ('county-run.json','run.lock','county-progress.json','crawl_county_taxes.py','Datacenter','county-tax.log'),
}


def read_state(path):
    try:
        state=json.loads(path.read_text())
        if not isinstance(state,dict): raise ValueError('Expected JSON object')
        return state
    except (OSError,ValueError) as error:
        raise ControlError(f'Cannot read {path.name}: {error}') from error


def write_state(path,state):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(state,indent=2)+'\n');temporary.replace(path)
    finally: temporary.unlink(missing_ok=True)


def flag(command,name):
    if command.count(name)!=1: raise ControlError(f'Saved command must include exactly one {name}')
    index=command.index(name)
    if index+1>=len(command): raise ControlError(f'Saved command is missing the value for {name}')
    return command[index+1]


def queue_busy(path):
    with path.open('a') as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return True
        fcntl.flock(stream,fcntl.LOCK_UN)
    return False


def process_info(pid):
    try:
        pid=int(pid)
        if pid<=1: return ''
        reply=subprocess.run(['ps','-p',str(pid),'-o','stat=,command='],capture_output=True,text=True,timeout=2)
        stat,_,command=reply.stdout.strip().partition(' ')
        return command.strip() if stat and not stat.startswith('Z') else ''
    except (TypeError,ValueError,OSError,subprocess.SubprocessError): return ''


def owns_process(command,script,output):
    match=re.search(r'(?:^|\s)--output\s+(.+?)(?=\s+--|$)',command)
    return bool(re.search(r'(?:^|\s)\S*/'+re.escape(script)+r'(?:\s|$)',command)
                and match and Path(match[1].strip().strip("\"'" )).resolve()==Path(output).resolve())


def interrupt_worker(pid,script,output):
    """Interrupt only the verified worker. Zillow's launcher must outlive its child."""
    actual=process_info(pid)
    if not owns_process(actual,script,output): raise ControlError('Worker identity changed; refusing to signal this PID')
    targets=[int(pid)]
    if script=='run.py':
        reply=subprocess.run(['ps','-axo','pid=,ppid=,command='],capture_output=True,text=True,timeout=2)
        children=[]
        for line in reply.stdout.splitlines():
            parts=line.strip().split(None,2)
            if len(parts)==3 and parts[1]==str(pid) and owns_process(parts[2],'zillow.py',output): children.append(int(parts[0]))
        if children: targets=children
    for target in targets:
        expected='zillow.py' if target!=int(pid) else script
        if not owns_process(process_info(target),expected,output): raise ControlError('Worker identity changed before interrupt')
        try: os.kill(target,signal.SIGINT)
        except ProcessLookupError: pass


def pending_work(database,worker):
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        query=("SELECT COUNT(*) FROM inventory_addresses WHERE status IN ('pending','resolving') AND lookup_url IS NOT NULL AND address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential')" if worker=='ZILLOW' else
               "SELECT COUNT(*) FROM county_tax_targets WHERE status IN ('pending','running','history_complete')")
        return db.execute(query).fetchone()[0]


def check_safety(run,progress):
    if run.get('maintenance'): raise ControlError('Maintenance: '+str(run['maintenance']))
    reason=progress.get('stop_reason') or run.get('stop_reason')
    if reason: raise ControlError('Saved safety stop: '+str(reason)+'; controls do not clear it')
    for state in (run,progress):
        if int(state.get('consecutive_failures',0) or 0)>10:
            raise ControlError('Eleven-failure safety stop; explicit review required')


def control_worker(output,database,worker,timeout=45):
    """Start stopped work or gracefully restart active work. Never retry terminal items."""
    reap_children()
    output=Path(output).resolve();database=Path(database).resolve()
    if worker not in WORKERS: raise ControlError('Unknown crawler')
    run_name,lock_name,progress_name,script,profile,log_name=WORKERS[worker]
    run_path=output/run_name;progress_path=output/progress_name;lock_path=output/lock_name
    with (output/'dashboard-restart.lock').open('a') as guard:
        try: fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ControlError('Another crawl control action is in progress') from None
        run=read_state(run_path);progress=read_state(progress_path)
        check_safety(run,progress)
        command=run.get('launcher_command') if worker=='ZILLOW' else run.get('command')
        if not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command): raise ControlError('Missing saved launcher command')
        if not any(Path(x).name==script and Path(x).is_absolute() and Path(x).is_file() for x in command): raise ControlError('Saved command does not use the required crawler launcher')
        if flag(command,'--profile')!=profile or Path(flag(command,'--output')).resolve()!=output: raise ControlError('Saved command targets a different profile or output queue')
        if worker=='ZILLOW':
            if '--skip-failed' not in command or flag(command,'--attempts')!='1': raise ControlError('Saved Zillow command must skip terminal failures and use one attempt')
        if flag(command,'--max-consecutive-failures')!='10': raise ControlError('Saved command must preserve the eleven-failure stop')
        if any(option in command for option in ('--retry-failed','--rotate-session','--repair-format-parcel')): raise ControlError('Terminal retries and session rotation are not allowed from these controls')
        # Current saved priority and inventory arguments must remain intact.
        for option in ('--priority-file','--county-inventory'):
            if not Path(flag(command,option)).is_file(): raise ControlError('Missing saved '+option+' resource')
        target_db=flag(command,'--inventory') if worker=='TAX' else str(output/'zillow.sqlite3')
        if Path(target_db).resolve()!=database: raise ControlError('Dashboard database does not match the crawler inventory')
        if worker=='TAX' and Path(flag(command,'--tax-database')).resolve()!=database: raise ControlError('Tax output must use the configured shared database')
        session_file=Path(flag(command,'--session-file')) if worker=='TAX' else output/'session.json'
        if read_state(session_file).get('profile')!=profile: raise ControlError('Saved session uses another profile')
        if not pending_work(database,worker): raise ControlError('No pending resources remain')
        pid=run.get('pid');actual=process_info(pid);busy=queue_busy(lock_path)
        if actual and not owns_process(actual,script,output): raise ControlError('Saved PID belongs to another process; refusing to restart')
        restarted=bool(actual)
        if busy and not actual: raise ControlError('Queue lock is held by another worker; refusing a second worker')
        if actual:
            interrupt_worker(pid,script,output)
            deadline=time.monotonic()+timeout
            while process_info(pid) or queue_busy(lock_path):
                if time.monotonic()>=deadline: raise ControlError('Worker is still finishing; no replacement started. Try again after it exits.')
                time.sleep(0.2)
        # A worker may have saved a safety stop while finishing its current resource.
        run=read_state(run_path);progress=read_state(progress_path)
        check_safety(run,progress)
        if queue_busy(lock_path): raise ControlError('Queue became busy; no second worker started')
        if not pending_work(database,worker): raise ControlError('Queue finished while stopping; no replacement needed')
        with (output/log_name).open('a') as log:
            process=subprocess.Popen(command,cwd=run.get('cwd') or str(next(Path(x).parent for x in command if Path(x).name==script)),
                                     stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        with _children_lock: _children.append(process)
        run.update(pid=process.pid,status='starting',started_at=datetime.now(timezone.utc).isoformat(),stopped_at=None,exit_code=None)
        # Progress JSON is intentionally never rewritten or reset.
        write_state(run_path,run)
        time.sleep(0.5)
        code=process.poll()
        if code is not None:
            latest=read_state(run_path)
            if latest.get('pid')==process.pid:
                latest.update(status='stopped',exit_code=code,stopped_at=datetime.now(timezone.utc).isoformat());write_state(run_path,latest)
            raise ControlError(f'Startup exited with code {code}; see the error feed and {log_name}')
        return f'{"Restart" if restarted else "Start"} requested for {worker}; PID {process.pid}. Watch for saved URLs.'


USER_STOP = 'Stopped by user from dashboard'


def stop_worker(output, worker, run_path=None, timeout=45):
    """Persist a manual pause and interrupt only this worker; never force-kill."""
    output = Path(output).resolve()
    worker = worker.upper()
    if worker not in WORKERS:
        raise ControlError('Unknown crawler')
    run_name, lock_name, _, script, _, _ = WORKERS[worker]
    run_path = Path(run_path) if run_path else output / run_name
    lock_path = output / lock_name
    with (output / 'dashboard-restart.lock').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlError('Another crawl control action is in progress') from None
        run = read_state(run_path)
        pid = run.get('pid')
        actual = process_info(pid)
        if actual and not owns_process(actual, script, output):
            raise ControlError('Saved PID belongs to another process; refusing to stop it')
        if not actual and queue_busy(lock_path):
            raise ControlError('Queue lock is held by another worker; cannot safely identify it')
        maintenance = run.get('maintenance') or USER_STOP
        requested = datetime.now(timezone.utc).isoformat()

        def persist(status):
            # The launcher can update its run JSON while finishing; retain that
            # metadata and reapply the manual pause so monitoring cannot resume it.
            latest = read_state(run_path)
            if latest.get('pid') != pid:
                raise ControlError('Worker PID changed during stop; refusing to overwrite its state')
            latest.update(maintenance=maintenance, user_stop_requested_at=requested, status=status)
            if status == 'stopped':
                latest['stopped_at'] = datetime.now(timezone.utc).isoformat()
            write_state(run_path, latest)

        persist('stopping' if actual else 'stopped')
        if actual:
            interrupt_worker(pid, script, output)
            deadline = time.monotonic() + timeout
            while process_info(pid) or queue_busy(lock_path):
                persist('stopping')
                if time.monotonic() >= deadline:
                    def finish_stop():
                        try:
                            while process_info(pid) or queue_busy(lock_path):
                                persist('stopping')
                                time.sleep(1)
                            persist('stopped')
                        except (ControlError, OSError):
                            return  # PID changed or dashboard/output was removed.
                    threading.Thread(target=finish_stop,daemon=True).start()
                    raise ControlError('Stop requested; worker is still finishing. It remains paused for monitoring; no force-kill was sent.')
                time.sleep(0.2)
            persist('stopped')
        return {'status': 'stopped', 'worker': worker.lower(),
                'message': 'Stopped by user. Queue and captures retained; Restart resumes pending work.'}
