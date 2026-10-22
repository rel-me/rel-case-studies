import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run as launcher


class LauncherTests(unittest.TestCase):
    def test_release_health(self):
        health = {'status': 'ok', 'data': {'build': {'configuration': 'Release'}}}
        with patch.object(launcher, 'urlopen', return_value=io.BytesIO(json.dumps(health).encode())) as request:
            launcher.wait_for_release()
        request.assert_called_once_with('http://127.0.0.1:17319/v1/health', timeout=2)

    def test_debug_endpoint_is_rejected(self):
        health = {'status': 'ok', 'data': {'build': {'configuration': 'Debug'}}}
        with patch.object(launcher, 'urlopen', return_value=io.BytesIO(json.dumps(health).encode())):
            with self.assertRaisesRegex(RuntimeError, 'not reporting a Release build'):
                launcher.wait_for_release()

    def test_unavailable_endpoint_times_out(self):
        with patch.object(launcher, 'urlopen', side_effect=OSError('not listening')), \
             patch.object(launcher.time, 'monotonic', side_effect=[0, 0, 31]), \
             patch.object(launcher.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'did not become ready'):
                launcher.wait_for_release()

    def test_launcher_preserves_child_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / 'REL.app'
            app.mkdir()
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'APP', app), \
                 patch.object(launcher, 'wait_for_release') as ready, \
                 patch.object(launcher.subprocess, 'run') as open_app, \
                 patch.object(launcher.subprocess, 'call', return_value=2) as crawl:
                self.assertEqual(launcher.run(['--max-pages', '3']), 2)
                open_app.assert_called_once_with(['open', str(app)], check=True)
                ready.assert_called_once_with()
                command = crawl.call_args.args[0]
                self.assertEqual(command[-2:], ['--base-url', launcher.BASE_URL])
                self.assertEqual(command[command.index('--max-pages') + 1], '3')
                self.assertTrue((root / 'output' / 'zillow-crawl.log').exists())

    def test_zillow_launcher_can_run_while_county_worker_owns_its_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / 'REL.app'
            app.mkdir()
            output = root / 'output'
            output.mkdir()
            county_lock = (output / 'run.lock').open('a')
            fcntl.flock(county_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'APP', app), \
                 patch.object(launcher, 'wait_for_release'), patch.object(launcher.subprocess, 'run'), \
                 patch.object(launcher.subprocess, 'call', return_value=0):
                self.assertEqual(launcher.run(['--output', str(output), '--profile', 'Zillow']), 0)
            self.assertTrue((output / 'zillow-run.lock').exists())
            county_lock.close()

    def test_launcher_retains_manual_pause_written_during_child_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);app=root/'REL.app';app.mkdir()
            def child(*args,**kwargs):
                path=root/'output/active-run.json'
                state=json.loads(path.read_text())
                state.update(maintenance='Stopped by user from dashboard',user_stop_requested_at='now')
                path.write_text(json.dumps(state))
                return 130
            with patch.object(launcher,'ROOT',root),patch.object(launcher,'APP',app), \
                 patch.object(launcher,'wait_for_release'),patch.object(launcher.subprocess,'run'), \
                 patch.object(launcher.subprocess,'call',side_effect=child):
                self.assertEqual(launcher.run([]),130)
            state=json.loads((root/'output/active-run.json').read_text())
            self.assertEqual(state['maintenance'],'Stopped by user from dashboard')
            self.assertEqual(state['status'],'stopped')

    def test_missing_app_does_not_start_crawler(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(launcher, 'APP', Path(temp) / 'missing.app'), \
             patch.object(launcher.subprocess, 'call') as crawl:
            with self.assertRaisesRegex(RuntimeError, 'Install REL'):
                launcher.run([])
            crawl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
