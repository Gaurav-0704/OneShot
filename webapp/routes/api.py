"""
webapp/routes/api.py - JSON REST endpoints.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("api", __name__)


def _root() -> Path:
    return current_app.config["ROOT"]


def _read_yaml(name: str) -> dict[str, Any]:
    p = _root() / "config" / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _write_yaml(name: str, data: dict) -> None:
    p = _root() / "config" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _read_csv(name: str) -> list[dict]:
    p = _root() / "outputs" / name
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Showcase ──────────────────────────────────────────────────────────────────

@bp.route("/showcase/build", methods=["POST"])
def showcase_build():
    """Build (or rebuild) config/showcase.pdf from GitHub + profile data.
    Idempotent — safe to call at any time. Returns the build result dict."""
    from core.showcase import ShowcaseBuilder
    try:
        result = ShowcaseBuilder(root=_root()).build()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/showcase/status", methods=["GET"])
def showcase_status():
    """Return whether config/showcase.pdf exists and its metadata."""
    from core.showcase import ShowcaseBuilder
    return jsonify(ShowcaseBuilder.status(root=_root()))


# ── Profile validation + resume upload + parse ───────────────────────────────

@bp.route("/profile/validate", methods=["GET"])
def profile_validate():
    from agents.profile import ProfileAgent
    return jsonify(ProfileAgent(_root() / "config").validate_profile())


@bp.route("/profile/upload-resume", methods=["POST"])
def upload_resume():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file in request"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "empty filename"}), 400
    cfg = _root() / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    ext = (Path(f.filename).suffix or ".pdf").lower()
    target = cfg / f"master_resume{ext}"
    f.save(str(target))
    return jsonify({"ok": True, "saved_as": target.name, "size_bytes": target.stat().st_size})


@bp.route("/profile/parse-resume", methods=["POST"])
def parse_resume():
    body = request.get_json(silent=True) or {}
    overwrite = bool(body.get("overwrite", False))
    from agents.profile import ProfileAgent
    return jsonify(ProfileAgent(_root() / "config").parse_resume_to_yaml(overwrite=overwrite))

@bp.route("/profile/suggest-search-terms", methods=["POST"])
def suggest_search_terms():
    """Run a one-shot LLM call on the resume → 6-10 search queries + level."""
    from agents.profile import ProfileAgent
    return jsonify(ProfileAgent(_root() / "config").suggest_search_terms())



# ── Profile + configs ────────────────────────────────────────────────────────

@bp.route("/profile", methods=["GET"])
def get_profile():
    try:
        from agents.profile import ProfileAgent
        p = ProfileAgent(_root() / "config").build()
        return jsonify({
            "full_name": p.full_name,
            "first_name": p.first_name, "middle_name": p.middle_name, "last_name": p.last_name,
            "email": p.email, "phone": p.phone,
            "linkedin_url": p.linkedin_url, "github_url": p.github_url, "website_url": p.website_url,
            "city": p.city, "state": p.state, "country": p.country, "zipcode": p.zipcode,
            "years_of_experience": p.years_of_experience,
            "summary": p.summary, "headline": p.headline,
            "user_information_summary": p.user_information_summary,
            "master_resume_path": str(p.master_resume_path or ""),
            "master_resume_chars": len(p.master_resume_text or ""),
            "github_repos": p.github_repos, "github_languages": p.github_languages, "github_bio": p.github_bio,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/personal", methods=["GET", "PUT"])
def personal():
    if request.method == "GET":
        return jsonify(_read_yaml("personal.yaml"))
    _write_yaml("personal.yaml", request.get_json() or {})
    return jsonify({"ok": True})


@bp.route("/preferences", methods=["GET", "PUT"])
def preferences():
    if request.method == "GET":
        return jsonify(_read_yaml("preferences.yaml"))
    _write_yaml("preferences.yaml", request.get_json() or {})
    return jsonify({"ok": True})


@bp.route("/questions", methods=["GET", "PUT"])
def questions():
    if request.method == "GET":
        return jsonify(_read_yaml("questions.yaml"))
    _write_yaml("questions.yaml", request.get_json() or {})
    return jsonify({"ok": True})


# ── Applications ─────────────────────────────────────────────────────────────

@bp.route("/applications/applied", methods=["GET"])
def applied():
    rows = _read_csv("applied_jobs.csv"); rows.reverse()
    return jsonify({"count": len(rows), "rows": rows})


@bp.route("/applications/pending", methods=["GET"])
def pending():
    rows = _read_csv("pending_review.csv")
    rows.reverse()
    # Enrich each row with named path fields the UI needs for Ready-to-Apply cards.
    for r in rows:
        files = [f.strip() for f in (r.get("files_attached") or "").split(",") if f.strip()]
        r["apply_url"]     = r.get("url", "")
        r["resume_path"]   = next(
            (f[7:] for f in files if f.startswith("resume:")),
            r.get("resume_pdf", ""),
        )
        r["cover_path"]    = next(
            (f[6:] for f in files if f.startswith("cover:")),
            r.get("cover_pdf", ""),
        )
        r["showcase_path"] = next(
            (f[9:] for f in files if f.startswith("showcase:")), ""
        )
        r["folder_path"]   = r.get("folder", "")
    return jsonify({"count": len(rows), "rows": rows})


@bp.route("/applications/failed", methods=["GET"])
def failed():
    rows = _read_csv("failed_jobs.csv"); rows.reverse()
    return jsonify({"count": len(rows), "rows": rows})


@bp.route("/profile/gap-analysis", methods=["GET"])
def gap_analysis():
    """Aggregate ATS missing-keyword data from every per-job audit on disk.
    Returns the keywords that came up most often across the user's recent
    runs - i.e., the skills/words they most need to add to their resume.

    Reads outputs/applications/<slug>/ats_audit.txt files written by WriterAgent.
    Lightweight: pure file walk + string parse, no LLM call."""
    apps_dir = _root() / "outputs" / "applications"
    if not apps_dir.exists():
        return jsonify({"count": 0, "keywords": [], "advice": [], "avg_ats_score": None})

    from collections import Counter
    counter: Counter = Counter()
    advice: list[str] = []
    scores: list[int] = []
    job_count = 0

    for audit in apps_dir.glob("*/ats_audit.txt"):
        job_count += 1
        try:
            text = audit.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Parse "Score: 72/100" header
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("score:"):
                try:
                    scores.append(int(line.split(":", 1)[1].strip().split("/")[0]))
                except Exception:
                    pass
            elif line.lower().startswith("missing keywords:"):
                kws = line.split(":", 1)[1]
                for k in kws.split(","):
                    k = k.strip().lower()
                    if k and len(k) >= 2 and len(k) < 40:
                        counter[k] += 1
            elif line.lower().startswith("advice:"):
                a = line.split(":", 1)[1].strip()
                if a and a not in advice:
                    advice.append(a)

    # Top 15 most-missed keywords with their frequency
    top = [{"keyword": k, "count": c} for k, c in counter.most_common(15)]
    avg = round(sum(scores) / len(scores), 1) if scores else None
    return jsonify({
        "count": job_count,
        "keywords": top,
        "advice": advice[:5],
        "avg_ats_score": avg,
    })


@bp.route("/applications/discovered", methods=["GET"])
def discovered():
    p = _root() / "outputs" / "last_discovered.json"
    if not p.exists():
        return jsonify({"count": 0, "rows": []})
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        rows = []
    return jsonify({"count": len(rows), "rows": rows})


def _rewrite_pending_without(idx: int) -> dict:
    pending_path = _root() / "outputs" / "pending_review.csv"
    if not pending_path.exists():
        return {"error": "no pending file", "code": 404}
    rows = _read_csv("pending_review.csv")
    if idx < 0 or idx >= len(rows):
        return {"error": "index out of range", "code": 400}
    remaining = [r for i, r in enumerate(rows) if i != idx]
    target = rows[idx]
    if remaining:
        keys = list(remaining[0].keys())
        with pending_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(remaining)
    else:
        pending_path.unlink()
    return {"ok": True, "removed": target}


@bp.route("/applications/<int:idx>/mark-submitted", methods=["POST"])
def mark_submitted(idx: int):
    rows = _read_csv("pending_review.csv")
    if idx < 0 or idx >= len(rows):
        return jsonify({"error": "index out of range"}), 400
    row = rows[idx]
    row["submitted"] = "True"
    row["applied_at"] = datetime.now().isoformat(timespec="seconds")
    from core.tracker import record_applied
    record_applied(_root() / "outputs" / "applied_jobs.csv", row)
    res = _rewrite_pending_without(idx)
    if "error" in res:
        return jsonify(res), res.get("code", 400)
    return jsonify({"ok": True})


@bp.route("/applications/<int:idx>/dismiss", methods=["POST"])
def dismiss(idx: int):
    res = _rewrite_pending_without(idx)
    if "error" in res:
        return jsonify(res), res.get("code", 400)
    return jsonify({"ok": True})


# ── Status / env ─────────────────────────────────────────────────────────────

@bp.route("/status", methods=["GET"])
def status():
    runner = current_app.config["RUNNER"]
    today = datetime.now().date().isoformat()

    def _today(rows: list[dict], key: str) -> int:
        return sum(1 for r in rows if (r.get(key) or "").startswith(today))

    applied_rows = _read_csv("applied_jobs.csv")
    pending_rows = _read_csv("pending_review.csv")
    failed_rows = _read_csv("failed_jobs.csv")

    return jsonify({
        "runner_status": runner.status,
        "runner_running": runner.is_running(),
        "run_id": runner.run_id,
        "applied_today": _today(applied_rows, "applied_at"),
        "applied_lifetime": len(applied_rows),
        "pending_today": _today(pending_rows, "pending_at"),
        "pending_lifetime": len(pending_rows),
        "failed_today": _today(failed_rows, "failed_at"),
        "failed_lifetime": len(failed_rows),
    })


@bp.route("/env", methods=["GET"])
def env():
    """UI-safe env snapshot. Never leak full secrets - only return masked tail."""
    def _mask(v: str) -> str:
        if not v:
            return ""
        return ("•" * max(0, len(v) - 4)) + v[-4:] if len(v) > 4 else "••••"
    return jsonify({
        "llm_provider": os.environ.get("LLM_PROVIDER", "claude"),
        "llm_provider_smart": (os.environ.get("LLM_PROVIDER_SMART") or "").strip().lower(),
        "llm_provider_cheap": (os.environ.get("LLM_PROVIDER_CHEAP") or "").strip().lower(),
        "claude_budget_usd": float(os.environ.get("CLAUDE_BUDGET_USD", "0") or 0),
        "ats_target_min": int(os.environ.get("ATS_TARGET_MIN", "80") or 80),
        "ats_max_rewrites": int(os.environ.get("ATS_MAX_REWRITES", "1") or 1),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "llm_model_cheap": os.environ.get("LLM_MODEL_CHEAP", ""),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key_set":    bool(os.environ.get("OPENAI_API_KEY")),
        "gemini_key_set":    bool(os.environ.get("GEMINI_API_KEY")),
        "anthropic_key_mask": _mask(os.environ.get("ANTHROPIC_API_KEY", "")),
        "openai_key_mask":    _mask(os.environ.get("OPENAI_API_KEY", "")),
        "gemini_key_mask":    _mask(os.environ.get("GEMINI_API_KEY", "")),
    })


# ── API Health (key validity + usage counter) ───────────────────────────────

def _key_mask(v: str) -> str:
    v = (v or "").strip().strip('"').strip("'")
    if not v:
        return ""
    if len(v) <= 14:
        return v[:4] + "..."
    return v[:10] + "..." + v[-4:]


def _test_provider_key(provider: str) -> dict:
    """Make ONE tiny completion call ("Reply OK") to verify a key works.
    Returns {valid, latency_ms, error, key_mask}. Caches result via core.usage."""
    import os, time
    from core.usage import record_test
    key_var = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}[provider]
    key = (os.environ.get(key_var, "") or "").strip().strip('"').strip("'")
    if not key:
        return {"provider": provider, "configured": False, "valid": False,
                "key_mask": "", "latency_ms": 0, "error": "no key set"}
    t0 = time.time()
    try:
        if provider == "claude":
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            m = c.messages.create(model="claude-haiku-4-5-20251001", max_tokens=10,
                                  messages=[{"role": "user", "content": "Reply with the word OK only."}])
            text = (m.content[0].text or "").strip()
        elif provider == "openai":
            from openai import OpenAI
            c = OpenAI(api_key=key)
            r = c.chat.completions.create(model="gpt-4o-mini", max_tokens=10,
                                          messages=[{"role": "user", "content": "Reply with the word OK only."}])
            text = (r.choices[0].message.content or "").strip()
        else:  # gemini — new google-genai SDK
            import google.genai as genai
            c = genai.Client(api_key=key)
            r = c.models.generate_content(
                model="gemini-2.5-flash", contents="Reply with the word OK only.")
            text = (r.text or "").strip()
            if not text:
                raise RuntimeError("empty response from gemini")
        latency = int((time.time() - t0) * 1000)
        # The test call itself counts - so the user sees their counter tick up
        from core.usage import record_call, usage_summary
        record_call(provider, success=True)
        u = usage_summary().get(provider, {})
        result = {"provider": provider, "configured": True, "valid": True,
                  "key_mask": _key_mask(key), "latency_ms": latency,
                  "reply": text[:40], "error": None,
                  "calls_today": u.get("today", 0),
                  "calls_lifetime": u.get("lifetime", 0)}
        record_test(provider, valid=True, latency_ms=latency, key_mask=_key_mask(key), error=None)
        return result
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        msg = str(e)[:300]
        # Friendlier hints for common failures
        hint = ""
        if "API_KEY_INVALID" in msg or "API key not valid" in msg or "401" in msg or "invalid_api_key" in msg.lower():
            hint = "  → key is invalid. Generate a new one in Settings."
        elif "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower():
            hint = "  → rate-limited or quota exhausted. Try again later."
        elif "PERMISSION_DENIED" in msg:
            hint = "  → key valid but project lacks API access."
        from core.usage import record_call, usage_summary
        record_call(provider, success=False)
        u = usage_summary().get(provider, {})
        record_test(provider, valid=False, latency_ms=latency,
                    key_mask=_key_mask(key), error=msg + hint)
        return {"provider": provider, "configured": True, "valid": False,
                "key_mask": _key_mask(key), "latency_ms": latency,
                "error": msg + hint,
                "calls_today": u.get("today", 0),
                "calls_lifetime": u.get("lifetime", 0)}


@bp.route("/health/keys", methods=["GET"])
def health_keys_list():
    """Read-only snapshot. Returns the LAST cached test result per provider
    plus current configured-state. Doesn't burn quota - just reads disk."""
    from core.usage import test_summary, usage_summary
    tests = test_summary()
    usage = usage_summary()
    out = []
    active = (os.environ.get("LLM_PROVIDER", "") or "").strip().lower()
    key_var = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
    for p in ("claude", "openai", "gemini"):
        key = (os.environ.get(key_var[p], "") or "").strip().strip('"').strip("'")
        cached = tests.get(p) or {}
        u = usage.get(p) or {}
        out.append({
            "provider": p,
            "active": (p == active),
            "configured": bool(key),
            "key_mask": _key_mask(key),
            "last_test": cached if cached else None,
            "calls_today": u.get("today", 0),
            "calls_lifetime": u.get("lifetime", 0),
            "errors_today": u.get("errors_today", 0),
            "errors_lifetime": u.get("errors_lifetime", 0),
            "last_call_at": u.get("last_call_at", ""),
        })
    # Budget summary - lets the UI show "$X.XX / $Y.YY spent on Claude"
    try:
        from core.usage import budget_summary
        budget_usd = float(os.environ.get("CLAUDE_BUDGET_USD", "0") or 0)
        budget = budget_summary(budget_usd)
    except Exception:
        budget = None
    return jsonify({"providers": out, "active_provider": active, "budget": budget})


@bp.route("/health/keys/test", methods=["POST"])
def health_keys_test():
    """Make a real test call. Body: {"provider": "claude"|"openai"|"gemini"}
    or omit `provider` to test all configured ones."""
    body = request.get_json(silent=True) or {}
    target = (body.get("provider") or "").strip().lower()
    providers = [target] if target in ("claude", "openai", "gemini") else ["claude", "openai", "gemini"]
    results = []
    for p in providers:
        key_var = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}[p]
        if not os.environ.get(key_var):
            continue  # Skip unconfigured providers when testing all
        results.append(_test_provider_key(p))
    return jsonify({"results": results})


@bp.route("/health/usage/reset", methods=["POST"])
def health_usage_reset():
    from core.usage import reset
    reset()
    return jsonify({"ok": True})





@bp.route("/env", methods=["PUT"])
def set_env():
    """Persist key/value pairs to .env on disk and into the live process env.
    Body: {"set": {"KEY": "value", ...}}"""
    body = request.get_json(silent=True) or {}
    pairs = body.get("set") or {}
    if not isinstance(pairs, dict):
        return jsonify({"ok": False, "error": "set must be a dict"}), 400
    env_path = _root() / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    written: list[str] = []
    for k, v in pairs.items():
        v = str(v)
        # Strip stray surrounding quotes the user might have pasted
        v_stripped = v.strip().strip('"').strip("'")
        os.environ[k] = v_stripped
        # Update or append in .env
        prefix = f"{k}="
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith(prefix) or ln.startswith(f"#{prefix}"):
                lines[i] = f"{k}={v_stripped}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{k}={v_stripped}")
        written.append(k)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify({"ok": True, "written": written})


# ── Provider enable/disable toggles ─────────────────────────────────────────

@bp.route("/providers", methods=["GET"])
def get_providers():
    """Return enabled/disabled state + key presence for each provider."""
    def _enabled(p):
        v = os.environ.get(f"PROVIDER_{p.upper()}_ENABLED", "true").strip().lower()
        return v not in ("false", "0", "no", "off")
    key_var = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
    out = []
    for p in ("gemini", "claude", "openai"):
        key = os.environ.get(key_var[p], "").strip().strip('"').strip("'")
        out.append({
            "provider": p,
            "enabled": _enabled(p),
            "has_key": bool(key and not key.endswith("...")),
        })
    # Also return current cheap/smart routing
    return jsonify({
        "providers": out,
        "cheap_tier": os.environ.get("LLM_PROVIDER_CHEAP", "").strip() or "auto",
        "smart_tier": os.environ.get("LLM_PROVIDER_SMART", "").strip() or "auto",
    })


@bp.route("/providers/toggle", methods=["POST"])
def toggle_provider():
    """Body: {"provider": "gemini", "enabled": true/false}
    Writes PROVIDER_<X>_ENABLED to .env and live env."""
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").lower().strip()
    if provider not in ("claude", "openai", "gemini"):
        return jsonify({"ok": False, "error": "unknown provider"}), 400
    enabled = bool(body.get("enabled", True))
    key = f"PROVIDER_{provider.upper()}_ENABLED"
    val = "true" if enabled else "false"
    os.environ[key] = val
    env_path = _root() / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    prefix = f"{key}="
    replaced = False
    for i, ln in enumerate(lines):
        if ln.startswith(prefix):
            lines[i] = f"{key}={val}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={val}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify({"ok": True, "provider": provider, "enabled": enabled})


# ── Resume info ──────────────────────────────────────────────────────────────

@bp.route("/resume/info", methods=["GET"])
def resume_info():
    """Return info about the last run so the Resume tab can show what can be continued."""
    import json as _json
    from datetime import datetime as _dt
    root = _root()
    snap = root / "outputs" / "last_discovered.json"
    applied_csv = root / "outputs" / "applied_jobs.csv"
    pending_csv = root / "outputs" / "pending_review.csv"
    failed_csv  = root / "outputs" / "failed_jobs.csv"

    if not snap.exists():
        return jsonify({"has_checkpoint": False})

    try:
        snap_data = _json.loads(snap.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"has_checkpoint": False})

    # Age of snapshot
    age_s = (_dt.now() - _dt.fromtimestamp(snap.stat().st_mtime)).total_seconds()
    age_h = round(age_s / 3600, 1)
    fresh = age_s < 4 * 3600  # 4 hour threshold

    # Collect already-processed job_ids
    processed = set()
    for csv_path in (applied_csv, pending_csv, failed_csv):
        if csv_path.exists():
            import csv as _csv
            with csv_path.open("r", newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    jid = row.get("job_id") or row.get("url", "")
                    if jid:
                        processed.add(jid)

    total = len(snap_data)
    above_threshold = [j for j in snap_data if j.get("above_threshold", True)]
    done = sum(1 for j in above_threshold if (j.get("job_id") or j.get("url", "")) in processed)
    remaining = max(0, len(above_threshold) - done)

    return jsonify({
        "has_checkpoint": True,
        "fresh": fresh,
        "age_hours": age_h,
        "total_discovered": total,
        "above_threshold": len(above_threshold),
        "already_processed": done,
        "remaining": remaining,
        "snap_mtime": _dt.fromtimestamp(snap.stat().st_mtime).isoformat(timespec="seconds"),
    })


# ── Learning insights ────────────────────────────────────────────────────────

@bp.route("/insights", methods=["GET"])
def get_insights():
    """Return the latest run_insights.json for the Dashboard card."""
    import json as _json
    p = _root() / "outputs" / "run_insights.json"
    if not p.exists():
        return jsonify({"has_insights": False})
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        data["has_insights"] = True
        return jsonify(data)
    except Exception as e:
        return jsonify({"has_insights": False, "error": str(e)})
