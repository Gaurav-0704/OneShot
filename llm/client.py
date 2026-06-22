"""
Unified LLM client - Claude / OpenAI / Gemini.

Two tiers:
  complete()        smart model (resume + cover letter)
  complete_cheap()  cheap model (parsing, scoring, audits, Q&A)

Auto-fallback: if the primary provider hits a rate-limit (429) the client
automatically tries the next enabled provider with a real key.

Provider priority order (cheap): gemini → claude → openai
Provider priority order (smart): claude → gemini → openai

Enable/disable per provider via .env:
  PROVIDER_CLAUDE_ENABLED=true   (default true)
  PROVIDER_GEMINI_ENABLED=true   (default true)
  PROVIDER_OPENAI_ENABLED=true   (default true)
"""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

log = logging.getLogger("llm.client")

Provider = Literal["claude", "openai", "gemini"]

_KEY_VAR = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
_ENABLED_VAR = {
    "claude": "PROVIDER_CLAUDE_ENABLED",
    "openai": "PROVIDER_OPENAI_ENABLED",
    "gemini": "PROVIDER_GEMINI_ENABLED",
}
_FAMILY_PREFIX = {
    "claude": ("claude-",),
    "openai": ("gpt-", "o1-", "o3-", "o4-"),
    "gemini": ("gemini-",),
}
_DEFAULT_SMART = {"claude": "claude-sonnet-4-6", "openai": "gpt-4o", "gemini": "gemini-2.5-pro"}
_DEFAULT_CHEAP = {"claude": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "gemini": "gemini-2.5-flash"}

# Priority fallback order per tier
_CHEAP_ORDER = ["gemini", "claude", "openai"]
_SMART_ORDER = ["claude", "gemini", "openai"]


# ── Provider enable/disable ───────────────────────────────────────────────────

def _provider_enabled(provider: str) -> bool:
    """True unless explicitly disabled via PROVIDER_<X>_ENABLED=false."""
    val = os.environ.get(_ENABLED_VAR.get(provider, ""), "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _resolve(env_var: str, default: str = "") -> str:
    return os.environ.get(env_var, default).strip().strip("'").strip('"').lower()


def _get_api_key(provider: str) -> str:
    key = os.environ.get(_KEY_VAR[provider], "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError(f"No API key for {provider}. Add it in the Settings tab.")
    if key in {"sk-...", "sk-ant-...", "AIza..."} or key.endswith("..."):
        raise RuntimeError(f"{provider} key looks like a placeholder ({key[:8]}...).")
    return key


def _provider_has_real_key(provider: str) -> bool:
    try:
        _get_api_key(provider)
        return True
    except RuntimeError:
        return False


def _provider_usable(provider: str) -> bool:
    """Enabled AND has a real key."""
    return _provider_enabled(provider) and _provider_has_real_key(provider)


def _fallback_chain(tier: str) -> list[str]:
    """Ordered list of usable providers for this tier, respecting enable flags."""
    order = _CHEAP_ORDER if tier == "cheap" else _SMART_ORDER
    return [p for p in order if _provider_usable(p)]


def _get_provider(tier: str = "smart") -> str:
    """Pick the primary provider for a tier, respecting explicit overrides."""
    tier_var = "LLM_PROVIDER_SMART" if tier == "smart" else "LLM_PROVIDER_CHEAP"
    explicit = _resolve(tier_var) or _resolve("LLM_PROVIDER")
    if explicit in _KEY_VAR and _provider_usable(explicit):
        return explicit
    # Fall back to first usable in priority order
    chain = _fallback_chain(tier)
    if chain:
        return chain[0]
    raise RuntimeError("No usable LLM provider found. Enable at least one in the Settings tab.")


def _model_matches_provider(model: str, provider: str) -> bool:
    return any(model.startswith(pfx) for pfx in _FAMILY_PREFIX[provider])


def _get_model(provider: str) -> str:
    explicit = os.environ.get("LLM_MODEL", "").strip()
    if explicit and _model_matches_provider(explicit, provider):
        return explicit
    return _DEFAULT_SMART[provider]


def _get_cheap_model(provider: str) -> str:
    explicit = os.environ.get("LLM_MODEL_CHEAP", "").strip()
    if explicit and _model_matches_provider(explicit, provider):
        return explicit
    return _DEFAULT_CHEAP[provider]


# ── Rate-limit detection ──────────────────────────────────────────────────────

def _is_rate_limit(exc: Exception) -> bool:
    """Return True if this exception looks like a 429 / quota error."""
    msg = str(exc).lower()
    return any(k in msg for k in (
        "429", "rate limit", "quota", "too many requests",
        "resource_exhausted", "exceeded", "retry_delay",
    ))


# ── Provider-specific callers ─────────────────────────────────────────────────

def _call_claude(system, user, model, api_key, max_tokens, json_mode=False):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    sys_p = system + ("\n\nReturn ONLY a valid JSON object, no commentary, no markdown fences." if json_mode else "")
    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=sys_p,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    _call_claude.last_usage = {
        "model": model,
        "prompt_tokens": getattr(msg.usage, "input_tokens", 0) if hasattr(msg, "usage") else 0,
        "completion_tokens": getattr(msg.usage, "output_tokens", 0) if hasattr(msg, "usage") else 0,
    }
    return text


def _call_openai(system, user, model, api_key, max_tokens, json_mode=False):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    kwargs = dict(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content.strip()
    _call_openai.last_usage = {
        "model": model,
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) if hasattr(resp, "usage") else 0,
        "completion_tokens": getattr(resp.usage, "completion_tokens", 0) if hasattr(resp, "usage") else 0,
    }
    return text


def _call_gemini(system, user, model, api_key, max_tokens, json_mode=False):
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    genai.configure(api_key=api_key)
    cfg_kwargs = dict(max_output_tokens=max_tokens)
    if json_mode:
        cfg_kwargs["response_mime_type"] = "application/json"
    try:
        thinking_cfg = genai.types.ThinkingConfig(thinking_budget=0)
        cfg_kwargs["thinking_config"] = thinking_cfg
    except Exception:
        pass
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT:        HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    gen = genai.GenerativeModel(
        model_name=model,
        system_instruction=system,
        generation_config=genai.GenerationConfig(**cfg_kwargs),
        safety_settings=safety_settings,
    )
    resp = gen.generate_content(user)
    try:
        cand = (resp.candidates or [None])[0]
        if cand is None:
            return ""
        finish_reason = getattr(cand, "finish_reason", None)
        parts = getattr(getattr(cand, "content", None), "parts", []) or []
        text_chunks = [getattr(p, "text", "") for p in parts]
        text = "".join(t for t in text_chunks if t).strip()
        if finish_reason and int(finish_reason) not in (0, 1):
            log.warning(f"gemini finish_reason={finish_reason} returned {len(text)} chars")
        meta = getattr(resp, "usage_metadata", None)
        _call_gemini.last_usage = {
            "model": model,
            "prompt_tokens": getattr(meta, "prompt_token_count", 0) if meta else 0,
            "completion_tokens": getattr(meta, "candidates_token_count", 0) if meta else 0,
        }
        if not text:
            raise RuntimeError(f"Gemini returned no text (finish_reason={finish_reason}).")
        return text
    except Exception as e:
        try:
            return resp.text.strip()
        except Exception:
            raise e


def _dispatch(system, user, provider, model, api_key, max_tokens, json_mode=False):
    if provider == "claude":
        return _call_claude(system, user, model, api_key, max_tokens, json_mode)
    if provider == "openai":
        return _call_openai(system, user, model, api_key, max_tokens, json_mode)
    if provider == "gemini":
        return _call_gemini(system, user, model, api_key, max_tokens, json_mode)
    raise ValueError(f"Unknown provider: {provider!r}")


def _record(provider: str, success: bool, usage: dict | None = None) -> None:
    try:
        from core.usage import record_call
        u = usage or {}
        record_call(
            provider, success=success,
            model=u.get("model", ""),
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
        )
    except Exception:
        pass


def _last_usage_for(provider: str) -> dict | None:
    fn = {"claude": _call_claude, "openai": _call_openai, "gemini": _call_gemini}.get(provider)
    if fn is None:
        return None
    return getattr(fn, "last_usage", None)


# ── Dispatch with auto-fallback ───────────────────────────────────────────────

def _dispatch_with_fallback(system, user, tier, *, primary_provider=None,
                             primary_model=None, max_tokens, json_mode=False):
    """
    Try the primary provider. If it raises a rate-limit error, automatically
    try the next enabled provider in the fallback chain.
    Logs each fallback so the user can see it in the Live tab.
    """
    chain = _fallback_chain(tier)
    if primary_provider and primary_provider not in chain:
        chain = [primary_provider] + chain  # always try the explicit pick first

    tried = []
    last_exc = None
    for provider in chain:
        if provider in tried:
            continue
        tried.append(provider)
        model = primary_model if (provider == primary_provider and primary_model) else (
            _get_model(provider) if tier == "smart" else _get_cheap_model(provider)
        )
        try:
            api_key = _get_api_key(provider)
        except RuntimeError:
            continue
        try:
            out = _dispatch(system, user, provider, model, api_key, max_tokens, json_mode)
            _record(provider, success=True, usage=_last_usage_for(provider))
            if len(tried) > 1:
                log.info(f"fallback succeeded with {provider} (tried: {tried[:-1]})")
            return out
        except Exception as exc:
            _record(provider, success=False, usage=_last_usage_for(provider))
            last_exc = exc
            if _is_rate_limit(exc):
                log.warning(f"{provider} rate-limited — trying next provider in chain")
                continue
            # Non-rate-limit error: re-raise immediately (don't silently swallow bugs)
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"No providers available for tier={tier}")


# ── Public API ────────────────────────────────────────────────────────────────

def complete(system, user, *, provider=None, model=None, api_key=None,
             max_tokens=4096, json_mode=False):
    """Smart-tier completion - resume + cover letter writing."""
    primary = provider or _get_provider("smart")
    return _dispatch_with_fallback(
        system, user, "smart",
        primary_provider=primary,
        primary_model=model,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


def complete_cheap(system, user, *, provider=None, api_key=None,
                   max_tokens=1024, json_mode=False):
    """Cheap-tier - Haiku / Gemini Flash / GPT-4o-mini. Auto-falls back on 429."""
    primary = provider or _get_provider("cheap")
    return _dispatch_with_fallback(
        system, user, "cheap",
        primary_provider=primary,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )
