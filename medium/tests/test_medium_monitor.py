"""Offline monitor checks: all errors, shared cooldowns, provenance and liveness."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "medium/scripts"))
import monitor_dcc as m
from collect_medium_history import Collector, atomic_json
from check_run import check
from parallel_medium_collection import ROOT, sha_file


class MediumMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache'
        self.c = Collector(self.cache, self.cache / 'report', min_free_gb=0, max_cache_gb=0)
        self.addCleanup(self.c.db.close)
        self.binding = dict(sources={'parallel_medium_collection.py': sha_file(ROOT / 'medium/scripts/parallel_medium_collection.py')})
        self.c.set('team_binding', self.binding)
        self.c.db.commit()
        atomic_json(self.cache / 'binding.json', self.binding)
        atomic_json(self.cache / 'request_gate.json', dict(cooldown_until=0, last_start=0, rate_events=[]))
        self.heartbeat = dict(worker_pid=99999999, node=os.uname().nodename, slurm_job_id=os.environ.get('SLURM_JOB_ID'),
                              state='pilot_complete', updated_at=datetime.now(timezone.utc).isoformat())
        atomic_json(self.cache / 'report/heartbeat.json', self.heartbeat)
        self.snapshot = dict(heartbeat=dict(self.heartbeat, worker_pid=os.getpid(), state='running'),
                             recorded_pid_alive_on_this_node=True, process_command_matches=True, same_node=True,
                             same_job=True, collector_lock_held=True, code_matches_binding=True,
                             file_matches_sqlite_binding=True, latest_request=None, cooldown_remaining_seconds=0,
                             stop=False, pause=None)

    def request(self, status, error=None):
        self.c.db.execute('INSERT INTO requests(url,kind,item_key,started_at,status,error) VALUES(?,?,?,?,?,?)',
                          ('https://medium.com/feed/@fixture', 'feed', 'https://medium.com/@fixture', '2026-10-03T00:00:00Z', status, error))
        self.c.db.commit()
        self.snapshot['latest_request'] = dict(id=self.c.db.execute('SELECT MAX(id) FROM requests').fetchone()[0])

    def test_every_http_transport_and_parse_error_logged_once_with_429_classified(self):
        for status, error in ((200, None), (404, 'HTTP 404'), (503, 'HTTP 503'), (429, 'HTTP 429'), (0, 'timeout'), (200, 'parser error')):
            self.request(status, error)
        with patch('requests.Session.get', side_effect=AssertionError('Monitor must not fetch')):
            first = m.observe(self.cache, inspector=lambda _: copy.deepcopy(self.snapshot))
            second = m.observe(self.cache, first, inspector=lambda _: copy.deepcopy(self.snapshot))
        events = [json.loads(s) for s in (self.cache / 'monitor_events.jsonl').read_text().splitlines()]
        errors = [e for e in events if e['kind'] in ('request_error', 'rate_limit')]
        self.assertEqual([2, 3, 4, 5, 6], [e['request']['id'] for e in errors])
        self.assertEqual('rate_limit', errors[2]['kind'])
        self.assertEqual(6, second['error_cursor'])
        self.assertEqual(1, len([e for e in events if e['kind'] == 'health_change']))
        self.assertEqual('running', second['health'])

    def test_shared_cooldown_is_healthy_even_with_old_progress_or_heartbeat(self):
        previous = dict(last_request_id=7, last_progress_epoch=100)
        self.snapshot['latest_request'] = dict(id=7)
        epoch=time.time()
        self.assertEqual('request_progress_stalled', m.classify(self.snapshot, previous, epoch))
        self.snapshot['cooldown_remaining_seconds'] = 60
        self.snapshot['heartbeat']['updated_at'] = '2000-01-01T00:00:00+00:00'
        self.assertEqual('cooldown_auto_resume', m.classify(self.snapshot, previous, epoch))

    def test_markers_bindings_node_job_and_process_mismatch_are_distinguished(self):
        for field, value, expected in (('pause', {'reason':'challenge'}, 'paused_for_review'), ('stop', True, 'operator_stopped'),
            ('code_matches_binding', False, 'binding_mismatch'), ('file_matches_sqlite_binding', False, 'binding_mismatch'),
            ('same_node', False, 'inspect_registered_node'), ('same_job', False, 'inspect_registered_job'),
            ('process_command_matches', False, 'process_or_lock_mismatch'), ('collector_lock_held', False, 'process_or_lock_mismatch')):
            with self.subTest(field=field):
                snap=copy.deepcopy(self.snapshot);snap[field]=value
                self.assertEqual(expected,m.classify(snap))

    def test_checkpoint_check_detects_changed_binding_and_does_not_claim_dead_pid_alive(self):
        snapshot=check(self.cache)
        self.assertFalse(snapshot['recorded_pid_alive_on_this_node'])
        self.assertFalse(snapshot['collector_lock_held'])
        self.assertEqual('finished',m.classify(snapshot))
        atomic_json(self.cache / 'binding.json',dict(self.binding,tampered=True))
        self.assertEqual('binding_mismatch',m.classify(check(self.cache)))

    def test_inspection_failure_is_recorded_and_later_recovery_keeps_error_cursor(self):
        self.request(429,'HTTP 429')
        first=m.observe(self.cache,inspector=lambda _: copy.deepcopy(self.snapshot))
        def broken(_):raise OSError('temporary read failure')
        second=m.observe(self.cache,first,inspector=broken)
        self.assertEqual('inspection_error',second['health'])
        self.assertEqual(first['error_cursor'],second['error_cursor'])
        third=m.observe(self.cache,second,inspector=lambda _: copy.deepcopy(self.snapshot))
        self.assertEqual('running',third['health'])
        events=[json.loads(s) for s in (self.cache/'monitor_events.jsonl').read_text().splitlines()]
        self.assertEqual(1,len([e for e in events if e['kind']=='rate_limit']))

    def test_start_refuses_unverified_allocation_without_spawning(self):
        with patch.dict(os.environ,{},clear=True),patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'dcc-agent'):
                m.start(self.cache,600,1800)
            spawn.assert_not_called()


if __name__ == '__main__':unittest.main()
