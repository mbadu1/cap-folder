"""Frozen candidate worklists must be disjoint, reproducible and read-only."""
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/medium"))
from freeze_medium_assignments import freeze


class MediumAssignmentTests(unittest.TestCase):
    def test_disjoint_deterministic_frame_preserves_failures_and_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "crawl.sqlite3"
            db = sqlite3.connect(checkpoint)
            db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE author_pool_profiles(profile_url TEXT PRIMARY KEY,creator_key TEXT);
                CREATE TABLE tasks(kind TEXT,item_key TEXT,url TEXT,priority TEXT,state TEXT);
                CREATE INDEX pending_tasks ON tasks(kind,state,priority);
                CREATE TABLE requests(id INTEGER PRIMARY KEY);''')
            db.executemany("INSERT INTO meta VALUES(?,?)", [
                ("author_pool_policy", json.dumps({"target": 5})), ("author_pool_count", "1")])
            db.executemany("INSERT INTO author_pool_profiles VALUES(?,?)", [
                ("https://medium.com/@a", "rss_candidate:abc"),
                ("https://medium.com/@a-alias", "rss_candidate:abc")])
            for i in range(8):
                profile = f"https://medium.com/@p{i}"
                db.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", ("feed", profile,
                    f"https://medium.com/feed/@p{i}", str(i), "error" if i == 0 else "pending"))
            db.execute("INSERT INTO requests VALUES(10)")
            db.commit()
            db.close()
            before = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            first = freeze(checkpoint, root / "one", target=5, reserve=2)
            second = freeze(checkpoint, root / "two", target=5, reserve=2)
            self.assertEqual(first["files"], second["files"])
            self.assertEqual(before, hashlib.sha256(checkpoint.read_bytes()).hexdigest())
            self.assertEqual({"shard-1": 2, "shard-2": 2}, first["shard_counts"])
            self.assertFalse(first["execution_ready"])
            def rows(name):
                with (root / "one" / name).open() as stream:
                    return list(csv.DictReader(stream))
            primary = rows("source_frame.csv")
            self.assertEqual(5, len(primary))
            self.assertNotIn("https://medium.com/@p0", {r["profile_url"] for r in primary})
            s1 = {r["profile_url"] for r in rows("shard-1.csv")}
            s2 = {r["profile_url"] for r in rows("shard-2.csv")}
            self.assertFalse(s1 & s2)
            reserve = {r["profile_url"] for r in rows("shard-1-reserve.csv") + rows("shard-2-reserve.csv")}
            self.assertFalse((s1 | s2) & reserve)
            with self.assertRaises(FileExistsError):
                freeze(checkpoint, root / "one", target=5, reserve=2)
            with self.assertRaises(ValueError):
                freeze(checkpoint, root / "wrong", target=6, reserve=0)


if __name__ == "__main__":
    unittest.main()
