# OneShot

Local job application helper. Scrapes job boards, writes a tailored resume and cover letter
for each posting, fills out the application form, and stops at the review page so you can
verify before submitting.

Runs on your laptop. Your data stays on your laptop.

## Setup

```bash
cd OneShot
py -3.13 setup.py
```

The setup script creates a virtualenv, installs dependencies, asks you for an API key
(Gemini has a free tier - pick option 2 if you don't want to pay), then opens
http://127.0.0.1:5000 in your browser.

Drop your resume at `config/master_resume.pdf` (or upload it through the Profile tab),
fill in the rest of the profile, and you're done.

## How it works

There are six pieces, each in its own file under `agents/`. They run in order:

1. `profile.py` - reads your YAML configs and resume, fetches your top GitHub repos,
   builds one `UserProfile` everyone else reads from.
2. `discovery.py` - calls JobSpy to pull jobs from LinkedIn / Indeed / Glassdoor /
   ZipRecruiter / Google. Drops jobs you've already applied to and the ones on your
   blacklist. Optionally scores each remaining job 1-10 against your resume.
3. `research.py` - for one job, fetches the JD page and the company's about page,
   asks the LLM for a short structured brief.
4. `writer.py` - rewrites your resume to match the job, drafts a cover letter, runs
   an ATS keyword check, saves both as PDFs.
5. `form_filler.py` - opens the job posting, picks the right platform handler
   (LinkedIn / Indeed / Greenhouse / manual), fills the form, attaches the PDFs.
   Stops at the review page.
6. `reviewer.py` - preflight: right files attached? ATS score above floor?
   Required fields filled? If yes, clicks submit. If no, leaves it on the
   Pending tab for you to handle.

`agents/orchestrator.py` runs them in a loop, one job at a time.

## Layout

```
setup.py               first-run installer
run.py                 CLI + web server entry
models.py              UserProfile and JobApplication dataclasses
.env.example           API keys, LinkedIn login, daily caps

agents/                the six pieces above + orchestrator
appliers/              platform-specific form fillers
browser/               undetected-chromedriver setup, selenium helpers
core/                  scraper / filter / parser / tailor / pdf / tracker
core/_vendor/jobspy/   vendored copy of cullenwatson/JobSpy
llm/                   provider abstraction (Claude / OpenAI / Gemini), prompts
webapp/                Flask backend + the single-page frontend

config/
  personal.yaml        you (name, email, phone, links, demographics)
  preferences.yaml     search terms, sites, blacklists, fit-score floor
  questions.yaml       pre-answered screening questions
  master_resume.pdf    your real resume

outputs/
  applied_jobs.csv     submitted
  pending_review.csv   filled but not submitted (waiting on you)
  failed_jobs.csv      something broke
  tailored/<slug>/     per-job PDFs + ATS audit
  logs/                run logs
```

## CLI

The web UI is the recommended way in. The CLI exists for headless use and quick
sanity checks.

```
python run.py                       open the web UI
python run.py serve                 same
python run.py run                   run the pipeline once (dry-run by default)
python run.py run --no-dry-run      same, but actually submit
python run.py profile               print the assembled UserProfile
python run.py status                today / lifetime application counts
python run.py history --limit 20    last N applied jobs
```

## What's safe by default

- Dry-run is on. The first run writes PDFs and walks every form, but doesn't click submit.
- Per-platform daily caps come from `.env`: 25 LinkedIn, 50 Indeed, 20 other.
  Hit the cap, the run stops on that platform.
- Pause-before-submit is on. Even with `--no-dry-run`, ReviewerAgent prompts you per
  application before the click. Disable with `--no-pause` in CLI or by toggling it
  in Settings.
- Already-applied dedupe. The pipeline reads `applied_jobs.csv` on startup and skips
  any job_id that's already there.

## A few things to know

- LinkedIn and Indeed both prohibit automation. People do get accounts restricted.
  Keep the daily caps low. Warm a fresh account for ~21 days before turning the
  bot on. Don't apply to 100 jobs in an afternoon.
- The auto-apply path is solid for LinkedIn Easy Apply and Greenhouse. For
  Workday, Lever, Ashby, and most company-specific career pages, the bot opens the
  page and you do the last click. Those land in `appliers/manual.py`.
- The LLM is used for: tailoring the resume, writing the cover letter, scoring
  fit, summarizing the company, answering screening questions when there's no
  preset answer. Default model is Claude Sonnet for writing and Claude Haiku for
  the rest. Switch providers in the Settings tab.

## Source of pieces

Built on top of four open-source projects. See `docs/CODEBASE_NOTES.md` for the
file-by-file mapping.

- [cullenwatson/JobSpy](https://github.com/cullenwatson/JobSpy) - scraping
- [rotsl/resume-tailor](https://github.com/rotsl/resume-tailor) - tailoring + PDFs
- [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) - LinkedIn Easy Apply
- [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply) - Greenhouse field reference
