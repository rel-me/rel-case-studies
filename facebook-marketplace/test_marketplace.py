import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from listings import item_id, miles, parse_listing
from notifications import connect, notify
from whatsapp import send, resolve_group
from comparisons import compare
from monitor import load_config

CONFIG = json.loads(Path(__file__).with_name('config.example.json').read_text())
CONFIG['_whatsapp_group_jid'] = '123456@g.us'
URL = 'https://www.facebook.com/marketplace/item/123/'


def fixture(title='Pronto Stroller Wagon', condition='Used - Good', location='36.9741,-122.0308', status='', message=True):
    return f'''<h1>Stale search title</h1><script>{{"is_sold":false}}</script>
    <div role="dialog" aria-label="Marketplace Listing Viewer">
    <h1>{title}</h1><span>$500</span><span>Condition</span><span>{condition}</span>
    <span>{status}</span>{'<button>Message</button>' if message else ''}
    <div style='background-image: url("https://external.xx.fbcdn.net/static_map.php?center={quote(location)}&amp;circle={quote('weight:2|'+location+'|2k')}")'></div>
    </div>'''


class MatchingTests(unittest.TestCase):
    def test_matching_local_used(self):
        row = parse_listing(fixture(), URL, CONFIG)
        self.assertTrue(row['matches'])
        self.assertEqual(row['title'], 'Pronto Stroller Wagon')
        self.assertAlmostEqual(row['location_uncertainty_miles'], 1.24, places=2)

    def test_live_condition_capitalization(self):
        self.assertTrue(parse_listing(fixture(condition='Used - like new'), URL, CONFIG)['matches'])

    def test_outside_and_boundary(self):
        for location in ('37.3382,-121.8863', '37.11,-122.0308'):
            with self.subTest(location=location):
                self.assertFalse(parse_listing(fixture(location=location), URL, CONFIG)['matches'])
        self.assertAlmostEqual(miles(0, 0, 0, 1), 69.0934, places=3)

    def test_reject_nonmatches(self):
        for kwargs in ({'title': 'Wagon'}, {'title': 'Stroller wagon seat attachment'},
                       {'title': 'Stroller wagon RENTAL'}, {'condition': 'New'},
                       {'condition': 'Unknown'}, {'status': 'Sold'}, {'status': 'Pending'},
                       {'message': False}, {'location': 'nan,0'}, {'location': 'bad'}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(parse_listing(fixture(**kwargs), URL, CONFIG)['matches'])

    def test_unavailable_layout_stops(self):
        with self.assertRaises(ValueError):
            parse_listing('<form>Log in</form>', URL, CONFIG)

    def test_id_scope_and_tracking(self):
        self.assertEqual(item_id(URL+'?ref=search#x'), '123')
        for url in ('https://evil.test/marketplace/item/123/', 'http://www.facebook.com/marketplace/item/123/',
                    'https://www.facebook.com/marketplace/search/?id=123'):
            self.assertIsNone(item_id(url))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.db = connect(self.path)
        self.row = parse_listing(fixture(), URL, CONFIG)
        self.sender = Mock()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_preview_does_not_consume_alert(self):
        with patch('builtins.print'):
            self.assertEqual(notify(self.db, self.row, CONFIG, sender=self.sender), 'preview')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM whatsapp_alerts').fetchone()[0], 0)
        self.sender.assert_not_called()
        self.assertEqual(notify(self.db, self.row, CONFIG, enabled=True, sender=self.sender), 'submitted')

    def test_restart_deduplicates(self):
        notify(self.db, self.row, CONFIG, enabled=True, sender=self.sender)
        self.db.close()
        self.db = connect(self.path)
        self.assertEqual(notify(self.db, self.row, CONFIG, enabled=True, sender=self.sender), 'already_attempted')
        self.sender.assert_called_once()

    def test_ambiguous_failure_never_automatically_retries(self):
        self.sender.side_effect = subprocess.TimeoutExpired('wacli', 30)
        with self.assertRaises(RuntimeError):
            notify(self.db, self.row, CONFIG, enabled=True, sender=self.sender)
        self.assertEqual(self.db.execute('SELECT status FROM whatsapp_alerts').fetchone()[0], 'uncertain')
        self.assertEqual(notify(self.db, self.row, CONFIG, enabled=True, sender=self.sender), 'already_attempted')
        self.sender.assert_called_once()

    def test_website_text_is_argv(self):
        malicious = '"; touch /tmp/nope; $(echo nope)'
        result = Mock(stdout=json.dumps({'success': True, 'data': {'sent': True, 'to': '123456@g.us', 'id': 'ABC'}}))
        with patch('whatsapp.subprocess.run', return_value=result) as run:
            send('123456@g.us', malicious)
            argv = run.call_args.args[0]
            self.assertEqual(argv[argv.index('--message')+1], malicious)
            self.assertNotIn('--message-escapes', argv)

    def test_non_group_prevents_send(self):
        with patch('whatsapp.subprocess.run') as run:
            with self.assertRaises(ValueError):
                send('+15551234567', 'test')
            run.assert_not_called()


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = load_config(Path(__file__).with_name('config.example.json'))
        self.assertEqual(config['radius_miles'], 10)
        self.assertEqual(config['whatsapp_group'], 'REL')

class ScanTests(unittest.TestCase):
    def test_capture_paths_metadata_and_resume(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from rel_crawler import CrawlSummary
        from monitor import scan
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def run(app):
                path = app.definition.capture_path(SimpleNamespace(link=SimpleNamespace(url=URL)))
                self.assertTrue(path.is_relative_to(root / 'passes'))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(fixture())
                path.with_suffix('.metadata.json').write_text(json.dumps({
                    'page': {'url': URL}, 'captured_at': datetime.now(timezone.utc).isoformat()}))
                return CrawlSummary(1, 1, 0, 0, 0, 'Session1', 0, 0, app.state_path)
            with patch('rel_crawler.CrawlApplication.run', run), patch('builtins.print'):
                scan(CONFIG, root, 'Session1', Mock(), enabled=False)
            self.assertFalse((root / 'active.json').exists())
            results = list(root.glob('passes/*/results.json'))
            self.assertTrue(json.loads(results[0].read_text())[0]['matches'])

    def test_empty_discovery_is_not_success(self):
        from rel_crawler import CrawlSummary
        from monitor import scan
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = CrawlSummary(0, 0, 0, 0, 0, 'Session1', 0, 0, root/'checkpoint.json')
            with patch('rel_crawler.CrawlApplication.run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'No visible listing links'):
                    scan(CONFIG, root, 'Session1', Mock(), enabled=False)
            self.assertTrue((root/'active.json').exists())

    def test_wrong_final_listing_is_rejected(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from rel_crawler import CrawlSummary
        from monitor import scan
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def run(app):
                path = app.definition.capture_path(SimpleNamespace(link=SimpleNamespace(url=URL)))
                path.parent.mkdir(parents=True)
                path.write_text(fixture())
                path.with_suffix('.metadata.json').write_text(json.dumps({
                    'page': {'url': URL.replace('123','999')}, 'captured_at': datetime.now(timezone.utc).isoformat()}))
                return CrawlSummary(1, 1, 0, 0, 0, 'Session1', 0, 0, app.state_path)
            with patch('rel_crawler.CrawlApplication.run', run):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    scan(CONFIG, root, 'Session1', Mock(), enabled=False)


class WhatsAppTests(unittest.TestCase):
    def test_unlinked_fails_before_send(self):
        with patch('whatsapp.command', return_value={'authenticated': False}) as cmd:
            with self.assertRaisesRegex(RuntimeError, 'not linked'):
                resolve_group('REL')
            self.assertEqual(cmd.call_count, 1)

    def test_exact_group_required(self):
        groups = [{'Name': 'REL friends', 'JID': '1@g.us'}, {'Name': 'REL', 'JID': '2@g.us'}]
        with patch('whatsapp.command', side_effect=[{'authenticated': True}, {}, groups]):
            self.assertEqual(resolve_group('REL'), '2@g.us')

    def test_duplicate_names_fail(self):
        groups = [{'Name': 'REL', 'JID': '1@g.us'}, {'Name': 'REL', 'JID': '2@g.us'}]
        with patch('whatsapp.command', side_effect=[{'authenticated': True}, {}, groups]):
            with self.assertRaisesRegex(RuntimeError, 'found 2'):
                resolve_group('REL')
        with patch('whatsapp.command', side_effect=[{'authenticated': True}, {}, groups]):
            self.assertEqual(resolve_group('REL', '2@g.us'), '2@g.us')

    def test_wrong_acknowledgement_fails(self):
        with patch('whatsapp.command', return_value={'sent': True, 'to': 'wrong@g.us', 'id': 'ABC'}):
            with self.assertRaises(RuntimeError):
                send('123@g.us', 'hello')


class ComparisonTests(unittest.TestCase):
    def test_price_context_and_expiration(self):
        from datetime import datetime, timezone, timedelta
        refs = json.loads(Path(__file__).with_name('price_references.json').read_text())
        checked = datetime.fromisoformat(refs['checked_at'])
        row = {'title': 'Pronto Stroller Wagon', 'price': '$500'}
        text = compare(row, references=refs, now=checked)
        self.assertIn('$350 (41%) less', text)
        self.assertIn('$125 (33%) more', text)
        self.assertIn('$150 (23%) less', text)
        self.assertIn('needs refresh', compare(row, references=refs, now=checked+timedelta(days=8)))
        self.assertIn('unavailable', compare(dict(row, title='Baby Trend stroller wagon'), references=refs, now=checked))
        self.assertIn('unavailable', compare(dict(row, title='Pronto Squared'), references=refs, now=checked))


if __name__ == '__main__':
    unittest.main()
