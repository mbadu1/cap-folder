"""Two-owner cutover: immutable assignments, exact checkpoint reuse and reconciliation."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_substack_team_collection as fixtures
import team_collection as team


class TwoShardTests(unittest.TestCase):
    setUp = fixtures.TeamCollectionTests.setUp
    payload = fixtures.TeamCollectionTests.payload
    args = fixtures.TeamCollectionTests.args
    collect = fixtures.TeamCollectionTests.collect

    def new_batch(self):
        target = self.root / "two-owner-batch"
        team.repartition(self.batch, target)
        return target

    def migration_args(self, target, shard=1, **overrides):
        return self.args(shard=shard, batch=target, cache_root=self.root / f"new-cache-{shard}",
                         source_batch=self.batch, source_shard=shard,
                         source_cache=self.args(shard=shard).cache_root, **overrides)

    def records(self, path):
        with team.readonly(path) as db:
            return db.execute("SELECT * FROM parsed ORDER BY stage,item_key").fetchall()

    def test_repartition_keeps_old_owners_and_splits_third_without_loss(self):
        old_bytes = {p.name: p.read_bytes() for p in self.batch.iterdir()}
        _, old = team.validate_batch(self.batch)
        target = self.new_batch()
        m, new = team.validate_batch(target)
        self.assertEqual(set(new), {1, 2})
        self.assertEqual([len(new[n]) for n in (1, 2)], [5, 4])
        for n in (1, 2):
            self.assertTrue({r['publication_url'] for r in old[n]} <= {r['publication_url'] for r in new[n]})
        for name in ('source_frame.csv', 'already_attempted.csv', 'existing_errors.csv'):
            self.assertEqual((target / name).read_bytes(), old_bytes[name])
        self.assertEqual({p.name: p.read_bytes() for p in self.batch.iterdir()}, old_bytes)
        self.assertEqual(m['supersedes_batch']['manifest_sha256'], team.sha_file(self.batch / 'manifest.json'))
        with self.assertRaises(FileExistsError):
            team.repartition(self.batch, target)
        with self.assertRaisesRegex(ValueError, 'three-shard'):
            team.repartition(target, self.root / 'invalid')

    def test_real_frozen_two_shard_assignment_covers_old_batch(self):
        root = Path(__file__).resolve().parents[1] / 'data/substack_assignments'
        old_m, old = team.validate_batch(root / '2026-09-28-team-v1', check_code=False)
        new_m, new = team.validate_batch(root / '2026-09-28-team-v2')
        self.assertEqual(new_m['shard_counts'], {'shard-1': 74127, 'shard-2': 74126})
        self.assertEqual(new_m['baseline_count'], old_m['baseline_count'])
        self.assertEqual(new_m['supersedes_batch']['manifest_sha256'], team.sha_file(root / '2026-09-28-team-v1/manifest.json'))
        for n in (1, 2):
            self.assertTrue({r['publication_url'] for r in old[n]} <= {r['publication_url'] for r in new[n]})
        self.assertEqual({r['publication_url'] for rows in old.values() for r in rows},
                         {r['publication_url'] for rows in new.values() for r in rows})

    def test_migration_preserves_exact_rows_and_resumes_only_new_work(self):
        self.collect(self.args(limit=1))
        original = self.records(self.args().cache_root / 'crawl.sqlite3')
        target = self.new_batch()
        args = self.migration_args(target)
        result = team.migrate(args)
        self.assertEqual((result['successful'], result['pending']), (1, 4))
        self.assertEqual(self.records(args.cache_root / 'crawl.sqlite3'), original)
        self.assertEqual(self.records(args.source_cache / 'crawl.sqlite3'), original)
        self.assertTrue((args.source_cache / 'STOP').exists())
        self.assertTrue((args.source_cache / 'RETIRED.json').exists())
        self.assertFalse((args.cache_root / 'STOP').exists())
        (result, code), calls = self.collect(args)
        self.assertEqual((code, calls, result['pending']), (0, 4, 0))
        with self.assertRaisesRegex(RuntimeError, 'retired'):
            self.collect()
        with self.assertRaisesRegex(RuntimeError, 'already migrated'):
            team.migrate(args)

    def test_migration_preserves_failed_rows_and_pause_and_stop(self):
        self.collect(self.args(limit=1))
        cache = self.args().cache_root
        cp = team.Checkpoint(cache / 'crawl.sqlite3')
        _, shards = team.validate_batch(self.batch)
        cp.put(team.HISTORY_STAGE, shards[1][1]['publication_url'], False, None, ValueError('keep this failure'))
        cp.close()
        team.write_json(cache / 'PAUSED.json', {'reason': '403', 'state': 'access_or_rate_stop'})
        (cache / 'STOP').write_text('user stop\n')
        args = self.migration_args(self.new_batch())
        result = team.migrate(args)
        self.assertEqual((result['successful'], result['failed'], result['pending']), (1, 1, 3))
        self.assertEqual(self.records(cache / 'crawl.sqlite3'), self.records(args.cache_root / 'crawl.sqlite3'))
        self.assertEqual((args.cache_root / 'PAUSED.json').read_bytes(), (cache / 'PAUSED.json').read_bytes())
        self.assertEqual((args.cache_root / 'STOP').read_text(), 'user stop\n')
        with self.assertRaisesRegex(RuntimeError, 'pause'):
            self.collect(args)

    def test_migration_rejects_wrong_owner_existing_cache_and_active_source(self):
        self.collect(self.args(limit=1))
        target = self.new_batch()
        args = self.migration_args(target)
        args.source_shard = 2
        with self.assertRaisesRegex(ValueError, 'source batch or owner'):
            team.migrate(args)
        args.source_shard = 1
        args.cache_root.mkdir()
        (args.cache_root / 'keep.txt').write_text('existing work')
        with self.assertRaisesRegex(FileExistsError, 'empty'):
            team.migrate(args)
        args.cache_root = self.root / 'unused-cache'
        with team.cache_lock(args.source_cache):
            with self.assertRaisesRegex(RuntimeError, 'lock'):
                team.migrate(args)
        self.assertFalse((args.source_cache / 'RETIRED.json').exists())

    def test_migration_rejects_changed_source_binding_or_foreign_records(self):
        self.collect(self.args(limit=1))
        args = self.migration_args(self.new_batch())
        binding = args.source_cache / 'assignment.json'
        original = binding.read_bytes()
        team.write_json(binding, {'wrong': 'binding'})
        with self.assertRaisesRegex(ValueError, 'different assignment'):
            team.migrate(args)
        binding.write_bytes(original)
        cp = team.Checkpoint(args.source_cache / 'crawl.sqlite3')
        cp.put(team.HISTORY_STAGE, 'https://unassigned.invalid', False, None, ValueError('foreign'))
        cp.close()
        with self.assertRaisesRegex(ValueError, 'outside'):
            team.migrate(args)

    def test_new_batch_rejects_old_checkpoint_and_absent_shard(self):
        self.collect(self.args(limit=1))
        target = self.new_batch()
        with self.assertRaisesRegex(ValueError, 'different assignment'):
            self.collect(self.args(batch=target))
        with self.assertRaisesRegex(ValueError, 'not part'):
            team.run(self.args(batch=target, shard=3, check=True))

    def test_two_shard_merge_rejects_missing_duplicate_or_old_exports(self):
        self.collect()
        old_export = self.root / 'old-export'
        team.export(self.args(output=old_export))
        target = self.new_batch()
        exports = []
        for n in (1, 2):
            args = self.args(shard=n, batch=target, cache_root=self.root / f'new-{n}', output=self.root / f'export-{n}')
            self.collect(args)
            team.export(args)
            exports.append(args.output)
        for i, chosen in enumerate(([exports[0]], [exports[0], exports[0]], [old_export, exports[1]])):
            with self.subTest(chosen=chosen), self.assertRaises(ValueError):
                team.merge(argparse.Namespace(batch=target, baseline_db=self.baseline, exports=chosen, output=self.root / f'bad-{i}.sqlite3'))
        output = self.root / 'good.sqlite3'
        team.merge(argparse.Namespace(batch=target, baseline_db=self.baseline, exports=exports, output=output))
        self.assertEqual(len(self.records(output)), 12)


if __name__ == '__main__':
    unittest.main()
