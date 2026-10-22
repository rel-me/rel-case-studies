#!/usr/bin/env python3
"""Start the Zillow crawl against installed Release REL.app."""
import fcntl
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from migrate import migrate

ROOT = Path(__file__).resolve().parent
APP = Path('/Applications/REL.app')
BASE_URL = 'http://127.0.0.1:17319/v1'


def wait_for_release(timeout=30):
    deadline = time.monotonic() + timeout
    last_error = 'agent has not responded'
    while time.monotonic() < deadline:
        try:
            with urlopen(BASE_URL + '/health', timeout=2) as response:
                health = json.load(response)
            if health.get('status') != 'ok':
                last_error = 'health response is not ready'
            elif health.get('data', {}).get('build', {}).get('configuration') != 'Release':
                raise RuntimeError('The Release endpoint is not reporting a Release build')
            else:
                return
        except (URLError, TimeoutError, OSError, ValueError) as error:
            last_error = str(error)
        time.sleep(1)
    raise RuntimeError(f'REL.app did not become ready within {timeout}s: {last_error}')


def run(args):
    if not APP.is_dir():
        raise RuntimeError(f'Install REL at {APP} before running this case study')
    output = ROOT / 'output'
    for i, arg in enumerate(args):
        if arg == '--output' and i + 1 < len(args):
            output = Path(args[i + 1])
        elif arg.startswith('--output='):
            output = Path(arg.split('=', 1)[1])
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=True)
    # Keep Zillow queue ownership independent from the county-tax/inventory lock.
    # Both crawlers write distinct tables in the shared SQLite database.
    with (output / 'zillow-run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('The Zillow crawl is already running; stop it before restarting') from None
        result = migrate(output / 'zillow.sqlite3')
        if result['status'] == 'migrated':
            print(json.dumps(result), flush=True)
        subprocess.run(['open', str(APP)], check=True)
        wait_for_release()
        print('Running Zillow crawl through Release REL.app; resuming saved progress.', flush=True)
        command = [sys.executable, str(ROOT / 'zillow.py'), *args, '--base-url', BASE_URL]
        state_path = output / 'active-run.json'
        state = {'pid': os.getpid(), 'command': command, 'launcher_command': [sys.executable, str(ROOT / 'run.py'), *args], 'started_at': datetime.now(timezone.utc).isoformat(),
                 'profile': next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '--profile'), None),
                 'log': str(output / 'zillow-crawl.log'), 'database': str(output / 'zillow.sqlite3'),
                 'status': 'running'}
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_path)
        with (output / 'zillow-crawl.log').open('a') as log_stream:
            exit_code = subprocess.call(command, cwd=ROOT, stdout=log_stream, stderr=subprocess.STDOUT)
        state.update(status='stopped', stopped_at=datetime.now(timezone.utc).isoformat(), exit_code=exit_code)
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_path)
        return exit_code


if __name__ == '__main__':
    try:
        raise SystemExit(run(sys.argv[1:]))
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Cannot start crawl: {error}', file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
