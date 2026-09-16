#!/usr/bin/env python3
"""Bounded REL scans with persistent WhatsApp group alert deduplication."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import logging
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parent


def options():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT / 'config.json')
    p.add_argument('--output', type=Path, default=ROOT / 'output')
    p.add_argument('--send', action='store_true', help='Send matches to the WhatsApp group (default: preview only)')
    p.add_argument('--watch', action='store_true', help='Repeat scans until Ctrl-C; Mac must stay awake')
    p.add_argument('--session-id', help='Use an existing REL session; saved for subsequent runs')
    p.add_argument('--max-listings', type=int, help='Override per-scan listing limit')
    p.add_argument('--reset-alert', metavar='LISTING_ID', help='Allow another attempt after manually checking an uncertain/failed alert')
    return p.parse_args()


def load_config(path):
    if not path.exists():
        raise ValueError('Copy config.example.json to config.json and configure the WhatsApp group before enabling --send')
    config = json.loads(path.read_text())
    for name in ('latitude', 'longitude', 'radius_miles', 'interval_minutes'):
        if isinstance(config.get(name), bool) or not isinstance(config.get(name), (int, float)) or not math.isfinite(config[name]):
            raise ValueError(f'{name} must be a finite number')
    if not (-90 <= config['latitude'] <= 90 and -180 <= config['longitude'] <= 180):
        raise ValueError('Invalid center coordinates')
    if config['radius_miles'] <= 0 or config['interval_minutes'] < 5:
        raise ValueError('Radius must be positive and interval at least five minutes')
    if not isinstance(config.get('max_listings'), int) or isinstance(config['max_listings'], bool) or config['max_listings'] < 1:
        raise ValueError('max_listings must be a positive integer')
    p = urlsplit(config['search_url'])
    if p.scheme != 'https' or p.hostname != 'www.facebook.com' or not re.fullmatch(r'/marketplace/106277849402612/search/', p.path):
        raise ValueError('Use the Santa Cruz Facebook Marketplace search URL')
    if parse_qs(p.query).get('query') != [config['query']] or not config['query'].strip():
        raise ValueError('search_url query must match query')
    return config


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, default=str))
    temp.replace(path)


def scan(config, output, session_id, client, *, enabled):
    from rel_crawler import CrawlApplication, CrawlDefinition
    from listings import item_id, parse_listing
    from notifications import connect, notify

    # Fresh checkpoint each pass rediscovers new listings and refreshes availability.
    # An interrupted pass resumes its captures/checkpoint on the next invocation.
    active = output / 'active.json'
    identity = {k: config[k] for k in ('query', 'latitude', 'longitude', 'radius_miles', 'search_url')}
    if active.exists():
        state = json.loads(active.read_text())
        if state['identity'] != identity:
            raise ValueError('Search changed during an unfinished pass; move output/active.json aside to start a fresh pass')
    else:
        state = {'pass': uuid.uuid4().hex, 'identity': identity}
        atomic_json(active, state)
    folder = output / 'passes' / state['pass']
    folder.mkdir(parents=True, exist_ok=True)
    db = connect(output / 'marketplace.sqlite3')
    try:
        definition = CrawlDefinition(
            start_url=config['search_url'], select_link=lambda link: bool(item_id(link.url)),
            link_key=lambda link: item_id(link.url),
            source_ready_selector='[role="main"][aria-label="Collection of Marketplace items"]',
            capture_ready_selector='[role="dialog"][aria-label="Marketplace Listing Viewer"] h1',
            capture_path=lambda item: folder / 'pages' / f'{item_id(item.link.url)}.html',
        )
        app = CrawlApplication(definition=definition, state_path=folder / 'checkpoint.json',
            capture_dir=folder / 'pages', session_id=session_id, client=client,
            timeout=45, max_links=config['max_listings'], max_attempts=2,
            max_session_restarts=0, action_delay=2)
        summary = app.run()
        results = []
        # Process saved captures every time, including captures recovered after a crash.
        # Only a completed pass is eligible to send; failures cannot look like no matches.
        if summary.failed or summary.pending:
            raise RuntimeError(f'Scan incomplete ({summary.failed} failed, {summary.pending} pending); inspect {folder}')
        if not summary.discovered:
            raise RuntimeError('No visible listing links: empty results or access/layout issue; inspect REL before resuming')
        for path in sorted((folder / 'pages').glob('*.html')):
            metadata = json.loads(path.with_suffix('.metadata.json').read_text())
            final_url = metadata['page']['url']
            if item_id(final_url) != path.stem:
                raise ValueError('Captured page does not match requested listing ID')
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(metadata['captured_at'].replace('Z', '+00:00'))).total_seconds()
            if enabled and not 0 <= age <= 900:
                active.unlink()
                raise RuntimeError('Capture is older than 15 minutes; rerun for a fresh pass before sending')
            row = parse_listing(path.read_text(), final_url, config)
            results.append(row)
        if len(results) != summary.captured:
            raise RuntimeError('Capture files do not match completed checkpoint; refusing an incomplete scan')
        atomic_json(folder / 'results.json', results)
        counts = {}
        for row in results:
            status = notify(db, row, config, enabled=enabled)
            counts[status] = counts.get(status, 0) + 1
        atomic_json(folder / 'summary.json', {'crawl': asdict(summary), 'alerts': counts})
        active.unlink()
        print(json.dumps({'pass': str(folder), 'listings': len(results), 'alerts': counts}), flush=True)
    finally:
        db.close()


def main():
    args = options()
    config = load_config(args.config)
    if args.max_listings is not None:
        if args.max_listings < 1:
            raise ValueError('--max-listings must be positive')
        config['max_listings'] = args.max_listings
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A Marketplace monitor is already running') from None
        if args.reset_alert:
            from notifications import connect
            db = connect(output / 'marketplace.sqlite3')
            with db:
                db.execute('DELETE FROM whatsapp_alerts WHERE listing_id=?', (args.reset_alert,))
            db.close()
            print('Alert reservation cleared; next live scan will recheck eligibility.')
            return
        if args.send:
            from whatsapp import resolve_group
            config['_whatsapp_group_jid'] = resolve_group(config.get('whatsapp_group'), config.get('whatsapp_group_jid', ''))
        from browser import MarketplaceClient
        client = MarketplaceClient()
        client.health()
        session_file = output / 'session.json'
        saved = json.loads(session_file.read_text()) if session_file.exists() else {}
        session_id = args.session_id or saved.get('session_id')
        if session_id and client.get_session(session_id) is None:
            raise RuntimeError('Saved REL session is missing; supply --session-id for an existing session')
        if not session_id:
            session_id = client.create_session(profile=config.get('profile'), group='facebook-marketplace-case-study')
        atomic_json(session_file, {'session_id': session_id})
        while True:
            scan(config, output, session_id, client, enabled=args.send)
            if not args.watch:
                break
            time.sleep(config['interval_minutes'] * 60)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print(f'Monitor stopped: {error}', file=sys.stderr)
        sys.exit(1)
