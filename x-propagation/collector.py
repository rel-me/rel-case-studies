"""Bounded, serial REL-only collection. Site content is untrusted data."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from crawlee import Request
from crawlee.configuration import Configuration
from crawlee.storages import RequestQueue
from crawlee.statistics import Statistics
from rel_crawlee import RelCrawler
from selectolax.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
STATUS = re.compile(r"^https://(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)(?:[/?#]|$)", re.I)


def now():
    return datetime.now(UTC).isoformat()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def normalize(url):
    p = urlsplit(url)
    host = p.netloc.lower()
    if host in ('twitter.com', 'www.twitter.com', 'www.x.com'):
        host = 'x.com'
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('twclid', 'fbclid')]
    return urlunsplit((p.scheme.lower(), host, p.path, urlencode(query), p.fragment))


def text(node):
    if node is None:
        return None
    # Emoji images carry text in alt, which .text() otherwise drops.
    node = HTMLParser(node.html).body
    for image in node.css('img[alt]'):
        image.replace_with(image.attributes['alt'] or '')
    for br in node.css('br'):
        br.replace_with('\n')
    return node.text(separator='', strip=False)


def parse_posts(html, location):
    tree = HTMLParser(html)
    records = []
    for article in tree.css('article[data-testid="tweet"]'):
        # Only a time-linked status identifies an article; quoted cards can have
        # their own time. The outer author block is authoritative when present.
        author_block = article.css_first('[data-testid="User-Name"]')
        author_links = author_block.css('a[href]') if author_block else []
        author = None
        for link in author_links:
            href = link.attributes.get('href', '')
            if re.fullmatch(r'/[A-Za-z0-9_]+', href):
                author = href[1:]
                break
        candidates = []
        for anchor in article.css('a[href]'):
            match = STATUS.match(urljoin('https://x.com', anchor.attributes['href']))
            clock = anchor.css_first('time[datetime]')
            parent = anchor.parent
            nested_author = False
            while parent is not None and parent.tag != 'article':
                if parent.attributes.get('data-testid') == 'User-Name':
                    nested_author = author_block is not None and parent.mem_id != author_block.mem_id
                    break
                parent = parent.parent
            if nested_author:
                continue
            if match and clock and (not author or match[1].lower() == author.lower()):
                candidates.append((match, clock))
        if not candidates:
            continue
        match, clock = candidates[0]
        post_id, author = match[2], match[1]
        links = []
        references = []
        for anchor in article.css('a[href]'):
            original = anchor.attributes['href']
            absolute = urljoin('https://x.com', original)
            advertised = anchor.attributes.get('title')
            links.append({'original': original, 'absolute': absolute, 'normalized': normalize(absolute),
                          'visible_text': text(anchor), 'advertised_destination': advertised,
                          'resolved_destination': None})
            reference = STATUS.match(absolute)
            if reference and reference[2] != post_id:
                item = {'post_id': reference[2], 'url': f'https://x.com/{reference[1]}/status/{reference[2]}',
                        'relationship': 'visible_link_unclassified'}
                if item not in references:
                    references.append(item)
        quote = article.css_first('[data-testid="quoteTweet"]')
        if quote is None:
            quote = next((node for node in article.css('div[role="link"]')
                          if node.css_first('[data-testid="User-Name"]') is not None
                          and node.css_first('[data-testid="tweetText"]') is not None), None)
        def outside_quote(node):
            parent = node
            while parent is not None and parent.mem_id != article.mem_id:
                if quote is not None and parent.mem_id == quote.mem_id:
                    return False
                parent = parent.parent
            return True
        body = next((node for node in article.css('[data-testid="tweetText"]')
                     if outside_quote(node)), None)
        incomplete = any(outside_quote(node) for node in
                         article.css('[data-testid="tweet-text-show-more-link"]'))
        social = text(article.css_first('[data-testid="socialContext"]'))
        # Do not infer reply parent IDs from timeline/thread placement.
        kind = 'repost' if social and 'reposted' in social.lower() else ('quote' if quote else 'unclassified')
        counts = [n.attributes.get('aria-label') for n in article.css('[role="group"][aria-label]')]
        counts += [n.attributes.get('aria-label') for n in article.css('button[data-testid][aria-label]')
                   if n.attributes.get('data-testid') in ('reply', 'retweet', 'like', 'bookmark')]
        records.append({'post_id': post_id, 'url': f'https://x.com/{author}/status/{post_id}',
                        'author_handle': author, 'stable_account_id': None,
                        'publication_timestamp_utc': clock.attributes['datetime'],
                        'text': text(body), 'post_type': kind, 'social_context': social,
                        'text_status': 'incomplete_show_more' if incomplete else ('rendered_text' if body else 'missing_or_media_only'),
                        'references': references, 'links': links,
                        'engagement_labels': counts, 'capture_location': location,
                        'quoted_content': text(quote.css_first('[data-testid="tweetText"]')) if quote is not None else None,
                        'quote_reference_status': 'requires_review' if quote is not None else 'not_detected',
                        'community_note': text(article.css_first('[data-testid="birdwatch-pivot"]'))})
    return records


def database():
    path = ROOT / 'data'
    path.mkdir(exist_ok=True)
    db = sqlite3.connect(path / 'study.sqlite3')
    db.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS posts(post_id TEXT PRIMARY KEY, url TEXT, author TEXT);
      CREATE TABLE IF NOT EXISTS versions(post_id TEXT, fingerprint TEXT, record TEXT,
        first_seen TEXT, PRIMARY KEY(post_id,fingerprint));
      CREATE TABLE IF NOT EXISTS observations(run_id TEXT, capture TEXT, post_id TEXT,
        fingerprint TEXT, collected_at TEXT, record TEXT,
        PRIMARY KEY(run_id,capture,post_id));
      CREATE TABLE IF NOT EXISTS coverage(run_id TEXT, request_key TEXT, attempted_at TEXT,
        url TEXT, status TEXT, detail TEXT);
    ''')
    return db


class CollectionWindowEnded(RuntimeError):
    pass


class ExecutionLimitReached(TimeoutError):
    pass


def is_rel_readiness_failure(error):
    message = str(error)
    return any(signal in message for signal in (
        'Timed out waiting for embedded Chromium', 'UPSTREAM_UNAVAILABLE',
        'Could not reach REL at ',
    ))


def is_configured_session_missing(error, session_id):
    return f'SESSION_NOT_FOUND: Browser session {session_id} was not found.' in str(error)


async def run_before_deadline(crawler, requests, deadline, execution_timeout_seconds=1200):
    # Request timeouts do not cover time spent waiting for Crawlee's resource
    # gate to dispatch work. Bound the whole execution and await its cleanup.
    remaining = ((deadline - datetime.now(UTC)).total_seconds()
                 if deadline is not None else float('inf'))
    execution_limited = execution_timeout_seconds < remaining
    timeout = asyncio.timeout(max(0, min(execution_timeout_seconds, remaining)))
    try:
        async with timeout:
            return await crawler.run(requests)
    except TimeoutError as exc:
        if timeout.expired() and execution_limited:
            raise ExecutionLimitReached('Execution time limit reached; retain queue for resume') from exc
        raise


def save_capture(db, run_id, html, location, index, deadline=None):
    if deadline is not None and datetime.now(UTC) >= deadline:
        raise CollectionWindowEnded('Collection deadline reached; no new capture saved')
    timestamp = now()
    folder = ROOT / 'data' / 'raw' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(location.encode()).hexdigest()[:16] + f'-{index}-' + datetime.now(UTC).strftime('%H%M%S%f') + '.html'
    path = folder / name
    path.write_text(html)
    atomic_json(path.with_suffix('.json'), {'collected_at_utc': timestamp, 'url': location,
                'session_id': CONFIG['session_id'], 'run_id': run_id, 'sha256': hashlib.sha256(html.encode()).hexdigest()})
    records = parse_posts(html, location)
    for record in records:
        # Completeness differences are observed variants, not proven edits.
        immutable = {k: v for k, v in record.items() if k not in ('engagement_labels', 'capture_location', 'community_note', 'social_context', 'post_type')}
        fingerprint = hashlib.sha256(json.dumps(immutable, sort_keys=True).encode()).hexdigest()
        record.update(collected_at_utc=timestamp, capture=str(path.relative_to(ROOT)))
        serialized = json.dumps(record, ensure_ascii=False)
        with db:
            db.execute('INSERT OR IGNORE INTO posts VALUES(?,?,?)', (record['post_id'], record['url'], record['author_handle']))
            db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?)', (record['post_id'], fingerprint, serialized, timestamp))
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)',
                       (run_id, str(path.relative_to(ROOT)), record['post_id'], fingerprint, timestamp, serialized))
    return records, str(path.relative_to(ROOT))


def export(db):
    out = ROOT / 'data' / 'exports'
    out.mkdir(exist_ok=True)
    for name, query in [('posts', 'SELECT record FROM versions ORDER BY first_seen,post_id'),
                        ('observations', 'SELECT record FROM observations ORDER BY collected_at,post_id')]:
        path = out / f'{name}.jsonl'
        temp = path.with_suffix('.tmp')
        with temp.open('w') as stream:
            for (record,) in db.execute(query):
                stream.write(record + '\n')
        temp.replace(path)
    columns = ['run_id', 'request_key', 'attempted_at_utc', 'url', 'status', 'detail']
    with (out / 'coverage.jsonl').open('w') as stream:
        for row in db.execute('SELECT * FROM coverage ORDER BY attempted_at'):
            stream.write(json.dumps(dict(zip(columns, row))) + '\n')
    # Deterministic matching only nominates candidates; stance remains for review.
    latest = {}
    for (serialized,) in db.execute('SELECT record FROM observations ORDER BY collected_at'):
        record = json.loads(serialized)
        latest[record['post_id']] = record
    buckets = {}
    for record in latest.values():
        phrase = re.sub(r'\s+', ' ', (record['text'] or '').casefold()).strip()
        if len(phrase) > 70:
            buckets.setdefault('exact_text:' + phrase, []).append(record['url'])
        for link in record['links']:
            host = urlsplit(link['normalized']).netloc
            if host and host not in ('x.com', 't.co'):
                buckets.setdefault('shared_url:' + link['normalized'], []).append(record['url'])
    atomic_json(out / 'matching-candidates.json', [{'signal': key, 'posts': sorted(set(urls)),
                'status': 'requires_semantic_and_stance_review'} for key, urls in buckets.items() if len(set(urls)) > 1])


def plan_requests(config, cycle, clock=None):
    """History is a baseline operation; recurring runs revisit every timeline."""
    seeds = config['pilot_seeds'] if cycle == 'pilot' else config['seeds']
    plans = [(f'https://x.com/{handle}', 'timeline') for handle in seeds]
    if cycle in ('pilot', 'baseline'):
        start = ((clock or datetime.now(UTC)) - timedelta(days=config['historical_days'])).date().isoformat()
        plans += [('https://x.com/search?' + urlencode({'q': f'from:{handle} since:{start}', 'f': 'live'}), 'history')
                  for handle in seeds]
        plans += [(url, 'evidence') for url in config['evidence_urls']
                  if cycle != 'pilot' or url.split('/')[3].lower() in {h.lower() for h in seeds}]
        plans += [('https://x.com/search?' + urlencode({'q': q, 'f': 'live'}), 'expansion')
                  for q in config['expansion_queries'][:config['search_budget']]]
    if len(plans) > config['max_requests_per_cycle']:
        raise ValueError('Plan exceeds request budget')
    return plans


async def collect(args):
    db = database()
    state_path = ROOT / 'data' / 'checkpoint.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if args.cycle == 'monitor' and not state.get('pilot_verified'):
        raise SystemExit('Pilot must be verified before monitoring; see README.')
    if args.cycle == 'monitor' and not state.get('monitor_started_at_utc'):
        state['monitor_started_at_utc'] = now()
        state['monitor_ends_at_utc'] = (datetime.now(UTC) + timedelta(days=CONFIG['duration_days'])).isoformat()
    deadline = datetime.fromisoformat(state['monitor_ends_at_utc']) if state.get('monitor_ends_at_utc') else None
    if deadline is not None and datetime.now(UTC) >= deadline:
        export(db)
        atomic_json(ROOT / 'data' / 'completion.json', {'completed_at_utc': now(), 'status': 'collection_window_ended',
                    'final_report_status': 'requires_final_evidence_review'})
        return
    resume = state.get('active_run')
    if resume and resume['cycle'] != args.cycle:
        raise SystemExit('Resume the unfinished cycle before starting another cycle.')
    run_id = resume['id'] if resume else datetime.now(UTC).strftime('%Y%m%dT%H%M%S') + '-' + args.cycle
    state['active_run'] = dict(resume) if resume else {'id': run_id, 'cycle': args.cycle}
    atomic_json(state_path, state)
    config = Configuration(storage_dir=str(ROOT / 'data' / 'queues'), purge_on_start=False)
    queue = await RequestQueue.open(name=run_id, configuration=config)
    run_clock = datetime.strptime(run_id.split('-')[0], '%Y%m%dT%H%M%S').replace(tzinfo=UTC)
    plans = plan_requests(CONFIG, args.cycle, clock=run_clock)
    detail_keys = set(resume.get('details', [])) if resume else set()
    crawler = RelCrawler(session_id=CONFIG['session_id'], configuration=config, request_manager=queue,
                         statistics=Statistics.with_default_state(persistence_enabled=True,
                                    persist_state_key=f'{run_id}-statistics'),
                         max_requests_per_crawl=CONFIG['max_requests_per_cycle'],
                         max_request_retries=CONFIG['max_request_retries'],
                         navigation_timeout=timedelta(seconds=45),
                         request_handler_timeout=timedelta(seconds=150))
    readiness_failures = 0

    def coverage(context, status, detail):
        with db:
            db.execute('INSERT INTO coverage VALUES(?,?,?,?,?,?)',
                       (run_id, context.request.unique_key, now(), context.request.url, status, detail))

    def service_failure(context, error):
        nonlocal readiness_failures
        if is_configured_session_missing(error, CONFIG['session_id']):
            coverage(context, 'cycle_stopped_session_unavailable', str(error))
            crawler.stop(reason='Configured caller-owned REL session is unavailable; restore the existing session before resuming')
            return
        if not is_rel_readiness_failure(error):
            return
        readiness_failures += 1
        if readiness_failures == 3:
            coverage(context, 'cycle_stopped_rel_unavailable', str(error))
            crawler.stop(reason='Three REL transport or browser readiness failures in this execution; retain queue for the next scheduled run')

    @crawler.router.default_handler
    async def handler(context):
        kind = context.request.user_data.get('kind', 'detail')
        coverage(context, 'attempt_started', kind)
        page = context.page
        context.request.user_data['_collector_stage'] = 'readiness_html_capture'
        readiness_html = await page.content()
        try:
            if not parse_posts(readiness_html, page.url):
                context.request.user_data['_collector_stage'] = 'readiness_selector_wait'
                await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        except Exception as exc:
            context.request.user_data['_collector_stage'] = 'html_capture_after_wait_error'
            html = await page.content()
            recovered, capture = save_capture(db, run_id, html, page.url, 0, deadline)
            visible = HTMLParser(html).text(separator=' ', strip=True)
            status = ('readiness_wait_warning_posts_recovered' if recovered else
                      'login_required' if '/login' in page.url or 'Sign in to X' in visible else 'no_posts_or_inaccessible')
            coverage(context, status, json.dumps({'error': str(exc), 'capture': capture}))
            if not recovered:
                return
        batches = CONFIG['max_scrolls'] + 1 if kind in ('timeline', 'history', 'expansion') else 1
        total = 0
        for index in range(batches):
            context.request.user_data['_collector_stage'] = f'html_capture_{index}'
            html = await page.content()
            records, capture = save_capture(db, run_id, html, page.url, index, deadline)
            expected = STATUS.match(context.request.url)
            if expected and not any(record['post_id'] == expected[2] for record in records):
                coverage(context, 'target_post_missing', json.dumps({'capture': capture}))
                raise RuntimeError('Rendered articles did not include the requested post ID')
            total += len(records)
            if kind != 'detail' and kind != 'expansion':
                for record in records:
                    if record['text_status'] != 'incomplete_show_more':
                        continue
                    url = record['url']
                    if url in detail_keys or len(detail_keys) >= CONFIG['max_detail_visits']:
                        continue
                    if len(plans) + len(detail_keys) >= CONFIG['max_requests_per_cycle']:
                        break
                    detail_keys.add(url)
                    state['active_run']['details'] = sorted(detail_keys)
                    atomic_json(state_path, state)
                    await queue.add_request(Request.from_url(url, user_data={'kind': 'detail'}))
            if index + 1 < batches:
                context.request.user_data['_collector_stage'] = f'page_down_after_capture_{index}'
                await page.press('body', 'PageDown')
                await page.wait_for_timeout(1000)
        coverage(context, 'bounded_capture', json.dumps({'articles_observed': total, 'captures': batches,
                 'historical_window_exhausted': False, 'http_status': context.response.status}))

    @crawler.error_handler
    async def attempt_failed(context, error):
        message = str(error)
        coverage(context, 'attempt_failed', json.dumps({'error': message,
                 'stage': context.request.user_data.get('_collector_stage', 'navigation')}))
        context.request.user_data['_collector_stage'] = 'navigation'
        service_failure(context, error)

    @crawler.failed_request_handler
    async def failed(context, error):
        coverage(context, 'retries_exhausted', str(error))
        # Crawlee calls this instead of error_handler on the final attempt.
        service_failure(context, error)

    # A handler can persist a detail checkpoint before Crawlee flushes its
    # deferred enqueue. Re-submit saved details on resume; queue deduplication
    # preserves handled requests and retry counts.
    requests = [Request.from_url(url, user_data={'kind': kind}) for url, kind in plans]
    requests += [Request.from_url(url, user_data={'kind': 'detail'}) for url in sorted(detail_keys)]
    execution_limited = False
    try:
        await run_before_deadline(crawler, requests, deadline, CONFIG['max_execution_seconds'])
    except ExecutionLimitReached as exc:
        execution_limited = True
        with db:
            db.execute('INSERT INTO coverage VALUES(?,?,?,?,?,?)',
                       (run_id, '__execution__', now(), None, 'execution_time_limit', str(exc)))
    except (TimeoutError, CollectionWindowEnded):
        if deadline is None or datetime.now(UTC) < deadline:
            raise
    if deadline is not None and datetime.now(UTC) >= deadline:
        export(db)
        atomic_json(ROOT / 'data' / 'completion.json', {'completed_at_utc': now(),
                    'status': 'collection_window_ended', 'active_cycle_interrupted': run_id,
                    'final_report_status': 'requires_final_evidence_review'})
        return
    for url in sorted(detail_keys):
        await queue.add_request(Request.from_url(url, user_data={'kind': 'detail'}))
    export(db)
    if execution_limited:
        atomic_json(state_path, state)
        raise SystemExit('Execution time limit reached; checkpoint retained. Resume with the same --cycle.')
    if not await queue.is_finished():
        atomic_json(state_path, state)
        raise SystemExit('Cycle interrupted; checkpoint retained. Resume with the same --cycle.')
    state['last_completed_run'] = state.pop('active_run')
    state['last_completed_at_utc'] = now()
    if args.cycle == 'baseline':
        state['baseline_completed_at_utc'] = state['last_completed_at_utc']
    atomic_json(state_path, state)
    print(json.dumps({'run_id': run_id, 'status': 'cycle_completed', 'unique_posts': db.execute('SELECT count(*) FROM posts').fetchone()[0]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cycle', choices=['pilot', 'baseline', 'monitor'], default='pilot')
    args = parser.parse_args()
    CONFIG = json.loads((ROOT / 'config.json').read_text())
    (ROOT / 'data').mkdir(exist_ok=True)
    with (ROOT / 'data' / 'collector.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another cycle is running; skipped to protect the shared REL session.')
        asyncio.run(collect(args))
