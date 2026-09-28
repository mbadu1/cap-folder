"""Offline fixtures for mirror field addressing, identity, and body retention."""
import sys
import json
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "medium"))
from medium_mirror_adapter import decode_references, parse_public_article, parse_public_response, story_id


STORY = "https://medium.com/p/0123456789ab"
CANONICAL = "https://medium.com/@fixture/story-0123456789ab"
MIRROR = "https://freedium-mirror.cfd/" + STORY + "/__data.json"


def fixture(is_free=True):
    values = []

    def encode(value):
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            values[index] = {key: encode(child) for key, child in value.items()}
        elif isinstance(value, list):
            values[index] = [encode(child) for child in value]
        else:
            values[index] = value
        return index

    encode({
        "originalUrl": STORY,
        "eager": {
            "html": "<p>HTML body must not be selected as Markdown.</p>",
            "markdown": "Actual Markdown body.",
            "article": {
                "url": CANONICAL, "title": "Fixture title",
                "authors": [{"name": "Fixture author"}],
                "publishedAt": "2020-02-01T00:30:00Z",
                "isFree": is_free,
            },
            "error": None,
        },
    })
    return {"nodes": [{"type": "skip"}, {"data": values}]}


class MirrorAdapterTests(unittest.TestCase):
    def streamed_fixture(self, is_free=True):
        original = fixture(is_free)
        values = original["nodes"][1]["data"]
        # The deferred chunk has its own reference graph, rooted at the
        # original explicit eager response. Unused graph entries are allowed.
        chunk_values = [values[values[0]["eager"]]] + values[1:]
        initial = {"type":"data","nodes":[{"type":"skip"},{"data":[
            {"originalUrl":1,"eager":2,"streamed":3},STORY,None,["Promise",4],7
        ]}]}
        return initial, {"type":"chunk","id":7,"data":chunk_values}

    def test_deferred_article_uses_explicit_chunk_and_keeps_access_gate(self):
        for free in [True,False]:
            records=self.streamed_fixture(free)
            raw=b"\n".join(json.dumps(r).encode() for r in records)
            result=parse_public_response(raw,STORY,MIRROR)
            self.assertEqual("streamed",result["response_mode"])
            self.assertEqual(free,"html" in result)
            self.assertEqual("0123456789ab",result["post_id"])

    def test_unresolved_or_duplicate_deferred_chunks_fail_closed(self):
        first,chunk=self.streamed_fixture()
        for records in [[first],[first,chunk,chunk]]:
            with self.assertRaises(ValueError):
                parse_public_response(b"\n".join(json.dumps(r).encode() for r in records),STORY,MIRROR)

    def test_deferred_error_does_not_produce_body(self):
        first,chunk=self.streamed_fixture()
        del chunk["data"]
        chunk["error"]=[{"message":1},"Fixture upstream failure"]
        with self.assertRaisesRegex(ValueError,"deferred source error"):
            parse_public_response(b"\n".join(json.dumps(r).encode() for r in [first,chunk]),STORY,MIRROR)

    def test_addresses_markdown_and_keeps_original_dates_and_urls(self):
        result = parse_public_article(fixture(), STORY, MIRROR)
        self.assertEqual(result["markdown"], "Actual Markdown body.")
        self.assertEqual(result["canonical_medium_url"], CANONICAL)
        self.assertEqual(result["requested_medium_url"], STORY)
        self.assertEqual(result["mirror_source_url"], MIRROR)
        self.assertEqual(result["published_at_source"], "2020-02-01T00:30:00Z")
        self.assertFalse(result["selected_primary_post"])
        self.assertEqual(result["identity_quality"], "display_name_only")

    def test_paid_or_unknown_access_returns_metadata_only(self):
        for flag in [False, None, 1, "true"]:
            with self.subTest(flag=flag):
                result = parse_public_article(fixture(flag), STORY, MIRROR)
                self.assertNotIn("markdown", result)
                self.assertNotIn("html", result)
                self.assertEqual(result["text_status"], "not_retained_access_unverified")

    def test_mismatched_story_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "requested story"):
            parse_public_article(fixture(), "https://medium.com/p/aaaaaaaaaaaa", MIRROR)

    def test_unsupported_references_and_cycles_fail_closed(self):
        for values in [[{"x": 99}], [{"x": -2}], [{"x": 0}], [{"x": True}]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                decode_references(values)

    def test_story_id_handles_guid_and_slug_without_tracking_queries(self):
        self.assertEqual(story_id(STORY), story_id(CANONICAL + "?source=rss"))
        self.assertEqual("6f8c2841e34", story_id("https://medium.com/@terryschwadron/a-decade-of-disruption-6f8c2841e34"))
        self.assertEqual("6f8c2841e34", story_id("https://medium.com/p/6f8c2841e34"))
        with self.assertRaises(ValueError):
            story_id("https://medium.com/@fixture")


if __name__ == "__main__":
    unittest.main()
