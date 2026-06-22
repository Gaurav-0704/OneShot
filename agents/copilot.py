"""
agents/copilot.py - CopilotAgent

Thin wrapper around core.copilot and core.qa_memory.QAStore.
Used by PackagerAgent (prebake) and the copilot Flask blueprint (live).

Methods:
  generate(question, job)                  → answer dict
  regenerate(question, job, instructions)  → answer dict (forces fresh LLM call)
  save(question, answer, job, preferred)   → row_id
"""
from __future__ import annotations

import logging
from pathlib import Path

from agents.base import Agent
from core.qa_memory import QAStore
from core.copilot import answer_question, build_profile_text
from models import JobApplication

log = logging.getLogger(__name__)


class CopilotAgent(Agent):
    name = "copilot"
    role = (
        "Answers job-application questions truthfully using only the candidate's "
        "verified profile. Never fabricates skills or experience."
    )

    def __init__(self, profile, *, root: Path):
        super().__init__(profile=profile, dry_run=True)
        self.root  = Path(root)
        self.store = QAStore(self.root / "outputs" / "qa_store.sqlite")

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, question: str, job: dict | JobApplication) -> dict:
        """Answer a question, using the semantic cache when confident enough."""
        return answer_question(
            question, self._job_dict(job), self.profile, self.store
        )

    def regenerate(
        self, question: str, job: dict | JobApplication, instructions: str = ""
    ) -> dict:
        """Force a fresh LLM call, bypassing the cache (e.g. after user edits)."""
        return answer_question(
            question, self._job_dict(job), self.profile, self.store,
            force_llm=True, instructions=instructions,
        )

    def save(
        self,
        question: str,
        answer: str,
        job: dict | JobApplication,
        *,
        preferred: bool = False,
    ) -> int:
        """Persist a (question, answer) pair to the SQLite store."""
        j = self._job_dict(job)
        row_id = self.store.upsert({
            "question":    question,
            "answer":      answer,
            "answer_type": "short_text",
            "job_id":      j.get("job_id", ""),
            "company":     j.get("company", ""),
            "confidence":  80.0,
            "preferred":   1 if preferred else 0,
            "approved":    1,
        })
        if preferred:
            self.store.mark_preferred(row_id)
        return row_id

    def profile_text(self) -> str:
        return build_profile_text(self.profile)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _job_dict(job: dict | JobApplication) -> dict:
        """Normalise a JobApplication or plain dict to a plain dict."""
        if isinstance(job, dict):
            return job
        return {
            "job_id":               job.job_id,
            "title":                job.title,
            "company":              job.company,
            "location":             job.location,
            "url":                  job.url,
            "site":                 job.site,
            "description":          job.raw_description,
            "enriched_description": job.enriched_description,
            "research_notes":       job.research_notes,
        }
