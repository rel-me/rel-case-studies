import io
import json
import signal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import run as launcher


class LauncherTests(unittest.TestCase):
    def test_interrupt_during_spawn_is_forwarded_after_child_assignment(self):
        child=Mock();child.poll.return_value=None;child.wait.return_value=130
        previous=signal.getsignal(signal.SIGINT)
        def spawn(*args,**kwargs):
            signal.getsignal(signal.SIGINT)(signal.SIGINT,None)
            return child
        with patch.object(launcher.subprocess,'Popen',side_effect=spawn):
            self.assertEqual(launcher.wait_for_child(['python','zillow.py']),130)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.wait.assert_called_once_with()
        self.assertIs(signal.getsignal(signal.SIGINT),previous)

    def test_interrupt_while_waiting_does_not_abandon_child(self):
        child=Mock();child.poll.return_value=None
        previous=signal.getsignal(signal.SIGINT)
        def wait():
            signal.getsignal(signal.SIGINT)(signal.SIGINT,None)
            return 130
        child.wait.side_effect=wait
        with patch.object(launcher.subprocess,'Popen',return_value=child):
            self.assertEqual(launcher.wait_for_child(['python','zillow.py']),130)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertIs(signal.getsignal(signal.SIGINT),previous)

    def test_failed_spawn_restores_original_signal_handler(self):
        previous=signal.getsignal(signal.SIGINT)
        with patch.object(launcher.subprocess,'Popen',side_effect=OSError('spawn failed')):
            with self.assertRaisesRegex(OSError,'spawn failed'):
                launcher.wait_for_child(['python','zillow.py'])
        self.assertIs(signal.getsignal(signal.SIGINT),previous)

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
                 patch.object(launcher, 'wait_for_child', return_value=2) as crawl:
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
                 patch.object(launcher, 'wait_for_child', return_value=0):
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
                 patch.object(launcher,'wait_for_child',side_effect=child):
                self.assertEqual(launcher.run([]),130)
            state=json.loads((root/'output/active-run.json').read_text())
            self.assertEqual(state['maintenance'],'Stopped by user from dashboard')
            self.assertEqual(state['status'],'stopped')

    def test_missing_app_does_not_start_crawler(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(launcher, 'APP', Path(temp) / 'missing.app'), \
             patch.object(launcher, 'wait_for_child') as crawl:
            with self.assertRaisesRegex(RuntimeError, 'Install REL'):
                launcher.run([])
            crawl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
