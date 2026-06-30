"""
Unified LLM client — SINGLE provider.

The whole engine runs on ONE provider, chosen in Settings via LLM_PROVIDER
(claude | openai | gemini, default claude). Every call — resume writing,
cover letters, fit scoring, parsing, copilot — uses that provider.

Two tiers map to two MODELS of the SAME provider:
  complete()        smart model (resume + cover letter)
  complete_cheap()  cheap model (parsing, scoring, audits, Q&A)

There is NO cross-provider auto-fallback. If the selected provider fails,
the error is raised with a clear message so the user can fix the key or
switch provider in Settings.

Model defaults per provider:
  claude  → smart: claude-sonnet-4-6   cheap: claude-haiku-4-5-20251001
  openai  → smart: gpt-4o              cheap: gpt-4o-mini
  gemini  → smart: gemini-2.5-pro      cheap: gemini-2.5-flash

Overrides (only honored when they belong to the selected provider):
  LLM_MODEL        smart-tier model
  LLM_MODEL_CHEAP  cheap-tier model
"""
from __future__ import annotations

import logging
import os
from typing import Literal

log = logging.getLogger("llm.client")

Provider = Literal["claude", "openai", "gemini"]

_KEY_VAR = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
_FAMILY_PREFIX = {
    "claude": ("claude-",),
    "openai": ("gpt-", "o1-", "o3-", "o4-"),
    "gemini": ("gemini-",),
}
_DEFAULT_SMART = {"claude": "claude-sonnet-4-6", "openai": "gpt-4o", "gemini": "gemini-2.5-pro"}
_DEFAULT_CHEAP = {"claude": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "gemini": "gemini-2.5-flash"}

_DEFAULT_PROVIDER = "claude"


# ── Provider selection ────────────────────────────────────────────────────────

def _selected_provider() -> str:
    """The single provider the whole engine uses. Defaults to claude."""
    p = (os.environ.get("LLM_PROVIDER", "") or "").strip().lower().strip("'").strip('"')
    if p in _KEY_VAR:
        return p
    return _DEFAULT_PROVIDER


# Public alias kept for callers (e.g. agents/tailor.py) — tier is ignored now
# since both tiers use the same selected provider.
def _get_provider(tier: str = "smart") -> str:
    return _selected_provider()


def _get_api_key(provider: str) -> str:
    key = os.environ.get(_KEY_VAR[provider], "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError(
            f"No API key for the selected provider '{provider}'. "
            f"Add the {_KEY_VAR[provider]} in the Settings tab or switch provider."
        )
    if key in {"sk-...", "sk-ant-...", "AIza..."} or key.endswith("..."):
        raise RuntimeError(
            f"The '{provider}' key looks like a placeholder ({key[:8]}...). "
            f"Paste a real key in Settings or switch provider."
        )
    return key


def _provider_has_real_key(provider: str) -> bool:
    try:
        _get_api_key(provider)
        return True
    except RuntimeError:
        return False


def _model_matches_provider(model: str, provider: str) -> bool:
    return any(model.startswith(pfx) for pfx in _FAMILY_PREFIX[provider])


def _get_model(provider: str) -> str:
    """Smart-tier model: env override only if it belongs to the provider."""
    explicit = os.environ.get("LLM_MODEL", "").strip()
    if explicit and _model_matches_provider(explicit, provider):
        return explicit
    return _DEFAULT_SMART[provider]


def _get_cheap_model(provider: str) -> str:
    """Cheap-tier model: env override only if it belongs to the provider."""
    explicit = os.environ.get("LLM_MODEL_CHEAP", "").strip()
    if explicit and _model_matches_provider(explicit, provider):
        return explicit
    return _DEFAULT_CHEAP[provider]


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
    # Use the new google-genai SDK (google-generativeai is deprecated)
    import google.genai as genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)

    cfg_kwargs: dict = dict(max_output_tokens=max_tokens, system_instruction=system)
    if json_mode:
        cfg_kwargs["response_mime_type"] = "application/json"

    config = genai_types.GenerateContentConfig(**cfg_kwargs)
    resp = client.models.generate_content(model=model, contents=user, config=config)

    text = (resp.text or "").strip()
    meta = getattr(resp, "usage_metadata", None)
    _call_gemini.last_usage = {
        "model": model,
        "prompt_tokens":     getattr(meta, "prompt_token_count",     0) if meta else 0,
        "completion_tokens": getattr(meta, "candidates_token_count", 0) if meta else 0,
    }
    if not text:
        raise RuntimeError("Gemini returned no text.")
    return text


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


# ── Single-provider dispatch ──────────────────────────────────────────────────

def _run(system, user, *, tier: str, max_tokens: int, json_mode: bool) -> str:
    """Resolve the selected provider + tier model and make exactly one call.
    No fallback — a failure is raised with an actionable message."""
    provider = _selected_provider()
    model = _get_model(provider) if tier == "smart" else _get_cheap_model(provider)
    api_key = _get_api_key(provider)   # raises a clear RuntimeError if missing
    try:
        out = _dispatch(system, user, provider, model, api_key, max_tokens, json_mode)
        _record(provider, success=True, usage=_last_usage_for(provider))
        return out
    except Exception as exc:
        _record(provider, success=False, usage=_last_usage_for(provider))
        raise RuntimeError(
            f"Selected provider '{provider}' failed: {exc}. "
            f"Check the {_KEY_VAR[provider]} in Settings or switch provider."
        ) from exc


# ── Public API ────────────────────────────────────────────────────────────────

def complete(system, user, *, provider=None, model=None, api_key=None,
             max_tokens=4096, json_mode=False):
    """Smart-tier completion (resume + cover letter) on the selected provider.

    `provider`/`model`/`api_key` are accepted for backward-compat but the engine
    always uses the single selected provider; set LLM_MODEL to override the
    smart-tier model for that provider."""
    return _run(system, user, tier="smart", max_tokens=max_tokens, json_mode=json_mode)


def complete_cheap(system, user, *, provider=None, api_key=None,
                   max_tokens=1024, json_mode=False):
    """Cheap-tier completion on the selected provider (no fallback)."""
    return _run(system, user, tier="cheap", max_tokens=max_tokens, json_mode=json_mode)
