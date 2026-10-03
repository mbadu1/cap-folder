from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/substack"))
from sample_substack_cached import Run, topic_index, TOPICS
from build_substack_platform_month import classify_topic, sha256_text
from collect_substack_history import HISTORY_STAGE


def post(ident, creators, stamp="2020-01-15T12:00:00Z", language="en", audience="everyone", text=None):
    text = " ".join(["word"]*210) if text is None else text
    return dict(post_id=ident, published_at=stamp, publication_id="10", language=language,
        publication=dict(publication_name="Technology",hero_text="Science education"),
        title="Science education", subtitle="", full_text=text, retained_word_count=len(text.split()),
        text_sha256=sha256_text(text) if text else "", audience=audience,is_paywalled=audience!="everyone",
        bylines=[dict(creator_id=c,name="Person "+c,handle="person"+c) for c in creators])


class CachedSamplingTests(unittest.TestCase):
    def prepare(self, root):
        source=root/"source.sqlite3"
        db=sqlite3.connect(source)
        db.execute("CREATE TABLE parsed(stage TEXT,item_key TEXT,ok INTEGER,payload TEXT,error_type TEXT,error_message TEXT,observed_at TEXT,PRIMARY KEY(stage,item_key))")
        base=[post("1",["1","2"]),post("2",["1"]),post("3",["1"]),post("4",["1"]),
              post("5",["3"],language=""),post("6",["4"],language="fr"),
              post("7",["5"],audience="only_paid",text=""),
              post("8",["6"],stamp="2026-09-18T00:00:00Z"),
              post("9",["7"]),post("10",["8"],stamp="2026-09-17T23:59:59Z")]
        rows=[("https://a.example",1,dict(posts=base)),
              ("https://b.example",1,dict(posts=[post("1",["1","2"]),post("9",["7"],text=" ".join(["changed"]*210))])),
              ("https://c.example",0,None)]
        for url,ok,payload in rows:
            db.execute("INSERT INTO parsed VALUES(?,?,?,?,?,?,?)",(HISTORY_STAGE,url,ok,
                json.dumps(payload) if payload else None,None if ok else "HTTP403",None if ok else "Restricted","2026-10-02T00:00:00Z"))
        db.commit();db.close()
        frame=root/"frame.csv"
        with frame.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["publication_url"])
            w.writerows([[r[0]] for r in rows]+[["https://pending.example"]])
        receipt=root/"merge.json"
        receipt.write_text(json.dumps(dict(combined_key_and_count_reconciliation="PASS",database_bytes=source.stat().st_size,
            full_frame_size=4,in_frame_attempted=3,pending=1)))
        return Namespace(source=source,frame=frame,merge_manifest=receipt,output=root/"sample",target=8,topic_floor=1)

    def test_complete_provisional_draw_dedup_coauthors_access_cutoff_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            args=self.prepare(Path(temp))
            source_hash=sha256_text(args.source.read_bytes().hex())
            run=Run(args);run.index();run.db.close();run.lock.close()
            run=Run(args);run.run();run.lock.close()
            root=args.output
            manifest=json.loads((root/"manifest.json").read_text())
            self.assertEqual(manifest["indexed_histories"],3)
            self.assertEqual(manifest["unique_posts"],9)
            self.assertEqual(manifest["conflicting_post_ids"],1)
            self.assertFalse(manifest["final_sampling_ready"])
            self.assertEqual(source_hash,sha256_text(args.source.read_bytes().hex()))
            db=sqlite3.connect(root/"index.sqlite3")
            selected=set(r[0] for r in db.execute("SELECT creator_id FROM selected_creators"))
            self.assertEqual(selected,{"1","2","3","8"})
            self.assertEqual(db.execute("SELECT count(*) FROM bylines WHERE post_id='1'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM post_sources WHERE post_id='1'").fetchone()[0],2)
            selected_1=[r[0] for r in db.execute("SELECT post_id FROM assignments WHERE creator_id='1' ORDER BY post_priority")]
            expected=sorted(["1","2","3","4"],key=lambda ident:sha256_text("post-priority-v1|substack|"+ident))[:3]
            self.assertEqual(selected_1,expected)
            jan=json.loads((root/"substack/2020/01/manifest.json").read_text())
            self.assertEqual(jan["creator_months"],3)
            self.assertEqual(jan["selected_text_assignments"],5)
            self.assertLessEqual(jan["unique_selected_texts"],jan["selected_text_assignments"])
            self.assertFalse(jan["ready_to_publish"])
            self.assertEqual(len(list((root/"substack").glob("*/*/manifest.json"))),81)
            sept=json.loads((root/"substack/2026/09/manifest.json").read_text())
            self.assertEqual((sept["creator_months"],sept["observed_days"]),(1,17))
            self.assertEqual(db.execute("SELECT count(*) FROM assignments a LEFT JOIN selected_text t USING(post_id) WHERE t.post_id IS NULL").fetchone()[0],0)
            db.close()

    def test_optimized_classifier_matches_shared_rules(self):
        for text in ("", "science policy election", "literature poetry travel", "music art finance", "Faith & spirituality", "technology science education"):
            self.assertEqual(TOPICS[topic_index(text)],classify_topic(text)[0])

    def test_resume_refuses_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            args=self.prepare(Path(temp))
            run=Run(args);run.db.close();run.lock.close()
            args.source.touch()
            with self.assertRaisesRegex(ValueError,"binding mismatch"):
                Run(args)


if __name__=="__main__":
    unittest.main()
