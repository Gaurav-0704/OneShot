"""
check_keys.py - one-shot test that every configured API key actually works.

Run from the project root with the venv active:
  venv\Scripts\python.exe check_keys.py

For each provider whose key is set in .env (or in the OS env), it makes ONE
tiny completion call ("reply with the word OK") and prints the result. No
prompts that count toward your quota in any meaningful way - costs ~1 token.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


# Load .env so the script sees what the Flask app sees
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if k.strip() and v and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v


_load_env()


def _short(v: str, n: int = 10) -> str:
    return v[:n] + "..." + v[-4:] if v and len(v) > n + 4 else v


def check_anthropic():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip("'").strip('"')
    if not key:
        return ("Anthropic", "skipped", "no key set")
    try:
        import anthropic
    except ImportError:
        return ("Anthropic", "fail", "anthropic package not installed")
    t0 = time.time()
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with the word OK only."}],
        )
        out = msg.content[0].text.strip()
        return ("Anthropic", "ok", f"{out!r} in {time.time()-t0:.1f}s · key {_short(key)}")
    except Exception as e:
        return ("Anthropic", "fail", f"{type(e).__name__}: {str(e)[:160]} · key {_short(key)}")


def check_openai():
    key = os.environ.get("OPENAI_API_KEY", "").strip().strip("'").strip('"')
    if not key:
        return ("OpenAI", "skipped", "no key set")
    try:
        from openai import OpenAI
    except ImportError:
        return ("OpenAI", "fail", "openai package not installed")
    t0 = time.time()
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with the word OK only."}],
        )
        out = resp.choices[0].message.content.strip()
        return ("OpenAI", "ok", f"{out!r} in {time.time()-t0:.1f}s · key {_short(key)}")
    except Exception as e:
        return ("OpenAI", "fail", f"{type(e).__name__}: {str(e)[:160]} · key {_short(key)}")


def check_gemini():
    key = os.environ.get("GEMINI_API_KEY", "").strip().strip("'").strip('"')
    if not key:
        return ("Gemini", "skipped", "no key set")
    try:
        import google.generativeai as genai
    except ImportError:
        return ("Gemini", "fail", "google-generativeai package not installed")
    t0 = time.time()
    try:
        genai.configure(api_key=key)
        gen = genai.GenerativeModel("gemini-2.5-flash")
        resp = gen.generate_content("Reply with the word OK only.")
        # Don't trust resp.text - it raises if finish_reason != STOP
        cand = (resp.candidates or [None])[0]
        if not cand:
            return ("Gemini", "fail", "no candidates returned")
        parts = getattr(getattr(cand, "content", None), "parts", []) or []
        text = "".join(getattr(p, "text", "") for p in parts).strip()
        if not text:
            fr = getattr(cand, "finish_reason", "?")
            return ("Gemini", "fail", f"empty text (finish_reason={fr}) · key {_short(key)}")
        return ("Gemini", "ok", f"{text!r} in {time.time()-t0:.1f}s · key {_short(key)}")
    except Exception as e:
        msg = str(e)[:200]
        hint = ""
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            hint = "  → key is invalid. Generate a new one at https://aistudio.google.com/apikey"
        elif "429" in msg or "quota" in msg.lower():
            hint = "  → daily free-tier quota exhausted. Wait until midnight Pacific or upgrade."
        elif "PERMISSION_DENIED" in msg:
            hint = "  → key valid but project lacks Generative Language API access."
        return ("Gemini", "fail", f"{type(e).__name__}: {msg} · key {_short(key)}{hint}")


def main() -> int:
    print("\nOneShot — API key health check")
    print("=" * 70)
    results = [check_anthropic(), check_openai(), check_gemini()]
    width = max(len(r[0]) for r in results)
    any_fail = False
    for name, status, detail in results:
        icon = {"ok": "✓", "fail": "✗", "skipped": "·"}[status]
        print(f"  {icon} {name:<{width}}  {status:<7}  {detail}")
        if status == "fail":
            any_fail = True
    print("=" * 70)
    active = os.environ.get("LLM_PROVIDER", "claude")
    print(f"  Active provider in .env: LLM_PROVIDER={active}")
    print()
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
