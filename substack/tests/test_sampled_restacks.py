from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/substack"))
import collect_sampled_restacks as r


def target(ident="1", pub="10"):
    return dict(post_id=ident, publication_id=pub, publication_url="https://fixture.substack.invalid",
                canonical_url="https://fixture.substack.invalid/p/post-" + ident, year_month="2020-01")


def response(payload, url="https://fixture.substack.invalid/api/v1/posts/by-id/1"):
    body = json.dumps(payload).encode()
    return dict(body=body, request_url=url, response_url=url, observed_at="2026-10-02T12:00:00Z", response_sha256=r.sha(body))


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def fetch(self, url):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, BaseException):
            raise value
        return response(value, url)


class RestacksTests(unittest.TestCase):
    def store(self, root, targets=None):
        targets = targets or [target()]
        s = r.Store(root)
        binding = dict(version=r.VERSION, target_sha256=r.sha(r.canonical(targets)), script_sha256="fixture")
        s.bind(targets, binding)
        self.addCleanup(s.close)
        return s, binding

    def test_zero_and_missing_and_bad_numeric_types(self):
        res = response({})
        t = target()
        self.assertEqual(r.metric_record(t, dict(id=1, publication_id=10, restacks=0), res, "fixture")["status"], "ok")
        missing = r.metric_record(t, dict(id=1, publication_id=10), res, "fixture")
        self.assertEqual(missing["status"], "missing_field")
        self.assertIsNone(missing["restacks_count"])
        for count in (True, -1, "2", 2.5):
            with self.assertRaises(r.Attention):
                r.metric_record(t, dict(id=1, publication_id=10, restacks=count), res, "fixture")

    def test_wrong_id_and_publication_pause_without_saving(self):
        for post in (dict(id=2, publication_id=10, restacks=2), dict(id=1, publication_id=11, restacks=2)):
            with tempfile.TemporaryDirectory() as tmp:
                s, _ = self.store(Path(tmp))
                net = FakeTransport({target()["publication_url"]+"/api/v1/posts/by-id/1": {"post": post}})
                with self.assertRaises(r.Attention):
                    r.collect(s, net)
                self.assertTrue((s.root/"PAUSED.json").exists())
                self.assertEqual(s.status()["terminal_posts"], 0)

    def test_batch_excludes_unsampled_bodies_and_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = target(); s, _ = self.store(Path(tmp))
            url = t["publication_url"]+"/api/v1/posts?limit=50&offset=0&sort=new"
            net = FakeTransport({url: [dict(id=1, publication_id=10, restacks=0, body_html="SECRET_SELECTED_BODY"),
                                       dict(id=999, publication_id=10, restacks=900, body_html="SECRET_UNSELECTED_BODY")]})
            r.collect(s, net, "hybrid")
            self.assertEqual(net.calls, [url])
            self.assertEqual(s.status()["counts"]["ok"], 1)
            r.collect(s, net, "hybrid")
            self.assertEqual(net.calls, [url])
            s.export()
            with (s.root/"post_restacks.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([row["post_id"] for row in rows], ["1"])
            self.assertEqual(rows[0]["restacks_count"], "0")
            dump = "\n".join(s.db.iterdump())
            self.assertNotIn("SECRET", dump)
            self.assertNotIn("900", dump)

    def test_request_budget_preserves_pending_then_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = target(); s, _ = self.store(Path(tmp))
            url = t["publication_url"]+"/api/v1/posts/by-id/1"
            r.collect(s, FakeTransport({url: r.BudgetReached()}))
            self.assertEqual(s.status()["pending"], 1)
            r.collect(s, FakeTransport({url: {"post": dict(id=1, publication_id=10, restacks=5)}}))
            self.assertEqual(s.status()["pending"], 0)
            r.collect(s, FakeTransport({}))  # no second request for success

    def test_batch_missing_count_uses_direct_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,_=self.store(Path(tmp));t=target()
            base=t["publication_url"]
            net=FakeTransport({base+"/api/v1/posts?limit=50&offset=0&sort=new":[dict(id=1,publication_id=10)],
                               base+"/api/v1/posts/by-id/1":{"post":dict(id=1,publication_id=10,restacks=4)}})
            r.collect(s,net,"hybrid")
            self.assertEqual(len(net.calls),2)
            self.assertEqual(s.status()["counts"]["ok"],1)

    def test_404_page_fallback_and_permanent_403_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, _ = self.store(Path(tmp)); t = target()
            url = t["publication_url"]+"/api/v1/posts/by-id/1"
            missing = urllib.error.HTTPError(url,404,"missing",{},io.BytesIO())
            raw = json.dumps(json.dumps({"post":dict(id=1,publication_id=10,restacks=3)}))
            class Fallback(FakeTransport):
                def fetch(self, url):
                    if url == t["canonical_url"]:
                        b = ("<script>window._preloads = JSON.parse(" + raw + ");</script>").encode()
                        return dict(response({}), body=b)
                    return super().fetch(url)
            r.collect(s, Fallback({url: missing}))
            result = json.loads(s.db.execute("SELECT record FROM results").fetchone()[0])
            self.assertEqual(result["restacks_count"], 3)
        with tempfile.TemporaryDirectory() as tmp:
            s, _ = self.store(Path(tmp)); url = target()["publication_url"]+"/api/v1/posts/by-id/1"
            net = FakeTransport({url:urllib.error.HTTPError(url,403,"forbidden",{},io.BytesIO())})
            r.collect(s, net);r.collect(s, net)
            self.assertEqual(len(net.calls),1)
            self.assertEqual(s.status()["counts"]["http_error"],1)

    def test_binding_and_stored_target_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, binding = self.store(Path(tmp))
            with self.assertRaisesRegex(ValueError,"binding mismatch"):
                s.bind([target()],dict(binding,script_sha256="changed"))
            s.db.execute("UPDATE targets SET metadata=?", (json.dumps(target("2")),));s.db.commit()
            with self.assertRaisesRegex(ValueError,"target .*changed"):
                s.bind([target()],binding)

    def test_batch_page_interruption_resumes_offset_without_refetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ts=[target("50"),target("60"),target("70")];s,_=self.store(Path(tmp),ts)
            base=ts[0]["publication_url"]+"/api/v1/posts?limit=50&offset="
            one=base+"0&sort=new";two=base+"50&sort=new"
            page1=[dict(id=i,publication_id=10,restacks=i) for i in range(1,51)]
            page2=[dict(id=i,publication_id=10,restacks=i) for i in range(51,101)]
            first=FakeTransport({one:page1,two:r.BudgetReached()})
            r.collect(s,first,"hybrid",2)
            self.assertEqual(s.status()["pending"],2)
            self.assertEqual(s.db.execute("SELECT offset FROM batches").fetchone()[0],50)
            second=FakeTransport({two:page2});r.collect(s,second,"hybrid",2)
            self.assertEqual(second.calls,[two]);self.assertEqual(s.status()["pending"],0)

    def test_export_left_join_has_every_target_and_null_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            ts = [target("1"),target("2"),target("3")];s, _ = self.store(Path(tmp),ts)
            s.save(r.metric_record(ts[0],dict(id=1,publication_id=10,restacks=0),response({}),"fixture"))
            s.save(r.metric_record(ts[1],dict(id=2,publication_id=10),response({}),"fixture"))
            s.export()
            with (s.root/"post_restacks.csv").open() as f: rows=list(csv.DictReader(f))
            self.assertEqual([x["restacks_count"] for x in rows],["0","",""])
            self.assertEqual([x["status"] for x in rows],["ok","missing_field","pending"])
            self.assertFalse(json.loads((s.root/"export_manifest.json").read_text())["complete_success"])

    def test_result_tampering_cannot_export_unselected_id_or_false_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,binding=self.store(Path(tmp)); t=target()
            record=r.metric_record(t,dict(id=1,publication_id=10,restacks=2),response({}),"fixture")
            s.save(record)
            for bad in (dict(record,post_id="999"),dict(record,status="missing_field",restacks_count=0)):
                s.db.execute("UPDATE results SET record=?",(json.dumps(bad),));s.db.commit()
                with self.assertRaises(ValueError):s.bind([t],binding)
                with self.assertRaises(ValueError):s.export()

    def fixture_dataset(self, root):
        folder=root/"substack/2020/01";folder.mkdir(parents=True)
        meta=folder/"posts_meta.csv"
        with meta.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(target()));w.writeheader();w.writerow(target())
        assigned=folder/"sampling_assignments.csv"
        assigned.write_text("post_id,creator_id\n1,1\n1,2\n")
        files={str(p.relative_to(root)):dict(bytes=p.stat().st_size,sha256=r.sha(p.read_bytes())) for p in (meta,assigned)}
        m=dict(files=files,months=[dict(month="2020-01")],unique_selected_text_rows=1,selected_text_assignments=2,
               dataset="fixture",source_binding={},provisional=True,final_sampling_ready=False)
        (root/"DELIVERY_MANIFEST.json").write_text(json.dumps(m))
        return meta,assigned,m

    def test_prepare_checks_hashes_and_selected_only_join_and_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);meta,assigned,m=self.fixture_dataset(root)
            with patch.object(urllib.request,"urlopen",side_effect=AssertionError("network")):
                ts,binding=r.load_targets(root)
            self.assertEqual(len(ts),1);self.assertEqual(binding["selected_posts"],1)
            meta.write_text(meta.read_text()+"bad")
            with self.assertRaisesRegex(ValueError,"checksum mismatch"):r.load_targets(root)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);meta,assigned,m=self.fixture_dataset(root)
            assigned.write_text("post_id,creator_id\n999,1\n")
            m["files"][str(assigned.relative_to(root))]=dict(bytes=assigned.stat().st_size,sha256=r.sha(assigned.read_bytes()))
            (root/"DELIVERY_MANIFEST.json").write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,"exactly the distinct assigned"):r.load_targets(root)

    def test_only_selected_pilot_ids_and_stop_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,_=self.store(Path(tmp))
            with self.assertRaisesRegex(ValueError,"unselected"):r.collect(s,FakeTransport({}),post_ids=["999"])
            (s.root/"STOP").touch()
            net=r.Transport(s,opener=lambda *a,**k:self.fail("request despite STOP"))
            r.collect(s,net)
            self.assertEqual(s.status()["pending"],1)


class TransportTests(unittest.TestCase):
    def test_429_retry_after_and_budget_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=r.Store(Path(tmp));self.addCleanup(s.close)
            clock=[1000.0]
            def sleep(seconds):clock[0]+=seconds
            calls=[]
            def opener(req,timeout):
                calls.append(clock[0])
                raise urllib.error.HTTPError(req.full_url,429,"rate",{"Retry-After":"5"},io.BytesIO(b"rate limited"))
            net=r.Transport(s,max_requests=1,delay=3,opener=opener,clock=lambda:clock[0],sleep=sleep)
            with self.assertRaises(r.BudgetReached):net.fetch("https://fixture.substack.invalid/post")
            self.assertEqual(float(s.state("cooldown_until")),1005)
            self.assertEqual(calls,[1000])
            self.assertEqual(s.db.execute("SELECT count(*) FROM requests").fetchone()[0],1)
            class Resp:
                status=200;headers={}
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def geturl(self):return "https://fixture.substack.invalid/post"
                def read(self,n):return b"{}"
            net=r.Transport(s,max_requests=1,delay=3,opener=lambda *a,**k:Resp(),clock=lambda:clock[0],sleep=sleep)
            net.fetch("https://fixture.substack.invalid/post")
            self.assertGreaterEqual(clock[0],1005)

    def test_rate403_and_challenge_stop_and_retry_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=r.Store(Path(tmp));self.addCleanup(s.close)
            def rate(req,timeout):raise urllib.error.HTTPError(req.full_url,403,"rate",{"Retry-After":"60"},io.BytesIO())
            with self.assertRaises(r.Attention):r.Transport(s,opener=rate).fetch("https://fixture.substack.invalid/p")
            clock=[1000.]
            def unavailable(req,timeout):raise urllib.error.HTTPError(req.full_url,503,"down",{},io.BytesIO())
            net=r.Transport(s,opener=unavailable,clock=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
            with self.assertRaises(urllib.error.HTTPError):net.fetch("https://fixture.substack.invalid/p")
            self.assertEqual(net.count,3)

    def test_success_status_challenge_is_not_parsed_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=r.Store(Path(tmp));self.addCleanup(s.close)
            class Resp:
                status=200;headers={}
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def geturl(self):return "https://fixture.substack.invalid/post"
                def read(self,n):return b"<title>Just a moment</title><script>challenge-platform</script>"
            net=r.Transport(s,opener=lambda *a,**k:Resp())
            with self.assertRaises(r.Attention):net.fetch("https://fixture.substack.invalid/post")
            self.assertEqual(net.count,1)


if __name__=="__main__":unittest.main()
