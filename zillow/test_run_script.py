import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RunScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='case study ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'run.sh'
        shutil.copyfile(Path(__file__).with_name('run.sh'), self.script)
        (self.root / 'requirements.txt').write_text('')
        (self.root / '.venv/bin').mkdir(parents=True)
        python = self.root / '.venv/bin/python'
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$CALL_LOG"\nexit "${TEST_EXIT:-0}"\n')
        python.chmod(0o755)
        (self.root / '.venv/.installed').touch()
        self.log = self.root / 'call.log'
        self.env = dict(os.environ, CALL_LOG=str(self.log))

    def invoke(self, *args):
        return subprocess.run(['bash', str(self.script), *args], cwd='/', env=self.env,
                              capture_output=True, text=True)

    def test_resume_from_another_directory_preserves_arguments_and_exit(self):
        self.env['TEST_EXIT'] = '2'
        result = self.invoke('--output', 'path with spaces', '--max-addresses', '50')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.log.read_text().splitlines(),
                         [str(self.root), 'run.py', '--output', 'path with spaces', '--max-addresses', '50'])

    def test_maintenance_commands_do_not_launch_crawl(self):
        for action in ['inventory', 'migrate']:
            self.assertEqual(self.invoke(action, '--output', 'data').returncode, 0)
            self.assertEqual(self.log.read_text().splitlines(), [str(self.root), action+'.py', '--output', 'data'])

    def test_help_and_current_setup_do_not_launch_python(self):
        self.assertEqual(self.invoke('--help').returncode, 0)
        self.assertEqual(self.invoke('setup').returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failed_install_does_not_mark_environment_ready(self):
        (self.root / '.venv/.installed').unlink()
        bootstrap = self.root / 'bootstrap'
        bootstrap.write_text('#!/bin/sh\nexit 0\n')
        bootstrap.chmod(0o755)
        self.env.update(PYTHON=str(bootstrap), TEST_EXIT='7')
        self.assertEqual(self.invoke().returncode, 7)
        self.assertFalse((self.root / '.venv/.installed').exists())
        self.assertEqual(self.log.read_text().splitlines()[1:], ['-m', 'pip', 'install', '-r', 'requirements.txt'])
