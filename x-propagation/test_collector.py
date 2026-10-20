import unittest
from collector import (CollectionWindowEnded, normalize, parse_posts, plan_requests,
                       run_before_deadline, save_capture)
from pathlib import Path
import json
import asyncio
from datetime import datetime, timedelta, timezone
import tempfile
from unittest.mock import patch
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
    async def test_deadline_cancels_and_awaits_the_running_cycle(self):
        class FakeCrawler:
            cleaned_up = False

            async def run(self, requests):
                try:
                    await asyncio.sleep(3600)
                finally:
                    self.cleaned_up = True

        crawler = FakeCrawler()
        with self.assertRaises(TimeoutError):
            await run_before_deadline(crawler, [],
                                     datetime.now(timezone.utc) + timedelta(milliseconds=20))
        self.assertTrue(crawler.cleaned_up)


if __name__ == '__main__':
    unittest.main()
