# OneShot

I built OneShot because every other job bot does the one thing that gets you ignored: it blasts the same generic resume at hundreds of postings. OneShot does the opposite. It finds real jobs, scores how well each one fits *your* resume, and then writes a unique, ATS-optimised resume and cover letter for every match — and hands them to you, ready to send.

It never submits anything for you. You stay in control: review the documents, tweak if you want, and apply yourself.

```
python run.py          # opens the web UI at http://127.0.0.1:5001
python run.py run      # CLI: search + tailor, results land in pending_review.csv
```

---

## ★ What makes OneShot different from every other job bot

> **Most job bots are spray-and-pray: one resume, a thousand auto-submits, zero quality control. OneShot is a quality pipeline — it writes a fresh, keyword-matched resume and cover letter per job, scores it against an ATS, rewrites until it passes, and only then gives it to you.**

| | Typical job bots | OneShot |
|---|---|---|
| **Resume** | One generic file sent everywhere | A unique resume per job, rewritten with that job's keywords |
| **ATS quality** | None | Score → rewrite loop until your target ATS score is hit |
| **Job fit** | Keyword match only | An LLM scores each job 1–10 against your real resume; you set the cutoff |
| **LLM cost** | Opaque routing | One provider you pick (Claude/OpenAI/Gemini) runs the whole engine — no surprise spend |
| **Who applies** | The bot auto-submits in the background | **You do** — OneShot stops at finished documents |
| **Your data** | A cloud subscription service | 100% local — your resume and history never leave your machine |
| **Bad LLM output** | Crashes | 4-stage JSON repair recovers it |

The heart of it is the **ATS feedback loop** (section 1 below). On the benchmark it lifts average ATS scores by +16 points and gets 3× more applications past the target threshold than a single-pass writer.

---

## What makes it different — in depth

Most job-bot projects stop at "scrape jobs, autofill forms." OneShot treats the problem as quality control, not a fire-and-forget script. And because it never touches a Submit button, you never risk your accounts getting flagged for automation.

### 1. Feedback-controlled resume generation

A naive tool writes a resume once and moves on. OneShot runs an ATS audit after every pass and rewrites whenever the score is below your target:

```
write resume  ->  ATS audit (score/100)  ->  below target?
    ^                                               |
    |_______________rewrite with audit notes________|
```

The rewrite prompt carries the previous score, the missing keywords, and the auditor's advice forward. OneShot keeps the best attempt by score across all passes, with a configurable cap (`ATS_MAX_REWRITES`) so you control the cost.

On the benchmark this improves average ATS scores by ~16 points and gets roughly 3× more applications past target vs single-pass generation.

### 2. Single-provider engine — your choice, end to end

You pick ONE provider in Settings (`LLM_PROVIDER`) and the whole pipeline —
scoring, resume, cover letters, copilot, parsing — runs on it. No
cross-provider fallback and no surprise spend on a provider you didn't choose.

| Provider | Smart model (writing) | Cheap model (scoring/parse) |
|---|---|---|
| Claude *(default)* | claude-sonnet-4-6 | claude-haiku-4-5 |
| OpenAI | gpt-4o | gpt-4o-mini |
| Gemini | gemini-2.5-pro | gemini-2.5-flash |

The two tiers are just two models of the **same** provider. If the selected
provider fails, OneShot raises a clear message ("Selected provider X failed…
check the key in Settings or switch provider") instead of silently switching.
`LLM_MODEL` / `LLM_MODEL_CHEAP` override the models, but only when they belong
to the selected provider. (The old `LLM_PROVIDER_SMART` / `LLM_PROVIDER_CHEAP`
routing was removed.)

Cost is tracked per provider and shown in Settings after each run.

### 3. Multi-tier JSON repair for unreliable LLM output

LLMs don't always return valid JSON — truncated responses, unescaped newlines, trailing commas. OneShot applies four strategies in order before giving up:

1. Direct `json.loads`
2. Extract the outermost `{...}` and retry
3. Walk the string char-by-char, escape control characters, strip trailing commas
4. Regex salvage — pull individual scored entries from a partially-valid array

The benchmark measures 75% overall recovery across 40 intentionally broken inputs, 100% on clean and well-formed-but-messy ones.

### 4. Local-first, no subscriptions

Everything runs on your machine. No account, no cloud sync, no middleman. Job data, tailored resumes, cover letters, and history all live in `outputs/` as CSVs and PDFs you can open without the app.

### 5. Review-first by design

OneShot writes every document and stops. You open the ready-to-apply record, read the resume and cover letter, and submit it yourself. Nothing is ever sent on your behalf — which is exactly why it's safe to run as often as you like.

Other niceties: already-seen jobs in `applied_jobs.csv` are skipped, and the same posting appearing at two locations is deduplicated before tailoring so you never burn LLM calls on a duplicate.

---

## Benchmark

```bash
python benchmark.py --mode repair   # no API key needed
python benchmark.py --simulate      # all sections, no API calls
python benchmark.py                 # full run with real LLM calls
python benchmark.py --provider gemini
```

**Fit scoring** — the LLM scorer against 25 hand-labeled jobs (15 relevant, 10 irrelevant) on a sample SWE resume:

```
               Predicted YES   Predicted NO
Actual YES         13  (TP)        2  (FN)
Actual NO           1  (FP)        9  (TN)

  Accuracy  : 88.0%   Precision : 92.9%
  Recall    : 86.7%   F1 Score  : 89.7%

Baseline (accept everything): 60.0% accuracy, 40% wasted applications
```

**ATS rewrite loop** — average score improvement across 3 sample jobs:

```
  job                             before   after   gain
  senior-backend-python-kafka         72      87    +15
  platform-engineer-kubernetes        65      81    +16
  data-engineer-airflow-dbt           61      79    +18

  Mean: 66 -> 82 (+16 pts)   Jobs reaching target: 0/3 -> 3/3
```

**JSON repair** — 40 intentionally broken outputs:

```
  clean               10/10  100%
  truncated            4/10   40%   (genuinely unrecoverable)
  unescaped newlines   8/10   80%
  trailing commas      8/10   80%
  Overall:            30/40   75%
```

Results save to `outputs/benchmark_<timestamp>.json`.

---

## Quick start

**Recommended Python: 3.11 or 3.12** (3.13 may fail to build some wheels).

```bash
git clone https://github.com/Gaurav-0704/OneShot
cd OneShot
python setup.py          # Windows: py -3.12 setup.py
```

That one command creates a virtualenv (`./venv`), installs dependencies, asks
for an API key (Gemini has a free tier), and opens the web UI at
http://127.0.0.1:5001.

Then, in the app:
1. **Profile tab** → upload your resume (auto-fills your details).
2. **Settings tab** → confirm your API key and pick a provider (Claude is the default).
3. **Search & Run** → set your search terms → **Start Run**.

**Minimum to run:** one API key from any provider + the required profile fields below.

> **Deploying to a server (Railway/cloud)?** The repo includes `Procfile`,
> `railway.toml`, `wsgi.py`, and `runtime.txt` for that. They are **deploy-only
> and ignored when you run locally** — you can leave them untouched. On a public
> deployment, set `APP_PASSWORD` (see `.env.example`) so only people with the
> password can use it.

Everything you generate (résumés, cover letters, tracking, logs) stays on your
own machine under `config/` and `outputs/` — both gitignored, so nothing is ever
shared or committed.

---

## What you actually need to fill in

OneShot only asks for what the search-and-tailor engine genuinely uses. Everything else is optional.

**Required**
- Master resume (PDF / DOCX / DOC / TXT / MD)
- First and last name
- Email
- City and country
- Years of experience
- A short profile summary the AI uses for tailoring

**Optional** — phone, LinkedIn, GitHub/portfolio, professional summary. Add them if you have them; leave them blank if you don't. No work-authorization questions, no demographics, no salary forms — those belonged to the old auto-apply flow and are gone.

---

## Configuration

```
config/
  personal.yaml       name, contact info, address
  preferences.yaml    search terms, sites, blacklists, salary floor, fit-score floor
  questions.yaml      years of experience + profile summary used for tailoring
  master_resume.pdf   your real resume
  resume_instructions.md   formatting rules the writer follows
```

All editable through the web UI or directly as YAML/PDF.

---

## How the pipeline runs

Six agents in `agents/`, one per stage:

```
ProfileAgent    reads YAML configs + resume + optional GitHub repos
    |
DiscoveryAgent  scrapes LinkedIn / Indeed / Glassdoor / ZipRecruiter / Google Jobs
    |           applies rule filters (blacklist, salary floor)
    |           optionally scores each job 1-10 against your resume
    |
TailorAgent     for each job above the fit-score floor:
    |             Phase 1 — fetch full JD, build a company brief (cheap LLM call)
    |             Phase 2 — write tailored resume + cover letter + ATS audit
    |                        rewrite if below target, keep the best attempt
    |
HumanizerAgent  strip AI-sounding filler from the generated text
    |
PackagerAgent   write the ready-to-apply record to pending_review.csv
    |
LearnerAgent    post-run: gap analysis across your recent runs
```

`agents/orchestrator.py` runs the loop one job at a time, checking a stop flag between jobs so a stop from the web UI takes effect promptly.

---

## CLI

```
python run.py                       open the web UI
python run.py run                   search + tailor; results go to pending_review.csv
python run.py run --limit 5         cap this run to 5 tailored applications
python run.py run --no-score        skip LLM fit scoring (faster, cheaper)
python run.py run --site linkedin   restrict discovery to one platform
python run.py profile               print the assembled profile, no scraping
python run.py status                today / lifetime counts
python run.py history --limit 20    last N records
```

---

## Outputs

```
outputs/
  pending_review.csv     finished applications waiting for your review
  applied_jobs.csv       the ones you marked as applied
  failed_jobs.csv        jobs where something broke
  last_discovered.json   last discovery snapshot (shown in the Discovered tab)
  tailored/<slug>/       per-job folder: resume.pdf, cover_letter.pdf, ats_audit.txt
  api_usage.json         LLM call counts and estimated cost per provider
  logs/                  timestamped run logs
  benchmark_*.json       benchmark results
```

---

## A few things to know

The LLM is used for scoring fit, writing resumes and cover letters, and building company briefs — all on the single provider you select in Settings (`LLM_PROVIDER`, default Claude). The smart model writes; the cheap model of the same provider handles scoring/parsing. Switch providers in the Settings tab or `.env`.

OneShot reads job boards that don't love being scraped, so keep your runs reasonable. But since it never logs in or submits on your behalf, there's no auto-apply footprint on your accounts.

---

## File layout

```
benchmark.py             pipeline quality measurements
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

Built on top of open-source projects. See `docs/CODEBASE_NOTES.md` for the file-by-file mapping.

- [cullenwatson/JobSpy](https://github.com/cullenwatson/JobSpy) — job board scraping
- [rotsl/resume-tailor](https://github.com/rotsl/resume-tailor) — resume tailoring and PDF generation
- [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) — profile/data-model reference
- [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply) — application field reference

---

## License

MIT. See [LICENSE](LICENSE).
