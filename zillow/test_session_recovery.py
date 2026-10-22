import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from crawlee.errors import HttpClientStatusCodeError
from session_recovery import RelApiError, RotationRules, SessionRecovery


class FakeSessionApi:
    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.supported_proxy = False
        self.fail_creation = False
        self.lose_creation_response = False
        self.sequence = 9000

    async def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == 'GET' and path == '/groups':
            raise RelApiError({'id':'NOT_FOUND','message':'legacy API'})
        if method == 'GET' and path == '/sessions':
            return {'sessions': list(self.sessions.values())}
        if method == 'GET' and path.startswith('/sessions/'):
            session = self.sessions.get(path.rsplit('/', 1)[1])
            if not session:
                raise RelApiError({'id': 'SESSION_NOT_FOUND', 'message': 'missing'})
            return {'session': dict(session)}
        if method == 'DELETE':
            self.sessions.pop(path.rsplit('/', 1)[1], None)
            return {}
        if path.startswith('/proxies/'):
            return {'proxy': {'oxylabs': {'enabled': self.supported_proxy}}}
        if method == 'POST' and path == '/sessions':
            if self.fail_creation:
                raise RuntimeError('Session creation failed')
            self.sequence += 1
            session = {'id': f'Session{self.sequence}', **payload}
            self.sessions[session['id']] = session
            if self.lose_creation_response:
                self.lose_creation_response = False
                raise TimeoutError('Lost creation response')
            return {'session': session}
        raise AssertionError((method, path, payload))


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.json'
        self.path.write_text(json.dumps({'session_id': 'Session9001', 'profile': 'Test',
                                        'base_url': 'http://localhost:17319/v1'}))
        self.api = FakeSessionApi()
        self.api.sequence = 9001
        self.api.sessions['Session9001'] = {
            'id': 'Session9001', 'profile': 'Test', 'group': 'test-crawl', 'proxy_alias': 'existing',
            'adblock_enabled': True, 'image_blocking_mode': 'all', 'image_size_limit_kb': 99}

    def manager(self, **rules):
        return SessionRecovery(self.path, 'Test', 'http://localhost:17319/v1', 'test-crawl',
                               RotationRules(cooldown_seconds=0, **rules), api=self.api)

    async def test_replacement_preserves_proxy_filters_and_profile(self):
        manager = self.manager()
        self.assertEqual(await manager.replace(), 'Session9002')
        self.assertNotIn('Session9001', self.api.sessions)
        new = self.api.sessions['Session9002']
        for key in ('profile', 'group', 'proxy_alias', 'adblock_enabled', 'image_blocking_mode', 'image_size_limit_kb'):
            self.assertEqual(new[key], {'profile': 'Test', 'group': 'test-crawl', 'proxy_alias': 'existing',
                             'adblock_enabled': True, 'image_blocking_mode': 'all', 'image_size_limit_kb': 99}[key])
        self.assertEqual(new['lifetime'], {'type': 'indefinite'})
        self.assertFalse(any('/rotate-session' in path for _, path, _ in self.api.calls))
        self.assertEqual(self.manager().state['generation'], 1)

    async def test_ensure_recreates_only_a_missing_saved_session(self):
        del self.api.sessions['Session9001']
        manager = self.manager()
        self.assertEqual(await manager.ensure(), 'Session9002')
        self.assertEqual(manager.state['generation'], 0)
        self.assertEqual(self.api.sessions['Session9002']['profile'], 'Test')
        self.assertEqual(self.api.sessions['Session9002']['group'], 'test-crawl')
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.api.calls), 1)

    async def test_ensure_refuses_a_saved_session_owned_by_another_group(self):
        self.api.sessions['Session9001']['group'] = 'other-crawl'
        with self.assertRaisesRegex(RuntimeError, 'different Profile or crawler group'):
            await self.manager().ensure()
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.api.calls), 0)

    async def test_supported_proxy_rotates_after_old_session_is_closed(self):
        self.api.supported_proxy = True
        await self.manager(rotate_supported_proxy=True).replace()
        actions = [(method, path) for method, path, _ in self.api.calls]
        self.assertLess(actions.index(('DELETE', '/sessions/Session9001')),
                        actions.index(('POST', '/proxies/existing/rotate-session')))
        event = json.loads(self.path.with_name('session-rotations.jsonl').read_text())
        self.assertTrue(event['proxy_rotated'])

    async def test_shared_proxy_is_not_rotated_or_closed(self):
        self.api.supported_proxy = True
        self.api.sessions['Session777'] = {'id': 'Session777', 'proxy_alias': 'existing', 'group': 'unrelated'}
        with self.assertRaisesRegex(RuntimeError, 'another REL session'):
            await self.manager(rotate_supported_proxy=True).replace()
        self.assertIn('Session9001', self.api.sessions)
        self.assertFalse(any(method in ('POST', 'DELETE') for method, _, _ in self.api.calls))

    async def test_shared_static_proxy_only_replaces_owned_browser(self):
        self.api.sessions['Session777'] = {'id': 'Session777', 'proxy_alias': 'existing', 'group': 'unrelated'}
        await self.manager().replace()
        self.assertIn('Session777', self.api.sessions)
        self.assertNotIn('Session9001', self.api.sessions)
        self.assertFalse(any('/rotate-session' in path for _, path, _ in self.api.calls))

    async def test_foreign_session_is_never_closed(self):
        self.api.sessions['Session9001']['group'] = 'caller-owned'
        with self.assertRaisesRegex(RuntimeError, 'outside the crawler'):
            await self.manager().replace()
        self.assertEqual(json.loads(self.path.read_text())['session_id'], 'Session9001')

    async def test_failed_creation_resumes_without_stale_active_session(self):
        self.api.fail_creation = True
        with self.assertRaisesRegex(RuntimeError, 'creation failed'):
            await self.manager().replace()
        self.assertIsNone(json.loads(self.path.read_text())['session_id'])
        self.api.fail_creation = False
        self.assertEqual(await self.manager().ensure(), 'Session9002')
        self.assertEqual(sum(method == 'DELETE' for method, _, _ in self.api.calls), 1)

    async def test_unknown_creation_outcome_reuses_created_replacement(self):
        self.api.lose_creation_response = True
        with self.assertRaises(TimeoutError):
            await self.manager().replace()
        self.assertEqual(await self.manager().ensure(), 'Session9002')
        self.assertEqual(sum(method == 'POST' and path == '/sessions' for method, path, _ in self.api.calls), 1)

    async def test_retry_handoff_is_checkpointed_and_deduplicatable(self):
        manager = self.manager()
        payload = {'url': 'https://example.com', 'unique_key': 'retry:1'}
        self.assertTrue(manager.schedule(HttpClientStatusCodeError('unavailable', 503), payload))
        enqueue = AsyncMock(side_effect=RuntimeError('queue unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'queue unavailable'):
            await manager.recover(enqueue)
        resumed = self.manager()
        self.assertTrue(resumed.pending['rotated'])
        enqueue = AsyncMock()
        await resumed.recover(enqueue)
        enqueue.assert_awaited_once_with(payload)
        self.assertIsNone(resumed.pending)
        self.assertEqual(sum(method == 'POST' and path == '/sessions' for method, path, _ in self.api.calls), 1)

    def test_limits_and_success_reset(self):
        manager = self.manager(max_consecutive_failures=2, max_rotations_per_run=2)
        error = HttpClientStatusCodeError('unavailable', 503)
        self.assertTrue(manager.schedule(error))
        self.assertFalse(manager.schedule(error))
        manager.successful_capture()
        self.assertTrue(manager.schedule(error))
        manager.rotations = 2
        self.assertFalse(manager.schedule(error))

    def test_structured_target_status_only(self):
        rules = RotationRules()
        self.assertFalse(rules.matches(HttpClientStatusCodeError('blocked', 403)))
        self.assertFalse(rules.matches(HttpClientStatusCodeError('rate limited', 429)))
        self.assertTrue(rules.matches(HttpClientStatusCodeError('unavailable', 503)))
        local_error = RelApiError({'id': 'PRO_REQUIRED', 'message': 'HTTP 403'})
        self.assertFalse(rules.matches(local_error))
        self.assertFalse(rules.matches(ValueError('captcha or wrong property')))
        self.assertFalse(rules.matches(HttpClientStatusCodeError('not found', 404)))

    def test_missing_page_streak_rotates_without_retrying_skipped_urls(self):
        manager = self.manager()
        error = HttpClientStatusCodeError('not found', 404)
        self.assertTrue(manager.rules.skips(error))
        self.assertEqual(manager.skipped(error), 'continue')
        resumed = self.manager()
        self.assertEqual(resumed.skipped(error), 'continue')
        self.assertEqual(resumed.skipped(error), 'rotate')
        self.assertIsNone(resumed.pending['retry'])

    def test_success_resets_missing_page_streak(self):
        manager = self.manager()
        error = HttpClientStatusCodeError('not found', 404)
        manager.skipped(error)
        manager.skipped(error)
        manager.successful_capture()
        self.assertEqual(manager.skipped(error), 'continue')
        self.assertEqual(manager.state['consecutive_skips'], 1)


if __name__ == '__main__':
    unittest.main()
