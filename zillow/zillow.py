#!/usr/bin/env python3
"""Crawl Santa Cruz property pages with rel-crawlee and Profile-managed REL sessions."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import random
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
from session_recovery import RotationRules, SessionRecovery

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


def inventory_seeds(db, priority_file=None):
    rows=db.execute(
        "SELECT * FROM inventory_addresses WHERE status IN ('pending','resolving') AND lookup_url IS NOT NULL "
        "AND address_key IN (SELECT address_key FROM inventory_parcels WHERE classification='residential') "
        "ORDER BY CASE WHEN address LIKE '%DOUGMAR%' THEN 0 ELSE 1 END,address_key").fetchall()
    if priority_file:
        order=json.loads(Path(priority_file).read_text())['address_keys']
        positions={key:i for i,key in enumerate(order)}
        rows.sort(key=lambda row:(positions.get(row['address_key'],len(positions)),row['address_key']))
    return [inventory_request(row) for row in rows]


async def prioritize_inventory_queue(queue, seeds):
    # Crawlee forefront requests are LIFO. Reverse the desired order and await
    # all batches so the first priority really is the next pending request.
    await queue.add_requests(list(reversed(seeds)),forefront=True,
        wait_time_between_batches=timedelta(seconds=0),wait_for_all_requests_to_be_added=True)


async def match_inventory(context, db, capture, data, final, priority_queue=None):
    key = context.request.user_data['inventory_key']
    target=db.execute('SELECT * FROM inventory_addresses WHERE address_key=?',(key,)).fetchone()
    expected=inventory.address_key(target['address']) if target else key
    def matches(address):
        if inventory.address_key(address.get('streetAddress') or '')!=expected or address.get('state')!='CA': return False
        if key.startswith('county:'):
            return str(address.get('zipcode') or '')[:5]==str(target['postal_code'])[:5]
        return str(address.get('city','')).casefold()=='santa cruz'
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
        if not matches(address):
            outcome('ambiguous', 'Zillow street address or unit does not match GIS')
            return None
        with db:
            records.save_home(db, capture, final, facts, county_inventory_key=key if key.startswith('county:') else None)
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
                and matches(info)):
            candidates[match[1]] = link
    if len(candidates) == 1:
        zpid, link = next(iter(candidates.items()))
        detail=Request.from_url(link,unique_key='inventory-detail:'+key,user_data={'inventory_key':key})
        if priority_queue is not None:
            await priority_queue.add_request(detail,forefront=True)
        else:
            await context.add_requests([detail])
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
               ('timed out', 'timeout', 'err_timed_out', 'request_cancelled', 'err_network_changed',
                'err_network_io_suspended', 'err_connection_reset', 'err_connection_closed'))


def failed_seeds(db, rules=None):
    """Explicit recovery batches have stable keys so interrupted runs deduplicate."""
    seeds = []
    for row in db.execute("SELECT * FROM inventory_addresses WHERE status='failed'"):
        status = re.search(r'status code:\s*(\d{3})', row['reason'] or '')
        eligible_status = rules and status and int(status[1]) in rules.http_statuses
        if not transient_failure(row['reason'] or '') and not eligible_status:
            continue
        detail = row['matched_url'] and records.HOME.fullmatch(urlsplit(row['matched_url']).path)
        url = row['matched_url'] if detail else row['lookup_url']
        error = db.execute('SELECT MAX(id) FROM crawl_errors WHERE url=? AND terminal=1', (url,)).fetchone()[0]
        if not url or error is None:
            continue
        seeds.append(Request.from_url(url, unique_key=f"inventory-retry:{row['address_key']}:{error}",
                                      user_data={'inventory_key': row['address_key']}))
    return seeds


def register_handlers(crawler, db, max_addresses=50, retry_delay=5, recovery=None, crawled_addresses=None, skip_failed=False, max_consecutive_failures=10, progress_file=None, priority_queue=None):
    crawled_addresses = set() if crawled_addresses is None else crawled_addresses
    progress=json.loads(progress_file.read_text()) if progress_file and progress_file.exists() else {}
    failures=progress.get('consecutive_failures',0)
    def save_progress(reason=None):
        progress.update(consecutive_failures=failures, failure_limit=max_consecutive_failures,
                        stop_reason=reason,updated_at=datetime.now(timezone.utc).isoformat())
        if progress_file:
            temporary=progress_file.with_suffix('.tmp');temporary.write_text(json.dumps(progress,indent=2)+'\n');temporary.replace(progress_file)
    async def process(context):
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
        if recovery:
            recovery.successful_capture()
        if context.request.user_data.get('inventory_key'):
            zpid = await match_inventory(context, db, capture, data, final, priority_queue=priority_queue)
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

    @crawler.router.default_handler
    async def handle(context):
        nonlocal failures
        await process(context)
        failures=0
        save_progress()
        if skip_failed: await asyncio.sleep(random.uniform(3,4))

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
        nonlocal failures
        record_error(context, error, True)
        failures += 1
        blocked=isinstance(error,AccessBlocked) or RotationRules.target_status(error) in (401,403,429) or re.search(r'captcha|verify.*human|access.denied',str(error),re.I)
        reason=('Access restriction; queue retained' if blocked else
                f'More than {max_consecutive_failures} consecutive failures; queue retained' if failures>max_consecutive_failures else None)
        save_progress(reason)
        if skip_failed:
            key=context.request.user_data.get('inventory_key')
            if key:
                with db: db.execute("UPDATE inventory_addresses SET status=?,reason=? WHERE address_key=?",('skipped' if RotationRules.target_status(error)==404 else 'failed',str(error),key))
            if reason: crawler.stop(reason)
            return
        rules = recovery.rules if recovery else RotationRules()
        if rules.skips(error):
            reason = str(error)
            with db:
                db.execute('INSERT OR REPLACE INTO skipped_urls VALUES(?,?,?)',
                           (context.request.url, reason, datetime.now(timezone.utc).isoformat()))
                if context.request.user_data.get('inventory_key'):
                    db.execute("UPDATE inventory_addresses SET status='skipped',reason=? WHERE address_key=?",
                               (reason, context.request.user_data['inventory_key']))
            context.log.warning(f'Skipped missing page; continuing: {context.request.url}')
            action = recovery.skipped(error) if recovery else 'continue'
            if action == 'rotate':
                crawler.stop('Consecutive missing pages; replace REL session before continuing')
            elif action == 'limit':
                crawler.stop('Session recovery limit reached after consecutive missing pages')
            return
        target_status = RotationRules.target_status(error)
        if target_status in (403, 429):
            crawler.stop(f'{context.request.url}: HTTP {target_status} access restriction; stopping without retry or session/proxy rotation')
            return
        if context.request.user_data.get('inventory_key'):
            with db:
                db.execute("UPDATE inventory_addresses SET status='failed',reason=? WHERE address_key=?",
                           (str(error), context.request.user_data['inventory_key']))
        if recovery and recovery.rules.matches(error):
            retries = context.request.user_data.get('_rotation_retries', 0)
            retry_payload = None
            if retries < recovery.rules.retries_per_url:
                root_key = context.request.user_data.get('_rotation_root', context.request.unique_key)
                retry_payload = {'url': context.request.url,
                    'unique_key': f'{root_key}:session-recovery:{retries + 1}',
                    'user_data': {**context.request.user_data, '_rotation_root': root_key,
                                  '_rotation_retries': retries + 1}}
            if recovery.schedule(error, retry_payload):
                context.log.warning('Stopping this session for bounded recovery; queue retained')
                crawler.stop(f'Rotate REL session: {context.request.url}: {error}')
                return
            context.log.warning('Session recovery limit reached; stopping with checkpoint retained')
        if transient_failure(str(error)):
            context.log.warning(f'Lookup exhausted its retry budget; recorded failure and continuing: {context.request.url}')
            return
        crawler.stop(f'{context.request.url}: {error}')

    return crawled_addresses


async def persistent_session(args):
    """Legacy helper for callers without automatic recovery."""
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


def crawl_configuration(output):
    # REL does the browser work in another process. Keep this serial, rate-limited
    # worker moving even when unrelated host work saturates the CPU. Memory and
    # event-loop overload checks retain their defaults.
    return Configuration(storage_dir=str(output / 'crawlee'), purge_on_start=False,
                         max_used_cpu_ratio=1.0)


async def crawl(args):
    configuration = crawl_configuration(args.output)
    storage = FileSystemStorageClient()
    queue = await RequestQueue.open(name='zillow-inventory', configuration=configuration, storage_client=storage)
    db = records.database(args.output / 'zillow.sqlite3')
    first_error_id = db.execute('SELECT COALESCE(MAX(id),0) FROM crawl_errors').fetchone()[0]
    try:
        if not db.execute('SELECT 1 FROM inventory_runs').fetchone():
            features, stamp, snapshot = await asyncio.to_thread(inventory.download, args.output)
            inventory.ingest(db, features, stamp, snapshot)
            inventory.export_addresses(db, args.output / 'inventory/addresses.csv')
        rules = RotationRules.load(getattr(args, 'rotation_rules', ROOT / 'rotation-rules.json'))
        recovery = SessionRecovery(args.output / 'session.json', args.profile, args.base_url,
                                   'zillow-case-study', rules)
        async def enqueue_recovery(payload):
            await queue.add_requests([Request.from_url(**payload)], forefront=True,
                                     wait_for_all_requests_to_be_added=True)
        # Finish a persisted replacement/retry before driving any pending URL.
        if recovery.pending:
            await recovery.recover(enqueue_recovery)
        session_id = await recovery.ensure()
        if getattr(args, 'rotate_session', False):
            await asyncio.sleep(rules.cooldown_seconds)
            session_id = await recovery.replace()
        if getattr(args,'county_inventory',None):
            print(json.dumps({'county_inventory':inventory.ingest_county(db,args.county_inventory)}),flush=True)
        progress_file=args.output/'zillow-progress.json'
        if progress_file.exists() and json.loads(progress_file.read_text()).get('stop_reason'):
            print('Saved safety stop requires explicit continuation',flush=True)
            return 2
        priority_file=getattr(args,'priority_file',None)
        seeds = inventory_seeds(db,priority_file) if priority_file else inventory_seeds(db)
        if priority_file:
            await prioritize_inventory_queue(queue,seeds)
            print(f'Applied saved geographic-area priority to {len(seeds)} pending inventory requests',flush=True)
            seeds=[]
        if getattr(args, 'retry_failed', False):
            retry_seeds = failed_seeds(db, rules)
            print(f'Retrying {len(retry_seeds)} failed recoverable inventory lookups with a fresh attempt budget.', flush=True)
            await queue.add_requests(retry_seeds, forefront=True, wait_for_all_requests_to_be_added=True)
        addresses = set()
        finished = failed_count = 0
        while finished + failed_count < args.max_pages and len(addresses) < args.max_addresses:
            crawler = RelCrawler(session_id=session_id, rel_base_url=args.base_url,
                                 configuration=configuration, request_manager=queue,
                                 storage_client=storage, event_manager=LocalEventManager(),
                                 statistics=Statistics.with_default_state(persistence_enabled=False),
                                 max_requests_per_crawl=args.max_pages - finished - failed_count,
                                 max_request_retries=args.attempts - 1,
                                 navigation_timeout=timedelta(seconds=getattr(args, 'navigation_timeout', 60)),
                                 request_handler_timeout=timedelta(seconds=getattr(args, 'navigation_timeout', 60) + 60),
                                 concurrency_settings=ConcurrencySettings(max_concurrency=1,
                                     desired_concurrency=1, max_tasks_per_minute=20))
            register_handlers(crawler, db, args.max_addresses, getattr(args, 'retry_delay', 5),
                              recovery=recovery, crawled_addresses=addresses,
                              skip_failed=getattr(args,'skip_failed',False),
                              max_consecutive_failures=getattr(args,'max_consecutive_failures',10),
                              progress_file=progress_file,priority_queue=queue if priority_file else None)
            print(f'Using persistent REL session: {session_id} (created from {args.profile})', flush=True)
            stats = await crawler.run(seeds, purge_request_queue=False)
            seeds = []
            finished += stats.requests_finished
            failed_count += stats.requests_failed
            if not recovery.pending:
                break
            if finished + failed_count >= args.max_pages:
                break  # Next process resumes the checkpointed recovery.
            await recovery.recover(enqueue_recovery)
            session_id = await recovery.ensure()
        result = {
            'coverage': inventory.summary(db),
            'queue': {'total': await queue.get_total_count(), 'handled': await queue.get_handled_count()},
            'run': {'finished': finished, 'failed': failed_count,
                    'addresses_crawled': len(addresses), 'address_limit': args.max_addresses,
                    'session_rotations': recovery.rotations,
                    'errors': [dict(row) for row in db.execute(
                        'SELECT * FROM crawl_errors WHERE id>? ORDER BY id', (first_error_id,))]},
            'homes': db.execute('SELECT count(*) FROM homes').fetchone()[0],
            'with_zestimate': db.execute('SELECT count(*) FROM latest_homes WHERE zestimate IS NOT NULL').fetchone()[0],
            'captures': db.execute('SELECT count(*) FROM captures').fetchone()[0],
            'error_history': [dict(row) for row in db.execute('SELECT * FROM crawl_errors ORDER BY id')],
        }
        print(json.dumps(result, indent=2))
        return 2 if failed_count else 0
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--profile', required=True, help='REL Profile name (configured with any proxy or no proxy)')
    parser.add_argument('--max-addresses', type=int, default=50, help='Maximum distinct home detail pages per run')
    parser.add_argument('--max-pages', type=int, default=500, help='Safety limit including search pages')
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--skip-failed',action='store_true',help='Skip exhausted resources and continue without session rotation')
    parser.add_argument('--max-consecutive-failures',type=int,default=10,help='Stop after more than this many consecutive failed resources')
    parser.add_argument('--priority-file',type=Path,help='Saved address-key order to apply to the existing persistent queue')
    parser.add_argument('--county-inventory',type=Path,help='Completed county residential inventory to add without resetting existing outcomes')
    parser.add_argument('--navigation-timeout', type=float, default=60, help='Seconds allowed per REL navigation')
    parser.add_argument('--retry-delay', type=float, default=5, help='Seconds before retrying transient browser errors')
    parser.add_argument('--retry-failed', action='store_true',
                        help='Explicitly retry failed transient or configured HTTP inventory lookups')
    parser.add_argument('--rotation-rules', type=Path, default=ROOT / 'rotation-rules.json',
                        help='JSON rules for bounded session/proxy recovery')
    parser.add_argument('--rotate-session', action='store_true',
                        help='Replace the managed REL session before resuming the queue')
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_addresses < 1 or args.attempts < 1:
        parser.error('max-addresses, max-pages and attempts must be positive')
    if not 0 < args.navigation_timeout <= 120 or not 0 <= args.retry_delay <= 60:
        parser.error('navigation-timeout must be in (0,120] and retry-delay in [0,60] seconds')
    return asyncio.run(crawl(args))


if __name__ == '__main__':
    raise SystemExit(main())
