# OneShot

**Most job bots fire the same résumé at a thousand listings and call it automation. OneShot does the opposite — it builds one ruthlessly-tailored, ATS-beating application per job, proves the quality with a score, prepares your screening answers, and hands it to you ready to send.**

Search the freshest roles → tailor a résumé + cover letter that actually clears the ATS → walk in with your answers already written. That's the whole game.

```bash
python run.py            # web UI at http://127.0.0.1:5001
python run.py run        # CLI: search + tailor, results in pending_review.csv
```

---

## What OneShot does that other job bots simply don't

Forget the table-stakes (scraping boards, basic keyword tailoring — everyone has that). Here's what's actually unique:

### 1. It rewrites your résumé until it *beats* the ATS — and proves it

Every other tailor writes a résumé once and ships it. OneShot **audits its own output against an ATS, sees the score, and rewrites with the audit notes** — looping until it clears your target, keeping the best attempt by score.

```
write résumé → ATS audit (score/100) → below target?
   ^                                          |
   |____________ rewrite with the misses _____|
```

**Proof:** on the benchmark it lifted mean ATS scores **66 → 82 (+16 points)** and took jobs reaching target from **0 of 3 to 3 of 3**. It's not "tailoring." It's optimization with a measured outcome.

### 2. It hunts *fresh* — and never shows you the same job twice

Apply-early-or-lose is real. OneShot starts at the tightest time window and **auto-widens only if it comes up empty**, so you catch roles posted hours ago. Then a cross-run memory kills repeats: a job you already saw won't come back — **even if it's reposted under a brand-new ID or a different location string**. Most bots re-dump the same listings every run. OneShot surfaces only what's genuinely new.

### 3. It prepares your *answers*, not just your documents

This one is unheard of in a job bot. For every prepared application, an **Application Copilot pre-bakes answers to that job's screening and behavioral questions** ("Why this company?", "Years with X?", "Walk me through a project") — grounded **only in your verified profile and résumé**, with a confidence score on each. You open a job and your answers are already written.

### 4. It refuses to lie for you

Recruiters smell fabrication. OneShot is **truth-locked**: résumé, cover letter, and Copilot answers can only use facts from your real résumé. A guardrail actively flags invented numeric claims (e.g. "7 years of Python" when your résumé says 5) and drops confidence instead of bluffing. Honesty is enforced in code, not hoped for.

### 5. It doesn't leak the wrong country

JobSpy-based scrapers happily return Beijing and Hong Kong "remote" roles on a US search. OneShot runs a **positive geo-filter** that keeps only your allowed countries (and ambiguous remote), so your queue isn't polluted with jobs you can't take.

### 6. It doesn't crash on a bad LLM response

LLMs return broken JSON — truncated, unescaped, half-finished. A hobby bot dies; OneShot **recovers**. A 4-stage JSON-repair pipeline plus a field-level salvage rescues **75% of intentionally-broken outputs**, and résumé extraction pulls your links and location even out of a JSON response that got cut off mid-sentence.

### 7. It won't surprise your wallet

One provider you pick (Claude, OpenAI, or Gemini) runs **the entire engine** — no silent cross-provider fallback spending money on a model you didn't choose. Every call is counted and costed live in Settings.

> **The philosophy:** quality over spray. One tailored, review-ready package per job. OneShot never clicks Submit — *you* do — so there's zero auto-apply footprint on your accounts, and you can run it as often as you like.

---

## Proof — measured, not claimed

Run it yourself: `python benchmark.py` (or `--simulate` for no API calls, `--mode repair` for no key at all).

**Fit scoring** — can the LLM tell a real match from a near-miss? Against 25 hand-labeled jobs on a sample résumé:

```
  Accuracy 88.0%   Precision 92.9%   Recall 86.7%   F1 89.7%
  vs. accept-everything baseline: 60% accuracy, 40% of applications wasted
```
**What it proves:** it cuts ~80% of irrelevant applications while missing only ~13% of good ones — you spend tokens (and attention) on jobs that actually fit.

**ATS rewrite loop** — does the feedback loop work?

```
  job                         before  after  gain
  senior-backend-python-kafka    72     87    +15
  platform-engineer-k8s          65     81    +16
  data-engineer-airflow          61     79    +18
  Mean 66 → 82 (+16)     Reaching target: 0/3 → 3/3
```
**What it proves:** the rewrite isn't cosmetic — it reliably pushes a résumé past the bar a single pass misses.

**JSON-repair resilience** — 40 deliberately-broken LLM outputs:

```
  clean 10/10 · unescaped 8/10 · trailing-comma 8/10 · truncated 4/10 → 75% overall
```
**What it proves:** the pipeline keeps producing applications when the model misbehaves, instead of throwing away the whole run.

---

## Quick start

**Use Python 3.11 or 3.12** (3.13 may fail to build some wheels).

```bash
git clone https://github.com/Gaurav-0704/OneShot
cd OneShot
python setup.py          # Windows: py -3.12 setup.py
```

One command sets up a virtualenv, installs everything, asks for an API key (Gemini has a free tier), and opens the UI. Then: **Profile** → upload your résumé (it auto-fills your details) → **Settings** → confirm your key/provider → **Search & Run** → set terms → **Start Run**.

Everything you generate stays on your machine under `config/` and `outputs/` (both gitignored).

> Deploying to a server? `Procfile` / `railway.toml` / `wsgi.py` / `runtime.txt` are deploy-only and ignored locally. On a public deployment set `APP_PASSWORD` (see `.env.example`) so only people with the password can use it — and your API keys.

---

## How the pipeline runs

```
ProfileAgent    your résumé + profile (and GitHub, if you add it)
   ↓
DiscoveryAgent  scrape LinkedIn / Indeed / Glassdoor / ZipRecruiter / Google
   ↓            geo-filter · freshness + repost kill · LLM fit score (1–10)
TailorAgent     per job: company brief → résumé + cover letter + ATS audit
   ↓                     rewrite until it clears the bar, keep the best
HumanizerAgent  strip AI-tells; truth-check against your real facts
   ↓
PackagerAgent   ready-to-apply record + pre-baked Copilot answers
   ↓
LearnerAgent    post-run gap analysis across your recent runs
```

Generation runs in parallel; ask for N applications and you get **exactly N** saved (failures don't eat your quota).

---

## Outputs

```
outputs/
  pending_review.csv     finished applications waiting for your review
  applied_jobs.csv       the ones you marked as applied
  tailored/<slug>/       per-job: resume.pdf, cover_letter.pdf, ats_audit.txt, copilot_data.json
  seen_jobs.sqlite       cross-run memory (the "never twice" engine)
  last_discovered.json   latest scored discovery snapshot
  api_usage.json         per-provider call counts + estimated cost
```

---

## Configuration & CLI

Edit everything in the web UI, or directly in `config/` (`personal.yaml`, `preferences.yaml`, `questions.yaml`, `master_resume.*`).

```bash
python run.py run --limit 5        prepare 5 applications
python run.py run --no-score       skip LLM fit scoring (faster/cheaper)
python run.py run --site linkedin  restrict to one board
python run.py status               today / lifetime counts
```

---

## Source attribution

Built on top of open-source projects (see `docs/CODEBASE_NOTES.md`):

- [cullenwatson/JobSpy](https://github.com/cullenwatson/JobSpy) — job-board scraping
- [rotsl/resume-tailor](https://github.com/rotsl/resume-tailor) — résumé tailoring + PDF generation
- [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) — profile/data-model reference
- [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply) — application field reference

---

## License

MIT. See [LICENSE](LICENSE).

Copyright © 2026 Gaurav Singh Thakur.
