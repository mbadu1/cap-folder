# Medium training sample

Public Medium longform articles with **real full text** (RSS `content:encoded`), filtered to >= 200 words.
Member-only stubs excluded. No paywall bypass.

- `medium_articles_full.csv` — all sampled articles + `full_text`
- `medium_train.csv` / `medium_val.csv` / `medium_test.csv` — stratified 70/15/15 by topic
- `medium_articles_meta.csv` — same rows without body (for browsing)
- `label` = `human` (for detector training as the human class)

Generated: 2026-10-02T17:57:34.641033+00:00
Articles: 92 | train=63 val=11 test=18
