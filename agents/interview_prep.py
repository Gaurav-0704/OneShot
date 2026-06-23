"""
agents/interview_prep.py - InterviewPrepAgent (STUB).

Triggered when an EmailReply with kind="interview" lands for an applied
job. The agent generates a tailored prep packet:

  - 8-12 likely behavioral + technical questions for that company / role
  - A "story bank" mapping the user's resume bullets to STAR-style answers
  - A two-page PDF with everything formatted for printing

This is a scaffold. Real implementation needs:
  1. Pull the JD + tailored resume + company web context (already on disk)
  2. Two LLM calls:
       - generate_questions(jd, role, company) -> [{question, kind, why}]
       - story_bank(resume_text, questions) -> [{question, story_outline}]
  3. Render to outputs/applications/<slug>/interview_prep.pdf via core.pdf

Until wired, generate_prep() returns a dict you can print or paste into
chat. The orchestrator can call it when an interview reply arrives, or
the user can trigger it manually from a future "Interview" tab.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import Agent
from models import JobApplication, UserProfile


class InterviewPrepAgent(Agent):
    name = "interview_prep"
    role = (
        "Senior interview coach. Generates likely interview questions and a "
        "STAR-format story bank tied to the candidate's actual resume bullets. "
        "Never invents stories - always uses real experience."
    )

    def __init__(self, profile: UserProfile, *, output_dir: Path):
        super().__init__(profile=profile, dry_run=True)
        self.output_dir = output_dir

    def generate_prep(self, application: JobApplication) -> dict:
        """Generate an interview prep packet for one job. STUB returns a
        placeholder shape so callers can be wired now and filled in later."""
        # ── TODO: real implementation ───────────────────────────────────
        # from llm.client import complete
        # questions = self._llm_generate_questions(application)
        # stories   = self._llm_build_story_bank(questions)
        # pdf_path  = self._render_pdf(application, questions, stories)
        # return {
        #     "ok": True,
        #     "questions": questions,
        #     "stories": stories,
        #     "pdf_path": str(pdf_path),
        # }

        slug = self._slug_for(application)
        return {
            "ok": False,
            "stub": True,
            "message": (
                f"Interview prep for '{application.title}' @ {application.company} "
                f"is scaffolded. Wire generate_prep() in agents/interview_prep.py "
                f"to start producing real prep packets."
            ),
            "would_write_to": str(self.output_dir / slug / "interview_prep.pdf"),
        }

    @staticmethod
    def _slug_for(app: JobApplication) -> str:
        """Mirror the slug WriterAgent uses so prep packets land alongside
        the tailored resume for the same job."""
        import re
        s = f"{app.company}_{app.title}".lower()
        s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
        return s[:60] or app.job_id


# ── Convenience: map an EmailReply.kind=='interview' back to an application ─

def is_interview_reply(reply_kind: str) -> bool:
    return reply_kind == "interview"


__implementation_status__ = "stub"
__last_updated__ = "2026-05-03"
