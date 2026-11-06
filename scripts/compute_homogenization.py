"""Homogenization measures on main-text ZIPs + scored topic codes.

Four families (monthly, and AI vs human cohorts where n allows):

1. Lexical diversity within posts  — mean entropy / TTR / hapax (from prior
   post-level features, or recomputed on the month sample).
2. Lexical overlap across posts   — mean pairwise Jaccard on content-word
   type sets; top-type mass concentration in the month sample.
3. Semantic diversity across posts — TF–IDF → TruncatedSVD (LSA), then mean
   pairwise cosine distance and mean distance to centroid.
4. Topic diversity                — Shannon entropy / effective # topics /
   Herfindahl over topic_code on the full scored month (not just the sample).

Homogenization shows up as: higher lexical overlap, lower semantic
dispersion, lower topic entropy — especially inside the AI cohort.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# Compact stoplist so Jaccard focuses on content types.
STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "while",
    "of", "to", "in", "on", "for", "with", "as", "by", "at", "from", "into",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did",
    "have", "has", "had", "will", "would", "could", "should", "may", "might",
    "can", "shall", "not", "no", "nor", "so", "than", "too", "very", "just",
    "about", "above", "after", "again", "against", "all", "also", "any", "because",
    "before", "below", "between", "both", "each", "few", "further", "here", "how",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they", "them",
    "their", "this", "that", "these", "those", "what", "which", "who", "whom",
    "why", "where", "there", "out", "up", "down", "over", "under", "again",
    "once", "only", "own", "same", "such", "more", "most", "other", "some",
    "now", "its", "via", "per", "etc",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def content_types(tokens: list[str]) -> set[str]:
    return {t for t in tokens if t not in STOP and len(t) > 2}


def lexical_within(tokens: list[str]) -> tuple[float, float, float]:
    n = len(tokens)
    if n == 0:
        return 0.0, 0.0, 0.0
    counts = Counter(tokens)
    v = len(counts)
    ent = 0.0
    hapax = 0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(p)
        if c == 1:
            hapax += 1
    return ent, (v / n), (hapax / v if v else 0.0)


def shannon_entropy(counts: Counter) -> tuple[float, float, float, float]:
    """Return H (bits), H_norm, effective_n (2**H), HHI."""
    total = sum(counts.values())
    if total <= 0:
        return 0.0, 0.0, 0.0, 1.0
    probs = [c / total for c in counts.values() if c > 0]
    h = -sum(p * math.log2(p) for p in probs)
    k = len(probs)
    h_norm = h / math.log2(k) if k > 1 else 0.0
    hhi = sum(p * p for p in probs)
    return h, h_norm, (2.0 ** h), hhi


def mean_pairwise_jaccard(sets: list[set[str]], max_pairs: int = 4000) -> float:
    m = len(sets)
    if m < 2:
        return float("nan")
    total = m * (m - 1) // 2
    idxs = []
    if total <= max_pairs:
        idxs = [(i, j) for i in range(m) for j in range(i + 1, m)]
    else:
        rng = random.Random(0)
        seen = set()
        while len(idxs) < max_pairs:
            i = rng.randrange(m)
            j = rng.randrange(m)
            if i == j:
                continue
            a, b = (i, j) if i < j else (j, i)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            idxs.append((a, b))
    s = 0.0
    n = 0
    for i, j in idxs:
        a, b = sets[i], sets[j]
        if not a and not b:
            continue
        inter = len(a & b)
        union = len(a | b)
        if union == 0:
            continue
        s += inter / union
        n += 1
    return s / n if n else float("nan")


def top_type_mass(token_lists: list[list[str]], top_n: int = 100) -> float:
    """Share of token mass carried by the month's top-N types (higher => more overlap-y)."""
    bag = Counter()
    for toks in token_lists:
        bag.update(toks)
    total = sum(bag.values())
    if total == 0:
        return float("nan")
    return sum(c for _, c in bag.most_common(top_n)) / total


def lsa_dispersion(texts: list[str], n_components: int = 40) -> tuple[float, float]:
    """Return (mean pairwise cosine distance, mean distance to centroid) in LSA space."""
    m = len(texts)
    if m < 5:
        return float("nan"), float("nan")
    n_comp = min(n_components, m - 1, 40)
    vec = TfidfVectorizer(
        max_features=4000,
        min_df=2 if m >= 40 else 1,
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    try:
        x = vec.fit_transform(texts)
    except ValueError:
        return float("nan"), float("nan")
    if x.shape[1] < 3:
        return float("nan"), float("nan")
    n_comp = min(n_comp, x.shape[1] - 1)
    svd = TruncatedSVD(n_components=n_comp, random_state=0)
    emb = svd.fit_transform(x)
    emb = normalize(emb)
    # pairwise
    sims = emb @ emb.T
    iu = np.triu_indices(m, k=1)
    pair_dist = float(np.mean(1.0 - sims[iu]))
    centroid = normalize(emb.mean(axis=0, keepdims=True))[0]
    to_cent = 1.0 - (emb @ centroid)
    return pair_dist, float(np.mean(to_cent))


def reservoir_push(bucket: list, item, cap: int, rng: random.Random, seen: list) -> None:
    """Classic reservoir; seen[0] tracks how many candidates seen."""
    seen[0] += 1
    if len(bucket) < cap:
        bucket.append(item)
        return
    j = rng.randrange(seen[0])
    if j < cap:
        bucket[j] = item


def find_member(names: list[str], suffix: str) -> str:
    hits = [n for n in names if n.endswith(suffix)]
    if len(hits) != 1:
        raise ValueError(f"expected one {suffix}, found {hits}")
    return hits[0]


def process_archive(
    archive_path: Path,
    out_month: Path,
    sample_n: int = 300,
    seed: int = 0,
) -> None:
    csv.field_size_limit(sys.maxsize)
    rng = random.Random(seed)

    with zipfile.ZipFile(archive_path) as zf:
        manifest_name = find_member(zf.namelist(), "/manifest.json")
        manifest = json.loads(zf.read(manifest_name))
        prefix = manifest_name.rsplit("/", 1)[0] + "/"
        text_name = prefix + "main_text.jsonl"
        scored_name = prefix + manifest["scored_csv"]

        scores: dict[tuple[str, str], dict] = {}
        topic_by_month: dict[str, Counter] = defaultdict(Counter)
        topic_by_month_ai: dict[str, Counter] = defaultdict(Counter)
        topic_by_month_human: dict[str, Counter] = defaultdict(Counter)
        n_by_month = Counter()
        n_ai_by_month = Counter()

        with zf.open(scored_name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
            for row in reader:
                key = (row["platform"], str(row["post_id"]))
                pub = (row.get("published_at") or row.get("published_date") or "")[:10]
                ym = pub[:7]
                try:
                    p_ai = float(row["P_AI"])
                except (TypeError, ValueError):
                    p_ai = float("nan")
                ai = int(p_ai >= 0.5) if p_ai == p_ai else None
                topic = str(row.get("topic_code", ""))
                scores[key] = {"published_date": pub, "P_AI": p_ai, "ai": ai, "topic": topic}
                if ym and topic != "":
                    topic_by_month[ym][topic] += 1
                    n_by_month[ym] += 1
                    if ai == 1:
                        topic_by_month_ai[ym][topic] += 1
                        n_ai_by_month[ym] += 1
                    elif ai == 0:
                        topic_by_month_human[ym][topic] += 1

        # reservoirs: ym -> list of dicts; ym|ai / ym|human separately
        samples: dict[str, list] = defaultdict(list)
        samples_ai: dict[str, list] = defaultdict(list)
        samples_human: dict[str, list] = defaultdict(list)
        seen = defaultdict(lambda: [0])
        seen_ai = defaultdict(lambda: [0])
        seen_human = defaultdict(lambda: [0])

        wrote = 0
        with zf.open(text_name) as data:
            for raw in data:
                rec = json.loads(raw)
                key = (rec["platform"], str(rec["post_id"]))
                sc = scores.get(key)
                if sc is None:
                    raise ValueError(f"score miss {key}")
                pub = (rec.get("published_date") or sc["published_date"] or "")[:10]
                ym = pub[:7]
                if not ym:
                    continue
                tokens = tokenize(rec.get("text") or "")
                item = {
                    "text": rec.get("text") or "",
                    "tokens": tokens,
                    "types": content_types(tokens),
                    "ai": sc["ai"],
                }
                reservoir_push(samples[ym], item, sample_n, rng, seen[ym])
                if sc["ai"] == 1:
                    reservoir_push(samples_ai[ym], item, sample_n, rng, seen_ai[ym])
                elif sc["ai"] == 0:
                    reservoir_push(samples_human[ym], item, sample_n, rng, seen_human[ym])
                wrote += 1
                if wrote % 50000 == 0:
                    print(f"  ... streamed {wrote:,}", flush=True)

        if wrote != manifest["rows"]:
            raise ValueError(f"row count {wrote} != manifest {manifest['rows']}")

    def cohort_metrics(items: list) -> dict:
        if len(items) < 2:
            return {
                "n_sample": len(items),
                "mean_entropy": float("nan"),
                "mean_ttr": float("nan"),
                "mean_hapax": float("nan"),
                "mean_jaccard": float("nan"),
                "top100_type_mass": float("nan"),
                "lsa_pairwise_distance": float("nan"),
                "lsa_centroid_distance": float("nan"),
            }
        ents, ttrs, hap = [], [], []
        for it in items:
            e, t, h = lexical_within(it["tokens"])
            ents.append(e)
            ttrs.append(t)
            hap.append(h)
        jacc = mean_pairwise_jaccard([it["types"] for it in items])
        mass = top_type_mass([it["tokens"] for it in items])
        pair_d, cent_d = lsa_dispersion([it["text"] for it in items])
        return {
            "n_sample": len(items),
            "mean_entropy": float(np.mean(ents)),
            "mean_ttr": float(np.mean(ttrs)),
            "mean_hapax": float(np.mean(hap)),
            "mean_jaccard": jacc,
            "top100_type_mass": mass,
            "lsa_pairwise_distance": pair_d,
            "lsa_centroid_distance": cent_d,
        }

    months = sorted(set(n_by_month) | set(samples))
    out_month.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "year_month", "n_posts", "n_ai", "share_ai",
        # topic diversity (full month)
        "topic_entropy", "topic_entropy_norm", "topic_effective_n", "topic_hhi",
        "topic_entropy_ai", "topic_effective_n_ai", "topic_hhi_ai",
        "topic_entropy_human", "topic_effective_n_human", "topic_hhi_human",
        # all-sample lexical within / overlap / semantic
        "n_sample", "mean_entropy", "mean_ttr", "mean_hapax",
        "mean_jaccard", "top100_type_mass",
        "lsa_pairwise_distance", "lsa_centroid_distance",
        # AI cohort
        "n_sample_ai", "mean_entropy_ai", "mean_ttr_ai", "mean_hapax_ai",
        "mean_jaccard_ai", "top100_type_mass_ai",
        "lsa_pairwise_distance_ai", "lsa_centroid_distance_ai",
        # human cohort
        "n_sample_human", "mean_entropy_human", "mean_ttr_human", "mean_hapax_human",
        "mean_jaccard_human", "top100_type_mass_human",
        "lsa_pairwise_distance_human", "lsa_centroid_distance_human",
    ]

    with out_month.open("w", encoding="utf-8", newline="") as mf:
        w = csv.DictWriter(mf, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for ym in months:
            n = n_by_month[ym]
            n_ai = n_ai_by_month[ym]
            th, thn, teff, thhi = shannon_entropy(topic_by_month[ym])
            tha, _, teffa, thhia = shannon_entropy(topic_by_month_ai[ym])
            thh, _, teffh, thhih = shannon_entropy(topic_by_month_human[ym])
            all_m = cohort_metrics(samples.get(ym, []))
            ai_m = cohort_metrics(samples_ai.get(ym, []))
            hu_m = cohort_metrics(samples_human.get(ym, []))
            row = {
                "year_month": ym,
                "n_posts": n,
                "n_ai": n_ai,
                "share_ai": (n_ai / n) if n else "",
                "topic_entropy": th,
                "topic_entropy_norm": thn,
                "topic_effective_n": teff,
                "topic_hhi": thhi,
                "topic_entropy_ai": tha if topic_by_month_ai[ym] else "",
                "topic_effective_n_ai": teffa if topic_by_month_ai[ym] else "",
                "topic_hhi_ai": thhia if topic_by_month_ai[ym] else "",
                "topic_entropy_human": thh if topic_by_month_human[ym] else "",
                "topic_effective_n_human": teffh if topic_by_month_human[ym] else "",
                "topic_hhi_human": thhih if topic_by_month_human[ym] else "",
                "n_sample": all_m["n_sample"],
                "mean_entropy": all_m["mean_entropy"],
                "mean_ttr": all_m["mean_ttr"],
                "mean_hapax": all_m["mean_hapax"],
                "mean_jaccard": all_m["mean_jaccard"],
                "top100_type_mass": all_m["top100_type_mass"],
                "lsa_pairwise_distance": all_m["lsa_pairwise_distance"],
                "lsa_centroid_distance": all_m["lsa_centroid_distance"],
                "n_sample_ai": ai_m["n_sample"],
                "mean_entropy_ai": ai_m["mean_entropy"],
                "mean_ttr_ai": ai_m["mean_ttr"],
                "mean_hapax_ai": ai_m["mean_hapax"],
                "mean_jaccard_ai": ai_m["mean_jaccard"],
                "top100_type_mass_ai": ai_m["top100_type_mass"],
                "lsa_pairwise_distance_ai": ai_m["lsa_pairwise_distance"],
                "lsa_centroid_distance_ai": ai_m["lsa_centroid_distance"],
                "n_sample_human": hu_m["n_sample"],
                "mean_entropy_human": hu_m["mean_entropy"],
                "mean_ttr_human": hu_m["mean_ttr"],
                "mean_hapax_human": hu_m["mean_hapax"],
                "mean_jaccard_human": hu_m["mean_jaccard"],
                "top100_type_mass_human": hu_m["top100_type_mass"],
                "lsa_pairwise_distance_human": hu_m["lsa_pairwise_distance"],
                "lsa_centroid_distance_human": hu_m["lsa_centroid_distance"],
            }
            w.writerow(row)
            print(f"  month {ym} done (n={n})", flush=True)

    print(f"wrote {out_month}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("--out-dir", type=Path, default=Path("data/homogenization"))
    p.add_argument("--sample-n", type=int, default=300)
    args = p.parse_args()
    platform = "medium" if "medium" in args.archive.name.lower() else "substack"
    out = args.out_dir / f"{platform}_monthly_homogenization.csv"
    print(f"processing {args.archive} -> {out}", flush=True)
    process_archive(args.archive, out, sample_n=args.sample_n)


if __name__ == "__main__":
    main()
