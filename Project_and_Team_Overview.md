# Project and Team Overview

**Project:** Is AI Killing the Internet? Measuring the Displacement of Human Culture Online  
**Platforms:** Substack and Medium  
**Date:** September 24, 2026  
**Phase:** Building the dataset

---

## 1. Project overview

### 1.1 What we’re trying to figure out

There’s a lot of worry that AI-generated writing is taking over the internet. We’re less interested in counting “AI posts” and more interested in a harder question: is human culture online actually getting squeezed out? In other words, are the people who write longform on the web starting to sound more alike, more generic, more machine-like after tools like ChatGPT became widely available?

Our job is to put numbers on that. We’ll build a dataset of writing from before and after ChatGPT, then see whether creators’ work really changed.

### 1.2 Research question

After ChatGPT launched in November 2022, did longform writing on Substack and Medium become less distinctive and more homogeneous — and does that look different depending on the platform, the topic, or how big the creator is?

We’re not claiming we can label every post as “human” or “AI.” We’re building the data so we can track style and diversity over time at the creator–month level. Downstream, collaborators are also training a detection model on samples we share.

### 1.3 Why Substack and Medium

Both are places where people write real essays and newsletters, so style is something we can actually measure. They also work differently:

| | Substack | Medium |
|---|---|---|
| Format | Newsletters, mostly email | Articles on the web |
| Who we sample | Publications (usually one author) | Individual writers (`@user`) |
| How we find “popular” | Official category leaderboards | We’ll build an elite list (Partner / high-reach / big publications) |
| What writers optimize for | Subscribers and paid plans | Reads, Boost, distribution |

If AI is really changing online culture, it should show up in both places — not just whichever platform has the worse spam problem.

### 1.4 Design, short version

| Piece | Our choice |
|---|---|
| Window | Jan 2020 – Sep 2026 |
| Breakpoint | Nov 30, 2022 (ChatGPT) |
| Panel | About 2,500 creators per platform |
| Mix | ~500 well-known + ~2,000 random, with topic floors |
| Topics | 8 shared topic groups |
| Who we sample | Creators |
| What we measure | Posts (the text) |
| How we analyze | Creator × month |
| Full text | Public English posts only; up to 2 posts per creator per month |
| Everything else | Keep metadata for all posts, including paywalled ones |

We’re sampling **creators**, not posts, on purpose. If you sample posts, high-volume accounts dominate, and a jump in “AI-sounding” text might just mean more spam accounts showed up. A creator panel lets us ask whether the same writers changed how they write, not only whether the feed got noisier.

### 1.5 Where we are

- Scope and sampling written up (`Dataset_Scope_and_Parameters.md` and the Substack design doc)
- Toy scraper running (`scripts/toy_scrape.py`) and writing CSVs in `data/toy/`
- Substack sample data is far enough along to share; Medium still needs a definitive scrape plan and a toy scrape
- What we’ve learned so far: top Substack pubs are often paywalled, so we get a lot of metadata but less free full text; Substack’s archive API and RSS are usable; Medium RSS works; we still need real historical pagination back to 2020

### 1.6 Rules we’re sticking to

- Public pages and feeds only; slow down; follow robots.txt  
- Don’t break paywalls — flag those posts in metadata instead  
- Academic use; don’t republish restricted text  
- Be honest about the population: sitemap-visible creators with public English text, not “everyone who writes online”

---

## 2. Team overview

### 2.1 Who’s on the team

| Name | Focus | Notes |
|---|---|---|
| Michael Badu | Dataset / scraping | Sampling design, Substack/Medium collection, analysis |
| Ziyang Qin | Team member | Capstone team |
| Zhenru Zhang | Team member | Capstone team |
| Richard So | Advisor | Weekly meetings; priorities and deadlines |
| Kevin Guo / Bhuwan Dhingra | Detection model | Receive sample data to train / fine-tune |

### 2.2 How we might split the work

| Workstream | Owner | What “done” looks like |
|---|---|---|
| Design & sampling frames | | Protocol locked; elite lists; topic map |
| Substack collection | | Creator panel + meta/text CSVs; sample shared with Kevin |
| Medium collection | | Definitive Medium doc + toy scrape; sample shared next |
| Data quality / monthly N | | Pilot memo; density tables |
| Features / measurement | | Clear outcomes; how we’ll check them |
| Analysis & figures | | Creator–month trends; pre/post contrasts |
| Writing & updates | | Docs and supervisor check-ins |

### 2.3 How we’ll work together

| Topic | Plan |
|---|---|
| Day to day | Slack; meet regularly with Richard |
| Files | Keep design, code, and schemas in this repo; share samples via private Git when possible |
| Big decisions | Write them into `Dataset_Scope_and_Parameters.md` so we don’t lose them |
| Grey areas | Ask before scraping anything that feels sketchy |
| Sequence | Toy → pilot → full build. Don’t add more platforms until monthly N looks okay |

---

## 3. Short team reflection and next steps

### 3.1 Reflection

We’ve been meeting regularly with Richard, and the last check-in went well. The main feedback was to keep moving on two tracks at once: get usable **Substack sample data** into the hands of the people training the detection model, and stop treating Medium as an afterthought — give it the same level of written plan Substack already has.

What feels solid so far is the Substack side: we have a sampling design, a scope doc, and toy scrapes that actually write CSVs. What’s still thin is Medium (no definitive scrape doc yet) and historical depth (toy feeds are mostly recent posts).

### 3.2 Major decisions so far

- Study longform on **Substack and Medium**, not the whole internet  
- Sample **creators**, measure **posts**, analyze **creator × month**  
- Aim for ~**2,500 creators per platform**, elite + topic-balanced random  
- Use **public text only**; keep paywalled posts in metadata  
- Build toward sharing samples with **Kevin Guo and Bhuwan Dhingra** so they can train / fine-tune a detection model  
- Prefer sharing through a **private Git repo** (email link to `k.guo@duke.edu` as backup)

### 3.3 Current plans (from our latest Slack priorities)

1. **Share Substack sample data with Kevin ASAP** (target: Sunday EOD), so Kevin and Bhuwan can start training / fine-tuning. Right now we only have Substack data ready to hand over.  
2. **Write a definitive Medium scrape document** — same kind of artifact we already have for Substack.  
3. **Run a Medium toy scrape**, then share Medium sample data with Kevin the following week.

### 3.4 Project direction

Near term: Substack sample out the door → Medium spec + toy scrape → Medium sample to Kevin.  
After that: historical pagination, a real pilot for monthly N, lock the panels, then features and the displacement analysis.

### 3.5 Artifacts

| Artifact | Status |
|---|---|
| `Dataset_Scope_and_Parameters.md` | Done (draft) |
| `Substack Sampling Design.docx` | Done |
| Toy Substack CSVs (`data/toy/`) | Done / expanding |
| Private Git share of Substack samples for Kevin / Bhuwan | In progress (this week) |
| Definitive Medium scrape document | Next |
| Medium toy scrape + sample share | Next week |
| Full panels, feature pipeline, analysis writeup | Later |

---

## 4. Short summary (for emails / intros)

We’re trying to measure whether AI is crowding out distinctive human writing online. We’re building matched creator panels on Substack and Medium (about 2,500 each) from 2020 through 2026, mixing well-known writers with a topic-balanced random sample. We follow creators over time, pull features from their posts, and analyze at the creator–month level around ChatGPT’s launch. We only use public text; paywalled posts stay in the metadata. Right now the push is sharing Substack samples for the detection model and catching Medium up to the same standard.

---

## 5. What’s in the repo

| File | What it is |
|---|---|
| `Project_and_Team_Overview.md` / `.docx` | This overview |
| `Dataset_Scope_and_Parameters.md` | Full sampling and N rationale |
| `Substack Sampling Design.docx` | Earlier Substack notes |
| `scripts/toy_scrape.py` | Toy scraper |
| `data/toy/` | Toy CSVs |
| Background PDFs | Related papers |

*Last updated: September 24, 2026*
