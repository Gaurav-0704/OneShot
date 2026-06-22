"""
core/text_clean.py — deterministic + optional LLM humaniser

Public surface:
  clean(text, kind)               -> str    always runs; no LLM
  polish(text, kind, facts)       -> str    cheap-LLM pass; only if text is long enough
  check(text, kind, facts)        -> dict   {ok, issues: [str]}
  deliver(text, kind, facts)      -> dict   {text, report: {ok, issues}, polished: bool}

kind values: "resume" | "cover" | "answer" | "showcase"
facts: plain text block of known true claims (master resume + profile dump)
       used only to check for fabrication — never injected into the final text.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

log = logging.getLogger(__name__)

Kind = Literal["resume", "cover", "answer", "showcase"]

# ── Filler words / phrases that signal AI prose ───────────────────────────────

_FILLER_SUBS: list[tuple[re.Pattern, str]] = [
    # Overused AI verbs
    (re.compile(r"\bleverage[sd]?\b",               re.I), "use"),
    (re.compile(r"\bdelve[sd]?\b",                  re.I), "explore"),
    (re.compile(r"\butilize[sd]?\b",                re.I), "use"),
    (re.compile(r"\bfacilitate[sd]?\b",             re.I), "support"),
    (re.compile(r"\bcultivat\w+\b",                 re.I), "build"),
    (re.compile(r"\bfoster\w*\b",                   re.I), "support"),
    (re.compile(r"\bempow\w+\b",                    re.I), "enable"),
    # AI filler phrases
    (re.compile(r"\bin today'?s (fast[- ]paced |dynamic |competitive )?(world|landscape|environment)\b", re.I), ""),
    (re.compile(r"\bin the (fast[- ]paced|dynamic|ever[- ]changing) (world|landscape) of\b", re.I), "in"),
    (re.compile(r"\bI am (excited|thrilled|passionate|eager) to\b", re.I), "I want to"),
    (re.compile(r"\bI am (excited|thrilled) (about|by)\b",          re.I), "I value"),
    (re.compile(r"\bI am (deeply |truly )?passionate about\b",      re.I), "I focus on"),
    (re.compile(r"\bI am (very |highly )?motivated (to|by)\b",      re.I), "I aim to"),
    (re.compile(r"\bI would be remiss\b",                           re.I), "I should note"),
    # Robotic transitions
    (re.compile(r"\bIn conclusion,?\b",             re.I), ""),
    (re.compile(r"\bTo summarize,?\b",              re.I), ""),
    (re.compile(r"\bIt is worth noting that\b",     re.I), ""),
    (re.compile(r"\bIt goes without saying\b",      re.I), ""),
    (re.compile(r"\bNeedless to say,?\b",           re.I), ""),
    (re.compile(r"\bAt the end of the day,?\b",     re.I), ""),
    (re.compile(r"\bIn light of the above,?\b",     re.I), ""),
    # Promotional fluff
    (re.compile(r"\bworld-?class\b",                re.I), "strong"),
    (re.compile(r"\bcutting-?edge\b",               re.I), "modern"),
    (re.compile(r"\bstate-?of-?the-?art\b",         re.I), "current"),
    (re.compile(r"\bgroundbreaking\b",              re.I), "notable"),
    (re.compile(r"\brevolutionary\b",               re.I), "significant"),
    (re.compile(r"\bsynergiz\w+\b",                 re.I), "combine"),
    (re.compile(r"\bsynergies\b",                   re.I), "shared benefits"),
    (re.compile(r"\bparadigm shift\b",              re.I), "change"),
    (re.compile(r"\bthought leader\b",              re.I), "expert"),
    (re.compile(r"\bvalue-?add(ed)?\b",             re.I), "contribution"),
    (re.compile(r"\bimpactful\b",                   re.I), "effective"),
    (re.compile(r"\bseamlessly\b",                  re.I), ""),
    (re.compile(r"\brobust\b(?! (error|exception|fallback|retry|logging))", re.I), "solid"),
    (re.compile(r"\bcomprehensive\b",               re.I), "full"),
]

# Em-dash overuse: more than 2 in a paragraph → replace extras with commas / semicolons
_EM_DASH = re.compile(r"—")

# Leftover markdown artefacts in prose
_MARKDOWN_FENCE  = re.compile(r"^```\w*\s*$",       re.MULTILINE)
_MARKDOWN_BOLD   = re.compile(r"\*\*(.+?)\*\*")
_MARKDOWN_ITALIC = re.compile(r"\*(.+?)\*")
_MARKDOWN_HASH   = re.compile(r"^#{1,3}\s+",         re.MULTILINE)

# Placeholder artefacts
_PLACEHOLDER = re.compile(
    r"\{\{[^}]+\}\}"                      # {{placeholder}}
    r"|\[INSERT [A-Z ]+\]"                # [INSERT NAME HERE]
    r"|\[YOUR [A-Z ]+\]"                  # [YOUR COMPANY]
    r"|<\s*(insert|placeholder)[^>]*>",   # <insert ...>
    re.I,
)

# JSON remnants that sneak into prose
_JSON_REMNANT = re.compile(
    r'^\s*"[a-z_]+"\s*:\s*"',            # "key": "  at line start
    re.MULTILINE,
)

# Min length to bother with LLM polish (chars)
_POLISH_MIN_LEN = {"resume": 400, "cover": 300, "answer": 0, "showcase": 200}


# ── Deterministic cleaner ─────────────────────────────────────────────────────

def clean(text: str, kind: Kind = "resume") -> str:
    """Deterministic pass — no LLM, always fast."""
    if not text:
        return text

    # Strip stray markdown (only in prose kinds, not resume bullets)
    if kind in ("cover", "answer", "showcase"):
        text = _MARKDOWN_FENCE.sub("", text)
        text = _MARKDOWN_HASH.sub("", text)
        text = _MARKDOWN_BOLD.sub(r"\1", text)
        text = _MARKDOWN_ITALIC.sub(r"\1", text)

    # Apply filler substitutions
    for pat, repl in _FILLER_SUBS:
        text = pat.sub(repl, text)

    # Collapse double-spaces left by empty replacements
    text = re.sub(r"  +", " ", text)
    text = re.sub(r" ([,;.!?])", r"\1", text)   # space before punctuation
    text = re.sub(r"\n{3,}", "\n\n", text)        # max 1 blank line between paragraphs

    # Em-dash overuse: if > 2 per paragraph, replace extras with ", "
    if kind in ("cover", "resume", "showcase"):
        paras = text.split("\n\n")
        cleaned_paras = []
        for para in paras:
            dashes = _EM_DASH.findall(para)
            if len(dashes) > 2:
                count = [0]
                def _replace_em(m):
                    count[0] += 1
                    return m.group() if count[0] <= 2 else ", "
                para = _EM_DASH.sub(_replace_em, para)
            cleaned_paras.append(para)
        text = "\n\n".join(cleaned_paras)

    # Strip residual placeholders
    text = _PLACEHOLDER.sub("", text)
    # Strip JSON remnants
    text = _JSON_REMNANT.sub("", text)

    return text.strip()


# ── Optional LLM polish ───────────────────────────────────────────────────────

_POLISH_SYSTEM = (
    "You are a professional editor making text sound natural and human. "
    "ABSOLUTE RULES — violating any of these is a critical failure:\n"
    "1. Do NOT add any fact, skill, company, metric, year, number, technology, "
    "   project, or claim that is not already present in the input text.\n"
    "2. Do NOT invent achievements, responsibilities, or experience.\n"
    "3. Preserve every specific detail (names, numbers, dates, URLs) exactly.\n"
    "4. Output only the rewritten text — no commentary, no preamble."
)


def _polish_prompt(text: str, kind: Kind) -> str:
    guidance = {
        "resume":   "Keep it concise and action-verb-led. Plain prose, no markdown.",
        "cover":    "First person, warm but professional, ≤4 paragraphs, blank line between each.",
        "answer":   "Concise, direct, professional, ≤80 words.",
        "showcase": "Third person or first person, clean prose, no marketing fluff.",
    }.get(kind, "Clean, professional prose.")
    return f"Rewrite the following to sound natural and human-written. {guidance}\n\n{text}"


def polish(text: str, kind: Kind = "resume", facts: str = "") -> str:
    """Cheap-LLM polish. Only called when text is long enough to need it."""
    min_len = _POLISH_MIN_LEN.get(kind, 300)
    if len(text) < min_len:
        return text
    try:
        from llm.client import complete_cheap
        return complete_cheap(_POLISH_SYSTEM, _polish_prompt(text, kind), max_tokens=3000).strip()
    except Exception as e:
        log.warning(f"text_clean.polish failed ({kind}): {e} — returning cleaned text as-is")
        return text


# ── Smoke-test checker ────────────────────────────────────────────────────────

def check(text: str, kind: Kind = "resume", facts: str = "") -> dict:
    """
    Smoke test. Returns {ok: bool, issues: [str]}.
    Checks: no leftover JSON/markdown/placeholders, structural shape, anti-fabrication.
    """
    issues: list[str] = []

    if not text or not text.strip():
        return {"ok": False, "issues": ["text is empty"]}

    # Leftover placeholders
    if _PLACEHOLDER.search(text):
        issues.append("contains unfilled placeholder (e.g. {{...}} or [INSERT ...])")

    # Leftover JSON keys
    if _JSON_REMNANT.search(text):
        issues.append("contains raw JSON key-value remnants")

    # Leftover code fences
    if re.search(r"^```", text, re.MULTILINE):
        issues.append("contains markdown code fences")

    # Cover letter: must have >=3 blank-line-separated paragraphs
    if kind == "cover":
        paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if len(paras) < 3:
            issues.append(
                f"cover letter has only {len(paras)} paragraph(s); "
                "expected ≥3 separated by blank lines"
            )
        if not any(p.lower().startswith("dear ") for p in paras):
            issues.append("cover letter missing 'Dear ...' salutation")
        last = paras[-1] if paras else ""
        if not re.search(r"\bsincerely\b|\bregards\b|\bthank\b", last, re.I):
            issues.append("cover letter missing sign-off (Sincerely / Best regards / Thank you)")

    # Resume: must not be shorter than 500 chars (probably truncated)
    if kind == "resume" and len(text) < 500:
        issues.append(f"resume seems too short ({len(text)} chars) — may be truncated")

    # Anti-fabrication: scan for year/number claims not in facts
    if facts and kind in ("resume", "cover", "answer"):
        year_claims = re.findall(r"(\d+)\s+year", text.lower())
        for yr in year_claims:
            if yr not in facts.lower():
                issues.append(
                    f"possible fabrication: '{yr} year' not found in known facts"
                )
                break  # report once per text

    return {"ok": len(issues) == 0, "issues": issues}


# ── Deliver: clean -> polish -> check -> retry ────────────────────────────────

def deliver(
    text: str,
    kind: Kind = "resume",
    facts: str = "",
    *,
    skip_polish: bool = False,
) -> dict:
    """
    Full pipeline:
      1. clean()   — deterministic, always
      2. polish()  — cheap LLM, only when long enough and not skip_polish
      3. check()   — smoke test
      4. If check fails and issue is fixable: one targeted clean() retry
      5. Return {text, report: {ok, issues}, polished: bool}
    """
    text = clean(text, kind)

    polished = False
    if not skip_polish:
        polished_text = polish(text, kind, facts)
        if polished_text and polished_text.strip() and polished_text != text:
            text = polished_text
            polished = True

    report = check(text, kind, facts)

    # One targeted cleanup retry if checker caught fixable issues
    if not report["ok"]:
        fixable = {"contains unfilled placeholder", "contains raw JSON key-value remnants",
                   "contains markdown code fences"}
        if any(any(f in iss for f in fixable) for iss in report["issues"]):
            text = clean(text, kind)   # second deterministic pass
            report = check(text, kind, facts)

    return {"text": text, "report": report, "polished": polished}
