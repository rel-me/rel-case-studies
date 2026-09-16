#!/usr/bin/env python3
"""Crawl Santa Cruz property pages with rel-crawlee and Profile-managed REL sessions."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from crawlee import ConcurrencySettings, Request
from crawlee.configuration import Configuration
from crawlee.storages import RequestQueue
from crawlee.statistics import Statistics
from crawlee.storage_clients import FileSystemStorageClient
from crawlee.events import LocalEventManager
from rel_crawlee import RelCrawler
from rel_playwright.async_api import async_playwright

import records
import inventory

ROOT = Path(__file__).resolve().parent


class AccessBlocked(RuntimeError):
    pass


def request(url, source=None):
    normalized = records.normalize(url)
    if not normalized:
        raise ValueError(f"Outside Zillow crawl scope: {url}")
    match = records.HOME.fullmatch(urlsplit(normalized).path)
    return Request.from_url(normalized, unique_key=f"zpid:{match[1]}" if match else normalized,
                            user_data={"source_url": source})


def inventory_request(row):
    detail = row['status'] == 'resolving'
    return Request.from_url(row['matched_url'] if detail else row['lookup_url'],
                            unique_key=('inventory-detail:' if detail else 'inventory:') + row['address_key'],
                            user_data={'inventory_key': row['address_key']})


def inventory_seeds(db):
    return [inventory_request(row) for row in db.execute(
        "SELECT * FROM inventory_addresses WHERE status IN ('pending','resolving') AND lookup_url IS NOT NULL "
        "AND address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential') "
        "ORDER BY address_key")]


async def match_inventory(context, db, capture, data, final):
    key = context.request.user_data['inventory_key']
    def outcome(status, reason=None, zpid=None):
        with db:
            db.execute('UPDATE inventory_addresses SET status=?,reason=?,zpid=?,matched_url=? WHERE address_key=?',
                       (status, reason, zpid, final if zpid else None, key))
    if urlsplit(final).hostname != 'www.zillow.com':
        raise ValueError('Inventory lookup redirected outside Zillow')
    home = records.HOME.fullmatch(urlsplit(final).path)
    if home:
        facts = data['facts']
        if not facts:
            raise ValueError('No detail record matching the final ZPID')
        address = facts.get('address', {})
        if str(address.get('city', '')).casefold() != 'santa cruz' or address.get('state') != 'CA':
            outcome('skipped', 'Property is outside Santa Cruz address scope')
            return None
        if inventory.address_key(address.get('streetAddress') or '') != key:
            outcome('ambiguous', 'Zillow street address or unit does not match GIS')
            return None
        with db:
            records.save_home(db, capture, final, facts)
        outcome('matched', zpid=home[1])
        return home[1]
    candidates = {}
    for card in records.walk(data['structured']):
        info = card.get('hdpData', {}).get('homeInfo') if isinstance(card.get('hdpData'), dict) else None
        link = records.normalize(card.get('detailUrl', ''))
        if not info or not link:
            continue
        match = records.HOME.fullmatch(urlsplit(link).path)
        if (match and str(info.get('zpid')) == match[1]
                and inventory.address_key(info.get('streetAddress') or '') == key
                and str(info.get('city', '')).casefold() == 'santa cruz' and info.get('state') == 'CA'):
            candidates[match[1]] = link
    if len(candidates) == 1:
        zpid, link = next(iter(candidates.items()))
        await context.add_requests([Request.from_url(link, unique_key='inventory-detail:' + key,
                                  user_data={'inventory_key': key})])
        with db:
            db.execute("UPDATE inventory_addresses SET status='resolving',reason=?,zpid=?,matched_url=? WHERE address_key=?",
                       ('Unique address candidate; detail verification pending', zpid, link, key))
    elif candidates:
        outcome('ambiguous', 'Multiple Zillow property IDs match the address')
    else:
        outcome('not_found', 'No exact address/unit candidate in the rendered response')
    return None


def transient_failure(message):
    return any(token in message.casefold() for token in
               ('timed out', 'timeout', 'request_cancelled', 'err_network_changed',
                'err_network_io_suspended', 'err_connection_reset', 'err_connection_closed'))


def failed_seeds(db):
    """Explicit recovery batches have stable keys so interrupted runs deduplicate."""
    seeds = []
    for row in db.execute("SELECT * FROM inventory_addresses WHERE status='failed'"):
        if not transient_failure(row['reason'] or ''):
            continue
        detail = row['matched_url'] and records.HOME.fullmatch(urlsplit(row['matched_url']).path)
        url = row['matched_url'] if detail else row['lookup_url']
        error = db.execute('SELECT MAX(id) FROM crawl_errors WHERE url=? AND terminal=1', (url,)).fetchone()[0]
        if not url or error is None:
            continue
        seeds.append(Request.from_url(url, unique_key=f"inventory-retry:{row['address_key']}:{error}",
                                      user_data={'inventory_key': row['address_key']}))
    return seeds


def register_handlers(crawler, db, max_addresses=50, retry_delay=5):
    crawled_addresses = set()
    @crawler.router.default_handler
    async def handle(context):
        url = context.request.url
        html = await context.page.content()
        final = context.page.url
        data = records.parse(html, final)
        with db:
            capture = records.save(db, url, final, context.response.status, html, data)
        if re.search(r'press\s*(?:&|and)\s*hold|verify you are (?:a )?human|access to this page has been denied|captcha',
                     data['title'] + ' ' + data['text'], re.I):
            context.request.no_retry = True
            raise AccessBlocked('Access challenge; stopping, capture retained')
        if context.request.user_data.get('inventory_key'):
            zpid = await match_inventory(context, db, capture, data, final)
            if zpid:
                crawled_addresses.add(zpid)
                context.log.info(f'Addresses verified: {len(crawled_addresses)}/{max_addresses}')
                if len(crawled_addresses) >= max_addresses:
                    crawler.stop('Address limit reached')
            return
        if not records.normalize(final):
            raise ValueError('Unexpected final URL')
        home = records.HOME.fullmatch(urlsplit(url).path)
        if home:
            reached = records.HOME.fullmatch(urlsplit(final).path)
            if not reached or reached[1] != home[1]:
                raise ValueError('Navigation did not reach the requested home')
            address = (data['facts'] or {}).get('address', {})
            if data['facts'] and (str(address.get('city', '')).casefold() != 'santa cruz' or address.get('state') != 'CA'):
                with db:
                    db.execute('INSERT OR REPLACE INTO skipped_urls VALUES(?,?,?)',
                               (url, 'Outside Santa Cruz address scope', datetime.now(timezone.utc).isoformat()))
                context.log.info(f'Skipped out-of-scope property: {url}')
                return
            with db:
                records.save_home(db, capture, url, data['facts'])
            crawled_addresses.add(home[1])
            context.log.info(f'Addresses crawled: {len(crawled_addresses)}/{max_addresses}')
            if len(crawled_addresses) >= max_addresses:
                crawler.stop('Address limit reached')
        else:
            if not records.SEARCH.fullmatch(urlsplit(final).path):
                raise ValueError('Search navigation left the city search scope')
            targets = [request(link, final) for link, _ in data['links']]
            if not any(records.HOME.fullmatch(urlsplit(target.url).path) for target in targets):
                raise ValueError('No property links; discovery incomplete')
            with db:
                records.save_search(db, capture, data)
            await context.add_requests(targets)
        context.log.info(f'Saved: {url}')

    def record_error(context, error, terminal):
        # Crawlee increments retry_count before calling its retry handler.
        attempt = context.request.retry_count + int(terminal)
        with db:
            db.execute('INSERT INTO crawl_errors(url,occurred_at,attempt,terminal,error) VALUES(?,?,?,?,?)',
                       (context.request.url, datetime.now(timezone.utc).isoformat(),
                        attempt, int(terminal), str(error)))
        print(f"ERROR [attempt {attempt}] {context.request.url}: {error}", file=sys.stderr, flush=True)

    @crawler.error_handler
    async def retry(context, error):
        record_error(context, error, False)
        # Retry this URL before processing any other queued request.
        context.request.forefront = True
        if transient_failure(str(error)) and retry_delay:
            context.log.warning(f'Waiting {retry_delay}s before retrying the same REL session')
            await asyncio.sleep(retry_delay)

    @crawler.failed_request_handler
    async def failed(context, error):
        record_error(context, error, True)
        if context.request.user_data.get('inventory_key'):
            with db:
                db.execute("UPDATE inventory_addresses SET status='failed',reason=? WHERE address_key=?",
                           (str(error), context.request.user_data['inventory_key']))
        crawler.stop(f'{context.request.url}: {error}')

    return crawled_addresses


async def persistent_session(args):
    """Create once, then attach to the saved session without rotating it."""
    state_file = args.output / 'session.json'
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state['profile'] != args.profile or state['base_url'] != args.base_url:
            raise ValueError('Saved session belongs to a different Profile or REL endpoint')
        return state['session_id']
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(profile=args.profile, rel_base_url=args.base_url,
                                           persist=True, group='zillow-case-study')
        page = await browser.new_page()
        state = dict(session_id=page.session_id, profile=args.profile, base_url=args.base_url)
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_file)
        return page.session_id


async def crawl(args):
    configuration = Configuration(storage_dir=str(args.output / 'crawlee'), purge_on_start=False)
    storage = FileSystemStorageClient()
    queue = await RequestQueue.open(name='zillow-inventory', configuration=configuration, storage_client=storage)
    db = records.database(args.output / 'zillow.sqlite3')
    first_error_id = db.execute('SELECT COALESCE(MAX(id),0) FROM crawl_errors').fetchone()[0]
    try:
        if not db.execute('SELECT 1 FROM inventory_runs').fetchone():
            features, stamp, snapshot = await asyncio.to_thread(inventory.download, args.output)
            inventory.ingest(db, features, stamp, snapshot)
            inventory.export_addresses(db, args.output / 'inventory/addresses.csv')
        session_id = await persistent_session(args)
        crawler = RelCrawler(session_id=session_id, rel_base_url=args.base_url,
                             configuration=configuration, request_manager=queue,
                             storage_client=storage, event_manager=LocalEventManager(),
                             statistics=Statistics.with_default_state(persistence_enabled=False),
                             max_requests_per_crawl=args.max_pages,
                             max_request_retries=args.attempts - 1,
                             navigation_timeout=timedelta(seconds=getattr(args, 'navigation_timeout', 60)),
                             request_handler_timeout=timedelta(seconds=getattr(args, 'navigation_timeout', 60) + 60),
                             concurrency_settings=ConcurrencySettings(max_concurrency=1,
                                 desired_concurrency=1, max_tasks_per_minute=20))
        addresses = register_handlers(crawler, db, args.max_addresses, getattr(args, 'retry_delay', 5))
        seeds = inventory_seeds(db)
        if getattr(args, 'retry_failed', False):
            recovery = failed_seeds(db)
            print(f'Retrying {len(recovery)} failed transient inventory lookups with a fresh attempt budget.', flush=True)
            await queue.add_requests(recovery, forefront=True, wait_for_all_requests_to_be_added=True)
        print(f'Using persistent REL session: {session_id} (created from {args.profile})', flush=True)
        stats = await crawler.run(seeds, purge_request_queue=False)
        result = {
            'coverage': inventory.summary(db),
            'queue': {'total': await queue.get_total_count(), 'handled': await queue.get_handled_count()},
            'run': {'finished': stats.requests_finished, 'failed': stats.requests_failed,
                    'addresses_crawled': len(addresses), 'address_limit': args.max_addresses,
                    'errors': [dict(row) for row in db.execute(
                        'SELECT * FROM crawl_errors WHERE id>? ORDER BY id', (first_error_id,))]},
            'homes': db.execute('SELECT count(*) FROM homes').fetchone()[0],
            'with_zestimate': db.execute('SELECT count(*) FROM latest_homes WHERE zestimate IS NOT NULL').fetchone()[0],
            'captures': db.execute('SELECT count(*) FROM captures').fetchone()[0],
            'error_history': [dict(row) for row in db.execute('SELECT * FROM crawl_errors ORDER BY id')],
        }
        print(json.dumps(result, indent=2))
        return 2 if stats.requests_failed else 0
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--profile', default='OxylabsDatacenter', help='REL Profile used when creating the crawl session')
    parser.add_argument('--max-addresses', type=int, default=50, help='Maximum distinct home detail pages per run')
    parser.add_argument('--max-pages', type=int, default=500, help='Safety limit including search pages')
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--navigation-timeout', type=float, default=60, help='Seconds allowed per REL navigation')
    parser.add_argument('--retry-delay', type=float, default=5, help='Seconds before retrying transient browser errors')
    parser.add_argument('--retry-failed', action='store_true', help='Explicitly retry failed transient inventory lookups')
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_addresses < 1 or args.attempts < 1:
        parser.error('max-addresses, max-pages and attempts must be positive')
    if not 0 < args.navigation_timeout <= 120 or not 0 <= args.retry_delay <= 60:
        parser.error('navigation-timeout must be in (0,120] and retry-delay in [0,60] seconds')
    return asyncio.run(crawl(args))


if __name__ == '__main__':
    raise SystemExit(main())
