# Capstone Monthly Progress Report — October 2026

**Project:** Is AI Killing the Internet? Measuring the Displacement of Human Culture Online  
**Team:** Michael Kofi Badu, Ziyang Qin, Zherui Zhang  
**Advisor:** Dr. Richard So  
**Mentor:** Dr. Astrid Giugni  
**Also working with:** Kevin Guo and Bhuwan Dhingra (detection model)  
**Period:** late September through October 2026


## 1. Project and team overview

### What we’re doing, short version

People keep saying AI is taking over writing online. We’re trying to measure that on Substack and Medium, not argue about it in the abstract. The concrete question is: after ChatGPT showed up in late 2022, did longform posts on these two platforms start looking more AI-like, and does that look different by platform or topic?

This month we finally got past “we’re still collecting” and into numbers we can show. We have a frozen Substack selected sample with a design check notebook, and two annual ~50k-per-year files with detector scores for Medium and Substack. The thing that jumps out: scores sit near zero before ChatGPT, then climb after December 2022, and Medium climbs a lot faster than Substack.

We’re not treating the detector as truth. `P_AI` is the model’s guess that a post was AI-written. It’s useful for trends. It still needs the validation checks Kevin and Bhuwan are working toward.

### Research question

After ChatGPT launched in November 2022, did writing on Substack and Medium get less distinctive / more AI-like? And does that depend on the platform, the topic, or how big the creator is?

### Design we’re sticking with

| Piece | Choice |
|---|---|
| Window | Jan 2020 – Sep 17, 2026 |
| Break | Dec 2022 |
| Who we sample | Creators |
| What we measure | Posts |
| How we analyze | Creator × month |
| Target | About 2,500 creators per platform |
| Text | Public English only; paywalled posts stay in metadata |

We sample creators on purpose. If we sampled posts first, high-volume accounts would dominate the averages.

### Who’s doing what

| Person | This month |
|---|---|
| Michael Badu | Scraping, dataset docs, Substack design EDA, scored Medium/Substack EDA, deck |
| Ziyang Qin | Collection / shard work, shared frames on OneDrive |
| Zherui Zhang | Sample packaging and the frozen Substack delivery |
| Richard So | Advisor meetings |
| Astrid Giugni | Mentoring |
| Kevin Guo / Bhuwan Dhingra | Detector side |


## 2. Weekly update history

| Date | Who | What got done | Where it shows up |
|---|---|---|---|
| Sep 18, 2026 | MB | Scope and monthly-N writeup in `Dataset_Scope_and_Parameters.md`; toy scrapers started | Design / data |
| Sep 24, 2026 | MB | `Project_and_Team_Overview.md`; agreed Substack-first share for the detector folks | Overview |
| Sep 25–Oct 2, 2026 | ZZ / ZQ / MB | Team Substack crawl and frozen selected sample on OneDrive | Progress |
| Oct 2, 2026 | MB | Medium RSS training scrape for full-text human articles (`data/training/`) | Data |
| Oct 9, 2026 | MB | Substack sample EDA notebook: coverage, topics, who comes back, length slope | Results |
| Oct 9, 2026 | MB | Sent Zherui the limits on the frozen sample (pending pubs, kept share, post cap) | Coordination |
| Oct 15, 2026 | MB | Rebuilt the advisor slides | Artifacts |
| Oct 30, 2026 | MB | Scored-platform EDA on the annual 50k Medium + Substack files | Results |

### Who did what, briefly

**Michael.** Built and ran the scrapers and notebooks. Did the Substack design EDA, the scored Medium vs Substack EDA, the training scrape helper, and the slides for Richard.

**Ziyang.** Worked the collection / shard side and the shared assignment frames on OneDrive so Medium and Substack can end up on the same footing.

**Zherui.** Put together the frozen Substack selected sample we actually analyzed (coverage, manifests, engagement join) and kept the shared corpus version straight.


## 3. Reflection and next steps *(monthly only)*

### Start / stop / continue

**Start**
- Say out loud in every write-up that the annual 50k scored files and the creator-month selected sample are different objects.
- Put detector accuracy / “left unresolved” rates next to any AI-share chart before we draft the semester paper.
- Start a small within-writer style pass on the ~3,793 Substack creators who show up both before and after Dec 2022.

**Stop**
- Stop reading flat selected-post counts as “people didn’t publish more.” The sampler only keeps 1–3 posts per creator-month.
- Stop treating `PREDICTION` as human vs AI. It’s which AI family wins among OpenAI / Anthropic / Gemini / Other, even when `P_HUMAN` is high.
- Stop putting Medium’s undated rows (~7.3k) into the monthly time plots.

**Continue**
- Check-ins with Richard; write decisions into the repo so we don’t lose them.
- Public text only. No paywall bypass.
- Keep this report and the notebooks current when the analysis file changes.

### Next month

1. Get a clear note from the detector side on how `P_AI` was trained and how often it’s wrong.
2. Lock the platform × month AI series (and family mix among AI-flagged posts).
3. Pilot a few style features on Substack text for the overlapping creators.
4. Confirm whether Medium’s annual 50k was built the same way as Substack’s creator-first design, and write that into the scope doc.
5. Start the paper Data / Methods draft from the notebooks and the deck.

### Open problems

| Issue | Why we care |
|---|---|
| Substack frame still provisional at freeze | Later crawl can change who was eligible |
| Topic codes in the scored CSVs have no names | Hard to talk about topics until we join a legend |
| Detector mistakes / new model versions | Can fake a post-2022 rise |
| Likes/comments on the selected zip are checkpoint totals | Bad for “engagement that month” claims |
| Medium AI scores much higher than Substack | Could be real, spam mix, or detector bias — we don’t know yet |


## 4. Design, background, and planning

### Background

The worry is that AI makes online writing more samey. We have a few related papers in the repo (`2309.14556v3`, `2410.04265v2`, `2510.22954v1`). Our cut is narrower: Substack and Medium, monthly series around ChatGPT, creator panels where we can build them, plus detector scores for “does this look AI.”

### Docs we actually use

| File | What it’s for |
|---|---|
| `Dataset_Scope_and_Parameters.md` | Sampling and N |
| `Substack Sampling Design.docx` | Earlier Substack notes |
| `Project_and_Team_Overview.md` | Team overview from Sep 24 |
| `Capstone_presentation.pptx` | Advisor talk |
| OneDrive Substack zip | Frozen selected sample |

### How the pieces connect right now

```text
Creator lists (Substack pubs / Medium writers)
        ->
Collection (API / RSS / team shards)
        ->
Samples (creator-month selected  OR  annual ~50k/year)
        ->
Detector scores (P_AI, families)
        ->
EDA notebooks
        ->
Later: Q1–Q4 (time, topic, engagement, similarity) and the paper
```

Decisions we made and are keeping: Dec 2022 as the break; default AI flag at `P_AI ≥ 0.5`, with 0.8 and 0.95 as checks; keep design EDA and scored EDA separate.


## 5. Progress and results

### Data we’re using

- Substack public archive / feeds, plus the team OneDrive freeze  
- Medium RSS / public pages for pilots and the training scrape  
- Local scored files:  
  `medium_analysis_full_annual_50k_scored_with_time.csv`  
  `substack_analysis_full_annual_50k_scored_with_time.csv`

### What’s in the scored files

Same 19 columns on both platforms. `P_AI` and `P_HUMAN` sum to 1. The family columns sum to `P_AI`. `PREDICTION` is the top AI family, not a human/AI call.

### Code and notebooks in the repo

| Path | What it is |
|---|---|
| `scripts/toy_scrape.py` | Early toy CSVs |
| `scripts/pilot_scrape.py` | Bigger pilot |
| `scripts/medium_scrape.py` | Medium RSS slice |
| `scripts/medium_training_scrape.py` | Full-text human training set |
| `data/training/` | Train / val / test splits |
| `data/substack_eda/` | Coverage / manifests and summaries |
| `notebooks/substack_sample_eda.ipynb` | Design check on the selected sample |
| `notebooks/scored_platforms_eda.ipynb` | Scored Medium vs Substack |

### Substack selected sample (design check)

From the frozen zip:

- About 2,500 creators and ~5,100 public posts in every full month  
- 201,810 creator-months; 413,252 unique posts  
- Eligible pool gets huge over time; we keep a smaller and smaller share (near 100% in early 2020, about 4% by 2026)  
- 24,744 creators total; typical creator is only in the sample for a few months; 3,793 show up on both sides of Dec 2022  
- Qualifying posts per selected creator stay around 3.5 before and after — so volume isn’t moving inside this file  
- Posts get longer over the window; that rise was already going before ChatGPT

Limits we flagged: frame still provisional, likes are late-2026 checkpoint totals, topics are keyword labels.

### Scored annual 50k (main result this month)

350,000 posts per platform. Substack is exactly 50k each year. Medium is a bit under after we drop 7,341 undated rows.

Share of posts with `P_AI ≥ 0.5` (dated rows):

| Year | Medium | Substack |
|---|---:|---:|
| 2020 | 0.2% | 0.2% |
| 2021 | 0.2% | 0.1% |
| 2022 | 3.0% | 0.3% |
| 2023 | 52.0% | 5.1% |
| 2024 | 56.9% | 10.8% |
| 2025 | 72.2% | 27.5% |
| 2026 | 74.1% | 30.1% |

A few other notes from the notebook:

- Scores are almost all near 0 or near 1, so the mean and the “share above 0.5” charts look alike.  
- Comparing the year before the cut to the year after: Medium’s mean `P_AI` goes from about 0.01 to about 0.48; Substack from about 0.003 to about 0.045.  
- After Dec 2022, Medium sits about 0.45 above Substack on mean `P_AI` on average.  
- When a post looks AI, OpenAI and Other take most of the mass. Anthropic and Gemini are smaller.  
- Topic code 7 is the big bucket on both. The AI rise shows up across most topic codes, not just one.

Plots live in `notebooks/scored_platforms_eda.ipynb`. Design plots live in `notebooks/substack_sample_eda.ipynb`.

### Rules we’re following

Public pages and feeds only. No paywall bypass. Academic use. Shared files stay on team OneDrive / private channels.


## 6. What to attach if they ask

| Thing | Where |
|---|---|
| This report | `Monthly_Progress_Report_October_2026.md` / `.docx` |
| Scope | `Dataset_Scope_and_Parameters.md` |
| Deck | `Capstone_presentation.pptx` |
| Design EDA | `notebooks/substack_sample_eda.ipynb` |
| Scored EDA | `notebooks/scored_platforms_eda.ipynb` |
| Scored CSVs | repo root |
| Training scrape | `data/training/` |

If they want screenshots: the monthly `P_AI` chart, the Medium−Substack gap chart, and a slide or two from the deck are enough.


## 7. Toward the semester writeup

Sections 1, 4, and 5 here are meant to grow into Intro / Data-Methods / early Results. Section 3 stays monthly only.

Working claim for now: on these annual scored samples, estimated AI prevalence is tiny before ChatGPT and rises after, with Medium much higher than Substack. Whether that means culture got more homogeneous still needs text-level work and a clearer read on detector error.


*Updated Oct 30, 2026*
