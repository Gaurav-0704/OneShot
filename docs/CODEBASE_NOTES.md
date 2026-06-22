# Codebase notes - where each piece came from

OneShot is a stitched-together pipeline. This file maps every module back to its
source repo so you can trace logic when something needs fixing.

## Source repos (siblings in `D:\AI Jobs Applier\`)

| Sibling folder | Original repo | Used for |
|---|---|---|
| `JobSpy/` | [cullenwatson/JobSpy](https://github.com/cullenwatson/JobSpy) | `core/scraper.py` (imports the published `python-jobspy` pip package - we don't fork the source) |
| `resume-tailor/` | [rotsl/resume-tailor](https://github.com/rotsl/resume-tailor) | `core/parser.py` (PDF/DOCX → text, copied), `core/pdf_generator.py` (ReportLab PDF render, copied), `core/tailor.py` (prompt structure ported), `config/resume_instructions.md` (formatting rules, copied) |
| `AutoApplyLinkedIn/` | [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) | `appliers/linkedin.py` (Easy Apply state machine, ported & restructured), `browser/chrome.py` (undetected-chromedriver setup, simplified), `browser/selenium_utils.py` (clickers/finders/helpers, trimmed) |
| `AutoApplyMultiATS/` | [simonfong6/auto-apply](https://github.com/simonfong6/auto-apply) | `appliers/greenhouse.py` (Greenhouse field IDs reference) |
| `AIHawk/` | [feder-cr/Jobs_Applier_AI_Agent_AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) | Not directly used - kept as reference for HTML/CSS resume templates if we ever swap renderers |

## Module → source mapping

### `core/scraper.py`
- **From scratch** wrapper around `from jobspy import scrape_jobs`. Maps our `preferences.yaml` to JobSpy's call signature.
- Returns normalized list of dicts via `df_to_job_dicts()`.

### `core/filter.py`
- **From scratch.** Two-stage filtering:
  1. `apply_rule_filters()` - cheap blacklist/whitelist (own logic).
  2. `score_and_filter()` - Claude scores 1-10, keep above threshold (own logic).
- Inspired conceptually by lookr-fyi's "semantic filtering" approach (their repo turned out to be marketing for a paid app, no usable code).

### `core/parser.py` & `core/pdf_generator.py`
- **Copied verbatim** from `resume-tailor/src/`.
- `parser.py` uses pdfplumber + python-docx.
- `pdf_generator.py` uses ReportLab; ATS-friendly section detection.

### `core/tailor.py`
- **Ported** from `resume-tailor/src/tailor.py`.
- Structure preserved (instruction loading, system + user prompt builders).
- Now routed through our unified `llm/client.py` so any provider works.

### `core/tracker.py`
- **Reimplemented** from `AutoApplyLinkedIn/runAiBot.py::submitted_jobs()` and `failed_job()`.
- Cleaner column schema; pandas-friendly; exposes `daily_count()` for rate limiting.

### `llm/client.py`
- **From scratch.** Three-provider abstraction (Claude / OpenAI / Gemini).
- Replaces resume-tailor's two-provider (Claude/Gemini) and AutoApplyLinkedIn's three-provider (OpenAI/DeepSeek/Gemini) systems.

### `llm/prompts.py`
- **From scratch**, but anti-hallucination rules borrowed verbatim from `resume-tailor/src/tailor.py` (the "ABSOLUTE RULES" block).
- Question-answering prompt inspired by `AutoApplyLinkedIn/modules/ai/prompts.py`.

### `browser/chrome.py`
- **Adapted** from `AutoApplyLinkedIn/modules/open_chrome.py`.
- Simplified: no globals, returns a driver instance.

### `browser/selenium_utils.py`
- **Trimmed port** of `AutoApplyLinkedIn/modules/clickers_and_finders.py` and `modules/helpers.py`.
- Kept: `try_xp`, `find_by_class`, `safe_click`, `set_text`, `scroll_to`, `human_delay`.
- Dropped: LinkedIn-specific helpers (those moved to `appliers/linkedin.py`).

### `appliers/base.py`
- **From scratch.** Abstract interface: `apply(job, resume_pdf, cover_letter_pdf) → ApplyResult`.

### `appliers/linkedin.py`
- **Ported** from `AutoApplyLinkedIn/runAiBot.py`.
- Original: 1,305-line monolith with global state.
- Now: single class, ~300 lines, takes one job at a time.
- Easy Apply state machine preserved. Question-answering logic refactored to use our config + LLM fallback.

### `appliers/greenhouse.py`
- **Hybrid:** field IDs from `simonfong6/auto-apply/auto_apply/greenhouse.py`, custom-question logic mirrored from our LinkedIn applier.

### `appliers/indeed.py`
- **Stub.** Falls back to `ManualApplier` until we hand-write the Indeed selectors.

### `appliers/manual.py`
- **From scratch.** Opens job URL, waits for user to apply manually. Catch-all for ATSs we don't automate (Workday, Lever, Ashby, Wellfound, custom career pages).

### `run.py`
- **From scratch.** Orchestrator. Wires all modules.

## What we explicitly did NOT pull in

Researched but rejected - see chat history for reasoning:

- **Skyvern** - purpose-built browser-agent, very impressive, but adds a heavy dependency for marginal current benefit. Worth revisiting if we hit too many ATSs to maintain selectors for.
- **browser-use** - same logic. We can pip-install it later for ATS variants we don't want to write Selenium for.
- **lookr-fyi** - repo turned out to be marketing for a paid Mac app.
- **claude-code-job-tailor** - TypeScript/React, doesn't fit our Python stack.
- **career-ops** - Node.js Claude Code app, same problem.
