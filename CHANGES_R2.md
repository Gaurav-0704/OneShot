# OneShot — Round 2 Changes

Single-provider engine + UX fixes. Each phase is its own commit on `main`;
every phase passes `python -m compileall -q .` and `node --check`.

## Phase 1 — Single-provider engine
- `llm/client.py` — rewritten. `complete()` / `complete_cheap()` resolve to the
  one `LLM_PROVIDER` (default `claude`); the two tiers are smart/cheap **models
  of the same provider**. Removed cross-provider auto-fallback
  (`_CHEAP_ORDER`/`_SMART_ORDER`, `_dispatch_with_fallback`) and
  `LLM_PROVIDER_SMART`/`LLM_PROVIDER_CHEAP`. On failure `_run()` raises
  `"Selected provider <X> failed: … Check the key in Settings or switch
  provider."` `LLM_MODEL`/`LLM_MODEL_CHEAP` honored only when the model belongs
  to the selected provider.
- `agents/tailor.py:387` — `_complete_with_fallback()` reduced to a single
  `complete()` call (no provider hopping).
- **Verified:** with `LLM_PROVIDER` = claude/openai/gemini the client resolves
  only that provider's smart+cheap models; a wrong-family `LLM_MODEL` is ignored.

## Phase 2 — Settings cleanup for single provider
- `webapp/templates/index.html` — removed the Smart-tier / Cheap-tier provider
  `<select>`s; one provider picker remains (sets `LLM_PROVIDER`) with an
  active-provider label + model overrides for the active provider. Added a
  **Remove** button beside each API-key field.
- `webapp/routes/api.py` — `/env` GET drops `llm_provider_smart/cheap`;
  `/providers` returns `active_provider`. New `DELETE /api/env`
  (`{"unset":[...]}`) clears a key from `.env` + live env.
- `webapp/static/app.js` — `loadEnv`/`saveSettings` no longer read/write the
  tier selectors; `clearKey()` calls `DELETE /api/env` and clears the input;
  Remove buttons wired; active-provider label set.

## Phase 3 — Run options defaults
- `webapp/templates/index.html:484-486` — "Research company before writing" and
  "Use cached discovery" now default **checked** (ATS rewrite loop already was).
  The run handler already posts `ats_check→run_ats_check`,
  `research→do_research`, `use_cache→use_cache`, so unchecking persists.

## Phase 4 — "Number of applications" + stop-when-saved
- `agents/orchestrator.py` — stop semantics changed from
  `(n_packaged + n_failed) >= run_limit` to **`n_packaged >= run_limit`** via a
  `reached` event that short-circuits remaining parallel workers; failures no
  longer consume the quota. Safety cap on attempts
  (`max(target*3, target+10)`, bounded by candidate count and `TAILOR_TOP_N`)
  prevents infinite loops.
- `webapp/templates/index.html` — count field relabelled "Number of
  applications to find & prepare" with a clarifying hint.

## Phase 5 — Start Run button at the bottom of Search
- `webapp/templates/index.html` — pinned bottom bar (count field + Start Run +
  Save) at the end of the Search Setup sub.
- `webapp/static/app.js` — bottom button copies its count into the form then
  triggers `#btn-run`; two-way count sync. `syncRunButtons()` +
  `setRunButtonsRunning()` keep every Start-Run button (top, card-head, bottom)
  in sync — disabled while a run is active and when the profile is incomplete.
  Corrected the Providers-stab wording to single-provider.

## Phase 6 — Move "Suggest from Resume"
- `webapp/templates/index.html` — `#btn-suggest-terms` + status span moved
  directly under the Job Titles / Search Terms field (same id/handler).

## Phase 7 — Show selected resume filename
- `webapp/static/app.js` — on file select, immediately show `name.pdf · NN KB`
  below the attach button (profile `#dz-name`, home `#home-dz-name`); after
  upload keep the user's original filename (not the renamed `master_resume.*`);
  reset to the default hint when the selection is cleared.

## Phase 8 — Pipeline tab: per-job result cards
- `webapp/templates/index.html` — Discovered/Tailored/Applied/Interview count
  widgets pinned at the top; "Prepared applications" card list below.
- `webapp/static/app.js` — `loadPipeline()` now just refreshes the four counts;
  `renderPendingTable()` cards gained **Resume** and **Cover letter** buttons
  that open the tailored PDFs via `/api/files/tailored/<slug>/<file>`, alongside
  the existing Open-job link and Copilot panel.
- `webapp/static/style.css` — `.pipe-stats` widget styles (kanban CSS removed).

## Phase 9 — Verify
- `compileall` clean; `node --check` clean on all JS; `create_app` imports.
- `/healthz`, `/`, `/api/env`, `/api/profile/validate`, `/api/status` → 200;
  `/` references `app.js` + `style.css`.
- Single-provider resolution confirmed for claude/gemini/openai (unit-level).
- README + `.env.example` updated for single-provider; `LLM_PROVIDER_SMART/CHEAP`
  documented as removed.

## Honest gaps / TODOs
- **Live calls not exercised** here (no API keys in this environment). The
  single-provider routing is verified at the unit level; please run one live
  pass per provider to confirm end-to-end.
- The **Interview** pipeline widget has no data source yet (always 0) — needs a
  "mark interviewing" action.
- `PROVIDER_<X>_ENABLED` toggles and the Providers stab still exist for the
  Key-Test feature; they no longer affect engine routing (which is purely
  `LLM_PROVIDER`).
