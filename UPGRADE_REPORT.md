# OneShot Upgrade Report

Self-hosted job-search **copilot** (not an auto-apply bot): scrape → score →
tailor resume + cover letter + ATS audit + prebaked Copilot answers → human
applies. Delivered as phased commits on `main`; each phase compiles
(`python -m compileall`) and passes `node --check`.

## Verification (Phase 9)

| Check | Result |
|---|---|
| `python -m compileall -q -x "venv\|_vendor\|graphify-out" .` | PY OK |
| `node --check` app.js / background.js / charts.js / extension/content.js | all OK |
| `from webapp.app import create_app` | import OK |
| `GET /healthz` `/` `/api/profile/validate` `/api/status` | all 200 |
| `GET /static/app.js` `/static/style.css` | 200 (no 404); referenced by `/` |

**Note on the live end-to-end pass:** a real discovery+generate run needs the
user's own API keys and live job-board access, which isn't available in this
build environment. The geo + freshness logic and the parallel speedup were
validated with deterministic checks instead (below). Please run one small live
pass (`Start Run`, limit 3–5) to confirm against real listings.

## Phase-by-phase changes

### Phase 0 — Map
- `MAP.md` — module reference + known-issue → phase mapping.

### Phase 1 — Location precision (no China/HK leak)
- `core/filter.py` — `apply_location_filter()` + `_detect_country()` (country
  aliases + US-state inference). Drops jobs whose detected country isn't
  allowed; keeps allowed + ambiguous "Remote". `remote_scope=worldwide` bypasses.
- `agents/discovery.py` — calls it after rule filter.
- `config/preferences.yaml` — `allowed_countries`, `allowed_regions`, `remote_scope`.
- `webapp/templates/index.html` + `app.js` — Search-tab Allowed-Countries input
  + Remote-Scope select (collect/load).
- **Evidence:** `['Acme','Gamma']` kept, `Hong Kong`/`Shenzhen, China` dropped on a US search.

### Phase 2 — Fresh data, no reposts
- `core/seen_store.py` — SQLite `outputs/seen_jobs.sqlite`, keyed on
  job_id / url / content-hash(title|company|location) / coarse(title|company).
- `agents/discovery.py` — `_drop_stale()` (date_posted window) + `_keep_fresh()`
  (drop seen + reposts, record rest, expose `new_count`). `fresh_only` toggle.
- `agents/orchestrator.py` — logs "X new since last run".
- UI — `fresh_only` checkbox.
- **Evidence:** run1 `new=2`; run2 (same jobs) `seen=2, new=0`; repost with a new
  id + changed location correctly classified as a repost.

### Phase 3 — Continuous background search
- `webapp/background.py` — `BackgroundDiscovery` daemon thread (no new deps),
  interval `DISCOVERY_INTERVAL_MIN` (default 60), skips while a full run is
  active, merges fresh matches into `last_discovered.json` via seen-store.
- `webapp/routes/pipeline.py` — `/api/pipeline/discovery/{start,stop,status}`.
- `webapp/app.py` — registers the service.
- UI — Search-tab control card with live status + 20 s polling.

### Phase 4 — Full upfront generation, but fast
- `agents/orchestrator.py` — per-job loop parallelized with `ThreadPoolExecutor`
  (`TAILOR_WORKERS`, default 4); CSV write lock; per-job SSE events
  (`on_event`); keeps stop-flag / `run_limit` / `TAILOR_TOP_N`; logs timings.
- `core/filter.py` — fit-score batches parallelized (`SCORE_WORKERS`, default 3).
- `agents/tailor.py` — ATS rewrite opt-in (`ATS_MAX_REWRITES` default **0**);
  company-About + research brief cached.
- `agents/profile.py` — GitHub enrichment cached (12 h TTL) + non-blocking.
- `core/cache.py` — keyed JSON cache under `outputs/cache/`.
- `webapp/pipeline_runner.py` — passes `on_event` to stream per-job completion.
- **Evidence (8 jobs × 2 s I/O-bound):** serial 16.0 s → 4 workers **4.0 s (4.0× faster)**.
  Everything is still generated for every matched job — only the wall-clock changed.

### Phase 5 — Copilot ready upfront, full context
- `core/copilot.py` — `build_profile_text()` adds per-skill years
  (`years_python` …) as source-of-truth; `answer_question()` passes
  `research_notes`.
- `llm/prompts.py` — `copilot_user_prompt()` adds a Company-Research section,
  wider JD excerpt (2500).
- `agents/packager.py` — prebake job dict includes `research_notes`; writes
  `job_context.json` (full JD + enriched + research) per job; expanded battery
  (fit, walk-through, strength/improve).
- `webapp/routes/copilot.py` — LIVE `/answer` loads `job_context.json` so live
  questions get the same context as the prebake (fixes the empty-description gap).

### Phase 6 — Profile template/JS drift
- `webapp/templates/index.html` — added the inputs `app.js` read/wrote but were
  missing: `years_of_experience` (required), `user_information_summary`
  (required), `headline`, `linkedin_summary`; removed the orphan
  `<textarea name=summary>`. Split into `sec-identity/address/links/experience/
  about` cards; City/Country badged required (State optional); `req-dot` +
  `data-required` markers.
- `webapp/static/app.js` — `jumpToField` country target fixed; focuses the field.
- `webapp/static/style.css` — textarea `.invalid`.
- **Evidence:** all four inputs + `data-field` markers present in rendered HTML;
  8 required fields → 100 % when filled.

### Phase 7 — Cleanup (not an auto-apply bot)
- Work-auth card, Skyvern settings, and ~10 dead profile JS fields were removed
  in the prior cleanup; this phase verified no stale references / broken Yes-No
  buttons remain and retitled the Search subtitle to a human-handoff framing.

### Phase 8 — Pipeline tracker + polish
- `webapp/templates/index.html` — Apply tab → **Pipeline** kanban
  (Discovered → Tailored → Applied → Interview).
- `webapp/static/app.js` — `loadPipeline()` builds the board from
  discovered/pending/applied endpoints; ATS + Copilot-ready + fit chips;
  loading and honest empty states. Ready-to-apply list kept below.
- `webapp/static/style.css` — `.pipeline-board` styles (vanilla CSS, responsive).

## New env vars (with defaults)

| Var | Default | Purpose |
|---|---|---|
| `DISCOVERY_INTERVAL_MIN` | 60 | Background discovery interval (min). |
| `TAILOR_WORKERS` | 4 | Parallel per-job tailoring workers. |
| `SCORE_WORKERS` | 3 | Parallel fit-score batch workers. |
| `ATS_MAX_REWRITES` | 0 | ATS rewrite passes (opt-in; was 1). |

New preferences keys: `allowed_countries`, `allowed_regions`, `remote_scope`
(country\|worldwide), `fresh_only`.

## How to run

```bash
python run.py                 # web UI (http://127.0.0.1:5001)
python run.py run --limit 5   # CLI: search + tailor 5 jobs (never submits)
```
1. Profile tab → upload resume → Extract to Profile → fill the 8 required fields.
2. Search tab → set Allowed Countries + Remote Scope → Save / Start Run, or hit
   **Start** on the background-search card.
3. Pipeline tab → watch Discovered → Tailored → Applied; open a ready job to use
   the Copilot (answers prebaked, full JD + research context).

## Remaining TODOs / honest gaps
- **Live validation pending:** run one real discovery+generate pass with your
  keys to confirm geo/freshness against live boards.
- **Interview column** has no data source yet (manual stage); needs a
  "mark interviewing" action to populate it.
- Per-job SSE events stream to the runner; the live UI still renders them as log
  lines — a richer per-card live update is a future polish.
- Background discovery shares the single-run lock with manual runs; it skips
  (rather than queues) when a run is active.
