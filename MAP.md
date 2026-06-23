# OneShot — Module Map (Phase 0 reference)

OneShot is a **job-search copilot**, not an auto-apply bot. It scrapes jobs,
scores fit, generates a tailored resume + cover letter + ATS audit + prebaked
Copilot answers, and hands a ready-to-apply package to the human. The human
reviews and applies themselves (`pending_review.csv` → "Mark as applied").

## Pipeline (6 agents)

| Stage | File | Job |
|---|---|---|
| Profile | `agents/profile.py` | Build `UserProfile` from `config/*.yaml` + resume + GitHub. `REQUIRED_FIELDS`/`RECOMMENDED_FIELDS` drive completeness %. `validate_profile()`, `parse_resume_to_yaml()`, `suggest_search_terms()`. |
| Discovery | `agents/discovery.py` | `core.scraper.scrape` → `df_to_job_dicts` → `apply_rule_filters` → `score_and_filter`. Caches snapshot to `last_discovered.json`. `all_scored` holds full list. |
| Tailor | `agents/tailor.py` + `core/tailor.py` | Per job: research/company brief (cheap LLM) → write resume + cover + ATS audit (smart LLM), rewrite loop. |
| Humanizer | `agents/humanizer.py` | Strip AI filler, inject salutation/sign-off. |
| Packager | `agents/packager.py` | Attach file paths, requirements preview (regex, no LLM), **Copilot prebake** over `_STANDARD_BATTERY`, write `pending_review.csv` + `<folder>/copilot_data.json`. |
| Learner | `agents/learner.py` | Post-run ATS gap analysis, Q&A promotion. |

Orchestrated by `agents/orchestrator.py::Orchestrator.run()` — serial per-job
loop (lines ~154-201), sorts freshest-first, dedups by (title, company).
`dry_run`/`pause`/`headless` params are kept for API compat only (no browser, no submit).

## Core

| File | Job |
|---|---|
| `core/scraper.py` | JobSpy wrapper. `scrape(prefs, limit)` parallel over (term×location), `_scale_for_limit` speed tiers, `is_remote` passed straight to JobSpy. Within-run dedup only (`drop_duplicates`). **No geo-bounding** → remote jobs come back worldwide. |
| `core/filter.py` | `apply_rule_filters` = dedupe + **negative** blacklist only (no positive country check). `score_and_filter` = batched LLM fit scoring (BATCH_SIZE=25, serial loop ~line 280), adaptive threshold relax. |
| `core/copilot.py` | `answer_question()` (classify → qa cache → LLM → confidence + guardrail), `build_profile_text()` (feeds resume[:4000] + profile facts to LLM). |
| `core/qa_memory.py` | `QAStore` SQLite semantic cache (embeddings or Jaccard). `search/upsert/mark_preferred/list_for_job`. Legacy `learned_qa.json` helpers. |
| `core/tracker.py` | `load_applied_ids`, `record_pending`, `record_applied`, `daily_count`. |
| `core/parser.py` `core/pdf_generator.py` `core/text_clean.py` `core/showcase.py` | resume text extract, PDF render, deterministic humanize, GitHub showcase PDF. |

## Copilot subsystem (already exists — build on, don't rebuild)

- `agents/copilot.py::CopilotAgent` — `generate/regenerate/save/profile_text`, wraps `core.copilot` + `QAStore`.
- `core/copilot.py` — answer engine + `build_profile_text`.
- `webapp/routes/copilot.py` — `/api/copilot/answer|regenerate|save|job/<id>`. `_job_for_id` reads pending CSV then `last_discovered.json`. Note: `_job_for_id` from pending CSV sets `description: ""` (no JD context for live questions — Phase 5 gap).
- `core/qa_memory.py::QAStore` — persistence.
- `agents/packager.py::_prebake` — prebakes `_STANDARD_BATTERY` (10 Qs) per job at package time.
- `extension/` — Chrome content script (manifest + content.js).

## Web

| File | Job |
|---|---|
| `webapp/app.py` | Flask factory, blueprints, `/healthz` `/ping`, resume bootstrap from `RESUME_URL`. |
| `webapp/pipeline_runner.py` | `PipelineRunner` — one run at a time in a daemon thread, SSE event queue, builds Orchestrator. |
| `webapp/routes/pipeline.py` | `/api/pipeline/start|state|stop|stream|resume|errors`. Blocks start if profile incomplete. |
| `webapp/routes/api.py` | profile/personal/questions/preferences CRUD, applications (applied/pending/failed/discovered), env, health/keys, insights, gap-analysis. |
| `webapp/routes/files.py` `webapp/routes/history.py` | file serving, run history. |
| `webapp/templates/index.html` | SPA, 7 tabs. **Profile form drift**: missing inputs `years_of_experience`, `user_information_summary`, `headline`, `linkedin_summary` that `app.js` reads/writes; orphan `<textarea name="summary">`; no `sec-*` anchors or `req-dot`/`data-required` markers. |
| `webapp/static/app.js` | All client logic. `loadProfile/saveProfile`, `renderValidity`, `FIELD_VALIDATORS`, `jumpToField`, settings, SSE live tab. |

## Config

- `personal.yaml` — name, contact, address (blanked template; demographics/work_auth removed).
- `questions.yaml` — years_of_experience, user_information_summary, headline, summary, skills (years_python etc.); legacy auto-apply keys retained but unused.
- `preferences.yaml` — search_terms, locations, remote, sites, date_posted, blacklists, salary, fit_score. **No allowed_countries / remote_scope yet** (Phase 1).

## Known issues mapped to upgrade phases

1. **Location leak** — `scraper.py` sends `is_remote=True` (worldwide); `filter.py` has no positive country filter.
2. **Reposts** — dedup only within one run; only `applied_ids` persists across runs.
3. **No background search** — discovery only runs on explicit Run.
4. **Serial generation** — orchestrator per-job loop + filter score batches are serial.
5. **Copilot live context** — `_job_for_id` (pending) returns empty `description`.
6. **Profile form drift** — see template note above.
7. **Auto-apply remnants** — mostly removed already; verify no console errors.
8. **No pipeline/tracker view** — Discovered/Tailored/Applied/Interview.
