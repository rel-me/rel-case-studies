"""Real Crawlee persistence checks; no site or browser access."""
import json
import os
import subprocess
import tempfile
import unittest
import sys


SCRIPT = '''
import asyncio, os, json
from crawlee.configuration import Configuration
from crawlee.storages import RequestQueue
async def main():
 c=Configuration(storage_dir=os.environ['TEST_STORAGE'],purge_on_start=False)
 q=await RequestQueue.open(name=os.environ['TEST_QUEUE'],configuration=c)
 await q.add_request('https://example.com/account')
 r=await q.fetch_next_request()
 print(json.dumps({'available':r is not None}))
 if r: await q.mark_request_as_handled(r)
asyncio.run(main())
'''


class QueueResumeTests(unittest.TestCase):
    def test_cycle_statistics_resume_without_leaking_into_next_cycle(self):
        script = '''
import asyncio,json,os
from crawlee.configuration import Configuration
from crawlee.storages import KeyValueStore
from crawlee.statistics import Statistics
async def main():
 c=Configuration(storage_dir=os.environ['TEST_STORAGE'],purge_on_start=False)
 async def store(): return await KeyValueStore.open(name='stats',configuration=c)
 stats=Statistics.with_default_state(persistence_enabled=True,persist_state_key=os.environ['TEST_QUEUE']+'-statistics',persist_state_kvs_factory=store)
 async with stats:
  print(json.dumps({'finished':stats.state.requests_finished}))
  stats.record_request_processing_start('post')
  stats.record_request_processing_finish('post')
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as folder:
            def run(cycle):
                env = dict(os.environ, TEST_STORAGE=folder, TEST_QUEUE=cycle)
                result = subprocess.run([sys.executable, '-c', script], env=env, text=True, capture_output=True, check=True)
                return json.loads(result.stdout.strip().splitlines()[-1])['finished']
            self.assertEqual(run('pilot'), 0)
            self.assertEqual(run('pilot'), 1)
            self.assertEqual(run('baseline'), 0)

    def test_persistence_deduplicates_within_cycle_but_allows_next_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            def run(queue):
                env = dict(os.environ, TEST_STORAGE=folder, TEST_QUEUE=queue)
                result = subprocess.run([sys.executable, '-c', SCRIPT], env=env, text=True, capture_output=True, check=True)
                return json.loads(result.stdout.strip().splitlines()[-1])['available']
            self.assertTrue(run('cycle-one'))
            self.assertFalse(run('cycle-one'))
            self.assertTrue(run('cycle-two'))


if __name__ == '__main__':
    unittest.main()
