#!/usr/bin/env python3
"""Start the Zillow crawl against installed Release REL.app."""
import fcntl
import json
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
    # Hold the lock across the child process. Repeated make invocations must not
    # launch concurrent workers against the same saved queue.
    with (output / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A make run crawl is already running; stop it with Ctrl-C before restarting') from None
        result = migrate(output / 'zillow.sqlite3')
        if result['status'] == 'migrated':
            print(json.dumps(result), flush=True)
        subprocess.run(['open', str(APP)], check=True)
        wait_for_release()
        print('Running Zillow crawl through Release REL.app; resuming saved progress.', flush=True)
        return subprocess.call([sys.executable, str(ROOT / 'zillow.py'),
                                *args, '--base-url', BASE_URL], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(run(sys.argv[1:]))
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Cannot start crawl: {error}', file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
