from contextlib import closing
import fcntl
import json
from pathlib import Path
import signal
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

import crawl_controls as controls


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name);self.db=self.output/'zillow.sqlite3'
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript("""CREATE TABLE inventory_addresses(address_key TEXT,status TEXT,lookup_url TEXT); CREATE TABLE inventory_parcels(address_key TEXT,classification TEXT); CREATE TABLE county_tax_targets(apn TEXT,status TEXT);
                INSERT INTO inventory_addresses VALUES('a','pending','https://www.zillow.com/'); INSERT INTO inventory_parcels VALUES('a','residential'); INSERT INTO county_tax_targets VALUES('12345678','pending');""")
        for name in ['run.py','crawl_county_taxes.py','zillow-priority.json','county-priority.json','county-inventory.sqlite3']:
            (self.output/name).touch()
        self.write('session.json',{'profile':'Zillow'});self.write('county-session.json',{'profile':'Datacenter'})
        common=['--output',str(self.output),'--max-consecutive-failures','10','--county-inventory',str(self.output/'county-inventory.sqlite3')]
        z=['/usr/bin/python3',str(self.output/'run.py'),'--profile','Zillow','--skip-failed','--attempts','1','--priority-file',str(self.output/'zillow-priority.json'),*common]
        tax=['/usr/bin/caffeinate','-i','/usr/bin/python3',str(self.output/'crawl_county_taxes.py'),'--profile','Datacenter','--session-file',str(self.output/'county-session.json'),'--inventory',str(self.db),'--tax-database',str(self.db),'--priority-file',str(self.output/'county-priority.json'),*common]
        self.write('active-run.json',{'pid':10,'launcher_command':z,'status':'stopped'})
        self.write('county-run.json',{'pid':20,'command':tax,'status':'stopped'})
        for name in ['zillow-progress.json','county-progress.json']:
            self.write(name,{'consecutive_failures':2,'stop_reason':None})
        self.popen=patch.object(controls.subprocess,'Popen',return_value=Mock(pid=300,poll=Mock(return_value=None))).start()
        self.addCleanup(patch.stopall)
        patch.object(controls,'process_info',return_value='').start()
        patch.object(controls.time,'sleep').start()

    def write(self,name,value): (self.output/name).write_text(json.dumps(value))
    def read(self,name): return json.loads((self.output/name).read_text())
    def run_worker(self,worker='ZILLOW',**kwargs): return controls.control_worker(self.output,self.db,worker,**kwargs)

    def test_starts_both_with_exact_saved_commands_and_unchanged_progress(self):
        for worker,run,progress,key in [('ZILLOW','active-run.json','zillow-progress.json','launcher_command'),('TAX','county-run.json','county-progress.json','command')]:
            command=self.read(run)[key];before=(self.output/progress).read_bytes()
            result=self.run_worker(worker)
            self.assertIn('Start requested',result)
            self.assertEqual(self.popen.call_args.args[0],command)
            self.assertTrue(self.popen.call_args.kwargs['start_new_session'])
            self.assertEqual(self.read(run)['pid'],300)
            self.assertEqual((self.output/progress).read_bytes(),before)

    def test_maintenance_safety_stops_and_eleventh_failure_never_launch(self):
        for worker,run,progress in [('ZILLOW','active-run.json','zillow-progress.json'),('TAX','county-run.json','county-progress.json')]:
            for flag in ['maintenance','stop_reason','failures']:
                original_run=self.read(run);original_progress=self.read(progress)
                if flag=='maintenance':self.write(run,dict(original_run,maintenance='repair'))
                elif flag=='stop_reason':self.write(progress,dict(original_progress,stop_reason='HTTP 403'))
                else:self.write(progress,dict(original_progress,consecutive_failures=11))
                with self.assertRaises(controls.ControlError):self.run_worker(worker)
                self.popen.assert_not_called()
                self.write(run,original_run);self.write(progress,original_progress)

    def test_foreign_lock_prevents_duplicate_and_independent_lock_allows_other_worker(self):
        with (self.output/'zillow-run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(controls.ControlError,'another worker'):self.run_worker()
            self.popen.assert_not_called()
            self.assertIn('TAX',self.run_worker('TAX'))

    def test_shared_dashboard_guard_serializes_control_actions(self):
        with (self.output/'dashboard-restart.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(controls.ControlError,'in progress'):self.run_worker()
        self.popen.assert_not_called()

    def test_wrong_profile_raw_launcher_and_retry_flags_are_rejected(self):
        original=self.read('active-run.json')
        for change in ['profile','raw','retry','output']:
            command=list(original['launcher_command'])
            if change=='profile':command[command.index('--profile')+1]='Other'
            elif change=='raw':command[1]=str(self.output/'zillow.py')
            elif change=='retry':command.append('--retry-failed')
            else:command[command.index('--output')+1]+='-other'
            self.write('active-run.json',dict(original,launcher_command=command))
            with self.assertRaises(controls.ControlError):self.run_worker()
            self.popen.assert_not_called()

    def test_resolving_is_pending_but_terminal_resources_are_not(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE inventory_addresses SET status='resolving'");db.commit()
        self.assertIn('Start requested',self.run_worker())
        self.popen.reset_mock()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE inventory_addresses SET status='failed'");db.commit()
        with self.assertRaisesRegex(controls.ControlError,'No pending'):self.run_worker()
        self.popen.assert_not_called()

    def test_running_restart_waits_for_pid_and_lock_before_launch(self):
        run=self.read('active-run.json');actual=f"/usr/bin/python3 {self.output}/run.py --output {self.output}"
        with patch.object(controls,'process_info',side_effect=[actual,'','']),patch.object(controls,'interrupt_worker') as stop:
            self.assertIn('Restart requested',self.run_worker())
            stop.assert_called_once_with(run['pid'],'run.py',self.output.resolve())
        self.assertEqual(self.popen.call_count,1)

    def test_worker_timeout_never_spawns_replacement(self):
        actual=f"/usr/bin/python3 {self.output}/run.py --output {self.output}"
        with patch.object(controls,'process_info',return_value=actual),patch.object(controls,'interrupt_worker'):
            with self.assertRaisesRegex(controls.ControlError,'still finishing'):self.run_worker(timeout=0)
        self.popen.assert_not_called()

    def test_new_safety_stop_while_finishing_is_preserved(self):
        actual=f"/usr/bin/python3 {self.output}/run.py --output {self.output}"
        def stop(*args):self.write('zillow-progress.json',{'stop_reason':'HTTP 429','consecutive_failures':1})
        with patch.object(controls,'process_info',side_effect=[actual,'']),patch.object(controls,'interrupt_worker',side_effect=stop):
            with self.assertRaisesRegex(controls.ControlError,'HTTP 429'):self.run_worker()
        self.popen.assert_not_called()

    def test_startup_exit_is_reported_without_clearing_streak(self):
        self.popen.return_value.poll.return_value=1
        with self.assertRaisesRegex(controls.ControlError,'exited with code 1'):self.run_worker()
        self.assertEqual(self.read('active-run.json')['status'],'stopped')
        self.assertEqual(self.read('zillow-progress.json')['consecutive_failures'],2)

    def test_pid_reuse_refuses_signal_and_launch(self):
        with patch.object(controls,'process_info',return_value='/usr/bin/some-other-process'),patch.object(controls.os,'kill') as kill:
            with self.assertRaisesRegex(controls.ControlError,'another process'):self.run_worker()
            kill.assert_not_called();self.popen.assert_not_called()

    def test_zillow_interrupts_child_and_lets_launcher_keep_lock(self):
        parent=f'/usr/bin/python3 {self.output}/run.py --output {self.output}'
        child=f'/usr/bin/python3 {self.output}/zillow.py --output {self.output}'
        with patch.object(controls,'process_info',side_effect=[parent,child]),patch.object(controls.subprocess,'run',return_value=Mock(stdout=f'11 10 {child}\n')),patch.object(controls.os,'kill') as kill:
            controls.interrupt_worker(10,'run.py',self.output)
            kill.assert_called_once_with(11,signal.SIGINT)

    def test_output_path_prefix_is_not_the_same_queue(self):
        self.assertFalse(controls.owns_process(f'/p/run.py --output {self.output}-other','run.py',self.output))


    def test_queue_exhausted_during_shutdown_does_not_restart(self):
        actual=f"/usr/bin/python3 {self.output}/run.py --output {self.output}"
        def stop(*args):
            with closing(sqlite3.connect(self.db)) as db:
                db.execute("UPDATE inventory_addresses SET status='matched'");db.commit()
        with patch.object(controls,'process_info',side_effect=[actual,'']),patch.object(controls,'interrupt_worker',side_effect=stop):
            with self.assertRaisesRegex(controls.ControlError,'Queue finished'):self.run_worker()
        self.popen.assert_not_called()

    def test_failed_spawn_preserves_checkpoint_and_does_not_claim_running(self):
        before=(self.output/'zillow-progress.json').read_bytes()
        run=self.read('active-run.json')
        self.popen.side_effect=OSError('exec failed')
        with self.assertRaisesRegex(OSError,'exec failed'):self.run_worker()
        self.assertEqual(self.read('active-run.json'),run)
        self.assertEqual((self.output/'zillow-progress.json').read_bytes(),before)

    def test_stop_preserves_progress_and_only_interrupts_selected_worker(self):
        for worker,run_name,script,progress in [('zillow','active-run.json','run.py','zillow-progress.json'),
                                               ('tax','county-run.json','crawl_county_taxes.py','county-progress.json')]:
            before=(self.output/progress).read_bytes()
            actual=f'/usr/bin/python3 {self.output}/{script} --output {self.output}'
            with patch.object(controls,'process_info',side_effect=[actual,'']), patch.object(controls,'interrupt_worker') as interrupt:
                result=controls.stop_worker(self.output,worker)
            self.assertEqual(result['status'],'stopped')
            interrupt.assert_called_once_with(self.read(run_name)['pid'],script,self.output.resolve())
            self.assertEqual(self.read(run_name)['maintenance'],controls.USER_STOP)
            self.assertEqual((self.output/progress).read_bytes(),before)
        self.popen.assert_not_called()

    def test_stop_timeout_retains_pause_and_never_force_kills_or_starts(self):
        actual=f'/usr/bin/python3 {self.output}/run.py --output {self.output}'
        with patch.object(controls,'process_info',return_value=actual), patch.object(controls,'interrupt_worker') as interrupt, patch.object(controls.threading,'Thread') as watcher:
            with self.assertRaisesRegex(controls.ControlError,'still finishing'):
                controls.stop_worker(self.output,'zillow',timeout=0)
        interrupt.assert_called_once()
        watcher.return_value.start.assert_called_once()
        self.assertEqual(self.read('active-run.json')['maintenance'],controls.USER_STOP)
        self.assertEqual(self.read('active-run.json')['status'],'stopping')
        self.popen.assert_not_called()

    def test_stop_refuses_unrelated_pid_or_unowned_queue_lock(self):
        with patch.object(controls,'process_info',return_value='/usr/bin/python3 other.py'), patch.object(controls,'interrupt_worker') as interrupt:
            with self.assertRaisesRegex(controls.ControlError,'another process'):
                controls.stop_worker(self.output,'zillow')
            interrupt.assert_not_called()
        with (self.output/'zillow-run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(controls.ControlError,'another worker'):
                controls.stop_worker(self.output,'zillow')
        self.assertNotIn('maintenance',self.read('active-run.json'))

    def test_stop_preserves_existing_maintenance_and_safety_stop(self):
        self.write('active-run.json',dict(self.read('active-run.json'),maintenance='repair'))
        self.write('zillow-progress.json',{'stop_reason':'HTTP 403','consecutive_failures':11})
        before=(self.output/'zillow-progress.json').read_bytes()
        controls.stop_worker(self.output,'zillow')
        self.assertEqual(self.read('active-run.json')['maintenance'],'repair')
        self.assertEqual((self.output/'zillow-progress.json').read_bytes(),before)

    def test_real_fake_workers_start_and_restart_under_separate_locks(self):
        """Exercise actual subprocesses and SIGINT, never REL or the live queues."""
        import sys
        patch.stopall()
        controls._children.clear()
        def cleanup():
            for process in list(controls._children):
                if process.poll() is None: process.terminate()
                process.wait(timeout=5)
            controls._children.clear()
        self.addCleanup(cleanup)
        code='''import fcntl,signal,sys,time
from pathlib import Path
out=Path(sys.argv[sys.argv.index('--output')+1])
name='zillow-run.lock' if Path(__file__).name=='run.py' else 'run.lock'
with (out/name).open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
    print('READY',flush=True)
    while True:time.sleep(0.1)
'''
        for worker,run_name,key,script in [('ZILLOW','active-run.json','launcher_command','run.py'),('TAX','county-run.json','command','crawl_county_taxes.py')]:
            (self.output/script).write_text(code)
            run=self.read(run_name);command=run[key];index=command.index(str(self.output/script))
            run[key]=[sys.executable,str(self.output/script),*command[index+1:]];run['pid']=0
            self.write(run_name,run)
            progress='zillow-progress.json' if worker=='ZILLOW' else 'county-progress.json'
            before=(self.output/progress).read_bytes()
            self.assertIn('Start requested',self.run_worker(worker))
            first=self.read(run_name)['pid']
            self.assertTrue(controls.process_info(first))
            self.assertTrue(controls.queue_busy(self.output/('zillow-run.lock' if worker=='ZILLOW' else 'run.lock')))
            self.assertIn('Restart requested',self.run_worker(worker,timeout=5))
            self.assertNotEqual(self.read(run_name)['pid'],first)
            self.assertFalse(controls.process_info(first))
            self.assertEqual((self.output/progress).read_bytes(),before)

            second=self.read(run_name)['pid']
            controls.stop_worker(self.output,worker,timeout=5)
            self.assertFalse(controls.process_info(second))
            self.assertFalse(controls.queue_busy(self.output/('zillow-run.lock' if worker=='ZILLOW' else 'run.lock')))
            self.assertEqual((self.output/progress).read_bytes(),before)
            self.assertEqual(self.read(run_name)['maintenance'],controls.USER_STOP)
