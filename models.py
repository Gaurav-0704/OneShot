"""
models.py - shared data classes that flow between agents.

Two main objects:
  - UserProfile    : owned by ProfileAgent. Read by every other agent.
  - JobApplication : grows as it moves through the pipeline. Each agent adds fields.

Design rule: agents never share state via globals; they pass these dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ── User profile (canonical) ──────────────────────────────────────────────────

@dataclass
class UserProfile:
    """Single source of truth for the user. Built by ProfileAgent from
    config/personal.yaml + optional GitHub / LinkedIn enrichment."""

    # Identity
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""

    # Links
    linkedin_url: str = ""
    github_url: str = ""
    website_url: str = ""

    # Address
    city: str = ""
    state: str = ""
    country: str = ""
    zipcode: str = ""

    # Demographics (any may be blank → "Decline to answer")
    gender: str = ""
    ethnicity: str = ""
    veteran_status: str = ""
    disability_status: str = ""

    # Work authorization
    auth_us: str = "No"
    auth_eu: str = "No"
    auth_uk: str = "No"
    auth_canada: str = "No"
    requires_sponsorship: str = "No"

    # Pre-answered screening defaults
    years_of_experience: int = 0
    desired_salary_usd: int = 0
    notice_period_days: int = 30

    # Long-form
    summary: str = ""
    headline: str = ""
    user_information_summary: str = ""

    # Master resume - text and source PDF path
    master_resume_path: Optional[Path] = None
    master_resume_text: str = ""

    # Enrichment from external sources
    github_repos: list[dict] = field(default_factory=list)
    github_languages: list[str] = field(default_factory=list)
    github_bio: str = ""
    linkedin_data: dict = field(default_factory=dict)

    # Raw configs (for agents that need niche fields)
    raw_personal: dict = field(default_factory=dict)
    raw_questions: dict = field(default_factory=dict)
    raw_preferences: dict = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)


# ── Job application (flows through the pipeline) ──────────────────────────────

@dataclass
class JobApplication:
    """One job's journey through the pipeline. Mutated by each agent."""

    # ── Stage 1: Discovery ───────────────────────────────────────────────────
    site: str = ""
    job_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    raw_description: str = ""
    is_remote: bool = False
    job_type: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    date_posted: str = ""
    fit_score: Optional[int] = None
    fit_reason: str = ""

    # ── Stage 2: Research (enrichment) ───────────────────────────────────────
    enriched_description: str = ""    # cleaned, possibly fuller JD
    company_about: str = ""
    company_size: str = ""
    company_industry: str = ""
    company_website: str = ""
    recent_news: list[str] = field(default_factory=list)
    research_notes: str = ""          # free-form notes from ResearchAgent

    # ── Stage 3: Writing ─────────────────────────────────────────────────────
    tailored_resume_text: str = ""
    tailored_resume_pdf: Optional[Path] = None
    cover_letter_text: str = ""
    cover_letter_pdf: Optional[Path] = None
    custom_answers: dict[str, str] = field(default_factory=dict)
    ats_score: Optional[int] = None
    ats_notes: list[str] = field(default_factory=list)

    # ── Stage 4: Form filling ────────────────────────────────────────────────
    applier_used: str = ""             # "linkedin" | "indeed" | "greenhouse" | "manual"
    form_state: str = "not_started"    # not_started|filling|filled|review|submitted|failed
    questions_answered: list[dict] = field(default_factory=list)
    files_attached: list[str] = field(default_factory=list)
    fill_screenshot: str = ""
    fill_error: str = ""

    # ── Stage 5: Review + submit ─────────────────────────────────────────────
    review_passed: Optional[bool] = None
    review_issues: list[str] = field(default_factory=list)
    submitted: bool = False
    submitted_at: str = ""
    submission_confirmation: str = ""

    # ── Stage 4 (packager) ──────────────────────────────────────────────────
    showcase_path: str = ""            # optional portfolio/showcase PDF path

    # ── Copilot data (written to copilot_data.json in the job folder) ────────
    requirements_preview: dict = field(default_factory=dict)
    prebaked_answers: list = field(default_factory=list)

    # Bookkeeping
    folder: Optional[Path] = None      # outputs/tailored/<slug>/

    def to_csv_row(self) -> dict[str, Any]:
        """Flat dict for CSV writing."""
        return {
            "applied_at": self.submitted_at or datetime.now().isoformat(timespec="seconds"),
            "site": self.site,
            "applier": self.applier_used,
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "fit_score": self.fit_score or "",
            "ats_score": self.ats_score or "",
            "questions_answered": len(self.questions_answered),
            "files_attached": ",".join(self.files_attached),
            "review_passed": self.review_passed if self.review_passed is not None else "",
            "review_issues": " | ".join(self.review_issues),
            "submitted": self.submitted,
            "form_state": self.form_state,
            "fill_error": self.fill_error,
            "resume_pdf": str(self.tailored_resume_pdf or ""),
            "cover_pdf": str(self.cover_letter_pdf or ""),
            "folder": str(self.folder or ""),
        }
