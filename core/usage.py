"""
core/usage.py - lightweight per-provider call counter + key-test cache.

Tracks every LLM call we make so the user can see totals per provider in the
API Health tab. Also caches the last "is this key alive?" test result so
re-opening the tab doesn't burn quota on every refresh.

State lives in outputs/api_usage.json:
{
  "calls": {
    "gemini": {"today": 14, "lifetime": 213, "last_call_at": "2026-05-03T03:07:24",
               "today_date": "2026-05-03", "errors_today": 0},
    "claude": {...}
  },
  "tests": {
    "gemini": {"valid": true, "latency_ms": 612, "tested_at": "2026-05-03T03:00:00",
               "key_mask": "AIzaSyD-7D...D3Q", "error": null},
    ...
  }
}

All access is best-effort; if the file is corrupt we reset it. Pure stdlib,
no external deps.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# Where the usage log lives. Set externally by the orchestrator/webapp.
_USAGE_PATH: Optional[Path] = None


def configure(path: Path) -> None:
    """Tell the module where to read/write the usage log."""
    global _USAGE_PATH
    _USAGE_PATH = path


def _path() -> Path:
    if _USAGE_PATH is None:
        # Fallback: cwd/outputs/api_usage.json
        return Path.cwd() / "outputs" / "api_usage.json"
    return _USAGE_PATH


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"calls": {}, "tests": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"calls": {}, "tests": {}}


def _save(data: dict) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass  # Never crash a real call because we couldn't write a counter


# ── Call counter ────────────────────────────────────────────────────────────

# ── Pricing table: USD per 1M tokens (mid-2025 public rates) ────────────────
# Easy to update when providers change pricing. Keys MUST match what the
# providers return in `model` strings, with prefix matching.
_PRICING_PER_1M = {
    # Anthropic (claude-sonnet-4-x, claude-haiku-4-x)
    "claude-sonnet-4":  {"input":  3.00, "output": 15.00, "cached_input":  0.30},
    "claude-haiku-4":   {"input":  1.00, "output":  5.00, "cached_input":  0.10},
    "claude-opus-4":    {"input": 15.00, "output": 75.00, "cached_input":  1.50},
    # OpenAI
    "gpt-4o":           {"input":  2.50, "output": 10.00},
    "gpt-4o-mini":      {"input":  0.15, "output":  0.60},
    # Gemini (free tier returns $0; paid tier listed for completeness)
    "gemini-2.5-pro":   {"input":  1.25, "output": 10.00, "free_tier": True},
    "gemini-2.5-flash": {"input":  0.075, "output": 0.30, "free_tier": True},
    "gemini-1.5-flash": {"input":  0.075, "output": 0.30, "free_tier": True},
}


def _price_for(model: str) -> dict | None:
    """Look up pricing by longest matching prefix. Returns None for unknown."""
    if not model:
        return None
    m = model.lower()
    best = None; best_len = 0
    for k, v in _PRICING_PER_1M.items():
        if m.startswith(k) and len(k) > best_len:
            best = v; best_len = len(k)
    return best


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost for a single call. Returns 0 if free tier or unknown."""
    p = _price_for(model)
    if not p or p.get("free_tier"):
        return 0.0
    in_cost  = (prompt_tokens or 0)     * p["input"]  / 1_000_000.0
    out_cost = (completion_tokens or 0) * p["output"] / 1_000_000.0
    return round(in_cost + out_cost, 6)


def record_call(provider: str, *, success: bool = True,
                model: str = "", prompt_tokens: int = 0,
                completion_tokens: int = 0) -> None:
    """Increment counters AND track token + cost spend per provider.

    Pass model/token counts when available so the budget tracker stays accurate.
    Falls back to a coarse per-call cost estimate if tokens aren't reported."""
    data = _load()
    today = datetime.now().date().isoformat()
    calls = data.setdefault("calls", {})
    rec = calls.setdefault(provider, {
        "today": 0, "lifetime": 0, "errors_today": 0, "errors_lifetime": 0,
        "tokens_in_today": 0, "tokens_out_today": 0,
        "tokens_in_lifetime": 0, "tokens_out_lifetime": 0,
        "spend_today_usd": 0.0, "spend_lifetime_usd": 0.0,
        "last_call_at": "", "today_date": today,
    })
    # Roll the daily counters when we cross midnight
    if rec.get("today_date") != today:
        rec["today"] = 0
        rec["errors_today"] = 0
        rec["tokens_in_today"] = 0
        rec["tokens_out_today"] = 0
        rec["spend_today_usd"] = 0.0
        rec["today_date"] = today
    rec["today"] += 1
    rec["lifetime"] += 1
    if not success:
        rec["errors_today"] += 1
        rec["errors_lifetime"] = rec.get("errors_lifetime", 0) + 1
    # Token + cost accounting (only if real numbers were provided)
    if prompt_tokens or completion_tokens:
        cost = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        rec["tokens_in_today"]    = rec.get("tokens_in_today", 0) + (prompt_tokens or 0)
        rec["tokens_out_today"]   = rec.get("tokens_out_today", 0) + (completion_tokens or 0)
        rec["tokens_in_lifetime"] = rec.get("tokens_in_lifetime", 0) + (prompt_tokens or 0)
        rec["tokens_out_lifetime"]= rec.get("tokens_out_lifetime", 0) + (completion_tokens or 0)
        rec["spend_today_usd"]    = round(rec.get("spend_today_usd", 0.0) + cost, 6)
        rec["spend_lifetime_usd"] = round(rec.get("spend_lifetime_usd", 0.0) + cost, 6)
    rec["last_call_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)


def usage_summary() -> dict:
    """Return per-provider call stats for the UI."""
    data = _load()
    calls = data.get("calls", {})
    today = datetime.now().date().isoformat()
    out = {}
    for prov, rec in calls.items():
        rolled = dict(rec)
        if rolled.get("today_date") != today:
            # Daily counters reset visually but keep lifetime
            rolled["today"] = 0
            rolled["errors_today"] = 0
            rolled["tokens_in_today"] = 0
            rolled["tokens_out_today"] = 0
            rolled["spend_today_usd"] = 0.0
        # Defaults for older records that pre-date the token tracker
        for k, default in [
            ("tokens_in_today", 0), ("tokens_out_today", 0),
            ("tokens_in_lifetime", 0), ("tokens_out_lifetime", 0),
            ("spend_today_usd", 0.0), ("spend_lifetime_usd", 0.0),
        ]:
            rolled.setdefault(k, default)
        out[prov] = rolled
    return out


def budget_summary(claude_budget_usd: float = 0.0) -> dict:
    """Aggregate spend across paid providers vs the user's set budget.
    Returns {claude: {budget, spent, remaining, pct_used, est_apps_left},
             gemini: {free_tier_used_today, free_tier_limit_rpd}}."""
    u = usage_summary()
    claude_spent = (u.get("claude") or {}).get("spend_lifetime_usd", 0.0)
    remaining = max(0.0, claude_budget_usd - claude_spent)
    pct = (claude_spent / claude_budget_usd * 100.0) if claude_budget_usd > 0 else 0.0
    # Average writer call costs ~$0.026 (Sonnet, ~2K in / ~1.3K out)
    est_apps = int(remaining / 0.026) if remaining > 0 else 0
    return {
        "claude": {
            "budget_usd": round(claude_budget_usd, 2),
            "spent_usd": round(claude_spent, 4),
            "remaining_usd": round(remaining, 4),
            "pct_used": round(pct, 1),
            "est_apps_remaining": est_apps,
        },
        "gemini": {
            "calls_today": (u.get("gemini") or {}).get("today", 0),
            "free_tier_rpd_limit": 250,  # Gemini 2.5 Flash free tier (rough)
        },
    }


# ── Key-test cache ──────────────────────────────────────────────────────────

def record_test(provider: str, *, valid: bool, latency_ms: int,
                key_mask: str, error: Optional[str] = None) -> None:
    data = _load()
    tests = data.setdefault("tests", {})
    tests[provider] = {
        "valid": bool(valid),
        "latency_ms": int(latency_ms),
        "tested_at": datetime.now().isoformat(timespec="seconds"),
        "key_mask": key_mask,
        "error": (error or "")[:300] if error else None,
    }
    _save(data)


def test_summary() -> dict:
    return _load().get("tests", {})


def reset() -> None:
    """Clear all counters - exposed so the user can wipe stats from the UI."""
    _save({"calls": {}, "tests": {}})
