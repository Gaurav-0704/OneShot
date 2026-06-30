"""
agents/packager.py - PackagerAgent

Browser-free pipeline terminus: assembles a ready-to-apply packet and queues
it for the user in pending_review.csv, with copilot data saved alongside.

Per-job steps:
  1. Resolve showcase PDF path.
  2. Attach file paths (resume / cover / showcase) to files_attached.
  3. Requirements preview  — regex-scan JD for required docs + screening Qs
                             (no LLM call; instant).
  4. Copilot prebake        — run CopilotAgent over a standard battery +
                             predicted questions; save to <folder>/copilot_data.json
                             and qa_store.sqlite (approved=False until user confirms).
  5. record_pending()       — write to pending_review.csv.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from agents.base import Agent
from core.tracker import record_pending
from models import JobApplication

log = logging.getLogger(__name__)


# ── Requirements preview ───────────────────────────────────────────────────────

_DOC_PATTERNS: dict[str, re.Pattern] = {
    "transcript":      re.compile(r"\btranscript\b", re.I),
    "portfolio":       re.compile(r"\bportfolio\b", re.I),
    "references":      re.compile(r"\breferences?\b", re.I),
    "writing sample":  re.compile(r"\bwriting sample\b", re.I),
    "cover letter":    re.compile(r"\bcover letter\b", re.I),
}

_SCREENING_PATTERNS: dict[str, re.Pattern] = {
    "work authorization":  re.compile(r"\b(authoriz|work auth|eligible to work)\b", re.I),
    "visa sponsorship":    re.compile(r"\b(sponsor|visa)\b", re.I),
    "relocation":          re.compile(r"\brelocat", re.I),
    "background check":    re.compile(r"\bbackground check\b", re.I),
    "drug test":           re.compile(r"\bdrug test\b", re.I),
    "security clearance":  re.compile(r"\b(clearance|cleared)\b", re.I),
    "age verification":    re.compile(r"\b(18 years|over 18|legal age)\b", re.I),
}


def build_requirements_preview(app: JobApplication, profile) -> dict:
    """
    Scan the job description (no LLM) and flag:
      - required_fields: explicit document requirements in the JD
      - likely_screening_questions: standard Yes/No questions likely to appear
      - extra_docs: non-resume docs explicitly requested
      - profile_gaps: requirements the profile cannot satisfy
    """
    jd = (app.enriched_description or app.raw_description or "").lower()

    required_fields: list[str] = []
    extra_docs: list[str] = []
    for doc, pat in _DOC_PATTERNS.items():
        if pat.search(jd):
            if doc == "cover letter":
                required_fields.append("cover letter (attached)")
            else:
                extra_docs.append(doc)
                required_fields.append(doc)

    screening: list[str] = [
        label for label, pat in _SCREENING_PATTERNS.items() if pat.search(jd)
    ]

    # Profile-gap analysis
    gaps: list[str] = []
    if "portfolio" in extra_docs:
        if not (getattr(profile, "website_url", "") or getattr(profile, "github_url", "")):
            gaps.append("Portfolio requested but no website/GitHub URL in profile")
    if "transcript" in extra_docs:
        gaps.append("Transcript may be needed — check if you have one ready")
    if "references" in extra_docs:
        gaps.append("References requested — prepare a references page")

    return {
        "required_fields":           required_fields,
        "likely_screening_questions": screening,
        "extra_docs":                extra_docs,
        "profile_gaps":              gaps,
    }


# ── Prebake battery ────────────────────────────────────────────────────────────

def _top_skill(profile) -> str:
    """Heuristically find the candidate's most prominent technical skill."""
    _SKILLS = [
        "Python", "Machine Learning", "Deep Learning", "Java", "JavaScript",
        "TypeScript", "C++", "C#", "Go", "Rust", "SQL", "React", "Node.js",
        "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Data Science",
    ]
    resume = (getattr(profile, "master_resume_text", "") or "").lower()
    for skill in _SKILLS:
        if skill.lower() in resume:
            return skill
    return "your primary technology"


_STANDARD_BATTERY = [
    "Are you authorized to work in {country} without employer sponsorship?",
    "Do you require visa sponsorship now or in the future?",
    "Are you willing to relocate for this position?",
    "What is your notice period? How soon can you start?",
    "Why are you interested in this {title} role?",
    "Why do you want to work at {company}?",
    "Why are you a strong fit for this {title} position?",
    "Walk me through your experience with {top_skill}.",
    "How many years of {top_skill} experience do you have?",
    "What is your greatest professional strength?",
    "What is an area you are working to improve?",
    "What are your salary expectations for this role?",
    "Are you available for a background check if required?",
    "Are you 18 years of age or older?",
]


def _prebake(app: JobApplication, profile, root: Path) -> list[dict]:
    """
    Run CopilotAgent over the standard battery + predicted screening questions.
    Saves results to <folder>/copilot_data.json and qa_store.sqlite.
    Returns list of prebaked answer dicts.

    Non-fatal: any LLM/IO failure is caught and logged; the packager continues.
    """
    try:
        from agents.copilot import CopilotAgent
        agent = CopilotAgent(profile, root=root)
    except Exception as e:
        log.warning(f"PackagerAgent: could not init CopilotAgent for prebake: {e}")
        return []

    country = getattr(profile, "country", "this country") or "this country"
    top_skill = _top_skill(profile)
    job_dict = {
        "job_id":   app.job_id,
        "title":    app.title,
        "company":  app.company,
        "location": app.location,
        "description": app.enriched_description or app.raw_description,
        "enriched_description": app.enriched_description,
        "research_notes": app.research_notes,
    }

    # Build the actual question list
    questions: list[str] = []
    for tmpl in _STANDARD_BATTERY:
        questions.append(
            tmpl.format(
                country=country,
                title=app.title or "this",
                company=app.company or "your company",
                top_skill=top_skill,
            )
        )

    prebaked: list[dict] = []
    for q in questions:
        try:
            result = agent.generate(q, job_dict)
            row = {
                "question":         q,
                "answer":           result["answer"],
                "confidence_score": result["confidence_score"],
                "answer_type":      result["answer_type"],
                "source":           result["source"],
                "needs_review":     result["needs_review"],
            }
            prebaked.append(row)
            # Persist to qa_store (approved=False; user confirms in UI)
            agent.store.upsert({
                "question":    q,
                "answer":      result["answer"],
                "answer_type": result["answer_type"],
                "job_id":      app.job_id,
                "company":     app.company,
                "confidence":  float(result["confidence_score"]),
                "approved":    0,
                "preferred":   0,
            })
        except Exception as e:
            log.debug(f"  prebake question failed [{q[:60]}]: {e}")
            prebaked.append({
                "question": q, "answer": "", "confidence_score": 0,
                "answer_type": "short_text", "source": "error",
                "needs_review": True,
            })

    return prebaked


# ── PackagerAgent ──────────────────────────────────────────────────────────────

class PackagerAgent(Agent):
    name = "packager"
    role = "Assembles a ready-to-apply packet and queues it for the user."

    def __init__(self, profile, *, pending_csv: Path, root: Path | None = None):
        super().__init__(profile=profile, dry_run=False)
        self.pending_csv    = pending_csv
        self.root           = Path(root) if root else Path(".")
        self._showcase_path = self._resolve_showcase()

    # ── Public API ────────────────────────────────────────────────────────────

    def package(self, app: JobApplication) -> JobApplication:
        """Record one job as 'ready_to_apply' in pending_review.csv."""
        if app.tailored_resume_pdf is None:
            self.warn(f"no resume PDF for {app.title} @ {app.company} — skipping package")
            return app

        # ── File paths ────────────────────────────────────────────────────────
        files: list[str] = []
        if app.tailored_resume_pdf:
            files.append(f"resume:{app.tailored_resume_pdf}")
        if app.cover_letter_pdf:
            files.append(f"cover:{app.cover_letter_pdf}")
        if self._showcase_path:
            files.append(f"showcase:{self._showcase_path}")
        app.files_attached = files

        app.form_state   = "ready_to_apply"
        app.applier_used = "packager"

        # ── Requirements preview (no LLM, instant) ────────────────────────────
        requirements = {}
        try:
            requirements = build_requirements_preview(app, self.profile)
            app.requirements_preview = requirements
        except Exception as e:
            log.warning(f"   requirements preview failed: {e}")

        # ── Copilot prebake (cheap LLM, graceful on failure) ──────────────────
        prebaked: list[dict] = []
        do_prebake = os.environ.get("COPILOT_PREBAKE", "1").strip() not in ("0", "false", "no")
        if do_prebake:
            try:
                prebaked = _prebake(app, self.profile, self.root)
                app.prebaked_answers = prebaked
                self.info(f"   prebaked {len(prebaked)} copilot answers")
            except Exception as e:
                log.warning(f"   copilot prebake failed (non-fatal): {e}")

        # ── Save copilot data to job folder ───────────────────────────────────
        if app.folder and (requirements or prebaked):
            try:
                copilot_data = {"requirements": requirements, "prebaked_answers": prebaked}
                (app.folder / "copilot_data.json").write_text(
                    json.dumps(copilot_data, indent=2), encoding="utf-8"
                )
            except Exception as e:
                log.debug(f"   copilot_data.json write failed: {e}")

        # ── Save full JD context so LIVE copilot questions have the same
        #    context the prebake had (Phase 5). ────────────────────────────────
        if app.folder:
            try:
                (app.folder / "job_context.json").write_text(json.dumps({
                    "job_id":      app.job_id,
                    "title":       app.title,
                    "company":     app.company,
                    "location":    app.location,
                    "url":         app.url,
                    "description": app.enriched_description or app.raw_description or "",
                    "enriched_description": app.enriched_description or "",
                    "research_notes":       app.research_notes or "",
                }, indent=2), encoding="utf-8")
            except Exception as e:
                log.debug(f"   job_context.json write failed: {e}")

        self.info(
            f"packaged: {app.title} @ {app.company} "
            f"(ATS {app.ats_score}/100, {len(files)} files, "
            f"{len(prebaked)} prebaked answers)"
        )
        record_pending(self.pending_csv, app.to_csv_row(), reason="ready_to_apply")
        return app

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_showcase() -> str:
        default = Path("config") / "showcase.pdf"
        if default.exists():
            return str(default.resolve())
        env_path = os.environ.get("SHOWCASE_PDF", "").strip()
        if env_path and Path(env_path).exists():
            return str(Path(env_path).resolve())
        return ""
