#!/usr/bin/env python3
"""Serve a small, read-only dashboard for the saved Zillow crawl."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import restart_status
from crawl_areas import AREAS, NOTE as AREA_NOTE, parcel_areas, address_areas, apn_key
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
LATEST = """WITH latest AS (
    SELECT zpid, MAX(capture_id) AS capture_id FROM observations GROUP BY zpid
), properties AS (
    SELECT h.*, c.captured_at, o.zestimate, o.rent_zestimate, o.price,
           o.home_status, o.rendered_bathrooms, o.bathrooms_conflict
    FROM latest l JOIN homes h ON h.zpid=l.zpid
    JOIN observations o ON o.zpid=l.zpid AND o.capture_id=l.capture_id
    JOIN captures c ON c.id=l.capture_id
) """
SORTS = {'recent', 'address', 'value', 'percentage-difference', 'percentage-difference-asc',
         'value-difference', 'value-difference-asc'}
PAGE_SIZE = 50
ASSESSMENT_VALUE = re.compile(r'TOTAL VALUE \$([\d,]+(?:\.\d{2})?) (?:LESS EXEMPTION|NET TAXABLE VALUE)')
ADDRESS_ALIASES = {'STREET':'ST', 'AVENUE':'AVE', 'ROAD':'RD', 'DRIVE':'DR',
                   'LANE':'LN', 'COURT':'CT', 'PLACE':'PL', 'BOULEVARD':'BLVD',
                   'CIRCLE':'CIR', 'TERRACE':'TER', 'APARTMENT':'UNIT', 'APT':'UNIT',
                   'SUITE':'UNIT', 'STE':'UNIT'}


def _address_key(address):
    words = re.sub(r'[^A-Z0-9/\\-]+', ' ', address.upper().replace('#', ' UNIT ')).split()
    return ' '.join(ADDRESS_ALIASES.get(word, word) for word in words)


def worker_running(output):
    try:
        with (output / 'zillow-run.lock').open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def _county_coverage(path, db):
    """Overlay the city Zillow review outcomes on the county residential-address roster.

    The county inventory is a separate, read-only parcel source. Its addresses
    are counted as pending until a Zillow lookup outcome exists for that
    normalized address; this keeps the dashboard denominator countywide without
    writing into the source database used by the tax collector.
    """
    if path is None or not path.is_file():
        return None
    county = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        complete = county.execute("SELECT value FROM inventory_state WHERE key='complete'").fetchone()
        if not complete or complete[0] != '1':
            return None
        addresses = {_address_key(row[0]) for row in county.execute(
            "SELECT DISTINCT address FROM parcels WHERE residential=1 "
            "AND address IS NOT NULL AND trim(address)<>''")}
    finally:
        county.close()
    if not addresses:
        return None
    status_by_address = {row['address_key']: row['status'] for row in db.execute(
        'SELECT address_key,status FROM inventory_addresses')}
    coverage = {}
    for key in addresses:
        status = status_by_address.get(key, 'pending')
        coverage[status] = coverage.get(status, 0) + 1
    return coverage


def tax_assessments(path, inventory_database_path):
    """Match one unambiguous annual county assessment to each Zillow property."""
    if path is None or not path.is_file():
        return {}
    inventory_db = sqlite3.connect(inventory_database_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        apns_by_zpid = {}
        for zpid, apn in inventory_db.execute('''SELECT a.zpid,p.apn FROM inventory_addresses a
                JOIN inventory_parcels p USING(address_key)
                WHERE a.status='matched' AND a.zpid IS NOT NULL AND p.classification='residential'
                  AND p.apn IS NOT NULL'''):
            apns_by_zpid.setdefault(zpid, set()).add(apn)
    finally:
        inventory_db.close()
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        db.execute('BEGIN')
        prefixed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='county_tax_listed_bills'").fetchone()
        prefix = 'county_tax_' if prefixed else ''
        values = {}
        for apn, year, text in db.execute(f'''SELECT b.apn,b.fiscal_start,c.text
            FROM {prefix}listed_bills b
            JOIN {prefix}captures c ON c.id=b.detail_capture_id
            WHERE b.bill_type='Annual Tax Bill'
              AND b.detail_status='complete' AND b.fiscal_start IS NOT NULL'''):
            match = ASSESSMENT_VALUE.search(text or '')
            if match:
                values.setdefault(apn, {}).setdefault(year, set()).add(float(match[1].replace(',', '')))
        result = {}
        for zpid, apns in apns_by_zpid.items():
            if len(apns) != 1:
                continue
            years = values.get(next(iter(apns)), {})
            valid = [(year, next(iter(amounts))) for year, amounts in years.items() if len(amounts) == 1]
            if valid:
                year, amount = min(valid, key=lambda item: (item[0] != 2025, -item[0]))
                result[zpid] = {'assessed_value': amount, 'assessment_year': year}
        return result
    finally:
        db.close()


def dashboard_data(path, params, county_inventory_path=None, tax_database_path=None):
    search = params.get('q', [''])[0].strip()[:200]
    status = params.get('status', [''])[0]
    sort = params.get('sort', ['recent'])[0]
    if sort not in SORTS:
        raise ValueError('Unknown sort order')
    try:
        page = max(1, int(params.get('page', ['1'])[0]))
    except ValueError:
        raise ValueError('Page must be a number') from None
    where, bindings = [], []
    if search:
        # Treat user input as literal text, including SQL LIKE wildcards.
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append("(address LIKE ? ESCAPE '\\' OR postal_code LIKE ? ESCAPE '\\' OR zpid LIKE ? ESCAPE '\\')")
        bindings.extend([pattern] * 3)
    if status:
        where.append('home_status = ?')
        bindings.append(status)
    condition = ' WHERE ' + ' AND '.join(where) if where else ''
    # mode=ro never creates, migrates or writes the database. One short snapshot
    # keeps totals and rows consistent while the crawler continues writing.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        summary = dict(db.execute(LATEST + '''SELECT COUNT(*) AS homes,
            COUNT(zestimate) AS with_zestimate FROM properties''').fetchone())
        capture = db.execute('SELECT COUNT(*), MAX(captured_at) FROM captures').fetchone()
        summary.update(captures=capture[0], last_capture=capture[1])
        coverage = dict(db.execute("SELECT status,COUNT(*) FROM inventory_addresses WHERE address_key IN "
                                  "(SELECT address_key FROM inventory_parcels WHERE classification='residential') "
                                  "GROUP BY status"))
        county_coverage = _county_coverage(county_inventory_path, db)
        if county_coverage is not None:
            coverage = county_coverage
        total = sum(coverage.values())
        reviewed = total - coverage.get('pending', 0) - coverage.get('resolving', 0) - coverage.get('needs_review', 0)
        summary.update(inventory_total=total, reviewed=reviewed, matched=coverage.get('matched', 0),
                       pending=coverage.get('pending', 0) + coverage.get('resolving', 0))
        rows = db.execute(LATEST + 'SELECT * FROM properties' + condition, bindings).fetchall()
        homes = [dict(row) for row in rows]
        assessments = tax_assessments(tax_database_path, path)
        for home in homes:
            home.update(assessments.get(home['zpid'], {'assessed_value': None, 'assessment_year': None}))
            estimate, assessed = home['zestimate'], home['assessed_value']
            difference = estimate - assessed if estimate is not None and assessed is not None else None
            home['value_difference'] = difference
            home['percentage_difference'] = difference / estimate * 100 if difference is not None and estimate > 0 else None
        if sort == 'recent':
            homes.sort(key=lambda h: (h['captured_at'] or '', h['zpid']), reverse=True)
        elif sort == 'address':
            homes.sort(key=lambda h: ((h['address'] or '').casefold(), h['zpid']))
        elif sort == 'value':
            homes.sort(key=lambda h: (h['zestimate'] is None, -(h['zestimate'] or 0), h['zpid']))
        elif sort in {'percentage-difference', 'percentage-difference-asc', 'value-difference', 'value-difference-asc'}:
            field = 'percentage_difference' if sort.startswith('percentage-') else 'value_difference'
            reverse = sort.endswith('-asc')
            homes.sort(key=lambda h: ((h[field] is None), (h[field] or 0) if reverse else -(h[field] or 0),
                                      (h['address'] or '').casefold(), h['zpid']))
        count = len(homes)
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        homes = homes[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        statuses = [row[0] for row in db.execute('SELECT DISTINCT home_status FROM observations '
                                               'WHERE home_status IS NOT NULL ORDER BY home_status')]
        errors = [dict(row) for row in db.execute('SELECT * FROM crawl_errors ORDER BY id DESC LIMIT 5')]
        return {'summary': summary, 'coverage': coverage, 'homes': homes, 'statuses': statuses,
                'errors': errors, 'total': count, 'page': page, 'pages': pages,
                'page_size': PAGE_SIZE, 'running': worker_running(path.parent)}
    finally:
        db.close()


def _county_worker_running(run_file):
    if not run_file or not run_file.is_file():
        return False
    try:
        run = json.loads(run_file.read_text())
        pid = int(run.get('pid', 0))
        if pid <= 0:
            return False
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                 capture_output=True, text=True, timeout=2).stdout
        return 'crawl_county_taxes.py' in command
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def _read_state(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _pid_alive(pid):
    try:
        status=subprocess.run(['ps', '-p', str(int(pid)), '-o', 'stat='],
                                  capture_output=True, text=True, timeout=2).stdout.strip()
        return bool(status and not status.startswith('Z'))
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        return False


def _restart_saved_crawler(database, worker, tax_run_file=None):
    """An explicit local action resumes pending work, never saved failed resources."""
    if worker not in {'zillow', 'tax'}:
        raise ValueError('Unknown crawler')
    output = database.parent
    run_path = output / 'active-run.json' if worker == 'zillow' else tax_run_file
    if not run_path:
        raise ValueError('Tax crawler is not configured')
    with (output / 'dashboard-restart.lock').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another restart is already in progress') from None
        run = _read_state(run_path)
        if run.get('maintenance'):
            raise ValueError('Crawler is under maintenance')
        if _pid_alive(run.get('pid')):
            raise ValueError('Crawler is already running or starting')
        lock_path = output / ('zillow-run.lock' if worker == 'zillow' else 'run.lock')
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Crawler queue is already in use') from None
            command = run.get('launcher_command') if worker == 'zillow' else run.get('command')
            script = 'run.py' if worker == 'zillow' else 'crawl_county_taxes.py'
            profile = 'Zillow' if worker == 'zillow' else 'Datacenter'
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError('No saved crawler command')
            if not any(Path(x).name == script for x in command) or '--profile' not in command or command[command.index('--profile') + 1] != profile:
                raise ValueError('Saved crawler command does not use the configured profile and launcher')
            db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
            try:
                table = 'inventory_addresses' if worker == 'zillow' else 'county_tax_targets'
                pending = db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('pending','resolving','running','history_complete')").fetchone()[0]
            finally:
                db.close()
            if not pending:
                raise ValueError('No pending resources remain')
        progress_path = output / ('zillow-progress.json' if worker == 'zillow' else 'county-progress.json')
        previous = _read_state(progress_path)
        now = datetime.now(timezone.utc).isoformat()
        with (output / 'continuation-history.jsonl').open('a') as history:
            history.write(json.dumps({'at': now, 'worker': worker, 'reason': 'User clicked dashboard Restart', 'previous_progress': previous}) + '\n')
        progress = dict(previous, consecutive_failures=0, stop_reason=None, resumed_at=now,
                        resume_reason='User clicked dashboard Restart')
        temporary = progress_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(progress, indent=2) + '\n'); temporary.replace(progress_path)
        try:
            with (output / ('zillow-runner.log' if worker == 'zillow' else 'county-tax.log')).open('a') as log:
                process = subprocess.Popen(command, cwd=run.get('cwd') or ROOT,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError:
            temporary.write_text(json.dumps(previous, indent=2) + '\n'); temporary.replace(progress_path)
            raise
        run.update(pid=process.pid, status='starting', started_at=now, stopped_at=None,
                   stop_reason=None, consecutive_failures=0)
        temporary = run_path.with_suffix('.dashboard.tmp')
        temporary.write_text(json.dumps(run, indent=2) + '\n'); temporary.replace(run_path)
        return {'status': 'starting', 'worker': worker}


def restart_crawler(database, worker, tax_run_file=None):
    if worker not in ('zillow','tax'): raise ValueError('Unknown crawler')
    output=database.parent
    run_file=output/'active-run.json' if worker=='zillow' else tax_run_file
    run=_read_state(run_file) if run_file else {}
    attempt=None
    if not _pid_alive(run.get('pid')):
        attempt=restart_status.begin(output,worker,database)
    try:
        response=_restart_saved_crawler(database,worker,tax_run_file)
    except (ValueError,OSError,sqlite3.Error) as error:
        if attempt:
            restart_status.result(output,worker,attempt,stage='failed',error=str(error),finished_at=datetime.now(timezone.utc).isoformat())
        raise
    if attempt:
        run=_read_state(run_file)
        restart_status.result(output,worker,attempt,stage='launched',pid=run.get('pid'))
    return response


def restart_progress_data(database,tax_database=None,tax_run_file=None):
    output=database.parent
    result={}
    for worker,run_path,progress_path in (
        ('zillow',output/'active-run.json',output/'zillow-progress.json'),
        ('tax',tax_run_file,output/'county-progress.json')):
        if not run_path:
            result[worker]={'available':False};continue
        run=_read_state(run_path);progress=_read_state(progress_path)
        alive=_pid_alive(run.get('pid'))
        busy=worker_running(output) if worker=='zillow' else _county_worker_running(run_path)
        item=restart_status.describe(output,worker,(tax_database or database) if worker=='tax' else database,run,progress,alive,busy)
        item['can_restart']=bool(not alive and not busy and not run.get('maintenance') and (run.get('launcher_command') if worker=='zillow' else run.get('command')))
        result[worker]=item
    return result


def area_status_data(database, tax_database, county_inventory):
    if not county_inventory or not county_inventory.is_file():
        return {'available': False, 'rows': []}
    parcels = parcel_areas(county_inventory)
    rows = [dict(area=name, zillow_remaining=0, tax_remaining=0) for name in AREAS]
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)) as db:
        addresses = address_areas(db, parcels)
        for key, in db.execute("SELECT address_key FROM inventory_addresses WHERE status IN ('pending','resolving') AND lookup_url IS NOT NULL"):
            if key in addresses:
                rows[addresses[key]]['zillow_remaining'] += 1
    if tax_database and tax_database.is_file():
        with closing(sqlite3.connect(tax_database.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)) as db:
            for apn, in db.execute("SELECT apn FROM county_tax_targets WHERE status IN ('pending','running','history_complete')"):
                rows[parcels.get(apn_key(apn),10)]['tax_remaining'] += 1
    return {'available': True, 'rows': rows, 'note': AREA_NOTE,
            'updated_at': datetime.now(timezone.utc).isoformat()}


def crawl_status_data(database, tax_database=None, tax_run_file=None):
    output = database.parent
    run = _read_state(output / 'active-run.json')
    progress = _read_state(output / 'zillow-progress.json')
    running = worker_running(output)
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        last_capture = db.execute('SELECT MAX(captured_at) FROM captures').fetchone()[0]
    finally:
        db.close()
    tax = tax_status_data(tax_database, tax_run_file)
    tax_run = _read_state(tax_run_file) if tax_run_file else {}
    tax['can_restart'] = bool(tax.get('available') and not tax.get('running') and tax_run.get('command') and not tax_run.get('maintenance') and not _pid_alive(tax_run.get('pid')))
    tax['starting'] = bool(not tax.get('running') and tax_run.get('status') == 'starting' and _pid_alive(tax_run.get('pid')))
    tax_progress = _read_state((tax_run_file.parent if tax_run_file else output) / 'county-progress.json')
    tax['consecutive_failures'] = tax_progress.get('consecutive_failures', 0)
    tax['stop_reason'] = tax_progress.get('stop_reason') or tax.get('stop_reason')
    if tax.get('available') and not tax.get('running') and (tax['stop_reason'] or tax.get('stopped_at')):
        tax['status'] = 'Stopped'
    return {
        'zillow': {
            'available': True, 'running': running,
            'can_restart': bool(not running and run.get('launcher_command') and not run.get('maintenance') and not _pid_alive(run.get('pid'))),
            'starting': bool(not running and run.get('status') == 'starting' and _pid_alive(run.get('pid'))),
            'status': 'Running' if running else 'Stopped' if run or progress else 'Idle',
            'profile': run.get('profile'), 'last_capture': last_capture,
            'consecutive_failures': progress.get('consecutive_failures', 0),
            'stop_reason': progress.get('stop_reason'),
        },
        'tax': tax,
    }


def _tax_error_category(error):
    message = str(error).lower()
    if any(term in message for term in ('captcha', 'access denied', '403', '429')):
        return 'Access restriction'
    if '404' in message:
        return 'Page not found'
    if any(term in message for term in ('timed out', 'timeout', 'request_cancelled')):
        return 'Browser or page timeout'
    if any(term in message for term in ('connection_refused', 'connection_timed_out', 'upstream_unavailable')):
        return 'Connection unavailable'
    return 'Collection error'


def tax_status_data(database, run_file=None):
    """Expose aggregate tax-crawl progress only; never return parcel IDs or captures."""
    if database is None or not database.is_file():
        return {'available': False, 'updated_at': datetime.now(timezone.utc).isoformat()}
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        db.execute('BEGIN')
        prefixed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='county_tax_targets'").fetchone()
        prefix = 'county_tax_' if prefixed else ''
        target_status = dict(db.execute(f'SELECT status,COUNT(*) FROM {prefix}targets GROUP BY status'))
        detail_status = dict(db.execute(f'SELECT detail_status,COUNT(*) FROM {prefix}listed_bills GROUP BY detail_status'))
        capture_rows = db.execute(f'SELECT kind,COUNT(*),MAX(captured_at) FROM {prefix}captures GROUP BY kind').fetchall()
        last_capture = db.execute(f'SELECT MAX(captured_at) FROM {prefix}captures').fetchone()[0]
        annual_bills = db.execute(f'SELECT COUNT(*) FROM {prefix}bills').fetchone()[0]
        listed_bill_count = db.execute(f'SELECT COUNT(*) FROM {prefix}listed_bills').fetchone()[0]
        recent_errors = db.execute(f'SELECT occurred_at,error FROM {prefix}errors WHERE terminal=1 ORDER BY id DESC LIMIT 3').fetchall()
    finally:
        db.close()

    run = {}
    if run_file and run_file.is_file():
        try:
            run = json.loads(run_file.read_text())
        except (OSError, json.JSONDecodeError):
            run = {}
    running = _county_worker_running(run_file)
    status = 'Running' if running else 'Stopped' if run.get('stop_reason') else 'Idle'
    targets_total = sum(target_status.values())
    details_total = sum(detail_status.values())
    captures = {row[0]: {'count': row[1], 'last_capture': row[2]} for row in capture_rows}
    errors = [{'occurred_at': row[0], 'category': _tax_error_category(row[1])} for row in recent_errors]
    return {
        'available': True,
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'running': running,
        'profile': run.get('profile'),
        'started_at': run.get('started_at'),
        'stopped_at': run.get('stopped_at'),
        'stop_reason': run.get('stop_reason'),
        'targets': target_status,
        'target_total': targets_total,
        'details': detail_status,
        'detail_total': details_total,
        'captures': captures,
        'last_capture': last_capture,
        'annual_bills': annual_bills,
        'listed_bills': listed_bill_count,
        'recent_errors': errors,
    }


def property_data(path, zpid, tax_database_path=None):
    """Return every stored property observation and capture, without the HTML blob."""
    if not zpid.isdecimal() or len(zpid) > 32:
        raise ValueError('Invalid property ID')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        # Match the dashboard table: summarize the newest observation, while
        # retaining the home fields and the full history below.
        property_row = db.execute(LATEST + 'SELECT * FROM properties WHERE zpid=?', (zpid,)).fetchone()
        if property_row is None:
            return None
        observations = [dict(row) for row in db.execute(
            'SELECT * FROM observations WHERE zpid=? ORDER BY capture_id DESC', (zpid,))]
        capture_ids = [row['capture_id'] for row in observations]
        captures = []
        for capture_id in capture_ids:
            row = db.execute('SELECT id,requested_url,final_url,captured_at,http_status,title,canonical_url,sha256,'
                             'text,metadata_json,structured_json,length(html) AS html_bytes '
                             'FROM captures WHERE id=?', (capture_id,)).fetchone()
            if row:
                captures.append(dict(row))
        for observation in observations:
            observation['facts'] = json.loads(observation['facts_json'])
        for capture in captures:
            capture['metadata'] = json.loads(capture.pop('metadata_json'))
            capture['structured_data'] = json.loads(capture.pop('structured_json'))
        property_fields = dict(property_row)
        property_fields.update(tax_assessments(tax_database_path, path).get(
            zpid, {'assessed_value': None, 'assessment_year': None}))
        return {'property': property_fields, 'observations': observations, 'captures': captures}
    finally:
        db.close()


def capture_html(path, capture_id):
    if not capture_id.isdecimal() or len(capture_id) > 16:
        raise ValueError('Invalid capture ID')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        row = db.execute('SELECT html FROM captures WHERE id=?', (capture_id,)).fetchone()
        return row[0] if row else None
    finally:
        db.close()


def handler_for(database, tax_database=None, tax_run_file=None, county_inventory_path=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            # Only our localhost page may trigger a restart; reject cross-site forms/fetches.
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            if origin != 'http://' + host or host != f'127.0.0.1:{self.server.server_port}' or self.headers.get('Content-Type') != 'application/json':
                self.respond(403, b'{"error":"Restart must be requested from this dashboard."}', 'application/json')
                return
            routes = {'/api/restart/zillow': 'zillow', '/api/restart/tax': 'tax'}
            worker = routes.get(self.path)
            if not worker:
                self.respond(404, b'{"error":"Not found"}', 'application/json')
                return
            try:
                data = restart_crawler(database, worker, tax_run_file)
                self.respond(202, json.dumps(data).encode(), 'application/json')
            except ValueError as error:
                self.respond(409, json.dumps({'error': str(error)}).encode(), 'application/json')
            except (OSError, sqlite3.Error) as error:
                self.respond(503, json.dumps({'error': str(error)}).encode(), 'application/json')

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path in ('/', '/index.html'):
                self.respond(200, (ROOT / 'dashboard.html').read_bytes(), 'text/html; charset=utf-8')
            elif parsed.path.startswith('/api/property/'):
                try:
                    zpid = parsed.path.removeprefix('/api/property/')
                    data = property_data(database, zpid, tax_database)
                    if data is None:
                        self.respond(404, b'{"error":"Property not found"}', 'application/json')
                    else:
                        self.respond(200, json.dumps(data).encode(), 'application/json')
                except ValueError as error:
                    self.respond(400, json.dumps({'error': str(error)}).encode(), 'application/json')
                except sqlite3.Error:
                    self.respond(503, b'{"error":"Property data is unavailable."}', 'application/json')
            elif parsed.path.startswith('/api/capture/') and parsed.path.endswith('/html'):
                capture_id = parsed.path.removeprefix('/api/capture/').removesuffix('/html').rstrip('/')
                try:
                    html = capture_html(database, capture_id)
                    if html is None:
                        self.respond(404, b'Capture not found', 'text/plain; charset=utf-8')
                    else:
                        self.respond(200, html.encode(), 'text/plain; charset=utf-8')
                except (ValueError, sqlite3.Error):
                    self.respond(404, b'Capture not found', 'text/plain; charset=utf-8')
            elif parsed.path == '/api/data':
                try:
                    data = dashboard_data(database, parse_qs(parsed.query), county_inventory_path, tax_database)
                    self.respond(200, json.dumps(data).encode(), 'application/json')
                except ValueError as error:
                    self.respond(400, json.dumps({'error': str(error)}).encode(), 'application/json')
                except sqlite3.Error:
                    self.respond(503, json.dumps({'error': 'Crawl data is unavailable. Check the database path and try again.'}).encode(),
                                 'application/json')
            elif parsed.path == '/api/restart-status':
                try:
                    data = restart_progress_data(database, tax_database, tax_run_file)
                    self.respond(200, json.dumps(data).encode(), 'application/json')
                except (OSError, sqlite3.Error):
                    self.respond(503, b'{"error":"Restart diagnostics are unavailable."}', 'application/json')
            elif parsed.path == '/api/crawl-status':
                try:
                    data = crawl_status_data(database, tax_database, tax_run_file)
                    data["areas"] = area_status_data(database, tax_database, county_inventory_path)
                    self.respond(200, json.dumps(data).encode(), 'application/json')
                except sqlite3.Error:
                    self.respond(503, b'{"error":"Crawler status is unavailable."}', 'application/json')
            elif parsed.path == '/api/tax-status':
                try:
                    data = tax_status_data(tax_database, tax_run_file)
                    self.respond(200, json.dumps(data).encode(), 'application/json')
                except sqlite3.Error:
                    self.respond(503, b'{"error":"County tax crawl status is unavailable."}', 'application/json')
            elif parsed.path == '/favicon.ico':
                self.respond(204, b'', 'image/x-icon')
            else:
                self.respond(404, b'Not found', 'text/plain')

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'output/zillow.sqlite3')
    parser.add_argument('--tax-database', type=Path)
    parser.add_argument('--tax-run-file', type=Path)
    parser.add_argument('--county-inventory', type=Path,
                        default=ROOT / 'output/county-inventory.sqlite3')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f'Database not found: {args.database}')
    ThreadingHTTPServer.allow_reuse_address = True
    county_inventory = args.county_inventory if args.county_inventory.is_file() else None
    server = ThreadingHTTPServer(('127.0.0.1', args.port),
                                 handler_for(args.database, args.tax_database, args.tax_run_file,
                                             county_inventory))
    print(f'Zillow dashboard: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
