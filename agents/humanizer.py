"""
agents/humanizer.py — HumanizerAgent

Pipeline stage between TailorAgent and PackagerAgent.
Cleans the generated resume, cover letter, and prebaked copilot answers so
they read like a real person wrote them, then re-checks before delivery.

The agent NEVER adds facts — it only rephrases, cuts filler, and trims
promotional language from text that is already grounded in the candidate's
master resume.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agents.base import Agent
from core.text_clean import deliver
from models import JobApplication

log = logging.getLogger(__name__)


class HumanizerAgent(Agent):
    name = "humanizer"
    role = (
        "Cleans generated documents to read like a real person wrote them. "
        "Never adds facts — only rephrases and removes AI filler."
    )

    def __init__(self, profile, *, root: Path):
        super().__init__(profile=profile, dry_run=True)
        self.root = Path(root)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, app: JobApplication, profile) -> JobApplication:
        """
        Clean + smoke-test all generated text on the JobApplication.
        Re-generates PDFs from the cleaned text so saved files stay consistent.
        Returns app (mutated in-place).
        """
        facts = self._build_facts(profile)
        n_issues = 0

        # Same conditional header links + close data the TailorAgent uses, so the
        # humanizer's PDF re-render keeps clickable links and the proper closing.
        _links = {
            "LinkedIn":  getattr(profile, "linkedin_url", "") or "",
            "GitHub":    getattr(profile, "github_url", "") or "",
            "Portfolio": getattr(profile, "website_url", "") or "",
        }
        _name     = getattr(profile, "full_name", "") or ""
        _portfolio = getattr(profile, "website_url", "") or ""

        # ── Resume ────────────────────────────────────────────────────────────
        if app.tailored_resume_text:
            result = deliver(app.tailored_resume_text, kind="resume", facts=facts)
            app.tailored_resume_text = result["text"]
            if not result["report"]["ok"]:
                n_issues += 1
                log.warning(
                    f"humanizer: resume issues for {app.company}: "
                    f"{result['report']['issues']}"
                )
            # Re-render PDF from cleaned text
            if app.tailored_resume_pdf and app.folder:
                try:
                    from core.pdf_generator import generate_resume_pdf
                    generate_resume_pdf(app.tailored_resume_text, str(app.tailored_resume_pdf), links=_links)
                    (app.folder / "resume.txt").write_text(
                        app.tailored_resume_text, encoding="utf-8"
                    )
                except Exception as e:
                    log.warning(f"humanizer: resume PDF re-render failed: {e}")

        # ── Cover letter ──────────────────────────────────────────────────────
        if app.cover_letter_text:
            result = deliver(app.cover_letter_text, kind="cover", facts=facts)
            app.cover_letter_text = result["text"]
            # Inject the salutation if the model dropped it. The CLOSING
            # (Thank you, / name / portfolio) is appended deterministically at
            # render time, so we no longer inject a sign-off here.
            issues = result["report"].get("issues", [])
            cl = app.cover_letter_text
            if any("salutation" in i for i in issues):
                cl = "Dear Hiring Manager,\n\n" + cl
            app.cover_letter_text = cl
            if not result["report"]["ok"]:
                remaining = [i for i in issues if "salutation" not in i and "sign-off" not in i]
                if remaining:
                    n_issues += 1
                    log.warning(
                        f"humanizer: cover issues for {app.company}: {remaining}"
                    )
            if app.cover_letter_pdf and app.folder:
                try:
                    from core.pdf_generator import generate_cover_letter_pdf
                    generate_cover_letter_pdf(
                        app.cover_letter_text, str(app.cover_letter_pdf),
                        links=_links, name=_name, portfolio_url=_portfolio,
                    )
                    (app.folder / "cover_letter.txt").write_text(
                        app.cover_letter_text, encoding="utf-8"
                    )
                except Exception as e:
                    log.warning(f"humanizer: cover PDF re-render failed: {e}")

        # ── Prebaked copilot answers ───────────────────────────────────────────
        if app.prebaked_answers:
            cleaned: list[dict] = []
            for ans in app.prebaked_answers:
                raw_answer = ans.get("answer", "")
                if not raw_answer:
                    cleaned.append(ans)
                    continue
                result = deliver(raw_answer, kind="answer", facts=facts, skip_polish=True)
                entry = dict(ans)
                entry["answer"] = result["text"]
                if not result["report"]["ok"]:
                    entry["needs_review"] = True
                    entry["confidence_score"] = min(int(entry.get("confidence_score") or 50), 35)
                cleaned.append(entry)
            app.prebaked_answers = cleaned

        log.info(
            f"humanizer: {app.title} @ {app.company} — "
            f"{'OK' if n_issues == 0 else f'{n_issues} issue(s) flagged'}"
        )
        return app

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_facts(profile) -> str:
        """Compact fact block from profile — used as ground truth for check()."""
        lines: list[str] = []
        resume = (getattr(profile, "master_resume_text", "") or "").strip()
        if resume:
            lines.append(resume[:5000])
        summary = getattr(profile, "user_information_summary", "") or getattr(profile, "summary", "")
        if summary:
            lines.append(summary)
        yoe = getattr(profile, "years_of_experience", 0)
        if yoe:
            lines.append(f"{yoe} years of experience")
        return "\n".join(lines)
