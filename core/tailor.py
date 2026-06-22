"""
core/tailor.py
Generates tailored resume + cover letter text for a given job.
Wraps llm.client.complete() with our prompts.
"""
from __future__ import annotations

from pathlib import Path

from llm.client import complete
from llm.prompts import (
    RESUME_SYSTEM,
    COVER_LETTER_SYSTEM,
    resume_user_prompt,
    cover_letter_user_prompt,
)


def _load_instructions() -> str:
    """Load formatting instructions used in both resume and cover letter prompts."""
    here = Path(__file__).resolve().parent.parent
    instruct = here / "config" / "resume_instructions.md"
    if instruct.exists():
        return instruct.read_text(encoding="utf-8")
    return "Follow standard ATS-friendly resume and cover letter formatting."


def tailor_resume(
    resume_text: str,
    job_description: str,
    web_context: str = "",
    *,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Return a tailored resume as plain text."""
    instructions = _load_instructions()
    system = RESUME_SYSTEM + "\n\nFORMATTING INSTRUCTIONS:\n" + instructions
    user = resume_user_prompt(resume_text, job_description, web_context)
    return complete(system, user, provider=provider, model=model, max_tokens=4096)


def generate_cover_letter(
    resume_text: str,
    job_description: str,
    tailored_resume: str,
    web_context: str = "",
    *,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Return a tailored cover letter as plain text."""
    instructions = _load_instructions()
    system = COVER_LETTER_SYSTEM + "\n\nFORMATTING INSTRUCTIONS:\n" + instructions
    user = cover_letter_user_prompt(resume_text, job_description, tailored_resume, web_context)
    return complete(system, user, provider=provider, model=model, max_tokens=2048)
