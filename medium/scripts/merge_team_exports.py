#!/usr/bin/env python3
"""Merge stopped Medium shard exports into a NEW copy of the baseline cache.

Preserve the baseline, source/body versions and errors. Produce a new checkpoint
for the next owner's reconciliation; never publish corpus data or draw a sample.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import json
from pathlib import Path
import shutil
import sqlite3

from collect_medium_history import digest, atomic_json, now
from parallel_medium_collection import sha_file


def merge(baseline, exports, output):
    baseline, output = Path(baseline), Path(output)
    if output.exists():
        raise FileExistsError("Use a new output cache; never overwrite a checkpoint")
    checked = []
    for directory in map(Path, exports):
        manifest = json.loads((directory / "manifest.json").read_text())
        for name, expected in manifest["files"].items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or sha_file(directory / rel) != expected:
                raise ValueError("Export hash mismatch or unsafe path: " + name)
        if any(t["state"] == "inflight" for t in manifest["tasks"]):
            raise ValueError("Export has unsettled requests")
        checked.append((directory, manifest))
    with (baseline / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True)
        atomic_json(output / "MERGE_INCOMPLETE.json", dict(at=now()))
        src = sqlite3.connect((baseline / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
        db = sqlite3.connect(output / "crawl.sqlite3")
        try:
            src.backup(db)
            # Copy content-addressed blobs with byte/hash checks, never hard links.
            for directory in [baseline] + [d for d, _ in checked]:
                if not (directory / "raw").exists():
                    continue
                for blob in (directory / "raw").rglob("*.gz"):
                    rel = blob.relative_to(directory)
                    target = output / rel
                    if digest(gzip.decompress(blob.read_bytes())) != blob.stem:
                        raise ValueError("Raw source hash mismatch")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(blob, target)
            imported = []
            db.execute("CREATE TABLE IF NOT EXISTS team_imports(export_sha256 TEXT PRIMARY KEY,receipt TEXT NOT NULL)")
            for directory, manifest in checked:
                export_hash = sha_file(directory / "manifest.json")
                if db.execute("SELECT 1 FROM team_imports WHERE export_sha256=?", (export_hash,)).fetchone():
                    continue
                other = sqlite3.connect((directory / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
                other.row_factory = sqlite3.Row
                try:
                    policy = json.loads(db.execute("SELECT value FROM meta WHERE key='author_pool_policy'").fetchone()[0])
                    shard_policy = json.loads(other.execute("SELECT value FROM meta WHERE key='author_pool_policy'").fetchone()[0])
                    if policy["target"] != shard_policy["target"]:
                        raise ValueError("Export target differs from baseline")
                    for table in ("sources", "story_frame", "post_aliases", "observations", "bodies", "author_pool_creators", "author_pool_profiles", "author_pool_mirrors"):
                        columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                        pk = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[5]]
                        for row in other.execute(f"SELECT * FROM {table}"):
                            values = tuple(row[c] for c in columns)
                            if table in ("observations", "bodies"):
                                old = db.execute(f"SELECT * FROM {table} WHERE " + " AND ".join(c + "=?" for c in pk), tuple(row[c] for c in pk)).fetchone()
                                if old and tuple(old) != values:
                                    raise ValueError("Conflicting source version in " + table)
                            db.execute(f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
                    for row in other.execute("SELECT * FROM creators WHERE feed_count IS NOT NULL"):
                        old = db.execute("SELECT feed_count FROM creators WHERE profile_url=?", (row["profile_url"],)).fetchone()
                        if old and old[0] is not None:
                            continue
                        db.execute("INSERT OR REPLACE INTO creators VALUES(?,?,?,?,?,?,?,?,?)", tuple(row))
                    for row in other.execute("SELECT * FROM tasks WHERE state IN ('done','error')"):
                        old = db.execute("SELECT state FROM tasks WHERE kind=? AND item_key=?", (row["kind"], row["item_key"])).fetchone()
                        if not old or old[0] == "pending":
                            db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", tuple(row))
                        if row["kind"] == "mirror":
                            db.execute("UPDATE author_pool_mirrors SET state=(SELECT state FROM tasks WHERE kind='mirror' AND item_key=post_id) WHERE post_id=?", (row["item_key"],))
                    for row in other.execute("SELECT * FROM requests ORDER BY id"):
                        db.execute("INSERT INTO requests(url,kind,item_key,started_at,status,bytes,response_hash,cf_mitigated,retry_after,error) VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(row)[1:])
                    for row in other.execute("SELECT * FROM routes WHERE state='blocked'"):
                        db.execute("INSERT OR REPLACE INTO routes VALUES(?,?,?,?)", tuple(row))
                    gate_path = directory / "request_gate.json"
                    if gate_path.exists():
                        gate = json.loads(gate_path.read_text())
                        old = json.loads((db.execute("SELECT value FROM meta WHERE key='cooldown_until'").fetchone() or ["0"])[0])
                        db.execute("INSERT OR REPLACE INTO meta VALUES('cooldown_until',?)", (json.dumps(max(old, gate["cooldown_until"])),))
                        events = json.loads((db.execute("SELECT value FROM meta WHERE key='rate_events'").fetchone() or ["[]"])[0])
                        db.execute("INSERT OR REPLACE INTO meta VALUES('rate_events',?)", (json.dumps(sorted(set(events + gate["rate_events"]))),))
                    receipt = dict(export_sha256=export_hash, binding=manifest["binding"], imported_at=now())
                    db.execute("INSERT INTO team_imports VALUES(?,?)", (export_hash, json.dumps(receipt)))
                    imported.append(receipt)
                finally:
                    other.close()
            count = db.execute("SELECT COUNT(*) FROM author_pool_creators").fetchone()[0]
            if count > policy["target"]:
                raise ValueError("Merged candidate count exceeds shared target; inspect ownership/concurrency")
            db.execute("INSERT OR REPLACE INTO meta VALUES('author_pool_count',?)", (json.dumps(count),))
            db.commit()
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Merged checkpoint integrity failure")
            # Preserve any active markers as evidence, requiring review before reuse.
            markers = []
            for directory in [baseline] + [d for d, _ in checked]:
                for name in ("STOP", "PAUSED.json"):
                    if (directory / name).exists():
                        saved = output / "handoff_evidence" / f"source-{len(markers)}-{name}"
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(directory / name, saved)
                        markers.append(str(saved.relative_to(output)))
                        if name == "STOP":
                            (output / "STOP").touch()
                        else:
                            atomic_json(output / "PAUSED.json", dict(at=now(), reason="Merge retains prior pause evidence", evidence=markers))
            receipt = dict(at=now(), imported=imported, known_candidate_ids=count, preserved_markers=markers,
                           complete_historical_archive=False, primary_sample_records=0)
            atomic_json(output / "merge_receipt.json", receipt)
            (output / "MERGE_INCOMPLETE.json").unlink()
            return receipt
        finally:
            src.close()
            db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-cache", type=Path, required=True)
    p.add_argument("--export", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(merge(a.baseline_cache, a.export, a.output), indent=2))


if __name__ == "__main__":
    main()
