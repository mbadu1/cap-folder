#!/usr/bin/env python3
"""Offline scraper alignment check and optional exported-data integrity audit."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

import collect_substack_history as history
import team_collection as team

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/substack_compatibility_v2"
DEFAULT_BATCH = ROOT / "data/substack_assignments/2026-09-28-team-v2"


def normalized_hash(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class FixtureTransport:
    """Only explicitly supplied synthetic URLs can be requested."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected fixture request: " + url)
        return json.dumps(self.responses[url], ensure_ascii=False).encode("utf-8")


def endpoint(publication, offset=0):
    return f"{publication}/api/v1/posts?limit=50&offset={offset}&sort=new"


def load_reference():
    reference = team.read_json(FIXTURES / "manifest.json")
    for name, digest in reference["files"].items():
        if Path(name).name != name or team.sha_file(FIXTURES / name) != digest:
            raise ValueError("Reference fixture checksum mismatch: " + name)
    expected = team.read_json(FIXTURES / "expected.json")
    if normalized_hash(expected) != reference["expected_normalized_sha256"]:
        raise ValueError("Expected output fingerprint does not match the reference")
    return reference, team.read_json(FIXTURES / "input.json"), expected


def check_reference(batch):
    reference, fixture, expected = load_reference()
    if team.code_hashes() != reference["collector_sha256"]:
        raise ValueError("Collector differs from Zherui's reference version; do not regenerate reference hashes")
    if team.sha_file(batch / "manifest.json") != reference["batch_manifest_sha256"]:
        raise ValueError("Assignment batch differs from Zherui's reference batch")
    manifest, shards = team.validate_batch(batch)
    transport = FixtureTransport({endpoint(fixture["publication_url"]): fixture["posts"]})
    actual = history.parse_publication_history(transport, fixture["publication_url"], team.START, team.END)
    if actual != expected or normalized_hash(actual) != reference["expected_normalized_sha256"]:
        raise ValueError("Real history parser output differs from the fixed reference output")
    return reference, manifest, shards, normalized_hash(actual)


def validate_payload(key, payload):
    """Check acquisition integrity; this does not certify primary-sample eligibility."""
    if not isinstance(payload, dict) or (payload.get("publication_url"), payload.get("start_date"), payload.get("end_date")) != (key, team.START, team.END):
        raise ValueError("Payload publication identity or study dates mismatch: " + key)
    posts = payload.get("posts")
    if not isinstance(posts, list):
        raise ValueError("Payload posts must be a list: " + key)
    seen, order = set(), []
    required = {"post_id", "publication_id", "published_at", "year_month", "full_text", "retained_word_count",
                "text_sha256", "source_sha256", "source_url", "source_type", "audience", "is_paywalled", "bylines"}
    for post in posts:
        if not isinstance(post, dict) or not required <= post.keys():
            raise ValueError("Post schema missing required fields: " + key)
        ident = post["post_id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("Missing or duplicate post ID within a publication: " + key)
        seen.add(ident)
        stamp = post["published_at"]
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None or not date.fromisoformat(team.START) <= parsed.date() <= date.fromisoformat(team.END):
            raise ValueError("Post date is outside the study window or lacks timezone: " + ident)
        if post["year_month"] != stamp[:7]:
            raise ValueError("Post month mismatch: " + ident)
        order.append((stamp, ident))
        text = post["full_text"]
        if not isinstance(text, str) or type(post["retained_word_count"]) is not int:
            raise ValueError("Incorrect text/count type: " + ident)
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        if post["text_sha256"] != expected_hash:
            raise ValueError("Text checksum mismatch: " + ident)
        if post["retained_word_count"] != len(re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE)):
            raise ValueError("Retained word count mismatch: " + ident)
        if type(post["is_paywalled"]) is not bool or post["is_paywalled"] != (post["audience"] != "everyone"):
            raise ValueError("Access metadata mismatch: " + ident)
        if post["audience"] != "everyone" and text:
            raise ValueError("Paid/unknown-access text retained: " + ident)
        if post["source_type"] != "public_posts_json_endpoint" or not post["source_url"].startswith(key + "/api/v1/posts?"):
            raise ValueError("Post source provenance mismatch: " + ident)
        if not re.fullmatch(r"[0-9a-f]{64}", post["source_sha256"]):
            raise ValueError("Missing source checksum: " + ident)
        if not isinstance(post["publication_id"], str) or not isinstance(post["bylines"], list):
            raise ValueError("Identifier/byline schema mismatch: " + ident)
        for byline in post["bylines"]:
            if not isinstance(byline, dict) or not isinstance(byline.get("creator_id"), str) or not byline["creator_id"]:
                raise ValueError("Unresolved byline encoded as a verified ID: " + ident)
    if order != sorted(order):
        raise ValueError("Publication post ordering differs from the shared parser: " + key)
    return len(posts)


def validate_export(path, batch_path, batch, rows, shard, allow_partial=False):
    m = team.read_json(path / "manifest.json")
    expected = {"batch_id": batch["batch_id"], "shard": shard,
                "batch_manifest_sha256": team.sha_file(batch_path / "manifest.json"),
                "assignment_sha256": batch["files"][f"shard-{shard}.csv"], "code_sha256": batch["code_sha256"]}
    if any(m.get(k) != v for k, v in expected.items()):
        raise ValueError("Export belongs to a different shard, batch, or collector version")
    if m.get("format") != "concatenated-decompressed-utf8-jsonl-v1":
        raise ValueError("Unsupported export format")
    names = [p["file"] for p in m["parts"]]
    if names != [f"records-{i:05d}.jsonl.gz" for i in range(len(names))]:
        raise ValueError("Export parts are missing, duplicated, or reordered")
    if m.get("status_sha256") and team.sha_file(path / "collection_status.json") != m["status_sha256"]:
        raise ValueError("Export status checksum mismatch")
    assigned = {r["publication_url"] for r in rows}
    keys, post_rows = {}, 0
    for row in team.exported_rows(path, m):
        if set(row) != set(team.DB_FIELDS) or row["stage"] != team.HISTORY_STAGE or type(row["ok"]) is not int or row["ok"] not in (0, 1):
            raise ValueError("Invalid exported checkpoint schema")
        key = row["item_key"]
        if key not in assigned or key in keys:
            raise ValueError("Out-of-assignment or repeated publication: " + key)
        observed = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("Observation timestamp lacks timezone")
        if row["ok"]:
            if row["error_type"] is not None or row["error_message"] is not None:
                raise ValueError("Successful checkpoint contains an error")
            post_rows += validate_payload(key, json.loads(row["payload"]))
        elif row["payload"] is not None or not row["error_type"]:
            raise ValueError("Failed checkpoint is missing its error or contains a successful payload")
        keys[key] = row["ok"]
    actual = team.counts(rows, keys)
    if any(m.get(k) != v for k, v in actual.items()) or m.get("partial") != bool(actual["pending"]):
        raise ValueError("Export counts/completeness flags disagree with its actual records")
    if actual["pending"] and not allow_partial:
        raise ValueError("Export is incomplete; use --allow-partial only for an interim check")
    return dict(actual, publication_post_rows=post_rows,
                status="PASS_PARTIAL" if actual["pending"] else "PASS",
                manifest_sha256=team.sha_file(path / "manifest.json"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shard", type=int, choices=(1, 2), required=True)
    p.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    p.add_argument("--report", type=Path)
    p.add_argument("--export-dir", type=Path, help="Optional offline audit of actual exported records")
    p.add_argument("--allow-partial", action="store_true", help="Label an incomplete export PASS_PARTIAL, never complete")
    args = p.parse_args()
    report_path = args.report or ROOT / f".cache/substack_compatibility/{args.batch.name}/shard-{args.shard}.json"
    report = {"status": "FAIL", "shard": args.shard, "checked_at": datetime.now(timezone.utc).isoformat(),
              "python": platform.python_version(), "platform": platform.system(), "network_requests": 0,
              "production_data_modified": False, "export_validation": {"status": "NOT_RUN"},
              "limitation": "Compatibility and integrity checks do not certify live source completeness or final analytic eligibility."}
    log = io.StringIO()
    try:
        report["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        report["git_commit"] = "unavailable"
    try:
        # Block accidental network access across both fixtures and the test suite.
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in compatibility checks")), patch("socket.create_connection", side_effect=AssertionError("Network forbidden in compatibility checks")):
            reference, batch, shards, golden = check_reference(args.batch)
            report.update(contract_version=reference["contract_version"], reference_commit=reference["reference_commit"],
                          fixture_manifest_sha256=team.sha_file(FIXTURES / "manifest.json"),
                          batch_manifest_sha256=team.sha_file(args.batch / "manifest.json"),
                          collector_sha256=team.code_hashes(),
                          parallel_runner_sha256=team.sha_file(ROOT / "scripts/substack/parallel_team_collection.py"),
                          golden_output_sha256=golden, assigned_publications=len(shards[args.shard]))
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_substack*.py")
            with redirect_stdout(log):
                result = unittest.TextTestRunner(stream=log, verbosity=1).run(suite)
            report["tests"] = {"run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
            if not result.wasSuccessful() or result.skipped or not result.testsRun:
                raise ValueError("Compatibility tests failed or were skipped; inspect the test log")
            if args.export_dir:
                report["export_validation"] = validate_export(args.export_dir, args.batch, batch, shards[args.shard], args.shard, args.allow_partial)
            report["status"] = "PASS_PARTIAL" if report["export_validation"]["status"] == "PASS_PARTIAL" else "PASS"
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    report["test_log"] = log.getvalue()
    team.write_json(report_path, report)
    print(f"{report['status']}: {report_path}")
    if report["status"] == "FAIL":
        print(report.get("error", "Compatibility check failed"))
        if log.getvalue():
            print(log.getvalue())
        return 1
    print(f"Tests: {report['tests']['run']} passed; golden output: {report['golden_output_sha256']}")
    print("Export validation: " + report["export_validation"]["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
