/* OneShot SPA — vanilla JS, no build step */

const $  = (s, p=document) => p.querySelector(s);
const $$ = (s, p=document) => Array.from(p.querySelectorAll(s));

// ── Tab / sub navigation ─────────────────────────────────────────────────

const TABS = ["home","search","apply","records","documents","profile","settings"];

// Backward-compat: old tab/sub names → new {tab, sub} pairs
const TAB_MAP = {
  // Legacy 4-tab names
  dashboard:  { tab: "search",   sub: "search"    },
  setup:      { tab: "settings", sub: null        },
  // Old sub names that moved
  live:       { tab: "search",   sub: "live"      },
  discovered: { tab: "search",   sub: "discovered"},
  errors:     { tab: "search",   sub: "errors"    },
  pending:    { tab: "apply",    sub: null        },
  documents:  { tab: "documents",sub: null        },
  applied:    { tab: "records",  sub: "applied"   },
  history:    { tab: "records",  sub: "history"   },
  profile:    { tab: "profile",  sub: null        },
  settings:   { tab: "settings", sub: null        },
  health:     { tab: "settings", sub: null        },  // settings sub-tab handled by stab system
  resume:     { tab: "search",   sub: "resume"    },
  // New 7-tab identity
  home:       { tab: "home",     sub: null        },
  search:     { tab: "search",   sub: "search"    },
  apply:      { tab: "apply",    sub: null        },
  records:    { tab: "records",  sub: "applied"   },
};

// Sub-sections within tabs that use .sub divs
const SUBS = {
  search:  ["search","live","discovered","resume","errors"],
  records: ["applied","history"],
};

// Track current sub per tab
const _activeSub = {};

// Track current records sub (for refresh button)
let currentRecordsSub = "applied";

function switchSub(tabName, subName) {
  _activeSub[tabName] = subName;
  if (tabName === "records") currentRecordsSub = subName;

  const subs = SUBS[tabName] || [];
  subs.forEach(s => {
    const el = $(`#sub-${s}`);
    if (el) { el.classList.toggle("active", s === subName); }
  });
  const subnav = $(`#subnav-${tabName}`);
  if (subnav) {
    subnav.querySelectorAll(".snav-item").forEach(b => {
      b.classList.toggle("active", b.dataset.sub === subName);
    });
  }
  // Lazy-load per sub
  if (subName === "applied")    loadApplications("applied");
  if (subName === "history")    loadHistory();
  if (subName === "discovered") loadDiscovered();
  if (subName === "resume")     loadResumeInfo();
  if (subName === "errors")     loadErrors();
  if (subName === "search")     { loadPreferences(); renderValidity(); loadGapAnalysis(); }
}

function switchTab(name) {
  const dest    = TAB_MAP[name] || { tab: name, sub: null };
  const tabName = TABS.includes(dest.tab) ? dest.tab : (TABS.includes(name) ? name : "home");
  const subName = dest.sub || (_activeSub[tabName] || (SUBS[tabName] || [])[0]);

  // Show/hide main tab sections
  TABS.forEach(t => {
    const el = $(`#tab-${t}`);
    if (el) el.hidden = (t !== tabName);
  });

  // Update sidebar nav active state
  $$(".nav-item[data-tab]").forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));

  // Switch sub if this tab has subs
  if (subName && SUBS[tabName]) switchSub(tabName, subName);

  // Per-tab refresh hooks
  if (tabName === "home")      loadHome();
  if (tabName === "apply")     loadApplications("pending");
  if (tabName === "documents") loadDocuments();
  if (tabName === "profile")   { loadProfile(); renderValidity(); loadShowcaseStatus(); }
  if (tabName === "settings")  { loadEnv(); bindSettingsHandlers(); loadHealth(); loadProviderToggles(); }
  if (tabName === "search" && subName === "search") { loadStatus(); loadInsights(); }
}

// Settings sub-tab (stab) switcher
document.addEventListener("click", e => {
  const btn = e.target.closest(".stab-item[data-stab]");
  if (!btn) return;
  const stabId = btn.dataset.stab;
  $$(".stab-item").forEach(b => b.classList.toggle("active", b.dataset.stab === stabId));
  $$(".stab").forEach(s => s.classList.toggle("active", s.id === `stab-${stabId}`));
  // Lazy loads
  if (stabId === "health")   { loadHealth(); loadProviderToggles(); }
  if (stabId === "errors-stab") loadSettingsErrors();
  if (stabId === "usage")    loadUsage();
});

// Subnav click (delegated)
document.addEventListener("click", e => {
  const btn = e.target.closest(".snav-item[data-sub]");
  if (!btn) return;
  const tabSection = btn.closest(".tab");
  if (!tabSection) return;
  const tabName = tabSection.id.replace("tab-", "");
  switchSub(tabName, btn.dataset.sub);
});

function bindJumps() {
  $$('[data-jump]').forEach(a => {
    if (a._bound) return;
    a._bound = true;
    a.addEventListener("click", e => {
      e.preventDefault();
      switchTab(a.dataset.jump);
    });
  });
}
bindJumps();

// ── Home tab ─────────────────────────────────────────────────────────────

let _homeViewLoaded = false;

async function loadHome() {
  // Greeting from /api/personal
  try {
    const p = await api.get("/api/personal");
    const name = (p.first_name || "").trim() || "there";
    const greetEl = $("#home-greeting-name");
    const heroEl  = $("#hero-greeting-name");
    if (greetEl) greetEl.textContent = name;
    if (heroEl)  heroEl.textContent  = name;
  } catch(_) {}

  // Restore view from localStorage
  const view = localStorage.getItem("oneshot_home_view") || "widgets";
  setHomeView(view, /* skipSave */ true);

  if (!_homeViewLoaded) {
    _homeViewLoaded = true;
    loadStatus();
    loadHomeCharts();
    loadInsights();
  }
}

function setHomeView(view, skipSave) {
  const plain   = $("#home-plain");
  const widgets = $("#home-widgets");
  const toggle  = $("#home-toggle-row");
  const btnP    = $("#vt-plain");
  const btnW    = $("#vt-widgets");

  if (view === "plain") {
    if (plain)   plain.style.display   = "";
    if (widgets) widgets.hidden = true;
    if (toggle)  toggle.hidden = true;
  } else {
    if (plain)   plain.style.display   = "none";
    if (widgets) widgets.hidden = false;
    if (toggle)  toggle.hidden = false;
  }
  if (btnP) btnP.classList.toggle("active", view === "plain");
  if (btnW) btnW.classList.toggle("active", view === "widgets");

  if (!skipSave) localStorage.setItem("oneshot_home_view", view);
}

async function loadHomeCharts() {
  try {
    const rows = await api.get("/api/applications/applied");
    if (window.renderAppliedChart) renderAppliedChart("chart-applied", rows);
    if (window.renderATSChart)     renderATSChart("chart-ats", rows);
    if (window.renderFitChart)     renderFitChart("chart-fit", rows);
    if (window.renderSourceChart)  renderSourceChart("chart-source", rows);
  } catch(e) { console.error("loadHomeCharts", e); }
}

// ── Sidebar completeness widget ──────────────────────────────────────────

async function updateSidebarCompleteness() {
  try {
    const d = await api.get("/api/profile/validate");
    const pct = Math.round(d.completeness_pct || 0);
    const widget = $("#completeness-widget");
    if (!widget) return;

    if (pct >= 100) { widget.hidden = true; return; }
    widget.hidden = false;

    const pctEl  = $("#completeness-pct");
    const barEl  = $("#progress-bar");
    const listEl = $("#cw-missing-list");
    if (pctEl) pctEl.textContent = pct + "%";
    if (barEl) barEl.style.width = pct + "%";

    if (listEl && d.missing_required) {
      const missing = d.missing_required.slice(0, 5);
      listEl.innerHTML = missing.map(m =>
        `<span class="cw-chip" data-jump="${m.field_path || 'profile'}">${escapeHtml(m.label || m.field)}</span>`
      ).join("");
      listEl.querySelectorAll(".cw-chip[data-jump]").forEach(c => {
        c.addEventListener("click", () => jumpToField(c.dataset.jump));
      });
    }
  } catch(_) {}
}

// ── Count-up animation ───────────────────────────────────────────────────

function countUp(el, target, duration) {
  if (!el) return;
  const start = parseInt(el.textContent) || 0;
  const n = parseInt(target) || 0;
  if (start === n) { el.textContent = n; return; }
  const steps = Math.min(30, Math.abs(n - start));
  let step = 0;
  const timer = setInterval(() => {
    step++;
    el.textContent = Math.round(start + (n - start) * (step / steps));
    if (step >= steps) { el.textContent = n; clearInterval(timer); }
  }, (duration || 600) / steps);
}

// ── Settings errors (stab) ───────────────────────────────────────────────

async function testKey() {
  const resEl1 = $("#test-key-result");
  const resEl2 = $("#test-key-result-2");
  [resEl1, resEl2].forEach(el => { if (el) el.textContent = "Testing…"; });
  try {
    const r = await api.post("/api/health/keys/test", {});
    const results = r.results || [];
    const ok  = results.filter(x => x.valid).length;
    const bad = results.filter(x => !x.valid).length;
    const msg = `${ok}/${results.length} valid`;
    [resEl1, resEl2].forEach(el => { if (el) { el.textContent = msg; el.className = bad ? "warn small" : "ok small"; } });
    toast(msg, bad ? "warn" : "ok");
    loadHealth();
  } catch (e) {
    [resEl1, resEl2].forEach(el => { if (el) { el.textContent = "Error"; el.className = "err small"; } });
    toast("Key test failed", "err");
  }
}

async function loadUsage() {
  const target = $("#usage-content");
  if (!target) return;
  target.innerHTML = `<div class="muted small">Loading…</div>`;
  try {
    const data = await api.get("/api/health/keys");
    const rows = Object.entries(data).map(([key, p]) => {
      if (!p || typeof p !== "object") return "";
      const today = p.calls_today || 0;
      const life  = p.calls_lifetime || 0;
      const cost  = p.cost_usd_today != null ? "$" + Number(p.cost_usd_today).toFixed(4) : "—";
      return `<tr>
        <td class="mono">${escapeHtml(key)}</td>
        <td class="num">${today}</td>
        <td class="num">${life}</td>
        <td class="num">${cost}</td>
      </tr>`;
    }).join("");
    target.innerHTML = `<div class="card no-lift"><div class="card-body">
      <table class="t"><thead><tr>
        <th>Provider</th><th>Calls today</th><th>Lifetime</th><th>Cost today</th>
      </tr></thead><tbody>${rows || `<tr><td colspan="4" class="muted small">No usage data yet.</td></tr>`}</tbody></table>
    </div></div>`;
  } catch(e) { if (target) target.innerHTML = `<div class="empty">Failed to load usage.</div>`; }
}

async function loadSettingsErrors() {
  const target = $("#errors-list-settings");
  if (!target) return;
  try {
    const d = await api.get("/api/pipeline/errors");
    if (!d.errors || !d.errors.length) {
      target.innerHTML = `<div class="empty"><div class="empty-icon">✅</div><div class="empty-title">No errors logged</div></div>`;
      return;
    }
    target.innerHTML = d.errors.map(err => `
      <div class="error-row">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <span class="pill warn">${escapeHtml(err.stage||"")}</span>
          <span class="muted small">${escapeHtml(err.job_title||"")} @ ${escapeHtml(err.company||"")}</span>
          <span class="muted small" style="margin-left:auto">${escapeHtml(err.timestamp||"").slice(0,16)}</span>
        </div>
        <div class="error-msg">${escapeHtml(err.message||"")}</div>
      </div>`).join("");
  } catch(e) { if (target) target.innerHTML = `<div class="empty">Failed to load errors.</div>`; }
}

// ── API helpers ──────────────────────────────────────────────────────────

const api = {
  // Cache-bust GETs so the browser doesn't serve stale JSON after a PUT
  get:  (u) => fetch(u + (u.includes("?") ? "&" : "?") + "_t=" + Date.now(),
                     { cache: "no-store" }).then(r => r.json()),
  put:  (u, body) => fetch(u, { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) }).then(r => r.json()),
  post: (u, body) => fetch(u, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body || {}) }).then(r => r.json()),
};

function toast(msg, kind="", durationMs=60000) {
  const el = $("#toast");
  el.className = "toast " + kind;
  el.innerHTML = "";
  const body = document.createElement("div");
  body.className = "toast-body";
  body.textContent = msg;
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.innerHTML = "&times;";
  close.onclick = () => { el.hidden = true; clearTimeout(toast._t); };
  el.appendChild(body);
  el.appendChild(close);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, durationMs);
}

// ── Dashboard / status ───────────────────────────────────────────────────

async function loadStatus() {
  try {
    const s = await api.get("/api/status");
    countUp($("#stat-applied-today"), s.applied_today, 600);
    countUp($("#stat-applied-life"),  s.applied_lifetime, 600);
    countUp($("#stat-pending-life"),  s.pending_lifetime, 600);
    countUp($("#stat-failed-life"),   s.failed_lifetime, 600);
    const runnerEl = $("#stat-runner"); if (runnerEl) runnerEl.textContent = s.runner_status;
    const runidEl  = $("#stat-runid");  if (runidEl)  runidEl.textContent  = s.run_id || "—";
    const caEl  = $("#count-applied");     if (caEl)  caEl.textContent  = s.applied_lifetime;
    const cpEl  = $("#count-pending");     if (cpEl)  cpEl.textContent  = s.pending_lifetime;
    const caEl2 = $("#count-applied-sub"); if (caEl2) caEl2.textContent = s.applied_lifetime;
    const cfEl  = $("#count-failed");      if (cfEl)  cfEl.textContent  = s.failed_lifetime;
    setRunnerDot(s.runner_status, s.runner_running);

    // Live badge visibility in sidebar
    const liveBadge = $("#nav-badge-live");
    if (liveBadge) liveBadge.hidden = !s.runner_running;
  } catch (e) { console.error(e); }
}

function setRunnerDot(status, running) {
  const dot = $("#status-dot");
  const txt = $("#status-text");
  dot.classList.remove("running","done","error");
  if (running) { dot.classList.add("running"); txt.textContent = "running"; }
  else if (status === "done")  { dot.classList.add("done");  txt.textContent = "idle (last: done)"; }
  else if (status === "error") { dot.classList.add("error"); txt.textContent = "idle (last: error)"; }
  else                          { txt.textContent = "idle"; }
}

async function loadEnv() {
  try {
    const e = await api.get("/api/env");

    // Sidebar footer status
    const provider = e.llm_provider + (e.llm_model ? ` · ${e.llm_model}` : "");
    const keyName  = e.llm_provider === "claude" ? "anthropic_key_set" :
                     e.llm_provider === "openai" ? "openai_key_set"     : "gemini_key_set";
    const keyOk    = e[keyName];
    const envEl    = $("#env-line");
    if (keyOk) {
      envEl.textContent = `${provider}  ✓ key`;
      envEl.style.color = "";
      envEl.style.cursor = "";
      envEl.onclick = null;
    } else {
      envEl.innerHTML = `⚠ No API key — <u style="cursor:pointer">add in Settings</u>`;
      envEl.style.color = "var(--warn, #f5a623)";
      envEl.style.cursor = "pointer";
      envEl.onclick = () => switchTab("settings");
    }

    // Settings form (only if it exists — settings tab may be hidden)
    const form = $("#settings-form");
    if (form) {
      // Provider buttons
      $$(".provider-btn").forEach(b => b.classList.toggle("active", b.dataset.provider === e.llm_provider));

      // Per-tier provider routing (blank = use default above)
      setVal(form, "LLM_PROVIDER_SMART", e.llm_provider_smart || "");
      setVal(form, "LLM_PROVIDER_CHEAP", e.llm_provider_cheap || "");
      setVal(form, "CLAUDE_BUDGET_USD",  e.claude_budget_usd  || "");
      setVal(form, "ATS_TARGET_MIN",     e.ats_target_min   || 80);
      setVal(form, "ATS_MAX_REWRITES",   e.ats_max_rewrites || 1);

      // Models
      setVal(form, "LLM_MODEL", e.llm_model || "");
      setVal(form, "LLM_MODEL_CHEAP", e.llm_model_cheap || "");

      // API keys — show masked tail if a key exists
      form.elements["ANTHROPIC_API_KEY"].placeholder = e.anthropic_key_mask || "sk-ant-…";
      form.elements["OPENAI_API_KEY"].placeholder    = e.openai_key_mask    || "sk-…";
      form.elements["GEMINI_API_KEY"].placeholder    = e.gemini_key_mask    || "AIza…";
      form.elements["ANTHROPIC_API_KEY"].value = "";
      form.elements["OPENAI_API_KEY"].value    = "";
      form.elements["GEMINI_API_KEY"].value    = "";
    }
  } catch (e) { console.error(e); }
}

// ── Settings: provider switcher + save ───────────────────────────────────

function bindProviderButtons() {
  $$(".provider-btn").forEach(b => {
    if (b._bound) return;
    b._bound = true;
    b.addEventListener("click", () => {
      $$(".provider-btn").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      // Auto-fill the model placeholders so the user knows what'll be used
      const f = $("#settings-form");
      const defaults = {
        claude: { smart: "claude-sonnet-4-6",       cheap: "claude-haiku-4-5-20251001" },
        openai: { smart: "gpt-4o",                  cheap: "gpt-4o-mini" },
        gemini: { smart: "gemini-2.5-pro",          cheap: "gemini-2.5-flash" },
      };
      const d = defaults[b.dataset.provider];
      if (f && d) {
        f.elements["LLM_MODEL"].placeholder = "auto: " + d.smart;
        f.elements["LLM_MODEL_CHEAP"].placeholder = "auto: " + d.cheap;
      }
    });
  });
}

async function saveSettings() {
  const form = $("#settings-form");
  const provider = ($(".provider-btn.active") || {}).dataset?.provider || "claude";

  const set = { LLM_PROVIDER: provider };
  const fields = [
    "LLM_MODEL","LLM_MODEL_CHEAP",
    "ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY",
  ];
  fields.forEach(k => {
    const v = (form.elements[k]?.value ?? "").toString();
    if (v) set[k] = v;
  });
  // Per-tier providers: always send (empty string clears the override)
  ["LLM_PROVIDER_SMART", "LLM_PROVIDER_CHEAP"].forEach(k => {
    set[k] = (form.elements[k]?.value ?? "").toString();
  });
  // Budget: always send so user can clear it by emptying the field
  set["CLAUDE_BUDGET_USD"] = (form.elements["CLAUDE_BUDGET_USD"]?.value ?? "").toString();
  // ATS rewrite tuning - always send so user can change them
  set["ATS_TARGET_MIN"]    = (form.elements["ATS_TARGET_MIN"]?.value ?? "80").toString();
  set["ATS_MAX_REWRITES"]  = (form.elements["ATS_MAX_REWRITES"]?.value ?? "1").toString();
  const res = await api.put("/api/env", { set });
  if (res.ok) {
    $("#settings-status").textContent = `Saved: ${res.written.join(", ") || "no changes"}`;
    toast("Settings saved", "ok");
    loadEnv();
  } else {
    toast("Save failed", "err");
  }
}

async function clearKey(name) {
  if (!confirm(`Clear ${name}?`)) return;
  await api.put("/api/env", { clear: [name] });
  toast(`${name} cleared`, "ok");
  loadEnv();
}

// Bind once, after DOM is ready (these elements only exist when settings tab loads)
function bindSettingsHandlers() {
  if (window._settingsBound) return;
  if (!$("#settings-form")) return;
  window._settingsBound = true;
  bindProviderButtons();
  const _on = (id, fn) => { const el = $("#" + id); if (el) el.addEventListener("click", fn); };
  _on("btn-save-settings",   saveSettings);
  _on("btn-save-settings2",  saveSettings);
  _on("btn-reload-settings", loadEnv);
  _on("btn-reload-settings2",loadEnv);
  _on("btn-test-key",        testKey);
  _on("btn-test-key-2",      testKey);
  $$("[data-clear]").forEach(b => b.addEventListener("click", () => clearKey(b.dataset.clear)));
}

// ── Profile ──────────────────────────────────────────────────────────────

async function loadProfile() {
  try {
    const personal = await api.get("/api/personal");
    const questions = await api.get("/api/questions");

    const form = $("#profile-form");
    // Identity
    setVal(form, "first_name", personal?.name?.first ?? "");
    setVal(form, "middle_name", personal?.name?.middle ?? "");
    setVal(form, "last_name",  personal?.name?.last ?? "");
    setVal(form, "email",      personal?.contact?.email ?? "");
    setVal(form, "phone",      personal?.contact?.phone ?? "");
    setVal(form, "linkedin_url",personal?.contact?.linkedin ?? "");
    setVal(form, "github_url", personal?.contact?.github ?? "");
    setVal(form, "website_url",personal?.contact?.website ?? "");
    // Address
    setVal(form, "city",       personal?.address?.city ?? "");
    setVal(form, "state",      personal?.address?.state ?? "");
    setVal(form, "zipcode",    personal?.address?.zipcode ?? "");
    setVal(form, "country",    personal?.address?.country ?? "");
    // Experience
    setVal(form, "years_of_experience", questions?.years_of_experience ?? "");
    // About
    setVal(form, "headline",            questions?.linkedin_headline ?? "");
    setVal(form, "linkedin_summary",    questions?.linkedin_summary ?? "");
    setVal(form, "user_information_summary", questions?.user_information_summary ?? "");

    // Resume status (file metadata, if any)
    refreshResumeStatus();

    // GitHub readout - shows pending state, then resolves async so a slow
    // GitHub API call doesn't block the rest of the form from rendering.
    const out = $("#github-readout");
    out.innerHTML = `<div class="muted small">Loading GitHub data ...</div>`;
    api.get("/api/profile").then(gh => {
      if (gh && gh.github_repos && gh.github_repos.length) {
        out.innerHTML = `<div class="muted small">Bio: ${escapeHtml(gh.github_bio || '-')}</div>
          <div class="muted small">Languages: ${(gh.github_languages || []).join(", ") || '-'}</div>
          ${gh.github_repos.slice(0,5).map(r => `
            <div class="gh-repo">
              <div class="name">${escapeHtml(r.name)} <span class="muted small">★${r.stars} · ${r.language || ''}</span></div>
              <div class="meta">${escapeHtml(r.description || '')}</div>
            </div>`).join("")}`;
      } else if (gh && gh.github_url) {
        out.innerHTML = `<div class="muted">Couldn't fetch GitHub repos. Check the URL or try Reload.</div>`;
      } else {
        out.innerHTML = `<div class="muted">No GitHub URL set yet. Add one in the Links section and Save.</div>`;
      }
    }).catch(() => {
      out.innerHTML = `<div class="muted">GitHub fetch failed. Try Reload.</div>`;
    });

    // Bind live-dot updates (once) and refresh
    bindLiveDotUpdates();
    renderValidity();
  } catch (e) { console.error(e); }
}

async function saveProfile() {
  const form = $("#profile-form");
  const personal = {
    name:    { first: getVal(form, "first_name"), middle: getVal(form, "middle_name"), last: getVal(form, "last_name") },
    contact: {
      email: getVal(form, "email"),
      phone: getVal(form, "phone"),
      linkedin: getVal(form, "linkedin_url"),
      github:   getVal(form, "github_url"),
      website:  getVal(form, "website_url"),
    },
    address: {
      city: getVal(form, "city"), state: getVal(form, "state"),
      zipcode: getVal(form, "zipcode"), country: getVal(form, "country"),
    },
  };
  // Merge into existing questions.yaml
  const q = await api.get("/api/questions");
  q.years_of_experience      = Number(getVal(form, "years_of_experience") || 0);
  q.linkedin_headline        = getVal(form, "headline");
  q.linkedin_summary         = getVal(form, "linkedin_summary");
  q.user_information_summary = getVal(form, "user_information_summary");

  await api.put("/api/personal", personal);
  await api.put("/api/questions", q);
  // Wait for validity refresh BEFORE showing the toast so the banner state
  // is correct everywhere the moment the user sees "Saved".
  await renderValidity();
  toast("Profile saved", "ok");
}

$("#btn-save-profile").addEventListener("click", saveProfile);
$("#btn-reload-profile").addEventListener("click", loadProfile);
const sp2 = $("#btn-save-profile2");   if (sp2)  sp2.addEventListener("click", saveProfile);
const rp2 = $("#btn-reload-profile2"); if (rp2)  rp2.addEventListener("click", loadProfile);

// ── Yes/No toggle buttons ────────────────────────────────────────────────

function renderYNGroups() {
  $$(".yn").forEach(group => {
    if (group._rendered) return;
    group._rendered = true;
    const name = group.dataset.name;
    group.innerHTML = `
      <button type="button" class="yes" data-val="Yes">Yes</button>
      <button type="button" class="no"  data-val="No">No</button>`;
    group.querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => setYN(name, b.dataset.val));
    });
  });
}
function setYN(name, val) {
  const group = $(`.yn[data-name="${name}"]`);
  if (!group) return;
  group._value = (val === true || val === "True" || val === "Yes") ? "Yes"
               : (val === false || val === "False" || val === "No") ? "No"
               : "";
  group.querySelectorAll("button").forEach(b => {
    b.classList.toggle("active", b.dataset.val === group._value);
  });
}
function getYN(name) {
  const group = $(`.yn[data-name="${name}"]`);
  return group ? (group._value || "") : "";
}

// ── Resume upload + parse ────────────────────────────────────────────────

function _setDzState(hasResume, filename, sizeKb) {
  // Update both the profile dropzone and the home dropzone
  const name   = $("#dz-name");
  const meta   = $("#dz-meta");
  const dz     = $("#dropzone");
  const hName  = $("#home-dz-name");
  const hMeta  = $("#home-dz-meta");
  const hDz    = $("#home-dropzone");

  if (hasResume) {
    const label = filename ? `✓ ${filename}` : "✓ Resume on file";
    const hint  = sizeKb   ? `${sizeKb} KB · click to replace` : "click to replace, or drag a new one";
    if (name) name.textContent = label;
    if (meta) meta.textContent = hint;
    if (dz)   dz.classList.add("ok-have");
    if (hName) hName.textContent = label;
    if (hMeta) hMeta.textContent = hint;
    if (hDz)  { hDz.style.borderColor = "var(--green,#4caf50)"; hDz.style.borderStyle = "solid"; }
  } else {
    if (name) name.textContent = "Drop PDF / DOCX here or click to browse";
    if (meta) meta.textContent = "PDF recommended · max 10 MB";
    if (dz)   dz.classList.remove("ok-have");
    if (hName) hName.textContent = "Upload your resume to get started";
    if (hMeta) hMeta.textContent = "PDF / DOCX · click or drag here";
    if (hDz)  { hDz.style.borderColor = ""; hDz.style.borderStyle = ""; }
  }
}

async function refreshResumeStatus() {
  try {
    const v = await api.get("/api/profile/validate");
    const hasResume = !v.missing_required.some(m => m.field === "master_resume");
    _setDzState(hasResume);
  } catch {}
}

async function uploadResume(file) {
  if (!file) return;
  // Show filename immediately — before the upload completes
  _setDzState(false);
  const nameEl = $("#dz-name"); const hNameEl = $("#home-dz-name");
  const metaEl = $("#dz-meta"); const hMetaEl = $("#home-dz-meta");
  const label = `⏳ Uploading ${file.name}…`;
  if (nameEl)  nameEl.textContent  = label;
  if (hNameEl) hNameEl.textContent = label;
  if (metaEl)  metaEl.textContent  = `${(file.size/1024).toFixed(0)} KB`;
  if (hMetaEl) hMetaEl.textContent = `${(file.size/1024).toFixed(0)} KB`;

  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/profile/upload-resume", { method: "POST", body: fd }).then(r => r.json());
  if (!res.ok) { toast("Upload failed: " + (res.error || ""), "err"); return; }

  _setDzState(true, res.saved_as, (res.size_bytes / 1024).toFixed(0));
  toast("Resume uploaded — auto-filling profile…", "ok");
  await parseResumeAndFill(false);
}

async function parseResumeAndFill(overwrite) {
  const statusEls = [$("#parse-status"), $("#home-parse-status")];
  statusEls.forEach(el => { if (el) el.textContent = "Reading resume with AI…"; });
  const res = await api.post("/api/profile/parse-resume", { overwrite });
  if (!res.ok) {
    statusEls.forEach(el => { if (el) el.textContent = ""; });
    const err = res.error || "";
    const msg = (err.includes("No usable LLM") || err.includes("No module"))
      ? "No API key set — go to Settings tab and add your Gemini or Claude key."
      : "Parse failed: " + err;
    toast(msg, "err");
    return;
  }
  const done = `✓ Filled ${res.written?.length || 0} fields`;
  statusEls.forEach(el => { if (el) el.textContent = done; });
  toast(`Profile auto-filled (${res.written?.length || 0} fields) — check the Profile tab`, "ok");
  await loadProfile();
}

$("#btn-parse-resume").addEventListener("click", () => parseResumeAndFill(false));
$("#btn-parse-resume-overwrite").addEventListener("click", () => {
  if (confirm("Force-overwrite existing values from the resume?")) parseResumeAndFill(true);
});

// Profile tab file input
$("#resume-file").addEventListener("change", e => {
  const f = e.target.files[0];
  if (f) uploadResume(f);
});

// Home tab file input
const homeFile = $("#home-resume-file");
if (homeFile) homeFile.addEventListener("change", e => {
  const f = e.target.files[0];
  if (f) uploadResume(f);
});

// Drag-and-drop for profile + home dropzones
const dz = $("#dropzone");
["dragenter","dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave","drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0];
  if (f) uploadResume(f);
});
const homeDz = $("#home-dropzone");
if (homeDz) {
  ["dragenter","dragover"].forEach(ev => homeDz.addEventListener(ev, e => { e.preventDefault(); homeDz.style.borderColor = "var(--accent)"; }));
  ["dragleave","drop"].forEach(ev => homeDz.addEventListener(ev, e => { e.preventDefault(); homeDz.style.borderColor = ""; }));
  homeDz.addEventListener("drop", e => {
    const f = e.dataTransfer.files[0];
    if (f) uploadResume(f);
  });
}

// ── Per-field live validators ────────────────────────────────────────────
// Each validator: { input | yn | dropzone, hint, check(value) -> bool }
// Live runs on every keystroke/change. Dot turns green when valid, red when not.

const FIELD_VALIDATORS = {
  "personal.name.first": {
    input: "first_name",
    hint: "Letters only - e.g. Misha",
    check: v => /^[A-Za-z][A-Za-z'\-\s.]{0,49}$/.test((v || "").trim()),
  },
  "personal.name.last": {
    input: "last_name",
    hint: "Letters only - e.g. Ramesh",
    check: v => /^[A-Za-z][A-Za-z'\-\s.]{0,49}$/.test((v || "").trim()),
  },
  "personal.contact.email": {
    input: "email",
    hint: "name@example.com",
    check: v => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((v || "").trim()),
  },
  "personal.contact.phone": {
    input: "phone",
    hint: "Digits, +/spaces ok - e.g. +1 555 123 4567",
    check: v => ((v || "").replace(/\D/g, "").length >= 7),
  },
  "personal.address.city": {
    input: "city",
    hint: "City name - e.g. Bengaluru",
    check: v => (v || "").trim().length >= 2,
  },
  "personal.address.country": {
    input: "country",
    hint: "Country - e.g. India",
    check: v => (v || "").trim().length >= 2,
  },
  "questions.years_of_experience": {
    input: "years_of_experience",
    hint: "Whole number 0-60 - e.g. 5",
    check: v => {
      const n = Number(v);
      return Number.isFinite(n) && Number.isInteger(n) && n >= 0 && n <= 60;
    },
  },
  "questions.user_information_summary": {
    input: "user_information_summary",
    hint: "3-5 sentences about you, at least 30 characters",
    check: v => (v || "").trim().length >= 30,
  },
  "master_resume": {
    dropzone: true,
    hint: "Upload PDF / DOCX / DOC / TXT / MD",
    // Cannot check from JS - relies on backend validate response.
    check: () => null,
  },
};

function _readFieldValue(spec) {
  const form = $("#profile-form");
  if (!form) return "";
  if (spec.input) {
    const el = form.elements.namedItem(spec.input);
    return el ? el.value : "";
  }
  if (spec.yn) return getYN(spec.yn);
  return "";
}

function updateOneDot(fieldPath, backendMissing) {
  const dot = $(`.req-dot[data-field="${fieldPath}"]`);
  if (!dot) return;
  const spec = FIELD_VALIDATORS[fieldPath];
  if (!spec) return;
  let ok;
  if (spec.dropzone) {
    // Resume: trust the backend (only it knows if a file is on disk)
    ok = !backendMissing;
  } else {
    ok = spec.check(_readFieldValue(spec));
  }
  dot.classList.toggle("ok", !!ok);
  dot.classList.toggle("bad", !ok);
}

function updateAllDots(backendMissingSet) {
  Object.keys(FIELD_VALIDATORS).forEach(path => {
    updateOneDot(path, backendMissingSet.has(path));
  });
}

function bindLiveDotUpdates() {
  if (window._dotsBound) return;
  const form = $("#profile-form");
  if (!form) return;
  window._dotsBound = true;
  // Watch every input/textarea/select for live revalidation
  form.addEventListener("input", () => updateAllDots(_lastMissing));
  form.addEventListener("change", () => updateAllDots(_lastMissing));
  // YN buttons fire click - hook their onclick after renderYNGroups runs
  $$(".yn[data-required]").forEach(g => {
    g.addEventListener("click", () => setTimeout(() => updateAllDots(_lastMissing), 10));
  });
}

let _lastMissing = new Set();   // updated by renderValidity

// ── Validity panel + run-button blocker ──────────────────────────────────

async function renderValidity() {
  const v = await api.get("/api/profile/validate");
  const pct = v.completeness_pct;

  // Sidebar completeness pct (legacy selector kept for compat; also update new search bar)
  const pctEl = $("#completeness-pct-search");
  if (pctEl) pctEl.textContent = pct;
  const barEl = $("#progress-bar-search");
  if (barEl) barEl.style.width = `${pct}%`;

  // Profile tab bar — hide entire card when 100% complete
  const profileCard = $("#profile-validity-card");
  if (profileCard) profileCard.hidden = (pct >= 100);
  const profilePct = $("#completeness-pct-profile");
  if (profilePct) profilePct.textContent = `${pct}%`;
  const profileBar = $("#progress-bar-profile");
  if (profileBar) profileBar.style.width = `${pct}%`;

  // Update sidebar widget
  updateSidebarCompleteness();

  // Mark required inputs / YN groups / dropzone invalid if blank
  const missingPaths = new Set((v.missing_required || []).map(m => m.field));
  _lastMissing = missingPaths;
  $$("[data-required]").forEach(el => {
    const path = el.dataset.required;
    el.classList.toggle("invalid", missingPaths.has(path));
  });
  // Update live green/red dots based on per-field validators (inputs)
  // and on backend signal (resume dropzone).
  updateAllDots(missingPaths);

  function renderMissingList(el) {
    if (!el) return;
    if (v.is_complete) {
      el.innerHTML = `<div class="muted small" style="color:var(--green)">✓ All required fields filled.</div>`;
    } else {
      el.innerHTML = v.missing_required.map(m =>
        `<span class="missing-chip req" data-field="${escapeHtml(m.field)}">${escapeHtml(m.label)}</span>`
      ).join("") + (v.missing_recommended && v.missing_recommended.length ? v.missing_recommended.map(m =>
        `<span class="missing-chip rec" data-field="${escapeHtml(m.field)}">+ ${escapeHtml(m.label)}</span>`
      ).join("") : "");
      el.querySelectorAll(".missing-chip").forEach(c => {
        c.addEventListener("click", () => jumpToField(c.dataset.field));
      });
    }
  }
  renderMissingList($("#missing-list"));
  renderMissingList($("#missing-list-profile"));

  // Run-button blocker
  const banner = $("#profile-incomplete-banner");
  const runBtn = $("#btn-run");
  if (banner) banner.hidden = v.is_complete;
  if ($("#banner-missing-summary")) {
    $("#banner-missing-summary").textContent = v.is_complete ? "" :
      `Missing: ${v.missing_required.map(m => m.label).join(", ")}`;
  }
  if (runBtn) {
    runBtn.disabled = !v.is_complete;
    runBtn.title = v.is_complete ? "" : "Complete your profile first";
    runBtn.style.opacity = v.is_complete ? "1" : "0.5";
    runBtn.style.cursor = v.is_complete ? "" : "not-allowed";
  }
}

function jumpToField(fieldPath) {
  switchTab("profile");
  const fieldToSection = {
    "personal.name.first":      "sec-identity",
    "personal.name.last":       "sec-identity",
    "personal.contact.email":   "sec-identity",
    "personal.contact.phone":   "sec-identity",
    "personal.contact.linkedin":"sec-links",
    "personal.contact.github":  "sec-links",
    "personal.address.city":    "sec-address",
    "personal.address.country": "sec-identity",
    "questions.years_of_experience":  "sec-experience",
    "questions.linkedin_headline":     "sec-about",
    "questions.user_information_summary": "sec-about",
    "master_resume":                   "sec-resume",
  };
  const secId = fieldToSection[fieldPath] || "sec-identity";
  setTimeout(() => {
    const sec = $(`#${secId}`);
    if (sec) {
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
      const card = sec.closest(".card");
      if (card) {
        card.classList.remove("flash");
        void card.offsetWidth;
        card.classList.add("flash");
      }
      // Also focus the matching input if any
      const input = $(`[data-required="${fieldPath}"]`);
      if (input) setTimeout(() => input.focus(), 400);
    }
  }, 50);
}

function setVal(form, name, value) {
  const el = form.elements.namedItem(name);
  if (!el) return;
  if (el.type === "checkbox") el.checked = !!value;
  else el.value = value ?? "";
}
function getVal(form, name) {
  const el = form.elements.namedItem(name);
  if (!el) return "";
  if (el.type === "checkbox") return el.checked;
  return el.value;
}

// ── Date-posted pill picker ──────────────────────────────────────────────

function setDatePosted(val) {
  const f = $("#search-form");
  if (!f) return;
  setVal(f, "date_posted", val);
  $$("#date-posted-pills .pill").forEach(p => {
    p.classList.toggle("active", p.dataset.val === val);
  });
}

(function bindDatePills() {
  document.addEventListener("click", e => {
    const pill = e.target.closest("#date-posted-pills .pill");
    if (!pill) return;
    setDatePosted(pill.dataset.val);
  });
})();

// ── Preferences (Search & Run) ───────────────────────────────────────────

async function loadPreferences() {
  try {
    const p = await api.get("/api/preferences");
    const f = $("#search-form");
    setVal(f, "search_terms", (p.search_terms || []).join("\n"));
    setVal(f, "locations",    (p.locations || []).join("\n"));
    setVal(f, "allowed_countries", (p.allowed_countries || []).join(", "));
    setVal(f, "remote_scope", p.remote_scope || "country");
    if (f.elements["fresh_only"]) f.elements["fresh_only"].checked = (p.fresh_only !== false);
    f.elements["remote"].checked = !!p.remote;
    f.elements["hybrid"].checked = !!p.hybrid;
    f.elements["onsite"].checked = !!p.onsite;
    setVal(f, "experience_level", p.experience_level || "mid_senior_level");
    const jts = new Set(p.job_types || ["fulltime"]);
    f.elements["job_fulltime"].checked   = jts.has("fulltime");
    f.elements["job_parttime"].checked   = jts.has("parttime");
    f.elements["job_contract"].checked   = jts.has("contract");
    f.elements["job_internship"].checked = jts.has("internship");
    f.elements["job_temporary"].checked  = jts.has("temporary");
    setDatePosted(p.date_posted || "week");
    setVal(f, "distance_miles", p.distance_miles || 50);
    setVal(f, "results_per_site", p.results_per_site || 25);
    const sites = new Set(p.sites || []);
    f.elements["site_linkedin"].checked  = sites.has("linkedin");
    f.elements["site_indeed"].checked    = sites.has("indeed");
    f.elements["site_glassdoor"].checked = sites.has("glassdoor");
    f.elements["site_zip"].checked       = sites.has("zip_recruiter");
    f.elements["site_google"].checked    = sites.has("google");
    setVal(f, "title_blacklist",   (p.blacklists?.title_keywords || []).join(", "));
    setVal(f, "company_blacklist", (p.blacklists?.companies || []).join(", "));
    setVal(f, "desc_blacklist",    (p.blacklists?.description_keywords || []).join(", "));
    setVal(f, "min_salary",        p.salary?.minimum_usd_annual || 0);
    f.elements["fit_enabled"].checked = (p.fit_score?.enabled !== false);
    setVal(f, "min_fit_score",     p.fit_score?.min_score || 6);
  } catch (e) { console.error(e); }
}

function collectPreferences() {
  const f = $("#search-form");
  const csv = (n) => getVal(f, n).split(",").map(s => s.trim()).filter(Boolean);
  const lines = (n) => getVal(f, n).split("\n").map(s => s.trim()).filter(Boolean);

  const job_types = [];
  if (f.elements["job_fulltime"].checked)   job_types.push("fulltime");
  if (f.elements["job_parttime"].checked)   job_types.push("parttime");
  if (f.elements["job_contract"].checked)   job_types.push("contract");
  if (f.elements["job_internship"].checked) job_types.push("internship");
  if (f.elements["job_temporary"].checked)  job_types.push("temporary");

  const sites = [];
  if (f.elements["site_linkedin"].checked)  sites.push("linkedin");
  if (f.elements["site_indeed"].checked)    sites.push("indeed");
  if (f.elements["site_glassdoor"].checked) sites.push("glassdoor");
  if (f.elements["site_zip"].checked)       sites.push("zip_recruiter");
  if (f.elements["site_google"].checked)    sites.push("google");

  return {
    search_terms: lines("search_terms"),
    locations:    lines("locations"),
    allowed_countries: csv("allowed_countries"),
    allowed_regions:   [],
    remote_scope: getVal(f, "remote_scope") || "country",
    fresh_only: f.elements["fresh_only"] ? f.elements["fresh_only"].checked : true,
    remote: f.elements["remote"].checked,
    hybrid: f.elements["hybrid"].checked,
    onsite: f.elements["onsite"].checked,
    experience_level: getVal(f, "experience_level"),
    job_types,
    date_posted: getVal(f, "date_posted"),
    distance_miles: Number(getVal(f, "distance_miles") || 50),
    results_per_site: Number(getVal(f, "results_per_site") || 25),
    sites,
    country_indeed: "usa",
    blacklists: {
      title_keywords: csv("title_blacklist"),
      companies:      csv("company_blacklist"),
      description_keywords: csv("desc_blacklist"),
      locations: [],
    },
    salary: {
      minimum_usd_annual: Number(getVal(f, "min_salary") || 0),
      desired_usd_annual: Number(getVal(f, "min_salary") || 0),
    },
    fit_score: {
      enabled: f.elements["fit_enabled"].checked,
      min_score: Number(getVal(f, "min_fit_score") || 6),
    },
  };
}

$("#btn-save-search").addEventListener("click", async () => {
  await api.put("/api/preferences", collectPreferences());
  toast("Filters saved", "ok");
});

// ── Background discovery (Phase 3) ───────────────────────────────────────────
function renderBgDiscovery(s) {
  const el = $("#bg-discovery-status");
  if (!el || !s) return;
  if (s.enabled && s.running) {
    const nxt = s.next_run_in_s != null ? ` · next pass in ${Math.ceil(s.next_run_in_s / 60)} min` : "";
    const last = s.last_run_at ? ` · last: ${s.last_new_count ?? 0} new` : "";
    el.innerHTML = `<span style="color:var(--green)">● On</span> — every ${s.interval_min} min${nxt}${last}`;
  } else {
    el.textContent = "Off — runs discovery on a timer so fresh matches appear on their own.";
  }
}
async function refreshBgDiscovery() {
  try { renderBgDiscovery(await api.get("/api/pipeline/discovery/status")); } catch (e) {}
}
{
  const start = $("#btn-bg-start"), stop = $("#btn-bg-stop");
  if (start) start.addEventListener("click", async () => {
    await api.put("/api/preferences", collectPreferences());   // use current filters
    const interval_min = Number(getVal2("#bg-interval-min") || 60);
    renderBgDiscovery(await api.post("/api/pipeline/discovery/start", { interval_min }));
    toast("Background search started", "ok");
  });
  if (stop) stop.addEventListener("click", async () => {
    renderBgDiscovery(await api.post("/api/pipeline/discovery/stop", {}));
    toast("Background search stopped", "ok");
  });
  setInterval(refreshBgDiscovery, 20000);
  refreshBgDiscovery();
}
function getVal2(sel) { const el = $(sel); return el ? el.value : ""; }

// Auto-suggest search terms from the master resume (one cheap LLM call).
// Pre-fills the textarea + experience-level dropdown so the user starts
// with role-appropriate queries instead of generic "Software Engineer".
const btnSuggestTerms = $("#btn-suggest-terms");
if (btnSuggestTerms) {
  btnSuggestTerms.addEventListener("click", async () => {
    const status = $("#suggest-terms-status");
    btnSuggestTerms.disabled = true;
    btnSuggestTerms.textContent = "Reading resume…";
    status.textContent = "";
    try {
      const res = await api.post("/api/profile/suggest-search-terms", {});
      if (!res.ok) {
        toast("Suggest failed: " + (res.error || "unknown"), "err");
        status.textContent = "× " + (res.error || "failed");
        return;
      }
      const f = $("#search-form");
      const terms = res.search_terms || [];
      const replace = (!f.elements["search_terms"].value.trim()) || confirm(
        `Replace your current search terms with these ${terms.length} suggestions?\n\n` +
        terms.map(t => "• " + t).join("\n")
      );
      if (replace) {
        f.elements["search_terms"].value = terms.join("\n");
        if (res.experience_level) {
          const dd = f.elements["experience_level"];
          const has = Array.from(dd.options).some(o => o.value === res.experience_level);
          if (has) dd.value = res.experience_level;
        }
        toast(`Filled ${terms.length} search terms`, "ok");
      }
      status.textContent = res.rationale ? "✓ " + res.rationale : `✓ suggested ${terms.length} terms`;
    } catch (e) {
      toast("Suggest failed: " + e, "err");
      status.textContent = "× " + e;
    } finally {
      btnSuggestTerms.disabled = false;
      btnSuggestTerms.textContent = "✨ Auto-suggest from my resume";
    }
  });
}

$("#btn-run").addEventListener("click", async () => {
  await api.put("/api/preferences", collectPreferences());
  const f = $("#search-form");
  const opts = {
    use_cache:     f.elements["use_cache"].checked,
    do_research:   f.elements["research"].checked,
    run_ats_check: f.elements["ats_check"].checked,
    limit:         Number(getVal(f, "limit") || 0) || null,
    min_ats:             Number(getVal(f, "min_ats") || 0),
    score_jobs:          f.elements["fit_enabled"].checked,
  };
  const res = await fetch("/api/pipeline/start", {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(opts)
  });
  const json = await res.json();
  if (!res.ok && json.error === "profile_incomplete") {
    toast(`Profile incomplete (${json.completeness_pct}%) — go fill the missing fields`, "err");
    switchTab("profile");
    renderValidity();
    return;
  }
  if (json.already_running) toast("Pipeline already running", "");
  else toast(`Pipeline started — run ${json.run_id}`, "ok");
  switchTab("live");
  startEventStream();
});

// ── Live log via SSE ─────────────────────────────────────────────────────

let _es = null;

function startEventStream() {
  if (_es) { try { _es.close(); } catch {} }
  appendLog({type:"stage", agent:"client", msg:"connecting…"});
  _es = new EventSource("/api/pipeline/stream");
  _es.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data);
      if (ev.type === "ping") return;
      appendLog(ev);
      if (ev.type === "done" || ev.type === "end" || ev.type === "error") {
        setRunnerDot(ev.status || (ev.type === "error" ? "error" : "done"), false);
        setStopButtonVisible(false);
        loadStatus();
        // Auto-load related tabs after a run finishes
        loadDiscovered(); loadDocuments();
        loadApplications("applied"); loadApplications("pending");
        loadErrors();
      } else {
        setRunnerDot("running", true);
        setStopButtonVisible(true);
        $("#nav-badge-live").hidden = false;
      }
    } catch {}
  };
  _es.onerror = () => {
    appendLog({type:"log", level:"WARN", agent:"client", msg:"stream disconnected"});
    try { _es.close(); } catch {}
    _es = null;
  };
}

function appendLog(ev) {
  const log = $("#log");
  const t = (ev.ts || "").slice(11,19);
  let line = `<span class="log-time">${t}</span>`;
  if (ev.type === "stage") {
    line += `<span class="log-agent">[${ev.agent || 'orch'}]</span><span class="log-stage">${escapeHtml(ev.msg || '')}</span>`;
  } else if (ev.type === "done" || ev.type === "end") {
    line += `<span class="log-agent">[done]</span><span class="log-stage">${escapeHtml(JSON.stringify(ev.summary || ev))}</span>`;
  } else if (ev.type === "error") {
    line += `<span class="log-agent">[error]</span><span class="log-err">${escapeHtml(ev.error || ev.msg || '')}</span>`;
  } else {
    const cls = ev.level === "WARNING" ? "log-warn" : ev.level === "ERROR" ? "log-err" : "log-info";
    line += `<span class="log-agent">[${ev.agent || 'log'}]</span><span class="${cls}">${escapeHtml(ev.msg || '')}</span>`;
  }
  const div = document.createElement("div");
  div.className = "log-line";
  div.innerHTML = line;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

$("#btn-clear-log").addEventListener("click", () => $("#log").innerHTML = "");

// ── Stop pipeline button ─────────────────────────────────────────────────

const stopBtn = $("#btn-stop-pipeline");
if (stopBtn) {
  stopBtn.addEventListener("click", async () => {
    if (!confirm("Stop the running pipeline? It will exit at the next safe checkpoint.")) return;
    stopBtn.disabled = true;
    stopBtn.textContent = "■ Stopping…";
    try {
      const r = await api.post("/api/pipeline/stop");
      if (r.ok && r.running) {
        toast("Stop requested. Pipeline will exit at the next checkpoint.", "warn");
      } else {
        toast("No run in progress.", "");
      }
    } catch (e) {
      toast("Stop failed: " + e, "err");
    } finally {
      setTimeout(() => { stopBtn.disabled = false; stopBtn.textContent = "■ Stop pipeline"; }, 5000);
    }
  });
}

function setStopButtonVisible(visible) {
  if (stopBtn) stopBtn.hidden = !visible;
}

// ── Errors tab ───────────────────────────────────────────────────────────

async function loadErrors() {
  const data = await api.get("/api/pipeline/errors");
  $("#count-errors").textContent = data.count;
  const target = $("#errors-list");
  if (!data.errors.length) {
    target.innerHTML = `<div class="empty">No errors captured this session.</div>`;
    return;
  }
  // Newest first
  const rows = data.errors.slice().reverse();
  target.innerHTML = rows.map((e, i) => `
    <div class="error-row" data-idx="${i}">
      <div class="row" style="justify-content: space-between; gap: 12px">
        <div>
          <span class="pill ${e.level === 'ERROR' ? 'err' : 'warn'}">${escapeHtml(e.level)}</span>
          <span class="muted small">${escapeHtml((e.ts || '').replace('T', ' '))}</span>
          <span class="pill acc">${escapeHtml(e.agent || '')}</span>
        </div>
        <button type="button" class="btn tiny" data-act="copy" data-idx="${i}">Copy summary</button>
      </div>
      <div class="error-msg">${escapeHtml(e.msg || '')}</div>
    </div>
  `).join("");
  target.querySelectorAll("button[data-act='copy']").forEach(b => {
    b.addEventListener("click", () => copyErrorSummary(rows[Number(b.dataset.idx)]));
  });
}


function copyErrorSummary(e) {
  const text = [
    "OneShot pipeline error",
    `Time:  ${e.ts}`,
    `Level: ${e.level}`,
    `Agent: ${e.agent}`,
    "",
    "Message:",
    e.msg || "",
  ].join("\n");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast("Error summary copied to clipboard.", "ok"),
      () => fallbackCopy(text),
    );
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); toast("Error summary copied.", "ok"); }
  catch (_) { toast("Could not copy. Select the text manually.", "err"); }
  document.body.removeChild(ta);
}

async function copyAllErrors() {
  // Pull a fresh batch so the user always copies what they SEE on the tab
  let data;
  try {
    data = await api.get("/api/pipeline/errors");
  } catch (e) {
    toast("Couldn't load errors: " + e, "err");
    return;
  }
  if (!data.errors || !data.errors.length) {
    toast("No errors to copy.", "");
    return;
  }
  const lines = [];
  lines.push("OneShot — full error log");
  lines.push("Captured: " + new Date().toISOString());
  lines.push("Total: " + data.errors.length + " entries");
  lines.push("=".repeat(70));
  lines.push("");
  // Newest first matches the on-screen order
  data.errors.slice().reverse().forEach((e, i) => {
    lines.push(`#${i + 1}  [${e.level || ''}] ${(e.ts || '').replace('T', ' ')}  (${e.agent || ''})`);
    lines.push(e.msg || "");
    lines.push("-".repeat(70));
  });
  const text = lines.join("\n");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast(`Copied ${data.errors.length} error entries to clipboard.`, "ok"),
      () => fallbackCopy(text),
    );
  } else {
    fallbackCopy(text);
  }
}

const copyAllErrBtn = $("#btn-copy-all-errors");
if (copyAllErrBtn) copyAllErrBtn.addEventListener("click", copyAllErrors);

const refreshErrBtn = $("#btn-refresh-errors");
if (refreshErrBtn) refreshErrBtn.addEventListener("click", loadErrors);

const clearErrBtn = $("#btn-clear-errors");
if (clearErrBtn) clearErrBtn.addEventListener("click", async () => {
  if (!confirm("Clear all captured errors?")) return;
  await api.post("/api/pipeline/errors/clear");
  loadErrors();
  toast("Errors cleared.", "ok");
});

// ── Tables: applied / pending / failed ───────────────────────────────────

async function loadApplications(kind) {
  const target = $(`#${kind}-table`);
  if (!target) return;
  const data = await api.get(`/api/applications/${kind}`);
  if (!data.rows.length) {
    target.innerHTML = `<div class="empty">No ${kind} applications yet.</div>`;
    return;
  }
  if (kind === "pending") {
    target.innerHTML = renderPendingTable(data.rows);
    // Bind all action buttons; pass optional data-path and data-job-id
    target.querySelectorAll("button[data-act]").forEach(b => {
      b.addEventListener("click", () =>
        handlePendingAction(b.dataset.act, Number(b.dataset.idx), b.dataset.path || "", b.dataset.jobId || "")
      );
    });
  } else {
    target.innerHTML = renderAppliedTable(data.rows);
  }
}

function renderAppliedTable(rows) {
  return `<table class="t">
    <thead><tr>
      <th>Date</th><th>Site</th><th>Company</th><th>Title</th>
      <th>Fit</th><th>ATS</th><th>Q</th><th>Files</th><th></th>
    </tr></thead>
    <tbody>${rows.map(r => `
      <tr>
        <td class="num small">${(r.applied_at || '').replace('T',' ').slice(0,16)}</td>
        <td><span class="pill acc">${escapeHtml(r.site || r.applier || '')}</span></td>
        <td>${escapeHtml(r.company || '')}</td>
        <td>${escapeHtml(r.title || '')}</td>
        <td class="num">${r.fit_score || '—'}</td>
        <td class="num">${r.ats_score || '—'}</td>
        <td class="num">${r.questions_answered || 0}</td>
        <td class="small muted">${escapeHtml((r.files_attached || '').slice(0,40))}</td>
        <td>${r.url ? `<a href="${escapeHtml(r.url)}" target="_blank">↗</a>` : ''}</td>
      </tr>`).join("")}</tbody>
  </table>`;
}

// (stale table-based renderPendingTable removed — card version below is active)

async function handlePendingAction(act, idx, path, jobId) {
  if (act === "mark") {
    const r = await api.post(`/api/applications/${idx}/mark-submitted`);
    if (r.ok) toast("Marked as applied", "ok");
    loadApplications("pending");
    loadApplications("applied");
    loadStatus();
  } else if (act === "dismiss") {
    if (!confirm("Remove this application from the Ready list?")) return;
    const r = await api.post(`/api/applications/${idx}/dismiss`);
    if (r.ok) toast("Dismissed", "ok");
    loadApplications("pending");
    loadStatus();
  } else if (act === "open-folder") {
    if (!path) return;
    api.post("/api/files/open-folder", {path}).catch(() => {});
  } else if (act === "copy-path") {
    if (!path) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path)
        .then(() => toast("Path copied to clipboard", "ok"))
        .catch(() => fallbackCopy(path));
    } else {
      fallbackCopy(path);
    }
  } else if (act === "toggle-copilot") {
    copilotTogglePanel(jobId);
  } else if (act === "copilot-ask") {
    copilotAsk(jobId, false);
  } else if (act === "copilot-regen") {
    copilotAsk(jobId, true);
  }
}

// ── Resume gap analysis (Dashboard card) ────────────────────────────────
// Pulls aggregated ATS-missing-keyword counts across every per-job audit
// on disk. Hides itself if no audits have been written yet.

async function loadGapAnalysis() {
  const card = $("#gap-card");
  if (!card) return;
  try {
    const data = await api.get("/api/profile/gap-analysis");
    if (!data.count || !data.keywords?.length) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    $("#gap-meta").textContent =
      `${data.count} job audit${data.count === 1 ? "" : "s"}` +
      (data.avg_ats_score != null ? ` · avg ATS ${data.avg_ats_score}/100` : "");
    // Size each pill by frequency so the eye lands on the most-missed words
    const max = Math.max(...data.keywords.map(k => k.count));
    $("#gap-keywords").innerHTML = data.keywords.map(k => {
      const weight = 0.85 + (k.count / max) * 0.4;   // 0.85x → 1.25x font size
      return `<span class="gap-pill" style="font-size:${(weight * 13).toFixed(1)}px"
                title="appeared in ${k.count} job audit${k.count === 1 ? '' : 's'}">
                ${escapeHtml(k.keyword)}<span class="gap-count">${k.count}</span>
              </span>`;
    }).join("");
    $("#gap-advice").innerHTML = (data.advice || []).slice(0, 3)
      .map(a => `<div>· ${escapeHtml(a)}</div>`).join("");
  } catch (e) {
    console.warn("gap analysis load failed", e);
    card.hidden = true;
  }
}

// ── Discovered + documents ──────────────────────────────────────────────

async function loadDiscovered() {
  const data = await api.get("/api/applications/discovered");
  $("#count-discovered").textContent = data.count;
  const target = $("#discovered-table");
  if (!data.rows.length) {
    target.innerHTML = `
      <div class="empty">
        <div style="font-size:15px; margin-bottom:10px"><b>No discoveries yet.</b></div>
        <div class="muted small" style="margin-bottom:14px">
          Run the pipeline to scrape jobs and score them against your resume.
          If you've already run it and still see nothing, your search terms
          are probably too narrow or too generic for your background.
        </div>
        <div class="row" style="gap:8px; justify-content:center">
          <a class="btn primary" data-jump="search">Go to Search & Run →</a>
          <button type="button" class="btn ghost" id="btn-empty-suggest">✨ Auto-suggest from resume</button>
        </div>
      </div>`;
    target.querySelectorAll('[data-jump]').forEach(a => a.addEventListener("click", e => { e.preventDefault(); switchTab(a.dataset.jump); }));
    const sg = target.querySelector("#btn-empty-suggest");
    if (sg) sg.addEventListener("click", () => {
      switchTab("search");
      setTimeout(() => $("#btn-suggest-terms")?.click(), 200);
    });
    return;
  }
  const above = data.rows.filter(r => r.above_threshold).length;
  const minScore = (data.rows[0] && data.rows[0].min_score) || 6;
  const relaxedTo = data.rows.find(r => r.fit_threshold_relaxed_to)?.fit_threshold_relaxed_to;
  const relaxedBanner = relaxedTo ? `
    <div class="banner warn" style="margin-bottom:10px">
      <div class="banner-icon">ⓘ</div>
      <div class="banner-body">
        <div><b>No jobs hit your ${minScore}/10 threshold</b> — automatically relaxed to ${relaxedTo}/10 so you'd see something.</div>
        <div class="muted small">Widen your search terms or lower the threshold in <a class="link" data-jump="search">Search & Run</a> for cleaner results.</div>
      </div>
    </div>` : "";
  const summary = `${relaxedBanner}<div class="muted small" style="margin-bottom:10px">
    ${data.rows.length} jobs scored ·
    <span style="color:var(--ok); font-weight:600">${above} above match threshold (≥${minScore * 10}%)</span> ·
    rest shown for transparency
  </div>`;
  target.innerHTML = summary + `<table class="t">
    <thead><tr>
      <th>Match</th><th>Site</th><th>Company</th><th>Title</th>
      <th>Location</th><th>Salary</th><th>Why</th><th></th>
    </tr></thead>
    <tbody>${data.rows.map(r => {
      const pct = (r.match_pct ?? (r.fit_score ? r.fit_score * 10 : null));
      const pctStr = pct == null ? '—' : `${pct}%`;
      const above = r.above_threshold;
      const matchCls = above ? 'ok' : 'warn';
      const salTxt = r.min_salary ? formatSalary(r.min_salary, r.max_salary) : '';
      const salBadge =
        r.salary_meets_minimum === true  ? `<span class="pill ok" title="Above your minimum">✓ ${salTxt}</span>` :
        r.salary_meets_minimum === false ? `<span class="pill warn" title="Below your minimum salary">⚠ ${salTxt}</span>` :
        `<span class="num small muted">${salTxt}</span>`;
      return `
      <tr style="${above ? '' : 'opacity:.7'}">
        <td><span class="pill ${matchCls}">${pctStr}</span></td>
        <td><span class="pill acc">${escapeHtml(r.site || '')}</span></td>
        <td>${escapeHtml(r.company || '')}</td>
        <td>${escapeHtml(r.title || '')}</td>
        <td class="small muted">${escapeHtml(r.location || '')}${r.is_remote ? " · remote" : ""}</td>
        <td class="num small">${salBadge}</td>
        <td class="small muted" style="max-width:280px">${escapeHtml((r.fit_reason || '').slice(0, 90))}</td>
        <td>${r.url ? `<a href="${escapeHtml(r.url)}" target="_blank">↗</a>` : ''}</td>
      </tr>`;}).join("")}</tbody>
  </table>`;
  // Re-bind the relaxed-banner jump
  target.querySelectorAll('[data-jump]').forEach(a => a.addEventListener("click", e => { e.preventDefault(); switchTab(a.dataset.jump); }));
}

async function loadDocuments() {
  const data = await api.get("/api/files/tailored");
  const target = $("#documents-list");
  if (!data.folders.length) {
    target.innerHTML = `<div class="empty">No documents yet. Run the pipeline first.</div>`;
    return;
  }
  target.innerHTML = data.folders.map(f => `
    <div class="card" style="margin-bottom:10px">
      <div class="card-head" style="border-bottom:1px solid var(--border)">
        <h3 style="font-family:var(--mono); font-size:12.5px">${escapeHtml(f.slug)}</h3>
        <span class="muted small">${new Date(f.modified*1000).toLocaleString()}</span>
      </div>
      <div class="card-body" style="display:flex; gap:10px; flex-wrap:wrap">
        ${f.files.map(name => `
          <a class="btn tiny" href="/api/files/tailored/${encodeURIComponent(f.slug)}/${encodeURIComponent(name)}" target="_blank">${escapeHtml(name)}</a>
        `).join("")}
      </div>
    </div>`).join("");
}

// ── Utils ───────────────────────────────────────────────────────────────

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function formatSalary(min, max) {
  if (!min) return "";
  const fmt = (n) => `$${Math.round(n/1000)}k`;
  return max && max !== min ? `${fmt(min)}–${fmt(max)}` : fmt(min);
}

// ── Showcase PDF ─────────────────────────────────────────────────────────────

async function loadShowcaseStatus() {
  const line = $("#showcase-status-line");
  if (!line) return;
  try {
    const s = await api.get("/api/showcase/status");
    if (s.exists) {
      line.textContent = `✓ showcase.pdf on file · ${s.size_kb} KB · ${(s.modified_at || "").replace("T", " ").slice(0, 16)}`;
      line.style.color = "var(--ok)";
    } else {
      line.textContent = "no showcase.pdf yet — click Build to create one";
      line.style.color = "";
    }
  } catch (e) {
    line.textContent = "status unavailable";
    line.style.color = "";
  }
}

(function bindShowcase() {
  const btn = $("#btn-build-showcase");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const statusEl = $("#showcase-build-status");
    btn.disabled = true;
    btn.textContent = "Building…";
    if (statusEl) statusEl.textContent = "Fetching GitHub repos and generating PDF…";
    try {
      const r = await api.post("/api/showcase/build", {});
      if (r.ok) {
        toast(`Showcase built — ${r.repo_count} repos included`, "ok");
        if (statusEl) statusEl.textContent =
          `✓ ${r.repo_count} repos · saved to ${r.path}`;
        loadShowcaseStatus();
      } else {
        toast("Showcase build failed: " + (r.error || "unknown"), "err");
        if (statusEl) statusEl.textContent = "× " + (r.error || "build failed");
      }
    } catch (e) {
      toast("Showcase build failed: " + e, "err");
      if (statusEl) statusEl.textContent = "× " + e;
    } finally {
      btn.disabled = false;
      btn.textContent = "⚡ Build showcase PDF";
    }
  });
})();

// ── Boot ────────────────────────────────────────────────────────────────

(function init() {
  renderYNGroups();
  bindJumps();

  // Start on home tab
  switchTab("home");

  // Startup data in parallel
  loadStatus();
  loadEnv();
  renderValidity();
  updateSidebarCompleteness();

  api.get("/api/pipeline/state").then(st => {
    if (st && st.running) startEventStream();
  }).catch(() => {});

  // Periodic refresh
  setInterval(loadStatus, 5000);
  setInterval(renderValidity, 8000);
  setInterval(updateSidebarCompleteness, 30000);
})();

// ── API Health tab ──────────────────────────────────────────────────────
// Renders one card per provider with: configured/active badges, key mask,
// last test result, today/lifetime call counts. "Test" buttons make a real
// (1-token) call to verify the key still works.

async function loadHealth() {
  const target = $("#health-cards");
  target.innerHTML = `<div class="muted small">Loading…</div>`;
  try {
    const data = await api.get("/api/health/keys");
    renderHealthCards(data);
  } catch (e) {
    target.innerHTML = `<div class="empty">Couldn't load health: ${escapeHtml(String(e))}</div>`;
  }
}

function renderHealthCards(data) {
  const target = $("#health-cards");
  const PROVIDERS = {
    claude: { name: "Anthropic Claude", model: "claude-haiku-4-5",
              url: "https://console.anthropic.com",
              usage_url: "https://console.anthropic.com/settings/usage" },
    openai: { name: "OpenAI",           model: "gpt-4o-mini",
              url: "https://platform.openai.com/api-keys",
              usage_url: "https://platform.openai.com/usage" },
    gemini: { name: "Google Gemini",    model: "gemini-2.5-flash",
              url: "https://aistudio.google.com/apikey",
              usage_url: "https://aistudio.google.com/usage" },
  };
  const providers = data.providers || [];

  // Sidebar count: how many configured keys, with bad ones flagged red
  const configured = providers.filter(p => p.configured).length;
  const bad = providers.filter(p => p.last_test && p.last_test.valid === false).length;
  const badge = $("#count-health");
  if (badge) {
    badge.textContent = bad ? `${configured} · ${bad} bad` : `${configured}`;
    badge.classList.toggle("err", bad > 0);
    badge.classList.toggle("ok",  bad === 0 && configured > 0);
  }

  // Budget banner at the top of the cards.
  // Defensive numeric coercion: any of these can be 0/null/undefined depending
  // on whether the user has set a budget, made any tracked-token calls yet,
  // or just upgraded from a pre-token-tracker api_usage.json. Never crash.
  const num = (v) => (typeof v === "number" && !Number.isNaN(v)) ? v : 0;
  let budgetBanner = "";
  if (data.budget) {
    const c = data.budget.claude || {};
    const g = data.budget.gemini || {};
    const budget    = num(c.budget_usd);
    const spent     = num(c.spent_usd);
    const remaining = num(c.remaining_usd);
    const pctUsed   = num(c.pct_used);
    const apps      = num(c.est_apps_remaining);
    const gToday    = num(g.calls_today);
    const gLimit    = num(g.free_tier_rpd_limit) || 250;
    if (budget > 0) {
      const pct  = Math.min(100, pctUsed);
      const tone = pct < 50 ? "ok" : pct < 85 ? "warn" : "err";
      budgetBanner = `
        <div class="budget-card">
          <div class="budget-row">
            <div>
              <b>Claude budget:</b>
              <span class="muted small">$${spent.toFixed(4)} spent of $${budget.toFixed(2)}</span>
              · <b style="color:var(--${tone})">$${remaining.toFixed(2)} remaining</b>
              · <span class="muted small">~${apps} apps left at avg cost</span>
            </div>
            <a class="btn ghost tiny" target="_blank" rel="noopener"
               href="https://console.anthropic.com/settings/billing">Anthropic billing ↗</a>
          </div>
          <div class="budget-bar"><div class="budget-fill ${tone}" style="width:${pct}%"></div></div>
          <div class="muted small" style="margin-top:8px">
            <b>Gemini free tier:</b> ${gToday} calls today (~${gLimit} daily limit on Flash)
          </div>
        </div>`;
    } else {
      budgetBanner = `
        <div class="budget-card" style="background:var(--surface-2)">
          <div class="muted small">
            <b>💡 Set your Claude budget</b> in <a class="link" data-jump="settings">Settings</a> (e.g. 20)
            and this card will track spend in real time and warn when you're running low.
          </div>
        </div>`;
    }
  }

  target.innerHTML = budgetBanner + providers.map(p => {
    const meta = PROVIDERS[p.provider] || { name: p.provider, model: "?", url: "#" };
    const t = p.last_test;
    let statusBadge, statusDetail;
    if (!p.configured) {
      statusBadge  = `<span class="pill warn">Not configured</span>`;
      statusDetail = `No key set in <code>.env</code>. Get one at <a href="${meta.url}" target="_blank">${meta.url}</a>`;
    } else if (!t) {
      statusBadge  = `<span class="pill acc">Untested</span>`;
      statusDetail = `Click <b>Test</b> to verify the key works.`;
    } else if (t.valid) {
      statusBadge  = `<span class="pill ok">✓ Valid</span>`;
      statusDetail = `Last tested ${formatAgo(t.tested_at)} · ${t.latency_ms}ms · reply ${escapeHtml((t.reply || "OK").slice(0, 30))}`;
    } else {
      statusBadge  = `<span class="pill err">✗ Invalid</span>`;
      statusDetail = `Last tested ${formatAgo(t.tested_at)} · ${escapeHtml((t.error || "unknown error").slice(0, 200))}`;
    }
    const activeChip = p.active ? `<span class="pill acc" style="margin-left:6px">ACTIVE</span>` : "";
    const lastCall = p.last_call_at ? `last call ${formatAgo(p.last_call_at)}` : "no calls yet";
    return `
      <div class="health-card">
        <div class="health-row">
          <div class="health-name">
            <b>${escapeHtml(meta.name)}</b> ${activeChip}
            <div class="muted small">${escapeHtml(meta.model)} · ${p.key_mask ? `<code>${escapeHtml(p.key_mask)}</code>` : "—"}</div>
          </div>
          <div class="health-status">${statusBadge}</div>
          <div class="health-actions">
            ${p.configured
              ? `<button type="button" class="btn primary tiny" data-test="${p.provider}">Test</button>`
              : ""}
            ${p.configured ? `<a class="btn ghost tiny" target="_blank" rel="noopener"
                href="${meta.usage_url || meta.url}" title="Open provider's own usage dashboard">
                Provider usage ↗</a>` : ""}
            <button type="button" class="btn ghost tiny" data-jump="settings">Change key →</button>
          </div>
        </div>
        <div class="health-detail muted small">${statusDetail}</div>
        <div class="health-stats">
          <div><span class="muted small">Today:</span> <b>${p.calls_today}</b> calls</div>
          <div><span class="muted small">Lifetime:</span> <b>${p.calls_lifetime}</b> calls</div>
          <div><span class="muted small">Errors today:</span> <b class="${p.errors_today ? 'err' : 'muted'}">${p.errors_today}</b></div>
          <div><span class="muted small">Spent today:</span> <b>$${num(p.spend_today_usd).toFixed(4)}</b></div>
          <div class="muted small" style="grid-column: 1 / -1">${lastCall}</div>
        </div>
      </div>`;
  }).join("");

  // Bind Test buttons + Change-key jumps
  target.querySelectorAll('[data-test]').forEach(b => {
    b.addEventListener("click", () => testOneProvider(b.dataset.test));
  });
  target.querySelectorAll('[data-jump]').forEach(a => {
    a.addEventListener("click", e => { e.preventDefault(); switchTab(a.dataset.jump); });
  });
}

async function testOneProvider(provider) {
  const target = $("#health-cards");
  toast(`Testing ${provider}…`, "");
  try {
    const r = await api.post("/api/health/keys/test", { provider });
    const result = (r.results || [])[0];
    if (!result) { toast("No result returned", "err"); return; }
    if (result.valid) toast(
      `${provider}: ✓ valid (${result.latency_ms}ms) · ${result.calls_today} calls today, ${result.calls_lifetime} lifetime`,
      "ok"
    );
    else toast(
      `${provider}: ✗ ${(result.error || "failed").slice(0, 100)} · ${result.calls_today} calls today`,
      "err"
    );
    await loadHealth();
  } catch (e) {
    toast(`Test failed: ${e}`, "err");
  }
}

async function testAllProviders() {
  const btn = $("#btn-test-all");
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = "Testing…";
  try {
    const r = await api.post("/api/health/keys/test", {});
    const results = r.results || [];
    const ok = results.filter(x => x.valid).length;
    const bad = results.filter(x => !x.valid).length;
    toast(`Tested ${results.length}: ${ok} valid, ${bad} bad`, bad ? "warn" : "ok");
    await loadHealth();
  } catch (e) {
    toast(`Test failed: ${e}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function formatAgo(iso) {
  if (!iso) return "never";
  const dt = new Date(iso);
  const ms = Date.now() - dt.getTime();
  if (ms < 60_000)         return `${Math.round(ms/1000)}s ago`;
  if (ms < 3_600_000)      return `${Math.round(ms/60_000)}m ago`;
  if (ms < 86_400_000)     return `${Math.round(ms/3_600_000)}h ago`;
  return dt.toLocaleString();
}

document.addEventListener("click", e => {
  // API Health tab handlers
  if (e.target.id === "btn-reload-health") loadHealth();
  if (e.target.id === "btn-test-all")      testAllProviders();
  if (e.target.id === "btn-reset-usage") {
    if (!confirm("Wipe all call counters? Test results stay.")) return;
    api.post("/api/health/usage/reset", {}).then(() => {
      loadHealth(); toast("Counts reset", "ok");
    });
  }
});

// ── Resume Run tab ────────────────────────────────────────────────────────

async function loadResumeInfo() {
  try {
    const d = await api.get("/api/resume/info");
    const noChk = $("#resume-no-checkpoint");
    const stale = $("#resume-stale-warning");
    const stats = $("#resume-stats-card");
    const opts  = $("#resume-options-card");
    const badge = $("#count-resume");
    if (!d.has_checkpoint) {
      noChk.hidden = false; stale.hidden = true; stats.hidden = true; opts.hidden = true;
      if (badge) badge.hidden = true;
      return;
    }
    noChk.hidden = true;
    stale.hidden = !!d.fresh;
    if (!d.fresh && $("#resume-age")) $("#resume-age").textContent = d.age_hours + "h";
    stats.hidden = false; opts.hidden = false;
    if ($("#resume-mtime"))      $("#resume-mtime").textContent     = (d.snap_mtime || "").replace("T", " ");
    if ($("#resume-total"))      $("#resume-total").textContent     = d.total_discovered;
    if ($("#resume-above"))      $("#resume-above").textContent     = d.above_threshold;
    if ($("#resume-done"))       $("#resume-done").textContent      = d.already_processed;
    if ($("#resume-remaining"))  $("#resume-remaining").textContent = d.remaining + " jobs remaining";
    if (badge) { badge.hidden = d.remaining <= 0; badge.textContent = d.remaining; }
  } catch(e) { console.error("loadResumeInfo", e); }
}

const _rBtn = $("#btn-reload-resume");
if (_rBtn) _rBtn.addEventListener("click", loadResumeInfo);

document.addEventListener("click", async e => {
  if (e.target.id !== "btn-resume-run") return;
  const dryRun   = $("#resume-dry-run") ? $("#resume-dry-run").checked : true;
  const pause    = $("#resume-pause") ? $("#resume-pause").checked : true;
  const research = $("#resume-research") ? $("#resume-research").checked : true;
  const limit    = Number($("#resume-limit") ? $("#resume-limit").value || 10 : 10);
  const res = await api.post("/api/pipeline/resume", {
    dry_run: dryRun, pause_before_submit: pause, do_research: research,
    limit, run_ats_check: true, score_jobs: false,
  });
  if (!res.ok) { toast(res.message || "Could not resume — no checkpoint.", "err"); return; }
  toast("Resuming from checkpoint — run " + res.run_id, "ok");
  switchTab("live"); startEventStream();
});

// ── Provider on/off toggles (API Health tab) ─────────────────────────────

async function loadProviderToggles() {
  const target = $("#provider-toggles");
  if (!target) return;
  try {
    const d = await api.get("/api/providers");
    const labels = { gemini: "Gemini (free first)", claude: "Claude (Anthropic)", openai: "OpenAI / GPT" };
    const models  = { gemini: "gemini-2.5-flash", claude: "claude-haiku-4-5", openai: "gpt-4o-mini" };
    target.innerHTML = `
      <div style="margin-bottom:12px" class="muted small">
        <b>Fallback order:</b> Gemini first (free) → Claude → OpenAI.
        When an enabled provider hits a rate limit, OneShot automatically switches to the next one mid-run — no manual action needed.
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px">
        ${d.providers.map(p => `
          <div class="health-card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <b>${escapeHtml(labels[p.provider] || p.provider)}</b>
              <span class="pill ${p.has_key ? "ok" : "err"}">${p.has_key ? "key set" : "no key"}</span>
            </div>
            <div class="muted small" style="margin-bottom:10px">Cheap model: <code>${escapeHtml(models[p.provider] || "")}</code></div>
            <label style="display:flex;align-items:center;gap:8px;cursor:${p.has_key ? "pointer" : "not-allowed"}">
              <input type="checkbox" class="provider-toggle" data-provider="${p.provider}"
                ${p.enabled ? "checked" : ""} ${p.has_key ? "" : "disabled"}>
              <span>${p.enabled ? "<b>ON</b> — active in fallback chain" : '<span class="muted">OFF — will be skipped</span>'}</span>
            </label>
          </div>`).join("")}
      </div>`;
    target.querySelectorAll(".provider-toggle").forEach(cb => {
      cb.addEventListener("change", async () => {
        const provider = cb.dataset.provider;
        const enabled  = cb.checked;
        const r = await api.post("/api/providers/toggle", { provider, enabled });
        if (r.ok) { toast((enabled ? "Enabled " : "Disabled ") + provider, "ok"); loadProviderToggles(); }
        else       { toast("Toggle failed: " + (r.error || ""), "err"); cb.checked = !enabled; }
      });
    });
    // Mirror to Providers stab
    const fullTarget = $("#provider-toggles-full");
    if (fullTarget) fullTarget.innerHTML = target.innerHTML;
  } catch(e) { if (target) target.innerHTML = `<div class="empty">Could not load providers.</div>`; }
}

// ── Ready-to-Apply cards ──────────────────────────────────────────────────────

function renderPendingTable(rows) {
  if (!rows.length) return `<div class="empty">No applications ready yet. Run the pipeline to generate tailored packages.</div>`;
  return rows.map((r, i) => {
    const resumePath   = r.resume_path   || r.resume_pdf   || "";
    const coverPath    = r.cover_path    || r.cover_pdf    || "";
    const showcasePath = r.showcase_path || "";
    const folderPath   = r.folder_path   || r.folder       || "";
    const applyUrl     = r.apply_url     || r.url          || "";
    const jobId        = r.job_id        || `row${i}`;
    return `
    <div class="card" style="margin-bottom:12px" data-job-id="${escapeHtml(jobId)}">
      <div class="card-head" style="gap:12px;flex-wrap:wrap">
        <div style="flex:1;min-width:200px">
          <div style="font-size:15px;font-weight:700">${escapeHtml(r.title || "Untitled")}</div>
          <div class="muted small" style="margin-top:3px">
            ${escapeHtml(r.company || "")}
            ${r.location ? " &nbsp;·&nbsp; " + escapeHtml(r.location) : ""}
            ${r.site || r.applier ? `&nbsp;·&nbsp; <span class="pill acc">${escapeHtml(r.site || r.applier || "")}</span>` : ""}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0">
          ${applyUrl ? `<a class="btn tiny primary" href="${escapeHtml(applyUrl)}" target="_blank">↗ Open job</a>` : ""}
          ${folderPath ? `<button class="btn tiny" data-act="open-folder" data-path="${escapeHtml(folderPath)}">📁 Open folder</button>` : ""}
          <button class="btn tiny" data-act="toggle-copilot" data-job-id="${escapeHtml(jobId)}">🤖 Copilot</button>
          <button class="btn tiny ok" data-act="mark" data-idx="${i}">✓ Mark applied</button>
          <button class="btn tiny ghost danger" data-act="dismiss" data-idx="${i}">✕ Dismiss</button>
        </div>
      </div>
      <div class="card-body" style="padding:12px 18px">
        <div class="readout" style="grid-template-columns:160px 1fr">
          <span class="k">Ready at</span><span class="v">${(r.pending_at || "").replace("T"," ").slice(0,16)}</span>
          <span class="k">ATS score</span><span class="v">${r.ats_score ? r.ats_score + "/100" : "—"}</span>
          <span class="k">Fit score</span><span class="v">${r.fit_score ? r.fit_score + "/10" : "—"}</span>
          ${resumePath ? `<span class="k">Resume</span><span class="v small muted" style="word-break:break-all">${escapeHtml(resumePath)}</span>` : ""}
          ${coverPath  ? `<span class="k">Cover</span><span class="v small muted" style="word-break:break-all">${escapeHtml(coverPath)}</span>` : ""}
          ${showcasePath ? `<span class="k">Showcase</span><span class="v small muted" style="word-break:break-all">${escapeHtml(showcasePath)}</span>` : ""}
        </div>
        ${applyUrl ? `<div style="margin-top:10px;padding:8px 12px;background:var(--surface-3);border-radius:6px;word-break:break-all">
          <span class="muted small">Apply URL:&nbsp;</span>
          <a href="${escapeHtml(applyUrl)}" target="_blank" style="font-family:var(--mono);font-size:12px">${escapeHtml(applyUrl)}</a>
        </div>` : ""}
      </div>

      <!-- ── Application Copilot panel (collapsed by default) ── -->
      <div id="copilot-panel-${escapeHtml(jobId)}" hidden
           style="border-top:1px solid var(--border);padding:14px 18px;background:var(--surface-2)">

        <!-- Requirements preview -->
        <div id="copilot-req-${escapeHtml(jobId)}">
          <div class="muted small" style="margin-bottom:4px">Loading requirements preview…</div>
        </div>

        <!-- Prebaked Q&A -->
        <div id="copilot-prebaked-${escapeHtml(jobId)}" style="margin-top:14px"></div>

        <!-- Free-form Q&A box -->
        <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
          <div class="field">
            <label style="font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.05em">
              Ask the Copilot
            </label>
            <textarea id="copilot-q-${escapeHtml(jobId)}" rows="2"
              style="margin-top:6px"
              placeholder="Paste a question from the application form — e.g. Why do you want to work here?"></textarea>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
            <button class="btn tiny primary" data-act="copilot-ask" data-job-id="${escapeHtml(jobId)}">
              ⚡ Generate answer
            </button>
            <button class="btn tiny ghost" data-act="copilot-regen" data-job-id="${escapeHtml(jobId)}">
              ↺ Regenerate
            </button>
          </div>
          <div id="copilot-result-${escapeHtml(jobId)}" style="margin-top:10px"></div>
        </div>
      </div>
    </div>`;
  }).join("");
}


// ── History tab ───────────────────────────────────────────────────────────────

let _historyExpanded = {};   // {date/task_N: true} — which task panels are open

async function loadHistory() {
  const el = $("#history-content");
  if (!el) return;
  el.innerHTML = `<div class="muted small" style="padding:16px">Loading…</div>`;
  try {
    const d = await api.get("/api/history");
    if (!d.ok || !d.grouped || !d.grouped.length) {
      el.innerHTML = `<div class="empty"><div class="empty-icon">⧖</div><div class="empty-title">No history yet</div><div class="empty-sub">Each pipeline run will be archived here when the next one starts.</div></div>`;
      return;
    }
    el.innerHTML = d.grouped.map(group => renderHistoryGroup(group)).join("");
    // Restore expanded state and bind toggles
    el.querySelectorAll("[data-history-key]").forEach(btn => {
      const key = btn.dataset.historyKey;
      const panel = $(`#hpanel-${CSS.escape(key)}`);
      if (_historyExpanded[key] && panel) panel.hidden = false;
      btn.addEventListener("click", () => toggleHistoryTask(key, btn));
    });
  } catch(e) {
    el.innerHTML = `<div class="empty err">Failed to load history: ${escapeHtml(String(e))}</div>`;
  }
}

function renderHistoryGroup(group) {
  const tasks = (group.tasks || []).slice().reverse();   // newest task first within day
  return `
  <div class="card" style="margin-bottom:14px">
    <div class="card-head" style="padding:14px 18px;border-bottom:1px solid var(--border)">
      <div style="font-weight:700;font-size:15px">📅 ${escapeHtml(group.date)}</div>
      <div class="muted small">${tasks.length} run${tasks.length !== 1 ? "s" : ""} this day</div>
    </div>
    <div class="card-body" style="padding:0">
      ${tasks.map(t => renderHistoryTask(group.date, t)).join("")}
    </div>
  </div>`;
}

function renderHistoryTask(date, t) {
  const key    = `${date}/task_${t.task_number}`;
  const label  = `Task ${t.task_number}`;
  const time   = (t.started_at || "").replace("T", " ").slice(0, 16);
  const terms  = (t.search_terms || []).slice(0, 4).join(", ");
  return `
  <div style="border-bottom:1px solid var(--border)">
    <button data-history-key="${escapeHtml(key)}"
            style="width:100%;text-align:left;background:none;border:none;padding:12px 18px;cursor:pointer;display:flex;align-items:center;gap:12px">
      <span style="font-size:11px;transition:transform .15s" id="harrow-${escapeHtml(key)}">▶</span>
      <span style="font-weight:700">${escapeHtml(label)}</span>
      <span class="pill acc" style="font-size:11px">${t.count} job${t.count !== 1 ? "s" : ""}</span>
      <span class="muted small">${escapeHtml(time)}</span>
      ${terms ? `<span class="muted small" style="margin-left:4px">· ${escapeHtml(terms)}</span>` : ""}
    </button>
    <div id="hpanel-${escapeHtml(key)}" hidden
         style="padding:0 0 8px 0;background:var(--surface-2)">
      <div id="hrows-${escapeHtml(key)}" style="padding:0 18px">
        <div class="muted small" style="padding:8px 0">Loading…</div>
      </div>
    </div>
  </div>`;
}

async function toggleHistoryTask(key, btn) {
  const panel = $(`#hpanel-${CSS.escape(key)}`);
  const arrow = $(`#harrow-${CSS.escape(key)}`);
  if (!panel) return;
  const opening = panel.hidden;
  panel.hidden = !panel.hidden;
  if (arrow) arrow.style.transform = opening ? "rotate(90deg)" : "";
  _historyExpanded[key] = opening;
  if (!opening) return;

  // Load rows on first open
  const rowsEl = $(`#hrows-${CSS.escape(key)}`);
  if (!rowsEl || rowsEl.dataset.loaded) return;
  rowsEl.dataset.loaded = "1";

  const [date, taskPart] = key.split("/");
  const taskNum = parseInt(taskPart.replace("task_", ""), 10);
  try {
    const d = await api.get(`/api/history/${encodeURIComponent(date)}/${taskNum}`);
    if (!d.ok || !d.rows.length) {
      rowsEl.innerHTML = `<div class="muted small" style="padding:8px 0">No rows in this run.</div>`;
      return;
    }
    rowsEl.innerHTML = `
      <table class="t" style="margin-top:6px">
        <thead><tr>
          <th>Company</th><th>Title</th><th>Fit</th><th>ATS</th><th>Site</th><th></th>
        </tr></thead>
        <tbody>${d.rows.map(r => `
          <tr>
            <td>${escapeHtml(r.company || "")}</td>
            <td>${escapeHtml(r.title || "")}</td>
            <td class="num">${r.fit_score || "—"}</td>
            <td class="num">${r.ats_score || "—"}</td>
            <td><span class="pill acc">${escapeHtml(r.site || r.applier || "")}</span></td>
            <td style="white-space:nowrap">
              ${r.apply_url ? `<a href="${escapeHtml(r.apply_url)}" target="_blank" class="btn tiny">↗ Job</a>` : ""}
              ${r.folder_path ? `<button class="btn tiny" onclick="api.post('/api/files/open-folder',{path:${JSON.stringify(r.folder_path)}})">📁</button>` : ""}
            </td>
          </tr>`).join("")}
        </tbody>
      </table>`;
  } catch(e) {
    rowsEl.innerHTML = `<div class="muted small err">Error: ${escapeHtml(String(e))}</div>`;
  }
}

// ── Application Copilot ───────────────────────────────────────────────────────

function renderConfidenceBadge(score) {
  const n = Number(score) || 0;
  const cls = n >= 70 ? "ok" : n >= 40 ? "warn" : "err";
  return `<span class="pill ${cls}" style="font-size:11px">${n}%</span>`;
}

function renderCopilotRequirements(jobId, req) {
  const el = $(`#copilot-req-${jobId}`);
  if (!el) return;
  if (!req || (!req.required_fields?.length && !req.likely_screening_questions?.length &&
               !req.extra_docs?.length && !req.profile_gaps?.length)) {
    el.innerHTML = `<div class="muted small">No requirements preview available.</div>`;
    return;
  }
  const section = (title, items, cls="") => items && items.length
    ? `<div style="margin-bottom:8px">
         <div style="font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">${title}</div>
         <div style="display:flex;gap:4px;flex-wrap:wrap">${items.map(s => `<span class="pill ${cls}">${escapeHtml(s)}</span>`).join("")}</div>
       </div>` : "";
  el.innerHTML = `
    <div style="font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Requirements Preview</div>
    ${section("Required fields",             req.required_fields,             "acc")}
    ${section("Likely screening questions",  req.likely_screening_questions,  "")}
    ${section("Extra documents needed",      req.extra_docs,                  "warn")}
    ${section("Profile gaps",               req.profile_gaps,                "err")}`;
}

function renderCopilotPrebaked(jobId, answers) {
  const el = $(`#copilot-prebaked-${jobId}`);
  if (!el) return;
  if (!answers || !answers.length) {
    el.innerHTML = `<div class="muted small" style="margin-top:4px">No pre-baked answers yet — run the pipeline to generate them.</div>`;
    return;
  }
  el.innerHTML = `
    <div style="font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">
      Pre-baked Answers <span class="muted" style="font-weight:400;font-size:11px">(${answers.length})</span>
    </div>
    ${answers.map((a, i) => {
      const needsFlag = a.needs_review
        ? `<span class="pill err" style="font-size:10px" title="Needs review">⚠ review</span>` : "";
      const srcPill   = `<span class="pill muted" style="font-size:10px">${escapeHtml(a.source || "generated")}</span>`;
      const typePill  = `<span class="pill acc"  style="font-size:10px">${escapeHtml(a.answer_type || "")}</span>`;
      return `
      <div class="card" style="margin-bottom:8px;padding:10px 14px" data-prebaked-idx="${i}">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
          ${renderConfidenceBadge(a.confidence_score)} ${srcPill} ${typePill} ${needsFlag}
        </div>
        <div style="font-size:12px;font-weight:700;margin-bottom:4px">${escapeHtml(a.question || "")}</div>
        <div class="prebaked-answer-text" style="white-space:pre-wrap;font-size:13px;line-height:1.5;margin-bottom:8px">${escapeHtml(a.answer || "")}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn tiny" onclick="copyText(${JSON.stringify(a.answer || "")})">📋 Copy</button>
          <button class="btn tiny" onclick="copilotSave(${JSON.stringify(jobId)}, ${JSON.stringify(a.question || "")}, ${JSON.stringify(a.answer || "")}, false)">💾 Save</button>
          <button class="btn tiny" onclick="copilotSave(${JSON.stringify(jobId)}, ${JSON.stringify(a.question || "")}, ${JSON.stringify(a.answer || "")}, true)">⭐ Preferred</button>
        </div>
      </div>`;
    }).join("")}`;
}

let _copilotLoaded = {};

async function copilotTogglePanel(jobId) {
  const panel = $(`#copilot-panel-${jobId}`);
  if (!panel) return;
  const opening = panel.hidden;
  panel.hidden = !panel.hidden;
  if (!opening) return;
  // First open: load data
  if (_copilotLoaded[jobId]) return;
  _copilotLoaded[jobId] = true;
  const reqEl = $(`#copilot-req-${jobId}`);
  if (reqEl) reqEl.innerHTML = `<div class="muted small">Loading…</div>`;
  try {
    const d = await api.get(`/api/copilot/job/${encodeURIComponent(jobId)}`);
    if (!d.ok) { if (reqEl) reqEl.innerHTML = `<div class="muted small err">Failed: ${escapeHtml(d.error || "")}</div>`; return; }
    renderCopilotRequirements(jobId, d.requirements);
    renderCopilotPrebaked(jobId, d.prebaked_answers);
  } catch (e) {
    if (reqEl) reqEl.innerHTML = `<div class="muted small err">Error loading copilot data.</div>`;
    _copilotLoaded[jobId] = false;
  }
}

async function copilotAsk(jobId, forceRegen) {
  const qEl  = $(`#copilot-q-${jobId}`);
  const rsEl = $(`#copilot-result-${jobId}`);
  if (!qEl || !rsEl) return;
  const question = (qEl.value || "").trim();
  if (!question) { toast("Paste a question first", "warn"); qEl.focus(); return; }
  rsEl.innerHTML = `<div class="muted small">Generating…</div>`;
  try {
    const endpoint = forceRegen ? "/api/copilot/regenerate" : "/api/copilot/answer";
    const body = { question, job_id: jobId };
    const d = await api.post(endpoint, body);
    if (!d.ok) { rsEl.innerHTML = `<div class="pill err">${escapeHtml(d.error || "Failed")}</div>`; return; }
    const needsFlag = d.needs_review
      ? `<span class="pill err" style="font-size:11px">⚠ needs review</span>` : "";
    rsEl.innerHTML = `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        ${renderConfidenceBadge(d.confidence_score)}
        <span class="pill acc" style="font-size:11px">${escapeHtml(d.answer_type || "")}</span>
        <span class="pill muted" style="font-size:11px">${escapeHtml(d.source || "")}</span>
        ${needsFlag}
      </div>
      <div style="white-space:pre-wrap;font-size:13px;line-height:1.55;padding:10px 14px;background:var(--surface-3);border-radius:6px;margin-bottom:8px" id="copilot-answer-text-${escapeHtml(jobId)}">${escapeHtml(d.answer || "")}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn tiny" onclick="copyText(${JSON.stringify(d.answer || "")})">📋 Copy</button>
        <button class="btn tiny ghost" data-act="copilot-regen" data-job-id="${escapeHtml(jobId)}">↺ Regenerate</button>
        <button class="btn tiny" onclick="copilotSave(${JSON.stringify(jobId)}, ${JSON.stringify(question)}, ${JSON.stringify(d.answer || "")}, false)">💾 Save</button>
        <button class="btn tiny" onclick="copilotSave(${JSON.stringify(jobId)}, ${JSON.stringify(question)}, ${JSON.stringify(d.answer || "")}, true)">⭐ Mark preferred</button>
      </div>`;
    // Re-bind the Regenerate button rendered inside result
    rsEl.querySelectorAll("button[data-act='copilot-regen']").forEach(b => {
      b.addEventListener("click", () => copilotAsk(jobId, true));
    });
  } catch (e) {
    rsEl.innerHTML = `<div class="pill err">Error: ${escapeHtml(String(e))}</div>`;
  }
}

async function copilotSave(jobId, question, answer, preferred) {
  try {
    const d = await api.post("/api/copilot/save", { question, answer, job_id: jobId, preferred });
    if (d.ok) toast(preferred ? "Saved as preferred answer" : "Saved for future use", "ok");
    else       toast("Save failed: " + (d.error || ""), "err");
  } catch(e) { toast("Save error", "err"); }
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast("Copied to clipboard", "ok")).catch(() => fallbackCopy(text));
  } else { fallbackCopy(text); }
}

// ── Learning Insights (Dashboard card) ───────────────────────────────────────

async function loadInsights() {
  try {
    const d = await api.get("/api/insights");
    const card = $("#insights-card");
    if (!card || !d.has_insights) return;

    card.hidden = false;

    // Meta line
    const meta = $("#insights-meta");
    if (meta && d.generated_at) {
      meta.textContent = "· last run " + (d.generated_at || "").replace("T", " ").slice(0, 16);
    }

    // Keyword cloud
    const kwEl = $("#insights-keywords");
    if (kwEl && d.top_missing_keywords && d.top_missing_keywords.length) {
      const maxCount = d.top_missing_keywords[0].count || 1;
      kwEl.innerHTML = d.top_missing_keywords.slice(0, 10).map(({keyword, count}) => {
        const size = Math.round(11 + (count / maxCount) * 5);
        return `<span class="gap-pill" style="font-size:${size}px" title="Missing in ${count} job(s)">
          ${escapeHtml(keyword)}<span class="gap-count">${count}</span>
        </span>`;
      }).join("");
    } else if (kwEl) {
      kwEl.innerHTML = `<span class="muted small">No gap data yet — run the pipeline first.</span>`;
    }

    // Stats readout
    const statsEl = $("#insights-stats");
    if (statsEl) {
      const avg = d.ats_average !== null ? d.ats_average + "/100" : "—";
      const qa  = d.qa_promoted  !== undefined ? d.qa_promoted : "—";
      statsEl.innerHTML = `
        <span class="k">Avg ATS score</span><span class="v ${d.ats_average >= 70 ? "ok" : "warn"}">${avg}</span>
        <span class="k">Jobs analysed</span><span class="v">${d.jobs_analysed || "—"}</span>
        <span class="k">Q&amp;A pairs learned</span><span class="v">${qa}</span>`;
    }

    // Blacklist flags
    const blEl = $("#insights-blacklist");
    if (blEl && d.flagged_companies && d.flagged_companies.length) {
      blEl.innerHTML = `<div class="muted small" style="font-weight:700;margin-bottom:6px">⚠ Consider blacklisting</div>` +
        d.flagged_companies.map(fc =>
          `<div class="muted small" style="padding:3px 0">
            ${escapeHtml(fc.company)} — failed ${fc.failures}× 
            <a class="link muted small" onclick="addToBlacklist(${JSON.stringify(fc.company)})">+ blacklist</a>
          </div>`
        ).join("");
    }

    // Advice block
    const advEl = $("#insights-advice");
    if (advEl && d.advice && d.advice.length) {
      advEl.style.display = "block";
      advEl.innerHTML = "<b>Advice from last run:</b> " + escapeHtml(d.advice[0]);
    }
  } catch(e) { console.error("loadInsights", e); }
}

async function addToBlacklist(company) {
  const prefs = await api.get("/api/preferences");
  const bl = prefs.blacklists || {};
  const companies = bl.companies || [];
  if (companies.includes(company)) { toast(company + " already blacklisted", "warn"); return; }
  companies.push(company);
  bl.companies = companies;
  prefs.blacklists = bl;
  const r = await api.put("/api/preferences", prefs);
  if (r.ok) toast(company + " added to blacklist", "ok");
  else toast("Could not update blacklist", "err");
}


// ── Secondary run/save buttons (bottom of Search & Run card) ─────────────
// Mirror the primary buttons at the top of the page
const _run2 = document.getElementById("btn-run2");
if (_run2) _run2.addEventListener("click", () => document.getElementById("btn-run").click());
const _save2 = document.getElementById("btn-save-search2");
if (_save2) _save2.addEventListener("click", () => document.getElementById("btn-save-search").click());
