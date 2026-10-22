import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from crawlee._service_locator import ServiceLocator
from crawlee.configuration import Configuration
from crawlee.events import LocalEventManager
from crawlee.storage_clients import FileSystemStorageClient
from crawlee.storages import RequestQueue
from crawlee.statistics import Statistics
from rel_crawlee import RelCrawler

import records
import zillow
from test_session_recovery import FakeSessionApi

URL = 'https://www.zillow.com/homedetails/123-Test-St-Santa-Cruz-CA-95060/123_zpid/'
OTHER = URL.replace('123_zpid', '456_zpid')


def fixture(zestimate=None):
    record = {'zpid': 123, 'address': {'streetAddress': '123 Test St', 'city': 'Santa Cruz',
              'state': 'CA', 'zipcode': '95060'}, 'price': 900000, 'zestimate': zestimate}
    return '<title>Test home</title><script type="application/json">' + json.dumps({
        'cache': json.dumps({'property': record}),
        'nearby': {'zpid': 999, 'zestimate': 777, 'address': {'city': 'Santa Cruz'}}}) + '</script>'


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = records.database(Path(self.tmp.name) / 'test.db')
        self.addCleanup(self.db.close)
        self.storage_patch = patch.object(ServiceLocator, 'global_storage_instance_manager', None)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.page = SimpleNamespace(session_id='Session9001', url='', content=AsyncMock(return_value=fixture(850000)))
        self.visits = []
        self.fail = False
        self.redirect = None
        self.status = 200
        async def goto(url, **kwargs):
            self.visits.append(url)
            if self.fail:
                raise RuntimeError('navigation failed')
            self.page.url = self.redirect or url
            return SimpleNamespace(status=self.status)
        self.page.goto = goto
        self.browser = SimpleNamespace(new_page=AsyncMock(return_value=self.page))
        self.launch = AsyncMock(return_value=self.browser)
        manager = AsyncMock()
        manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))
        self.adapter = patch('rel_crawlee._crawler.async_playwright', return_value=manager)
        self.creation_adapter = patch('zillow.async_playwright', return_value=manager)
        self.creation_adapter.start()
        self.addCleanup(self.creation_adapter.stop)
        download = patch('inventory.download', return_value=([], '2026-09-15', Path(self.tmp.name) / 'snapshot.json'))
        download.start()
        self.addCleanup(download.stop)
        self.adapter.start()
        self.addCleanup(self.adapter.stop)
        self.session_api = FakeSessionApi()
        api_patch = patch('session_recovery.RelSessionApi', return_value=self.session_api)
        api_patch.start()
        self.addCleanup(api_patch.stop)

    async def run_crawler(self, urls=(), limit=10, address_limit=50, skip_failed=False, progress_file=None):
        config = zillow.crawl_configuration(Path(self.tmp.name))
        storage = FileSystemStorageClient()
        queue = await RequestQueue.open(name='test', configuration=config, storage_client=storage)
        crawler = RelCrawler(session_id='Session9001', configuration=config, storage_client=storage,
                             event_manager=LocalEventManager(), request_manager=queue,
                             max_requests_per_crawl=limit, max_request_retries=1, configure_logging=False)
        zillow.register_handlers(crawler, self.db, address_limit, retry_delay=0, skip_failed=skip_failed, progress_file=progress_file)
        stats = await crawler.run([zillow.request(url) for url in urls], purge_request_queue=False)
        return stats, queue

    async def test_application_configuration_and_exit_status(self):
        args = SimpleNamespace(output=Path(self.tmp.name) / 'application',
                               profile='TestProfile', base_url='http://127.0.0.1:17319/v1',
                               max_pages=1, max_addresses=50, attempts=2, seed_file=None)
        with patch.object(zillow, 'inventory_seeds', return_value=[zillow.request(URL)]):
            self.assertEqual(await zillow.crawl(args), 0)
        self.assertEqual(self.visits, [URL])

    async def test_application_403_stops_without_retry_or_session_replacement(self):
        visits = 0
        async def goto(url, **kwargs):
            nonlocal visits
            self.visits.append(url)
            visits += 1
            self.page.url = url
            self.page.content.return_value = fixture(850000).replace('123', '456') if url == OTHER else fixture(850000)
            return SimpleNamespace(status=403 if visits == 1 else 200)
        self.page.goto = goto
        policy = Path(self.tmp.name) / 'rules.json'
        policy.write_text('{"cooldown_seconds": 0}')
        args = SimpleNamespace(output=Path(self.tmp.name), profile='TestProfile',
                               base_url='http://127.0.0.1:17319/v1', max_pages=10,
                               max_addresses=50, attempts=2, rotation_rules=policy)
        with patch.object(zillow, 'inventory_seeds', return_value=[zillow.request(URL), zillow.request(OTHER)]):
            self.assertEqual(await zillow.crawl(args), 2)
        self.assertEqual(self.visits, [URL])
        with sqlite3.connect(Path(self.tmp.name) / 'zillow.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM homes').fetchone()[0], 0)
        self.assertEqual(len(self.session_api.sessions), 1)
        self.assertEqual(json.loads((Path(self.tmp.name) / 'session.json').read_text())['generation'], 0)

    async def test_application_403_stops_before_any_bounded_rotation(self):
        self.status = 403
        policy = Path(self.tmp.name) / 'rules.json'
        policy.write_text('{"cooldown_seconds": 0, "max_consecutive_failures": 3}')
        args = SimpleNamespace(output=Path(self.tmp.name), profile='TestProfile',
                               base_url='http://127.0.0.1:17319/v1', max_pages=20,
                               max_addresses=50, attempts=2, rotation_rules=policy)
        with patch.object(zillow, 'inventory_seeds', return_value=[zillow.request(URL), zillow.request(OTHER)]):
            self.assertEqual(await zillow.crawl(args), 2)
        self.assertEqual(self.visits, [URL])
        self.assertEqual(json.loads((Path(self.tmp.name) / 'session.json').read_text())['generation'], 0)
        self.assertEqual(len(self.session_api.sessions), 1)

    async def test_old_persisted_failures_do_not_fail_new_run(self):
        # Simulate an older run that persisted its failure statistics, then a
        # fresh process whose statistics ID starts at the same value.
        statistics_id = Statistics._Statistics__next_id
        self.fail = True
        stats, _ = await self.run_crawler([URL])
        self.assertEqual(stats.requests_failed, 1)
        self.fail = False
        ServiceLocator.global_storage_instance_manager = None
        args = SimpleNamespace(output=Path(self.tmp.name), profile='TestProfile',
                               base_url='http://127.0.0.1:17319/v1', max_pages=1,
                               max_addresses=1, attempts=2, seed_file=None)
        with patch.object(Statistics, '_Statistics__next_id', statistics_id), \
             patch.object(zillow, 'inventory_seeds', return_value=[zillow.request(URL)]):
            self.assertEqual(await zillow.crawl(args), 0)

    async def test_session_created_once_and_retained_across_restarts(self):
        args = SimpleNamespace(output=Path(self.tmp.name) / 'persistent',
                               profile='TestProfile', base_url='http://127.0.0.1:17319/v1')
        self.assertEqual(await zillow.persistent_session(args), 'Session9001')
        self.assertEqual(await zillow.persistent_session(args), 'Session9001')
        self.launch.assert_awaited_once()
        self.assertTrue(self.launch.call_args.kwargs['persist'])
        self.assertEqual(self.launch.call_args.kwargs['profile'], 'TestProfile')
        args.profile = 'Different'
        with self.assertRaisesRegex(ValueError, 'different Profile'):
            await zillow.persistent_session(args)
        self.launch.assert_awaited_once()

    async def test_queue_resume_dedup_and_profile_sessions(self):
        await self.run_crawler([URL, URL + '?other=1'], limit=1)
        ServiceLocator.global_storage_instance_manager = None
        await self.run_crawler([URL])
        self.assertEqual(self.visits, [URL])
        self.assertEqual(self.db.execute('SELECT count(*) FROM observations').fetchone()[0], 1)
        self.assertEqual(self.launch.call_args.kwargs['session_id'], 'Session9001')
        self.assertIsNone(self.launch.call_args.kwargs['profile'])

    async def test_skip_policy_stops_at_eleven_failed_resources_and_retains_queue(self):
        self.fail=True
        urls=[URL.replace('123_zpid',f'{n}_zpid') for n in range(100,112)]
        stats,queue=await self.run_crawler(urls,limit=12,skip_failed=True)
        self.assertEqual(stats.requests_failed,11)
        self.assertEqual(await queue.get_handled_count(),11)
        self.assertEqual(await queue.get_total_count(),12)
        self.assertEqual(self.db.execute('SELECT count(*) FROM crawl_errors WHERE terminal=1').fetchone()[0],11)

    async def test_success_resets_skip_policy_failure_streak(self):
        async def goto(url,**kwargs):
            self.visits.append(url)
            if url!=URL: raise RuntimeError('navigation failed')
            self.page.url=url
            return SimpleNamespace(status=200)
        self.page.goto=goto
        urls=[URL.replace('123_zpid',f'{n}_zpid') for n in range(100,106)]+[URL]+[URL.replace('123_zpid',f'{n}_zpid') for n in range(106,112)]
        stats,queue=await self.run_crawler(urls,limit=13,skip_failed=True)
        self.assertEqual(stats.requests_failed,12)
        self.assertEqual(stats.requests_finished,1)
        self.assertEqual(await queue.get_handled_count(),13)

    async def test_skip_policy_keeps_failure_streak_after_process_restart(self):
        self.fail=True
        state=Path(self.tmp.name)/'progress.json'
        state.write_text(json.dumps({'consecutive_failures':10}))
        stats,queue=await self.run_crawler([URL,OTHER],skip_failed=True,progress_file=state)
        self.assertEqual(stats.requests_failed,1)
        self.assertEqual(await queue.get_handled_count(),1)
        saved=json.loads(state.read_text())
        self.assertEqual(saved['consecutive_failures'],11)
        self.assertIn('More than 10',saved['stop_reason'])

    async def test_priority_reorders_existing_queue_and_keeps_handled_requests(self):
        config=zillow.crawl_configuration(Path(self.tmp.name))
        queue=await RequestQueue.open(name='priority-test',configuration=config,storage_client=FileSystemStorageClient())
        handled=zillow.request(URL.replace('123_zpid','789_zpid'))
        near=zillow.request(URL);far=zillow.request(OTHER)
        await queue.add_requests([handled,far,near],wait_for_all_requests_to_be_added=True)
        first=await queue.fetch_next_request()
        await queue.mark_request_as_handled(first)
        await zillow.prioritize_inventory_queue(queue,[near,far,handled])
        self.assertEqual(await queue.get_total_count(),3)
        self.assertEqual(await queue.get_handled_count(),1)
        next_request=await queue.fetch_next_request()
        self.assertEqual(next_request.unique_key,near.unique_key)
        await queue.mark_request_as_handled(next_request)
        detail=zillow.request(URL.replace('123_zpid','999_zpid'))
        await queue.add_request(detail,forefront=True)
        self.assertEqual((await queue.fetch_next_request()).unique_key,detail.unique_key)

    async def test_repeated_failure_stops_before_next_url(self):
        self.fail = True
        stats, queue = await self.run_crawler([URL, OTHER])
        self.assertEqual(self.visits, [URL, URL])
        self.assertEqual(stats.requests_failed, 1)
        self.assertEqual(await queue.get_handled_count(), 1)
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT attempt,terminal FROM crawl_errors')],
                         [(1, 0), (2, 1)])

    async def test_exhausted_timeout_continues_to_next_property(self):
        async def goto(url, **kwargs):
            self.visits.append(url)
            if url == URL:
                raise TimeoutError('Timed out waiting for embedded Chromium')
            self.page.url = url
            return SimpleNamespace(status=200)
        self.page.goto = goto
        self.page.content.return_value = fixture(850000).replace('123', '456')
        stats, queue = await self.run_crawler([URL, OTHER])
        self.assertEqual(self.visits, [URL, URL, OTHER])
        self.assertEqual(stats.requests_failed, 1)
        self.assertEqual(stats.requests_finished, 1)
        self.assertEqual(await queue.get_handled_count(), 2)
        self.assertEqual(self.db.execute('SELECT zpid FROM homes').fetchone()[0], '456')
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT attempt,terminal FROM crawl_errors')],
                         [(1, 0), (2, 1)])

    async def test_host_cpu_load_does_not_pause_serial_rel_worker(self):
        from datetime import datetime, timezone
        from crawlee._autoscaling import Snapshotter, SystemStatus
        event = SimpleNamespace(cpu_info=SimpleNamespace(used_ratio=1.0, created_at=datetime.now(timezone.utc)))
        default = Snapshotter.from_config(Configuration())
        serial = Snapshotter.from_config(zillow.crawl_configuration(Path(self.tmp.name)))
        async with default, serial:
            await default._snapshot_cpu(event)
            await serial._snapshot_cpu(event)
            self.assertFalse(SystemStatus(default).get_current_system_info().is_system_idle)
            self.assertTrue(SystemStatus(serial).get_current_system_info().is_system_idle)

    async def test_challenge_stops_without_retry_and_keeps_capture(self):
        self.page.content.return_value = '<title>Access denied</title><p>Press &amp; Hold</p>'
        stats, _ = await self.run_crawler([URL, OTHER])
        self.assertEqual(self.visits, [URL])
        self.assertEqual(stats.requests_failed, 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM captures').fetchone()[0], 1)

    async def test_address_limit_excludes_search_pages_and_stops_before_next_home(self):
        self.page.content.side_effect = [f'<a href="{URL}">Home</a>', fixture(850000)]
        stats, queue = await self.run_crawler([records.STARTS[0], URL, OTHER], address_limit=1)
        self.assertEqual(self.visits, [records.STARTS[0], URL])
        self.assertEqual(stats.requests_finished, 2)
        self.assertEqual(await queue.get_handled_count(), 2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM homes').fetchone()[0], 1)

    async def test_outside_city_is_skipped_and_next_home_continues(self):
        outside = fixture().replace('Santa Cruz', 'Scotts Valley').replace('123', '456')
        self.page.content.side_effect = [outside, fixture(850000)]
        stats, _ = await self.run_crawler([OTHER, URL])
        self.assertEqual(stats.requests_failed, 0)
        self.assertEqual(self.visits, [OTHER, URL])
        self.assertEqual(self.db.execute('SELECT count(*) FROM skipped_urls').fetchone()[0], 1)

    async def test_http_403_stops_before_extraction(self):
        self.status = 403
        stats, queue = await self.run_crawler([URL, OTHER])
        self.assertEqual(self.visits, [URL])
        self.assertEqual(stats.requests_failed, 1)
        self.assertEqual(await queue.get_handled_count(), 1)
        self.page.content.assert_not_awaited()
        error = self.db.execute('SELECT error FROM crawl_errors').fetchone()[0]
        self.assertIn('403', error)

    async def test_http_404_skips_page_and_continues_to_next_home(self):
        async def goto(url, **kwargs):
            self.visits.append(url)
            self.page.url = url
            return SimpleNamespace(status=404 if url == URL else 200)
        self.page.goto = goto
        self.page.content.return_value = fixture(850000).replace('123', '456')
        stats, queue = await self.run_crawler([URL, OTHER])
        self.assertEqual(self.visits, [URL, OTHER])
        self.assertEqual(stats.requests_finished, 1)
        self.assertEqual(await queue.get_handled_count(), 2)
        self.assertEqual(self.db.execute('SELECT url FROM skipped_urls').fetchone()[0], URL)
        self.assertEqual(self.db.execute('SELECT zpid FROM homes').fetchone()[0], '456')

    async def test_application_three_404s_rotates_then_continues(self):
        third = URL.replace('123_zpid', '789_zpid')
        good = URL.replace('123_zpid', '999_zpid')
        async def goto(url, **kwargs):
            self.visits.append(url)
            self.page.url = url
            return SimpleNamespace(status=200 if url == good else 404)
        self.page.goto = goto
        self.page.content.return_value = fixture(850000).replace('123', '999')
        policy = Path(self.tmp.name) / 'rules.json'
        policy.write_text('{"cooldown_seconds": 0}')
        args = SimpleNamespace(output=Path(self.tmp.name), profile='TestProfile',
                               base_url='http://127.0.0.1:17319/v1', max_pages=10,
                               max_addresses=50, attempts=2, rotation_rules=policy)
        with patch.object(zillow, 'inventory_seeds', return_value=[zillow.request(url) for url in [URL, OTHER, third, good]]):
            self.assertEqual(await zillow.crawl(args), 2)
        self.assertEqual(self.visits, [URL, OTHER, third, good])
        self.assertEqual(json.loads((Path(self.tmp.name) / 'session.json').read_text())['generation'], 1)

    async def test_wrong_property_not_saved(self):
        self.redirect = OTHER
        stats, _ = await self.run_crawler([URL])
        self.assertEqual(stats.requests_failed, 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM homes').fetchone()[0], 0)

    async def test_search_discovery_retains_source(self):
        self.page.content.return_value = f'<a href="{URL}">Home</a><a href="https://evil.test/">No</a>'
        _, queue = await self.run_crawler([records.STARTS[0]], limit=1)
        target = await queue.get_request('zpid:123')
        self.assertEqual(target.user_data['source_url'], records.STARTS[0])
        self.assertEqual(await queue.get_total_count(), 2)

    def test_target_identity_and_missing_estimate(self):
        data = records.parse(fixture(), URL)
        self.assertEqual(data['facts']['zpid'], 123)
        with self.db:
            capture = records.save(self.db, URL, URL, 200, fixture(), data)
            records.save_home(self.db, capture, URL, data['facts'])
        row = self.db.execute('SELECT * FROM latest_homes').fetchone()
        self.assertIsNone(row['zestimate'])
        self.assertEqual(row['price'], 900000)

    def test_url_and_city_scope(self):
        for url in ['https://evil.test/santa-cruz-ca/', 'http://www.zillow.com/santa-cruz-ca/',
                    'https://www.zillow.com/santa-cruz-county-ca/']:
            with self.assertRaises(ValueError):
                zillow.request(url)
        facts = records.parse(fixture(), URL)['facts']
        facts['address']['city'] = 'Capitola'
        with self.assertRaisesRegex(ValueError, 'outside'):
            records.save_home(self.db, 1, URL, facts)

    def test_json_ld_identity_and_rendered_estimate(self):
        listing = {'@type': 'RealEstateListing', 'url': URL,
                   'offers': {'itemOffered': {'address': {'addressLocality': 'Santa Cruz',
                                                        'addressRegion': 'CA'}}}}
        html = ('<script type="application/ld+json">' + json.dumps(listing) + '</script>'
                '<span data-testid="primary-zestimate">$850,000</span>')
        self.assertEqual(records.parse(html, URL)['facts']['zestimate'], 850000)
        self.assertIsNone(records.parse(html, OTHER)['facts'])


if __name__ == '__main__':
    unittest.main()
