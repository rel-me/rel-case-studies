import unittest
from collector import (CollectionWindowEnded, ExecutionLimitReached, normalize, parse_posts, plan_requests,
                       run_before_deadline, save_capture)
from pathlib import Path
import json
import asyncio
from datetime import datetime, timedelta, timezone
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
import collector


class ExtractionTests(unittest.TestCase):
    def test_expanded_sample_fits_baseline_and_revisits_all_accounts(self):
        config = json.loads(Path(__file__).with_name('config.json').read_text())
        self.assertTrue(20 <= len(config['seeds']) <= 30)
        self.assertEqual(len(config['seeds']), len({s.lower() for s in config['seeds']}))
        baseline = plan_requests(config, 'baseline')
        self.assertLessEqual(len(baseline) + config['max_detail_visits'], config['max_requests_per_cycle'])
        monitor = plan_requests(config, 'monitor')
        self.assertEqual({url for url, kind in monitor if kind == 'timeline'},
                         {f'https://x.com/{handle}' for handle in config['seeds']})
        self.assertFalse(any(kind == 'history' for _, kind in monitor))

    def test_nested_quote_is_not_outer_author_or_text(self):
        html = '''<article data-testid="tweet">
        <div data-testid="User-Name"><a href="/outer">Outer</a></div>
        <div data-testid="tweetText">Hello<img alt="🇺🇸"><br>world</div>
        <div data-testid="quoteTweet"><div data-testid="User-Name"><a href="/inner">Inner</a></div>
        <div data-testid="tweetText">Quoted only</div>
        <a href="/inner/status/222"><time datetime="2026-10-10T12:00:00Z">then</time></a></div>
        <a href="/outer/status/333"><time datetime="2026-10-11T12:00:00Z">now</time></a>
        <img alt=""><button data-testid="tweet-text-show-more-link">Show more</button>
        </article>'''
        record, = parse_posts(html, 'test')
        self.assertEqual(record['post_id'], '333')
        self.assertEqual(record['author_handle'], 'outer')
        self.assertEqual(record['text'], 'Hello🇺🇸\nworld')
        self.assertEqual(record['text_status'], 'incomplete_show_more')
        self.assertEqual(record['references'][0]['post_id'], '222')
        self.assertEqual(record['references'][0]['relationship'], 'visible_link_unclassified')

    def test_tracking_normalization_keeps_meaningful_query(self):
        self.assertEqual(normalize('https://example.com/a?id=4&utm_source=x&lang=en#part'),
                         'https://example.com/a?id=4&lang=en#part')

    def test_media_only_quote_does_not_attribute_quoted_words_to_outer_post(self):
        html = '''<article data-testid="tweet">
        <div data-testid="User-Name"><a href="/outer">Outer</a></div>
        <a href="/outer/status/333"><time datetime="2026-10-11T12:00:00Z">now</time></a>
        <div role="link"><div data-testid="User-Name"><a href="/inner">Inner</a></div>
        <div data-testid="tweetText">Quoted only</div>
        <button data-testid="tweet-text-show-more-link">Show more</button></div>
        </article>'''
        record, = parse_posts(html, 'test')
        self.assertIsNone(record['text'])
        self.assertEqual(record['text_status'], 'missing_or_media_only')
        self.assertEqual(record['quoted_content'], 'Quoted only')

    def test_quoted_show_more_does_not_mark_complete_outer_text_incomplete(self):
        html = '''<article data-testid="tweet">
        <div data-testid="User-Name"><a href="/outer">Outer</a></div>
        <a href="/outer/status/333"><time datetime="2026-10-11T12:00:00Z">now</time></a>
        <div data-testid="tweetText">My reaction</div>
        <div data-testid="quoteTweet"><div data-testid="tweetText">Partial quote</div>
        <button data-testid="tweet-text-show-more-link">Show more</button></div>
        </article>'''
        record, = parse_posts(html, 'test')
        self.assertEqual(record['text'], 'My reaction')
        self.assertEqual(record['text_status'], 'rendered_text')

    def test_missing_time_does_not_invent_id_or_timestamp(self):
        self.assertEqual(parse_posts('<article data-testid="tweet"><a href="/a/status/1">one</a></article>', 'test'), [])

    def test_expired_capture_cannot_write_new_records(self):
        with self.assertRaises(CollectionWindowEnded):
            save_capture(None, 'unused', '<html/>', 'unused', 0,
                         datetime.now(timezone.utc) - timedelta(seconds=1))

    def test_latest_textless_quote_does_not_reuse_older_misattributed_text(self):
        phrase = 'A long distinctive quoted headline with enough words to nominate a matching candidate across posts.'
        with tempfile.TemporaryDirectory() as folder, patch.object(collector, 'ROOT', Path(folder)):
            db = collector.database()
            records = [('1', phrase), ('1', None), ('2', phrase)]
            for index, (post_id, body) in enumerate(records):
                record = {'post_id': post_id, 'text': body, 'links': [],
                          'url': f'https://x.com/example/status/{post_id}'}
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',
                           ('run', str(index), post_id, str(index), str(index), json.dumps(record)))
            collector.export(db)
            candidates = json.loads((Path(folder) / 'data/exports/matching-candidates.json').read_text())
            self.assertEqual(candidates, [])
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 3)
            db.close()


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_cap_exports_and_retains_active_checkpoint(self):
        class FakeCrawler:
            def __init__(self, **kwargs):
                self.router = SimpleNamespace(default_handler=lambda handler: handler)

            def error_handler(self, handler):
                return handler

            def failed_request_handler(self, handler):
                return handler

            async def run(self, requests):
                await asyncio.sleep(3600)

        state = {'pilot_verified': True,
                 'monitor_started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'monitor_ends_at_utc': (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                 'baseline_completed_at_utc': 'preserved',
                 'active_run': {'id': '20261020T231621-monitor', 'cycle': 'monitor'}}
        config = json.loads(Path(__file__).with_name('config.json').read_text())
        config['max_execution_seconds'] = 0.02
        queue = AsyncMock()
        queue.is_finished.return_value = True  # Cancellation must not imply completion.
        db = MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/checkpoint.json').write_text(json.dumps(state))
            with patch.object(collector, 'ROOT', root), patch.object(collector, 'CONFIG', config, create=True), \
                    patch.object(collector, 'database', return_value=db), \
                    patch.object(collector, 'export') as export, \
                    patch.object(collector, 'RelCrawler', FakeCrawler), \
                    patch.object(collector.RequestQueue, 'open', AsyncMock(return_value=queue)):
                with self.assertRaisesRegex(SystemExit, 'Execution time limit reached'):
                    await collector.collect(SimpleNamespace(cycle='monitor'))
                export.assert_called_once_with(db)
            self.assertEqual(json.loads((root / 'data/checkpoint.json').read_text()), state)
        queue.is_finished.assert_not_awaited()
        self.assertEqual(db.execute.call_args.args[1][4], 'execution_time_limit')

    async def test_execution_limit_cancels_and_awaits_cleanup_before_study_deadline(self):
        class FakeCrawler:
            cleaned_up = False

            async def run(self, requests):
                try:
                    await asyncio.sleep(3600)
                finally:
                    await asyncio.sleep(0.02)
                    self.cleaned_up = True

        crawler = FakeCrawler()
        deadline = datetime.now(timezone.utc) + timedelta(days=14)
        with self.assertRaises(ExecutionLimitReached):
            await run_before_deadline(crawler, [], deadline, execution_timeout_seconds=0.02)
        self.assertTrue(crawler.cleaned_up)

    async def test_execution_limit_does_not_mask_crawler_timeout(self):
        class FakeCrawler:
            async def run(self, requests):
                raise TimeoutError('request failed')

        with self.assertRaisesRegex(TimeoutError, 'request failed') as error:
            await run_before_deadline(FakeCrawler(), [], None)
        self.assertNotIsInstance(error.exception, ExecutionLimitReached)

    async def test_completed_execution_returns_without_waiting_for_limit(self):
        class FakeCrawler:
            async def run(self, requests):
                return requests

        self.assertEqual(await run_before_deadline(FakeCrawler(), ['done'], None), ['done'])

    async def test_deadline_cancels_and_awaits_the_running_cycle(self):
        class FakeCrawler:
            cleaned_up = False

            async def run(self, requests):
                try:
                    await asyncio.sleep(3600)
                finally:
                    self.cleaned_up = True

        crawler = FakeCrawler()
        with self.assertRaises(TimeoutError) as error:
            await run_before_deadline(crawler, [],
                                     datetime.now(timezone.utc) + timedelta(milliseconds=20))
        self.assertNotIsInstance(error.exception, ExecutionLimitReached)
        self.assertTrue(crawler.cleaned_up)


class ServiceFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_session_is_specific_to_configured_caller_owned_session(self):
        message = 'SESSION_NOT_FOUND: Browser session Session16781 was not found.'
        self.assertTrue(collector.is_configured_session_missing(RuntimeError(message), 'Session16781'))
        for unrelated in ('Page was not found.',
                          'SESSION_NOT_FOUND: Browser session Session2 was not found.',
                          'Browser session Session16781 was not found.'):
            self.assertFalse(collector.is_configured_session_missing(RuntimeError(unrelated), 'Session16781'))

    def test_only_rel_service_errors_are_readiness_failures(self):
        for message in ('Timed out waiting for embedded Chromium after 30.0 seconds',
                        'UPSTREAM_UNAVAILABLE',
                        'Could not reach REL at http://127.0.0.1:17319/v1: Connection refused',
                        'Could not reach REL at http://127.0.0.1:17319/v1: Remote end closed connection without response'):
            self.assertTrue(collector.is_rel_readiness_failure(RuntimeError(message)))
        for message in ('Target post missing', 'Connection refused by unrelated service',
                        'Request handler timed out after 150 seconds'):
            self.assertFalse(collector.is_rel_readiness_failure(RuntimeError(message)))

    async def test_three_retry_or_final_service_failures_stop_and_preserve_pending_cycle(self):
        errors = [
            'Could not reach REL at http://127.0.0.1:17319/v1: Connection refused',
            'Timed out waiting for embedded Chromium after 30.0 seconds',
            'Could not reach REL at http://127.0.0.1:17319/v1: Remote end closed connection without response',
        ]
        await self.assert_failures_preserve_cycle(errors, ('final', 'retry', 'final'),
                                                  'cycle_stopped_rel_unavailable')

    async def test_missing_session_stops_after_first_retry_or_final_failure(self):
        for callback in ('retry', 'final'):
            with self.subTest(callback=callback):
                await self.assert_failures_preserve_cycle(
                    ['SESSION_NOT_FOUND: Browser session Session16781 was not found.'],
                    (callback,), 'cycle_stopped_session_unavailable')

    async def assert_failures_preserve_cycle(self, errors, callbacks, stop_status):
        crawlers = []

        class FakeCrawler:
            def __init__(self, **kwargs):
                self.router = SimpleNamespace(default_handler=lambda handler: handler)
                self.stopped = False
                self.dispatched = []
                self.pending = []
                crawlers.append(self)

            def error_handler(self, handler):
                self.retry_handler = handler
                return handler

            def failed_request_handler(self, handler):
                self.final_handler = handler
                return handler

            def stop(self, **kwargs):
                self.stopped = True

            async def run(self, requests):
                self.pending = list(requests)
                for callback, message in zip(callbacks, errors):
                    handler = self.final_handler if callback == 'final' else self.retry_handler
                    request = self.pending.pop(0)
                    self.dispatched.append(request.url)
                    await handler(SimpleNamespace(request=request), RuntimeError(message))
                    if self.stopped:
                        return
                raise AssertionError('Collector did not stop after expected service failures')

        state = {'pilot_verified': True,
                 'monitor_started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'monitor_ends_at_utc': (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                 'baseline_completed_at_utc': 'preserved',
                 'active_run': {'id': '20261021T004601-monitor', 'cycle': 'monitor'}}
        config = json.loads(Path(__file__).with_name('config.json').read_text())
        queue = AsyncMock()
        queue.is_finished.return_value = False
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/checkpoint.json').write_text(json.dumps(state))
            with patch.object(collector, 'ROOT', root), patch.object(collector, 'CONFIG', config, create=True), \
                    patch.object(collector, 'RelCrawler', FakeCrawler), \
                    patch.object(collector.RequestQueue, 'open', AsyncMock(return_value=queue)):
                db = collector.database()
                try:
                    with patch.object(collector, 'database', return_value=db), \
                            self.assertRaisesRegex(SystemExit, 'Cycle interrupted'):
                        await collector.collect(SimpleNamespace(cycle='monitor'))
                finally:
                    db.close()
            self.assertEqual(json.loads((root / 'data/checkpoint.json').read_text()), state)
            rows = [json.loads(line) for line in (root / 'data/exports/coverage.jsonl').read_text().splitlines()]
            self.assertEqual([r['detail'] if r['status'] == 'retries_exhausted'
                              else json.loads(r['detail'])['error'] for r in rows[:-1]], errors)
            self.assertEqual(rows[-1]['status'], stop_status)
        self.assertEqual(len(crawlers[0].dispatched), len(errors))
        self.assertEqual(len(crawlers[0].pending), len(config['seeds']) - len(errors))
        queue.is_finished.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
