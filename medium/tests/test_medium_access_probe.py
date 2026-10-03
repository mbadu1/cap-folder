"""Offline checks for the bounded access probe and evidence replay."""

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "medium" / "probe_medium_history_access.py"
spec = importlib.util.spec_from_file_location("medium_access_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_candidate_order_is_independent_of_sitemap_order(self):
        urls = [f"https://medium.com/@test{i}" for i in range(10)]
        self.assertEqual(probe.candidates(urls), probe.candidates(list(reversed(urls)) + urls))

    def test_live_probe_stops_at_challenge_and_keeps_no_challenge_body(self):
        calls = []

        class Response:
            url = "https://medium.com/robots.txt"
            status_code = 403
            headers = {"Content-Type": "text/html", "cf-mitigated": "challenge"}
            content = b"<html><title>Just a moment...</title>challenge payload</html>"

        class Session:
            headers = {}

            def get(self, url, **kwargs):
                calls.append(url)
                return Response()

        with patch.object(probe.requests, "Session", Session):
            probe.collect(self.root, delay=3.0)
        self.assertEqual(calls, ["https://medium.com/robots.txt"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["request_log.json"])
        result = probe.report(self.root, self.root / "report")
        self.assertEqual(result["status"], "access_blocked")
        self.assertEqual(result["full_text_records_collected"], 0)

    def test_replay_rejects_modified_source_snapshot(self):
        (self.root / "robots.txt").write_text("modified")
        probe.write_json(self.root / "request_log.json", [{"url": "https://medium.com/robots.txt", "status": 200, "sha256": "different"}])
        with self.assertRaisesRegex(ValueError, "Snapshot cannot be reconciled"):
            probe.report(self.root, self.root / "report")

    def check_feed(self, body):
        xml = (
            '<rss version="2.0"><channel><title>Example</title>'
            '<link>https://medium.com/@example?source=rss-abcdef123456------2</link>'
            f'<description>Profile feed</description>{body}</channel></rss>'
        ).encode()
        (self.root / "profile_feed_0.xml").write_bytes(xml)
        probe.write_json(self.root / "request_log.json", [])
        probe.write_json(self.root / "feed_request_log.json", [{"url": "https://medium.com/feed/@example", "status": 200, "sha256": probe.digest(xml)}])
        result = probe.report(self.root, self.root / "report")
        self.assertEqual(result["status"], "incomplete_access_probe")
        self.assertFalse(result["identity_tracking_parameters_verified"])
        self.assertFalse(result["history_pagination_verified"])
        self.assertEqual(result["stable_creator_ids_verified"], 0)
        self.assertEqual(result["full_text_records_collected"], 0)
        self.assertIn("rss_only_unknown_completeness", (self.root / "report" / "profile_feed_audit.csv").read_text())

    def test_empty_feed_is_not_complete_history(self):
        self.check_feed("")

    def test_feed_entry_does_not_establish_history_or_stable_identity(self):
        self.check_feed("<item><guid>https://medium.com/p/example</guid><description>Preview only</description></item>")


if __name__ == "__main__":
    unittest.main()
