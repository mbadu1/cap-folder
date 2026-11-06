"""Stream main-text ZIPs and compute lexical (+ light semantic) diversity features.

Lexical (per post):
  - token_count, type_count
  - ttr (types/tokens)
  - unigram Shannon entropy (bits)
  - entropy_norm = entropy / log2(types) when types>1
  - hapax_rate (share of types that appear once)
  - mean_token_len

Corpus-month semantic proxy (not embeddings):
  - For each year-month, take up to N posts, build TF vectors over a shared
    vocabulary (top K tokens that month), compute mean pairwise cosine distance.
  - Higher distance => more semantic/lexical spread that month.

Joins to the scored CSV already in the repo (or the one inside the ZIP).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def lexical_features(tokens: list[str]) -> dict:
    n = len(tokens)
    if n == 0:
        return {
            "token_count": 0,
            "type_count": 0,
            "ttr": 0.0,
            "entropy": 0.0,
            "entropy_norm": 0.0,
            "hapax_rate": 0.0,
            "mean_token_len": 0.0,
        }
    counts = Counter(tokens)
    v = len(counts)
    ent = 0.0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(p)
    hapax = sum(1 for c in counts.values() if c == 1)
    return {
        "token_count": n,
        "type_count": v,
        "ttr": v / n,
        "entropy": ent,
        "entropy_norm": (ent / math.log2(v)) if v > 1 else 0.0,
        "hapax_rate": hapax / v,
        "mean_token_len": sum(len(t) for t in tokens) / n,
    }


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def mean_pairwise_distance(vecs: list[dict[str, float]], max_pairs: int = 5000) -> float:
    m = len(vecs)
    if m < 2:
        return float("nan")
    # subsample pairs if needed
    total_pairs = m * (m - 1) // 2
    if total_pairs <= max_pairs:
        s = 0.0
        n = 0
        for i in range(m):
            for j in range(i + 1, m):
                s += 1.0 - cosine(vecs[i], vecs[j])
                n += 1
        return s / n
    # random-ish deterministic stride sample
    s = 0.0
    n = 0
    step = max(1, int(total_pairs / max_pairs))
    seen = 0
    for i in range(m):
        for j in range(i + 1, m):
            if seen % step == 0:
                s += 1.0 - cosine(vecs[i], vecs[j])
                n += 1
                if n >= max_pairs:
                    return s / n
            seen += 1
    return s / n if n else float("nan")


def find_member(names: list[str], suffix: str) -> str:
    hits = [n for n in names if n.endswith(suffix)]
    if len(hits) != 1:
        raise ValueError(f"expected one {suffix}, found {hits}")
    return hits[0]


def process_archive(
    archive_path: Path,
    scored_csv: Path | None,
    out_post: Path,
    out_month: Path,
    semantic_n: int = 400,
    semantic_k: int = 300,
) -> None:
    csv.field_size_limit(sys.maxsize)
    with zipfile.ZipFile(archive_path) as zf:
        manifest_name = find_member(zf.namelist(), "/manifest.json")
        manifest = json.loads(zf.read(manifest_name))
        prefix = manifest_name.rsplit("/", 1)[0] + "/"
        text_name = prefix + "main_text.jsonl"
        scored_name = prefix + manifest["scored_csv"]

        # load scores: prefer external scored file if provided (same keys)
        scores: dict[tuple[str, str], dict] = {}
        score_path = scored_csv
        if score_path is None:
            # stream from zip into memory dict of needed columns only
            with zf.open(scored_name) as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
                for row in reader:
                    key = (row["platform"], row["post_id"])
                    scores[key] = {
                        "published_date": row.get("published_at") or row.get("published_date") or "",
                        "P_AI": row.get("P_AI", ""),
                        "P_HUMAN": row.get("P_HUMAN", ""),
                        "PREDICTION": row.get("PREDICTION", ""),
                        "topic_code": row.get("topic_code", ""),
                        "word_count_source": row.get("word_count_source", ""),
                    }
        else:
            with score_path.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    key = (row["platform"], str(row["post_id"]))
                    pub = row.get("published_at") or row.get("published_date") or ""
                    scores[key] = {
                        "published_date": pub[:10],
                        "P_AI": row.get("P_AI", ""),
                        "P_HUMAN": row.get("P_HUMAN", ""),
                        "PREDICTION": row.get("PREDICTION", ""),
                        "topic_code": row.get("topic_code", ""),
                        "word_count_source": row.get("word_count_source", ""),
                    }

        # month buckets for semantic sample: list of token counters
        month_token_docs: dict[str, list[Counter]] = defaultdict(list)
        month_stats = defaultdict(lambda: {
            "n": 0,
            "sum_entropy": 0.0,
            "sum_ttr": 0.0,
            "sum_entropy_norm": 0.0,
            "sum_hapax": 0.0,
            "sum_tokens": 0.0,
            "sum_types": 0.0,
            "sum_p_ai": 0.0,
            "n_ai": 0,
        })

        out_post.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "platform", "post_id", "published_date", "year_month",
            "P_AI", "ai_flag", "topic_code", "word_count_source",
            "token_count", "type_count", "ttr", "entropy", "entropy_norm",
            "hapax_rate", "mean_token_len",
        ]
        wrote = 0
        with out_post.open("w", encoding="utf-8", newline="") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            with zf.open(text_name) as data:
                for raw in data:
                    rec = json.loads(raw)
                    key = (rec["platform"], str(rec["post_id"]))
                    sc = scores.get(key)
                    if sc is None:
                        raise ValueError(f"score miss for {key}")
                    tokens = tokenize(rec.get("text") or "")
                    feat = lexical_features(tokens)
                    pub = (rec.get("published_date") or sc["published_date"] or "")[:10]
                    ym = pub[:7] if len(pub) >= 7 else ""
                    try:
                        p_ai = float(sc["P_AI"])
                    except (TypeError, ValueError):
                        p_ai = float("nan")
                    ai_flag = int(p_ai >= 0.5) if p_ai == p_ai else ""
                    row = {
                        "platform": key[0],
                        "post_id": key[1],
                        "published_date": pub,
                        "year_month": ym,
                        "P_AI": sc["P_AI"],
                        "ai_flag": ai_flag,
                        "topic_code": sc["topic_code"],
                        "word_count_source": sc["word_count_source"],
                        **feat,
                    }
                    writer.writerow(row)
                    wrote += 1

                    if ym:
                        st = month_stats[ym]
                        st["n"] += 1
                        st["sum_entropy"] += feat["entropy"]
                        st["sum_ttr"] += feat["ttr"]
                        st["sum_entropy_norm"] += feat["entropy_norm"]
                        st["sum_hapax"] += feat["hapax_rate"]
                        st["sum_tokens"] += feat["token_count"]
                        st["sum_types"] += feat["type_count"]
                        if p_ai == p_ai:
                            st["sum_p_ai"] += p_ai
                        if ai_flag == 1:
                            st["n_ai"] += 1
                        # keep a capped sample of docs for semantic proxy
                        bucket = month_token_docs[ym]
                        if len(bucket) < semantic_n and tokens:
                            bucket.append(Counter(tokens))

                    if wrote % 25000 == 0:
                        print(f"  ... {wrote:,} posts", flush=True)

        if wrote != manifest["rows"]:
            raise ValueError(f"row count {wrote} != manifest {manifest['rows']}")

        # monthly semantic proxy + means
        with out_month.open("w", encoding="utf-8", newline="") as mf:
            mfields = [
                "year_month", "n", "mean_p_ai", "share_ai",
                "mean_entropy", "mean_entropy_norm", "mean_ttr", "mean_hapax_rate",
                "mean_token_count", "mean_type_count",
                "semantic_mean_pairwise_distance", "semantic_n_docs",
            ]
            mw = csv.DictWriter(mf, fieldnames=mfields, lineterminator="\n")
            mw.writeheader()
            for ym in sorted(month_stats):
                st = month_stats[ym]
                n = st["n"]
                docs = month_token_docs.get(ym, [])
                # build shared top-K vocab from sampled docs
                vocab_counts = Counter()
                for c in docs:
                    vocab_counts.update(c)
                vocab = [w for w, _ in vocab_counts.most_common(semantic_k)]
                vecs = []
                for c in docs:
                    total = sum(c[w] for w in vocab) or 1
                    vecs.append({w: c[w] / total for w in vocab if c[w]})
                dist = mean_pairwise_distance(vecs) if len(vecs) >= 2 else float("nan")
                mw.writerow({
                    "year_month": ym,
                    "n": n,
                    "mean_p_ai": st["sum_p_ai"] / n if n else "",
                    "share_ai": st["n_ai"] / n if n else "",
                    "mean_entropy": st["sum_entropy"] / n,
                    "mean_entropy_norm": st["sum_entropy_norm"] / n,
                    "mean_ttr": st["sum_ttr"] / n,
                    "mean_hapax_rate": st["sum_hapax"] / n,
                    "mean_token_count": st["sum_tokens"] / n,
                    "mean_type_count": st["sum_types"] / n,
                    "semantic_mean_pairwise_distance": dist,
                    "semantic_n_docs": len(docs),
                })

    print(f"wrote {wrote:,} post rows -> {out_post}")
    print(f"wrote monthly summary -> {out_month}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path, help="medium_main_text_*.zip or substack_main_text_*.zip")
    p.add_argument("--scored-csv", type=Path, default=None,
                   help="Optional external scored CSV (defaults to CSV inside the ZIP)")
    p.add_argument("--out-dir", type=Path, default=Path("data/diversity"))
    p.add_argument("--semantic-n", type=int, default=400)
    p.add_argument("--semantic-k", type=int, default=300)
    args = p.parse_args()

    stem = args.archive.stem.replace("_2026-11-05", "").replace("_main_text", "")
    # stem like medium_main_text or medium
    platform = "medium" if "medium" in args.archive.name.lower() else "substack"
    out_dir = args.out_dir
    out_post = out_dir / f"{platform}_post_diversity.csv"
    out_month = out_dir / f"{platform}_monthly_diversity.csv"
    print(f"processing {args.archive} ...", flush=True)
    process_archive(
        args.archive,
        args.scored_csv,
        out_post,
        out_month,
        semantic_n=args.semantic_n,
        semantic_k=args.semantic_k,
    )


if __name__ == "__main__":
    main()
