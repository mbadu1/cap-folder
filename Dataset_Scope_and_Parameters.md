# Dataset Scope and Parameters
## Capstone: Is AI Killing the Internet? Measuring the Displacement of Human Culture Online

**Platforms:** Substack + Medium  
**Status:** Draft v0.2 — platform + statistical defense filled in; toy scrapes started  
**Last updated:** 2026-09-18

---

## 0. Checklist against advisor brief

| Requirement | Covered in v0.1? | Status in v0.2 |
|---|---|---|
| Platform-defensible (how people use it, what’s popular) | Thin | **§1** filled from platform facts |
| Statistically defensible (N overall / month / year / subgroup) | Soft “~40%” only | **§3** with explicit monthly cell targets |
| Strategy: most popular + subgroups of interest | Named, under-motivated | **§4** locked as elite + topic-stratified random |
| Month-by-month N for time analysis | Missing | **§3.2–3.3** |
| Core unit clear (post vs author) + data-volume implications | Named | **§2** sharpened |
| Then start scraping (toy) | No CSV yet | **`data/toy/`** + `scripts/shared/toy_scrape.py` |

---

## 1. Platform defense (why these designs fit how the platforms work)

### 1.1 Substack

**What it is:** Email-first newsletter platform; the product unit is a **publication** (almost always one creator: ~95% of active newsletters are solo). Readers subscribe; writers monetize via paid tiers (Substack takes ~10%).

**How people use it:**
- Typical cadence among ~75k public newsletters: **monthly (~24%) or weekly (~21%)**; daily is rare (~3%).
- Large pubs post **≥ weekly**; many top pubs post multiple times per week.
- Alongside posts: **Notes** (short social posts) — we **exclude Notes**; cultural longform lives in newsletter posts.
- Heavy **free/paid split**: ~36% offer paid plans; paywalled posts are common among earners. Public RSS/archive often still lists paywalled items with limited body — store paywall flag, don’t bypass.

**What’s popular:**
- Attention is **top-heavy**: politics (esp. U.S.) dominates mega-pubs (≥500k subs); business, tech, finance, food also appear.
- Discovery infrastructure: **category leaderboards** — Top Bestsellers (ARR) and Rising (recent paid growth). Official, category-based, updated daily / every few hours. This is the platform’s own definition of “most popular.”
- Bestseller ≠ biggest audience always (revenue vs free reach diverge) — for *cultural visibility*, leaderboard + subscriber-visible elite still tracks the “center.”

**Design implication:** Sample **publications** (not posts). Elite stratum = leaderboard bestsellers (and optionally Rising for dynamism). Random stratum = sitemap publications with **topic floors** so politics doesn’t swallow the panel.

### 1.2 Medium

**What it is:** Web-first article platform; unit is an **individual writer** (`@handle`), who may also publish into multi-author **publications**. Monetization via Member reading time (Partner Program), Boost, and distribution tiers — not primarily email subs.

**How people use it:**
- Posting norms shifted after ~early 2023: Medium pushed **quality over quantity**; top earners publish *fewer* stories. A few strong pieces/month often beats high weekly volume.
- Discovery runs through tags, publications, Boost/curation — not a clean public ARR leaderboard.
- Paywall: member-only stories; public RSS often returns recent posts with partial content. Same rule: public text only.
- Publications raise reach vs self-publish, but authorship stays with the user — so **user** is the right panel unit (comparable to Substack’s solo publication).

**What’s popular:**
- No Substack-style public category ARR board. Proxies: **Partner Program / verified high-reach writers**, contributors to major publications, follower counts, clap/read signals when visible.
- Topics among top earners fluctuate month to month (education, startups, poetry, politics, productivity, etc.) — reinforces need for **topic subgroups**, not tech-only scraping.

**Design implication:** Sample **users**. Elite = verified/Partner-visible + major-pub contributors (frozen list). Random = user sitemaps + tag→topic floors. Do **not** sample from post sitemaps for the panel (oversamples prolific posters — especially wrong after Medium’s quality shift).

### 1.3 Why both platforms for “displacement of human culture”

| | Substack | Medium |
|---|---|---|
| Distribution | Email / subscription relationship | Algorithmic + publication networks |
| Incentive | Paid subscribers, cadence commitments | Member read-time, Boost, claps |
| AI pressure point | Faster drafting of newsletter issues | Higher volume / SEO-ish posts (even if platform now penalizes spam) |
| Popularity signal | Leaderboards (ARR / rising) | Fragmented; must construct elite frame |

If AI “kills” human texture, we should see it in **subscription longform** and **open web longform**, not only one incentive system.

---

## 2. Core unit (locked)

| Role | Unit | Rationale |
|---|---|---|
| **Sampling unit** | **Creator** (Substack publication / Medium user) | Frames enumerate creators; selection probability is defined here |
| **Measurement unit** | **Post / article** | Style, creativity, homogeneity features need text |
| **Analysis unit** | **Creator × month** | Time series of *creators’* output — answers “are human creators changing?” |

### Why not sample posts as the primary unit?

Sampling posts (or post sitemaps) over-represents high-volume writers and drifts composition month to month. A rise in “AI-like” posts could just mean prolific AI users entered the mix — not that existing human culture was displaced. A **creator panel** lets us:
- track within-creator change (difference-in-differences / event study around ChatGPT),
- separate composition effects (who posts) from behavioral effects (how they write),
- still analyze posts as the feature-bearing objects inside each creator-month.

### Data-volume implication of this unit choice

We need enough **creators posting in each calendar month**, not just a huge pile of posts. Caps (**K = 2 posts per creator-month**) keep computation bounded and reduce verbosity bias; metadata still records all posts for activity/composition analyses.

---

## 3. Statistical defense — especially month-by-month N

### 3.1 What “enough N” means here

Primary time analyses (illustrative):
1. Monthly mean of a linguistic/cultural feature among active creators (overall, then by stratum/topic).
2. Within-creator change pre vs post ChatGPT (creator FE / event study).
3. Platform contrast (Substack vs Medium) in those trajectories.

For (1), the effective N in month *t* is **number of panel creators with ≥1 eligible public post in month *t*** (creator-months), not raw post count.

Rough planning assumptions (to be replaced by toy/pilot empirics):

| Assumption | Value | Source |
|---|---|---|
| Panel size per platform | **N = 2,500** creators | Design target |
| Share posting in a given month | **p ≈ 0.35–0.45** elite higher, random lower | Substack cadence (weekly/monthly mix); Medium quality-over-quantity |
| Active creators / month (overall) | **≈ 875–1,125** | N × p |
| Posts sampled / active creator-month | **K = 2** | Cap |
| Full-text docs / month / platform | **≈ 1,750–2,250** | |

### 3.2 Monthly cell targets (defensibility thresholds)

| Analysis cell | Target active creators / month | With N=2500, is it plausible? |
|---|---|---|
| Platform overall | **≥ 400** (prefer ≥800) | Yes if p ≳ 0.35 |
| Elite only (~500) | **≥ 150** | Yes if elite p ≳ 0.4–0.5 (likely) |
| Random only (~2000) | **≥ 400** | Yes if p ≳ 0.25 |
| Topic family (8-way) | **≥ 40–50** (exploratory); **≥ 80** for main claims | Needs topic floors; politics will be fat, creative writing thinner |
| Topic × elite | Often **thin** | Treat as secondary / pool quarters |
| Year aggregates | Trivial once months work | 12× monthly |

**Rule of thumb we will defend in the paper:**  
Main monthly claims require **≥ ~100 creator-months per cell**; topic-monthly claims require **≥ ~40** and will be marked exploratory if thinner. If pilot shows early-2020 months below threshold, we either (a) shorten the window to when the panel is dense, or (b) report quarterly series for thin cells.

### 3.3 Why N ≈ 2,500 per platform (not 200, not 20,000)

- **200 creators:** even at p=0.5 → ~100/month overall; topic cells collapse; fragile.
- **2,500:** aims for ~1,000/month overall and ~40–120/topic/month after floors — enough for time series + subgroup plots.
- **20,000:** statistically nicer but scrape/ToS/compute heavy for a capstone; diminishing returns once monthly cells are stable.

**Year-level N** will be large; the binding constraint is **month × subgroup**, which is why we size the panel around monthly cells and use topic floors.

### 3.4 Power intuition (transparent, not fake precision)

For a within-creator pre/post contrast on a standardized feature, even **n ≈ 400 creators with both pre and post observations** detects moderate effects; we expect far more if we require activity on both sides of Nov 2022. Exact power depends on feature variance — **pilot will estimate ICC / within-creator SD** and we adjust N or K if needed. We do **not** pretend a single magic N without that pilot.

### 3.5 Expected corpus size (order of magnitude)

| Object | Per platform (order) |
|---|---|
| Creators | 2,500 |
| Creator-months with activity (81 × ~1000) | ~80k |
| Full-text posts at K=2 | ~160k |
| Metadata posts (uncapped) | higher; keep all |

---

## 4. Sampling strategy (most popular + subgroups)

**Locked strategy:** **Elite (most popular) + stratified random with topic floors (subgroups of interest).**

This matches common computational social science practice and the advisor’s “most popular + subgroups” note.

### 4.1 Substack

| Stratum | How | Target |
|---|---|---|
| Elite | Category **Top Bestsellers** leaderboards (ARR); optionally Rising | ~500 after dedupe |
| Random | Sitemap publications → map to 8 topic families → floor then proportional fill | ~2,000 |

### 4.2 Medium

| Stratum | How | Target |
|---|---|---|
| Elite | Frozen list: Partner/verified high-reach + contributors to named major publications + follower tie-break | ~500 |
| Random | User sitemaps → tags → 8 families → floor + proportional | ~2,000 |

### 4.3 Shared topic families (subgroups of interest)

1. Politics and current affairs  
2. Society, history and ideas  
3. Business, finance and economics  
4. Technology, science, education and environment  
5. Literature and creative writing  
6. Arts, culture, media and design  
7. Health, family and personal life  
8. Lifestyle, food, travel, home and leisure  

**Floor:** **50** creators per family in the random stratum (400 minimum), remainder proportional. Elite may be politics-heavy by nature — that’s scientifically interesting (cultural center), not a bug; random stratum rebalances breadth.

### 4.4 Within-creator post sampling

For each creator-month with eligible public posts: sample **K = 2** uniformly at random; weight `w = n_posts / K` if n > K.

---

## 5. Shared scope parameters

| Parameter | Value |
|---|---|
| Start | 2020-01-01 |
| End | 2026-09-17 |
| Cutpoint | 2022-11-30 (ChatGPT) |
| Language | English full-text analytic sample |
| Include | Longform posts; public text |
| Exclude | Notes/comments; paywalled full body (keep metadata); pure media posts |
| Creators / platform | 2,500 |
| K posts / creator-month | 2 |

**Target population:** Sitemap-indexed longform creators on Substack/Medium with ≥1 public post in-window and English public text for features.

---

## 6. Schema (CSV outputs)

- `creators.csv` — panel membership  
- `posts_meta.csv` — every collected post’s metadata  
- `posts_text.csv` — full text when public  

Toy versions live in `data/toy/`.

---

## 7. Collection constraints

- Public endpoints only; respect robots.txt; throttle; no paywall bypass  
- Medium: RSS works in toy tests; some HTML/JSON paths hit bot protection — prefer RSS + allowed APIs  
- Substack: `/api/v1/archive` + `/feed` work on publication hosts  
- Some “Substack-famous” writers have **migrated off** Substack (e.g. to Ghost) — elite lists must verify still-on-platform  

---

## 8. Pilot → full build

1. **Toy scrape (now):** few elite creators/platform → prove CSV pipeline  
2. **Pilot:** 50 elite + 100 random / platform → estimate p, paywall rate, English rate, month density 2020 vs 2025  
3. **Recalibrate** N, floors, window if early months too thin  
4. **Full panel scrape**

---

## 9. Open decisions (narrowed)

| # | Decision | Recommendation |
|---|---|---|
| A | K | **2** |
| B | Topic floor | **50** in random stratum |
| C | Medium elite | Partner/verified + major pubs |
| D | Language | English v1 |
| E | Pre/post overlap rule | Prefer creators with activity both sides of cut for DiD subsample; keep others for composition |
| F | Equal N platforms | Yes |

---

## 10. Methods blurb

> We build parallel creator panels on Substack and Medium (N ≈ 2,500 each), January 2020–September 2026. Sampling combines an elite “most popular” stratum (Substack category bestsellers; Medium high-reach / major-publication writers) with a sitemap-based random stratum floored across eight topic families. The sampling unit is the creator; posts carry text features; analysis is at the creator–month level so monthly time series remain statistically meaningful (target hundreds of active creators per month overall, tens per topic). We store metadata for all public posts and full text for up to two public posts per creator-month. Paywalled bodies are excluded from text features.
