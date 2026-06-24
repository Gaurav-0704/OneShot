"""
agents/tailor.py - TailorAgent

Replaces ResearchAgent + WriterAgent as a single, coherent step.

Phase 1 — Research/enrich (formerly ResearchAgent.enrich):
  Fetches the full JD (sometimes truncated by scrapers), builds a company
  brief with one cheap LLM call (skipped when JD is already long enough),
  and stores all context on the JobApplication so PackagerAgent and future
  Copilot features can reuse it without re-fetching.

Phase 2 — Write (formerly WriterAgent.write):
  One smart-tier LLM call produces a tailored resume + cover letter + ATS
  audit JSON.  Optional rewrite passes when the first attempt scores below
  ATS_TARGET_MIN.  Always keeps the best attempt.

Public API:
  agent = TailorAgent(profile, do_research=True, run_ats_check=True)
  app   = agent.tailor(app)      # mutates app in-place, returns it
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agents.base import Agent
from core.pdf_generator import generate_cover_letter_pdf, generate_resume_pdf
from llm.client import complete
from llm.prompts import (
    COMBINED_WRITER_SYSTEM, combined_writer_user_prompt,
    IMPROVE_WRITER_SYSTEM, improve_writer_user_prompt,
)
from models import JobApplication

log = logging.getLogger(__name__)

# ── Research prompts ──────────────────────────────────────────────────────────

_RESEARCH_SYSTEM = """You are a researcher preparing a one-page brief on a company
for a job applicant. You read raw HTML/text from a job posting and (when supplied)
the company's About page, then output a tight JSON brief.

Be concrete. No marketing fluff. If a field is unknown, leave it as an empty string.
Output ONLY valid JSON, no commentary, no code fences."""


def _research_user_prompt(job_title: str, company: str, jd_text: str, about_text: str) -> str:
    return f"""Produce a JSON brief for this role.

=== JOB TITLE ===
{job_title}

=== COMPANY ===
{company}

=== JOB DESCRIPTION ===
{jd_text[:4000]}

=== COMPANY ABOUT (may be empty) ===
{about_text[:3000]}

Output exactly this JSON shape (no other keys):
{{
  "company_about": "<2-3 sentences on what the company does>",
  "company_size": "<startup | small | mid | large | unknown>",
  "company_industry": "<one phrase>",
  "company_website": "<url or empty>",
  "key_responsibilities": ["<bullet>", "..."],
  "key_requirements": ["<bullet>", "..."],
  "tech_stack": ["<tech>", "..."],
  "salary_signal": "<phrase or empty>",
  "notes": "<one sentence the writer should know>"
}}"""


# ── Writer JSON helpers ───────────────────────────────────────────────────────

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)


def _parse_json_loose(raw: str):
    if not raw:
        return None
    text = _FENCE.sub("", raw.strip())
    try:
        return json.loads(text)
    except Exception:
        pass
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last <= first:
        return None
    substr = text[first:last + 1]
    try:
        return json.loads(substr)
    except Exception:
        pass
    out, in_str, esc = [], False, False
    for ch in substr:
        if esc:
            out.append(ch); esc = False; continue
        if ch == "\\":
            out.append(ch); esc = True; continue
        if ch == '"':
            in_str = not in_str
        elif in_str and ch == "\n":
            out.append("\\n"); continue
        elif in_str and ch == "\r":
            continue
        elif in_str and ch == "\t":
            out.append("\\t"); continue
        out.append(ch)
    repaired = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        return json.loads(repaired)
    except Exception:
        return None


def _load_formatting_instructions(root: Path) -> str:
    p = root / "config" / "resume_instructions.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "Standard ATS-friendly resume and cover letter formatting."


# ── TailorAgent ───────────────────────────────────────────────────────────────

class TailorAgent(Agent):
    name = "tailor"
    role = (
        "Researches the company and job, then writes an ATS-ready tailored resume "
        "and cover letter using only facts from the candidate's master resume."
    )

    # If the JD is at least this long, skip the LLM research brief — the
    # writer already has enough context from the JD itself. Saves one cheap call.
    SKIP_RESEARCH_IF_JD_LONGER_THAN = 1500

    def __init__(
        self,
        profile,
        *,
        do_research: bool = True,
        run_ats_check: bool = True,   # kept for compat; ATS audit is baked into writer call
        fetch_company_page: bool = True,
    ):
        super().__init__(profile=profile, dry_run=True)
        self.do_research = do_research
        self.run_ats_check = run_ats_check
        self.fetch_company_page = fetch_company_page

    # ── Public API ────────────────────────────────────────────────────────────

    def tailor(self, app: JobApplication) -> JobApplication:
        """Phase 1 (research) then Phase 2 (write) in one call."""
        if self.do_research:
            self._enrich(app)
        self._write(app)
        return app

    # ── Phase 1: Research / enrich ────────────────────────────────────────────

    def _enrich(self, app: JobApplication) -> None:
        """Populate app.enriched_description and app.research_notes.
        Stores structured company brief fields on the app for Copilot reuse."""
        jd_text = app.raw_description or self._fetch_url_text(app.url)
        app.enriched_description = jd_text

        # Long JD → skip the LLM brief (writer has enough from the JD)
        if len(jd_text) >= self.SKIP_RESEARCH_IF_JD_LONGER_THAN:
            app.research_notes = (
                f"Company: {app.company}. "
                f"(Detailed JD: {len(jd_text)} chars — LLM brief skipped.)"
            )
            return

        about_text = ""
        if self.fetch_company_page and app.company:
            about_text = self._try_fetch_company_about(app.company)

        # Phase 4: cache the research brief by (title, company, jd hash) so a
        # repeat run never re-pays the LLM call for the same posting.
        from core import cache as _cache
        import hashlib as _hl
        brief_key = f"{app.company}|{app.title}|{_hl.sha1(jd_text.encode('utf-8')).hexdigest()[:12]}"
        brief = _cache.get("research_brief", brief_key, ttl_seconds=14 * 86400)
        if brief is None:
            try:
                brief = self._call_research_llm(app.title, app.company, jd_text, about_text)
                _cache.set("research_brief", brief_key, brief)
            except Exception as e:
                self.warn(f"research LLM failed for {app.title}: {e}")
                app.research_notes = f"Company: {app.company}. (Research failed; using JD only.)"
                return
        app.company_about    = brief.get("company_about", "")
        app.company_size     = brief.get("company_size", "")
        app.company_industry = brief.get("company_industry", "")
        app.company_website  = brief.get("company_website", "")
        app.research_notes   = self._compose_research_notes(brief)

    def _call_research_llm(self, title: str, company: str, jd: str, about: str) -> dict:
        from llm.client import complete_cheap
        raw = complete_cheap(
            _RESEARCH_SYSTEM,
            _research_user_prompt(title, company, jd, about),
            max_tokens=900,
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        return json.loads(raw)

    @staticmethod
    def _compose_research_notes(brief: dict) -> str:
        lines = []
        if brief.get("company_about"):
            lines.append(f"About: {brief['company_about']}")
        if brief.get("company_size"):
            lines.append(f"Size: {brief['company_size']}  Industry: {brief.get('company_industry','')}")
        if brief.get("key_responsibilities"):
            lines.append("Responsibilities: " + "; ".join(brief["key_responsibilities"][:5]))
        if brief.get("key_requirements"):
            lines.append("Requirements: " + "; ".join(brief["key_requirements"][:5]))
        if brief.get("tech_stack"):
            lines.append("Stack: " + ", ".join(brief["tech_stack"][:10]))
        if brief.get("salary_signal"):
            lines.append(f"Salary signal: {brief['salary_signal']}")
        if brief.get("notes"):
            lines.append(f"Note: {brief['notes']}")
        return "\n".join(lines)

    def _fetch_url_text(self, url: str) -> str:
        if not url:
            return ""
        try:
            import httpx
            with httpx.Client(timeout=10, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = c.get(url)
                if r.status_code == 200:
                    return self._strip_html(r.text)[:8000]
        except Exception as e:
            self.debug(f"fetch failed {url}: {e}")
        return ""

    def _try_fetch_company_about(self, company: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "", company.lower())
        if not slug:
            return ""
        # Phase 4: cache the company About fetch by slug (30-day TTL).
        from core import cache as _cache
        cached = _cache.get("company_about", slug, ttl_seconds=30 * 86400)
        if cached is not None:
            return cached
        text = ""
        for u in (f"https://{slug}.com/about", f"https://{slug}.com"):
            t = self._fetch_url_text(u)
            if t:
                text = t
                break
        _cache.set("company_about", slug, text)
        return text

    @staticmethod
    def _strip_html(html: str) -> str:
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        except Exception:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

    # ── Phase 2: Write ────────────────────────────────────────────────────────

    def _write(self, app: JobApplication) -> None:
        """Generate tailored resume + cover letter for one job.

        Tunables (env):
          ATS_TARGET_MIN   (default 80) — if first pass < this, rewrite
          ATS_MAX_REWRITES (default 1)  — hard cap on rewrite passes (cost guard)

        Always keeps the BEST attempt by score."""
        import os
        if app.folder is None:
            raise ValueError("TailorAgent requires app.folder to be set by orchestrator")

        jd_full     = app.enriched_description or app.raw_description
        web_context = app.research_notes
        instructions = _load_formatting_instructions(
            Path(__file__).resolve().parent.parent
        )
        target_score = int(os.environ.get("ATS_TARGET_MIN", "80") or 80)
        # One rewrite pass by default — the score→rewrite loop is the headline
        # differentiator. Set ATS_MAX_REWRITES=0 to skip it for speed, or higher
        # to push harder toward the target.
        max_rewrites = int(os.environ.get("ATS_MAX_REWRITES", "1") or 1)

        self.info(f"writing for {app.title} @ {app.company} (target ATS >= {target_score})")

        # First pass
        try:
            raw = self._complete_with_fallback(
                COMBINED_WRITER_SYSTEM,
                combined_writer_user_prompt(
                    self.profile.master_resume_text, jd_full, web_context, instructions,
                ),
            )
        except Exception as e:
            self.warn(f"writer LLM call failed (all providers): {e}")
            return

        data = self._parse_writer_response(raw)
        if not data:
            self.warn("writer returned unparseable JSON or empty resume; skipping job")
            return

        best = self._extract_attempt(data, attempt=1)
        self.info(f"  attempt 1: ATS {best['score']}/100")

        # Rewrite passes if below target
        attempt = 1
        while best["score"] < target_score and attempt <= max_rewrites:
            attempt += 1
            self.info(
                f"  attempt {attempt}: rewriting "
                f"(was {best['score']}/100, target {target_score})"
            )
            try:
                raw2 = self._complete_with_fallback(
                    IMPROVE_WRITER_SYSTEM,
                    improve_writer_user_prompt(
                        resume_text=self.profile.master_resume_text,
                        job_description=jd_full,
                        previous_resume=best["resume"],
                        previous_cover_letter=best["cover"],
                        previous_score=best["score"],
                        missing_keywords=best["missing_keywords"],
                        previous_advice=best["advice"],
                        target_score=target_score,
                        web_context=web_context,
                        formatting_instructions=instructions,
                    ),
                )
            except Exception as e:
                self.warn(f"  rewrite attempt {attempt} failed: {e}; keeping best so far")
                break
            new_data = self._parse_writer_response(raw2)
            if not new_data:
                self.warn(f"  attempt {attempt} returned bad JSON; keeping best so far")
                break
            cand = self._extract_attempt(new_data, attempt=attempt)
            self.info(
                f"  attempt {attempt}: ATS {cand['score']}/100"
                + (f" (notes: {cand.get('rewrite_notes','')[:80]})"
                   if cand.get("rewrite_notes") else "")
            )
            if cand["score"] > best["score"]:
                best = cand
            else:
                self.info(
                    f"  rewrite did not improve "
                    f"({cand['score']} <= {best['score']}); keeping previous"
                )

        # Render PDFs
        resume_text = best["resume"]
        cover_text  = best["cover"]
        resume_pdf  = app.folder / "resume.pdf"
        cover_pdf   = app.folder / "cover_letter.pdf"
        try:
            generate_resume_pdf(resume_text, str(resume_pdf))
            (app.folder / "resume.txt").write_text(resume_text, encoding="utf-8")
            if cover_text:
                generate_cover_letter_pdf(cover_text, str(cover_pdf))
                (app.folder / "cover_letter.txt").write_text(cover_text, encoding="utf-8")
        except Exception as e:
            self.warn(f"PDF render failed: {e}")
            return

        app.tailored_resume_text = resume_text
        app.tailored_resume_pdf  = resume_pdf
        app.cover_letter_text    = cover_text
        app.cover_letter_pdf     = cover_pdf if cover_text else None
        app.ats_score            = best["score"]
        app.ats_notes            = best["notes"]

        # Logging
        if best["score"] >= target_score:
            self.info(
                f"ATS score: {best['score']}/100 ✓ "
                f"({attempt} attempt{'s' if attempt > 1 else ''})"
            )
        else:
            self.warn(
                f"ATS score: {best['score']}/100 below target {target_score} "
                f"after {attempt} attempt{'s' if attempt > 1 else ''}"
            )

        # Persist audit notes for LearnerAgent gap analysis
        audit_lines = [f"Score: {best['score']}/100", ""]
        audit_lines.extend(best["notes"])
        audit_lines.append("")
        audit_lines.append(f"Attempts: {attempt}, Target: {target_score}")
        (app.folder / "ats_audit.txt").write_text(
            "\n".join(audit_lines), encoding="utf-8"
        )

    def _complete_with_fallback(self, system: str, user: str) -> str:
        """Single-provider smart-tier writer call. No cross-provider fallback —
        the client raises a clear error if the selected provider fails."""
        return complete(system, user, max_tokens=8000, json_mode=True)

    @staticmethod
    def _parse_writer_response(raw: str) -> dict | None:
        data = _parse_json_loose(raw)
        if not data:
            return None
        if not (data.get("tailored_resume") or "").strip():
            return None
        return data

    @staticmethod
    def _extract_attempt(data: dict, *, attempt: int) -> dict:
        try:
            score = max(0, min(100, int(data.get("ats_score") or 0)))
        except Exception:
            score = 0
        missing = [
            str(k).strip()
            for k in (data.get("ats_missing_keywords") or [])
            if str(k).strip()
        ]
        advice        = str(data.get("ats_advice") or "").strip()
        rewrite_notes = str(data.get("rewrite_notes") or "").strip()
        notes = []
        if missing:
            notes.append("Missing keywords: " + ", ".join(missing[:10]))
        if advice:
            notes.append(f"Advice: {advice}")
        if rewrite_notes:
            notes.append(f"Rewrite notes: {rewrite_notes}")
        return {
            "attempt":        attempt,
            "resume":         (data.get("tailored_resume") or "").strip(),
            "cover":          (data.get("cover_letter") or "").strip(),
            "score":          score,
            "missing_keywords": missing,
            "advice":         advice,
            "rewrite_notes":  rewrite_notes,
            "notes":          notes,
        }
