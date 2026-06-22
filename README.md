# OneShot

Automated job applications that run locally, keep your data on your machine, and don't submit anything without your say-so.

The pipeline scrapes job boards, scores each listing against your resume, writes a tailored resume and cover letter for every match, fills out the application form, and stops at the review page. You click Submit.

```
python run.py          # opens http://127.0.0.1:5000
python run.py run      # CLI, dry-run by default
```

---

## ★ What makes this different from every other job bot

> **Most job bots mass-blast one generic resume and hope for replies. OneShot is a quality-control pipeline that writes a unique, ATS-optimised resume and cover letter per job, checks the score, rewrites until it passes, and only then hands it to you.**

| Feature | Typical job bots | OneShot |
|---|---|---|
| Resume tailoring | One generic resume sent everywhere | Unique resume per job, rewritten with job-specific keywords |
| ATS optimisation | None | Score → rewrite loop until target ATS score is met |
| LLM cost control | None / one provider | Gemini (free) → Claude (quality) → OpenAI fallback, auto-switches on rate-limit |
| Output | Submits silently in background | You review every application; you click Submit |
| Data privacy | Cloud/subscription service | 100% local — all data stays on your machine |
| Fit filtering | Keyword match only | LLM scores each job 1–10 against your actual resume; configurable threshold |
| JSON reliability | Crashes on bad LLM output | 4-stage repair: parse → extract → escape → regex salvage |

The core differentiator is the **ATS feedback loop** (point 1 below). The benchmark shows it lifts average ATS scores by +16 points and moves 3× more applications past the target threshold vs single-pass generation.

---

## What makes this different — in depth

Most job-bot projects stop at "scrape jobs and autofill forms." This one treats the problem as a quality-control pipeline, not a fire-and-forget script.

### 1. Feedback-controlled resume generation

A naive implementation writes a resume once and moves on. OneShot runs an ATS audit after every generation pass and rewrites if the score falls below your target:

```
write resume  ->  ATS audit (score/100)  ->  below target?
    ^                                               |
    |_______________rewrite with audit notes________|
```

The rewrite prompt includes the previous score, the missing keywords, and the auditor's advice. The pipeline keeps the best attempt by score across all passes. There's a configurable cap on rewrites (`ATS_MAX_REWRITES`) so you control the cost.

The benchmark shows this improves average ATS scores by ~16 points and moves roughly 3x more applications past the target threshold compared to single-pass generation.

### 2. Multi-provider cost arbitrage with live fallback

Three LLM providers are wired in parallel:

| Provider | Default use | Why |
|---|---|---|
| Gemini 2.5 Flash | Scoring, filtering, audits | Free tier; fast enough for batch work |
| Claude Sonnet | Resume writing, cover letters | Best output quality for writing tasks |
| OpenAI GPT-4o | Fallback | Reliable when others rate-limit |

If the primary provider returns a 429 or quota error mid-run, the client automatically retries with the next enabled provider that has a valid key — no user action, no dropped jobs. You can disable any provider or force a specific one in `.env`.

Cost is tracked per provider and shown in the Settings tab after each run.

### 3. Multi-tier JSON repair for unreliable LLM output

LLMs don't always return valid JSON. Truncated responses, unescaped newlines in strings, and trailing commas from verbose chain-of-thought outputs are common. The pipeline has a repair function that applies four strategies in order before giving up:

1. Direct `json.loads` parse
2. Extract the outermost `{...}` substring and retry
3. Walk the string char-by-char, escape control characters, strip trailing commas
4. Regex salvage — pull individual scored entries from a partially-valid array

Failure at any stage falls through to the next. The benchmark measures this at 75% overall recovery across a fixture of 40 intentionally broken inputs, with 100% on clean and well-formed-but-messy inputs.

### 4. Local-first, no subscriptions

Everything runs on your machine. No account, no cloud sync, no rate limits from a middleman. Job data, tailored resumes, cover letters, and application history all live in `outputs/` as CSVs and PDFs you can open without the app.

### 5. Safe by default

- **Review-first handoff**: the pipeline writes every document and stops. You open the ready-to-apply record in your browser and click Submit yourself. Nothing is submitted on your behalf.
- **Daily caps per platform**: hit the cap and the run stops. Keeps accounts from getting flagged.
- **Already-applied dedupe**: `applied_jobs.csv` is read at startup; known job IDs are skipped.
- **Duplicate suppression**: the same job posting at two locations is deduplicated before tailoring so you don't waste LLM calls on the same listing twice.

---

## Benchmark

Run the quality benchmarks yourself:

```bash
# No API key needed — repair benchmark only
python benchmark.py --mode repair

# All three sections with simulated scores (no API calls)
python benchmark.py --simulate

# Full benchmark with real LLM calls
python benchmark.py

# Use a specific provider
python benchmark.py --provider gemini
```

Three sections:

**Fit scoring** — runs the LLM scorer against 25 hand-labeled jobs (15 relevant, 10 irrelevant) against a sample software engineer resume. Compares against the all-accept baseline.

```
               Predicted YES   Predicted NO
Actual YES         13  (TP)        2  (FN)
Actual NO           1  (FP)        9  (TN)

  Accuracy   :  88.0%
  Precision  :  92.9%
  Recall     :  86.7%
  F1 Score   :  89.7%

Baseline (accept everything past rule filters):
  Accuracy   :  60.0%
  Precision  :  60.0%
  Recall     : 100.0%   <- catches everything, but 40% are wasted applications
```

The LLM scorer cuts irrelevant applications by ~80% while missing only ~13% of good matches. Numbers vary with your resume and the threshold you set.

**ATS rewrite loop** — measures average score improvement across 3 sample jobs:

```
  job                             before   after   gain
  senior-backend-python-kafka         72      87    +15
  platform-engineer-kubernetes        65      81    +16
  data-engineer-airflow-dbt           61      79    +18

  Mean before: 66/100   Mean after: 82/100   (+16 pts)
  Jobs reaching target: 0/3 -> 3/3 after rewrite
```

**JSON repair** — runs 40 intentionally broken LLM outputs through the repair function:

```
  Type                 Recovered   Rate
  clean                  10/10    100%
  truncated               4/10     40%   (genuinely unrecoverable cuts)
  unescaped newlines      8/10     80%
  trailing commas         8/10     80%
  Overall:               30/40     75%
```

Full results are saved to `outputs/benchmark_<timestamp>.json`.

---

## Setup

```bash
cd OneShot
py -3.13 setup.py
```

The setup script creates a virtualenv, installs dependencies, asks for API keys (Gemini has a free tier), and opens the web UI.

Drop your resume at `config/master_resume.pdf` (or upload it in the Profile tab), fill in the rest of the profile, and run.

**Minimum:** one API key from any of the three providers. Gemini free tier works for light use. Add Claude for better resume quality.

---

## Configuration

```
config/
  personal.yaml       name, contact info, work authorization, demographics
  preferences.yaml    search terms, sites, blacklists, salary floor, fit score floor
  questions.yaml      pre-filled answers for common screening questions
  master_resume.pdf   your real resume
  resume_instructions.md   formatting rules the writer follows
```

All editable through the web UI or directly as YAML/PDF.

---

## How the pipeline runs

Six agents in `agents/`, one per stage:

```
ProfileAgent    reads YAML configs + resume PDF + GitHub repos
    |
DiscoveryAgent  scrapes LinkedIn / Indeed / Glassdoor / ZipRecruiter / Google Jobs
    |           applies rule filters (blacklist, salary floor)
    |           optionally scores each job 1-10 against your resume
    |
TailorAgent     for each job above the fit-score floor:
    |             Phase 1 — fetch full JD, build company brief (cheap LLM call)
    |             Phase 2 — write tailored resume + cover letter + ATS audit
    |                        rewrite if below target, keep best attempt
    |
HumanizerAgent  strip AI-sounding filler from the generated text
    |
PackagerAgent   write the ready-to-apply record to pending_review.csv
    |
LearnerAgent    post-run: gap analysis, promote learned Q&A answers
```

`agents/orchestrator.py` runs the loop, one job at a time, checking the stop flag between jobs so a web-triggered stop takes effect promptly.

---

## CLI

```
python run.py                       open the web UI
python run.py serve                 same
python run.py run                   run the pipeline; outputs go to pending_review.csv
python run.py run --limit 5         cap this run to 5 tailored applications
python run.py run --no-score        skip LLM scoring (faster, cheaper)
python run.py run --site linkedin   restrict discovery to one platform
python run.py profile               print the assembled profile, no scraping
python run.py status                today / lifetime counts
python run.py history --limit 20    last N applied jobs
```

---

## Outputs

```
outputs/
  applied_jobs.csv       submitted applications
  pending_review.csv     filled forms waiting for your click
  failed_jobs.csv        jobs where something broke
  last_discovered.json   last discovery snapshot (shown in the Discovered tab)
  tailored/<slug>/       per-job folder: resume.pdf, cover_letter.pdf, ats_audit.txt
  api_usage.json         LLM call counts and estimated cost per provider
  logs/                  timestamped run logs
  benchmark_*.json       benchmark results
```

---

## Platform support

Auto-apply (fills form + attaches PDFs):
- LinkedIn Easy Apply
- Greenhouse

Opens the page, you do the last click:
- Workday, Lever, Ashby, most company career pages

---

## A few things to know

LinkedIn and Indeed both prohibit automation. People do get accounts restricted. Keep daily caps low (`DAILY_LIMIT_LINKEDIN=25` in `.env`). Warm a fresh account for a few weeks before running the bot. Don't apply to 100 jobs in one afternoon.

The LLM is used for: scoring fit, writing resumes, writing cover letters, building company briefs, and answering screening questions when no preset answer exists. Default is Claude Sonnet for writing, Claude Haiku for everything else. Switch providers in `.env` or the Settings tab.

---

## File layout

```
benchmark.py             pipeline quality measurements (run this first)
run.py                   CLI entry point
setup.py                 first-run installer
models.py                UserProfile and JobApplication dataclasses

agents/                  six pipeline stages + orchestrator
core/                    scraper, filter, parser, PDF generator, tracker, utilities
core/_vendor/jobspy/     vendored copy of cullenwatson/JobSpy
llm/                     LLM provider abstraction (Claude / OpenAI / Gemini)
webapp/                  Flask backend + single-page web UI
config/                  your profile, preferences, and resume (gitignored)
outputs/                 generated files (gitignored)
docs/                    source attribution notes
```

---

## Source attribution

Built on top of four open-source projects. See `docs/CODEBASE_NOTES.md` for the file-by-file mapping.

- [cullenwatson/JobSpy](https://github.com/cullenwatson/JobSpy) — job board scraping
- [rotsl/resume-tailor](https://github.com/rotsl/resume-tailor) — resume tailoring and PDF generation
- [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) — LinkedIn Easy Apply automation
- [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply) — Greenhouse field reference

---

## License

MIT. See [LICENSE](LICENSE).
