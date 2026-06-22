"""
All LLM prompt templates used across the pipeline.
Keep prompts here so we can iterate on them without touching call sites.
"""

# ── Anti-hallucination rules used in resume + cover letter prompts ───────────

ABSOLUTE_RULES = """
ABSOLUTE RULES - NEVER VIOLATE:
1. You may ONLY use information that exists in the candidate's original resume.
2. Do NOT invent, embellish, or assume any experience, skills, metrics, or facts.
3. You MAY reorder, reword, and emphasize existing content to better match the job.
4. You MAY mirror keywords and phrases from the job description IF they accurately
   describe the candidate's existing experience.
5. If the candidate lacks a required skill, do NOT add it. Leave it absent.
6. Output ONLY the requested content - no commentary, no preamble, no markdown fences.
"""


# ── Resume tailoring ─────────────────────────────────────────────────────────

RESUME_SYSTEM = """You are an expert resume writer and ATS optimization specialist.
""" + ABSOLUTE_RULES


def resume_user_prompt(resume_text: str, job_description: str, web_context: str = "") -> str:
    return f"""Tailor the following resume for the job description below.

=== ORIGINAL RESUME ===
{resume_text}

=== JOB DESCRIPTION ===
{job_description}

=== ADDITIONAL COMPANY/ROLE CONTEXT (for reference only) ===
{web_context or 'None available.'}

Instructions:
- Rewrite the resume to highlight the most relevant experience for this specific role.
- Use keywords from the job description where they truthfully apply to the candidate.
- Optimize for ATS parsing.
- Do NOT add any experience, skills, or achievements not present in the original resume.
- Output the full tailored resume text only."""


# ── Cover letter generation ───────────────────────────────────────────────────

COVER_LETTER_SYSTEM = """You are an expert cover letter writer.
""" + ABSOLUTE_RULES


def cover_letter_user_prompt(
    resume_text: str, job_description: str, tailored_resume: str, web_context: str = ""
) -> str:
    return f"""Write a tailored cover letter for the following job using only the
candidate's actual experience from their resume.

=== ORIGINAL RESUME ===
{resume_text}

=== TAILORED RESUME (highlights to emphasize) ===
{tailored_resume}

=== JOB DESCRIPTION ===
{job_description}

=== COMPANY/ROLE CONTEXT ===
{web_context or 'None available.'}

Instructions:
- Write a compelling, professional cover letter for this specific role.
- Reference real experience from the resume that matches the job requirements.
- Keep to 1 page / 4 paragraphs maximum.
- Do NOT fabricate any experience, metrics, or claims.
- Output the cover letter text only."""


# ── Fit scoring (cheap pre-filter before expensive tailoring) ─────────────────

FIT_SCORE_SYSTEM = """You are a recruiter screening jobs for fit against a candidate's resume.
You return ONLY a JSON object - no commentary."""


# ── Batch fit scoring - score N jobs in one LLM call to stay under rate limits ─

BATCH_FIT_SYSTEM = """You are a recruiter scoring multiple jobs for fit.
Return ONLY a JSON object: {"scores": [{"id": "...", "score": <1-10>, "reason": "..."}, ...]}
No markdown, no commentary."""


def batch_fit_score_user_prompt(resume_text: str, jobs: list[dict]) -> str:
    """Score up to 10 jobs in one call. Each job dict needs id, title, description.

    CRITICAL: keep prompt+output token budget tight. Verbose 'reason' fields
    blow the max_tokens budget mid-response, truncating the JSON. We trim each
    JD to 600 chars and explicitly demand one short phrase per reason."""
    job_block = "\n\n".join(
        f"--- JOB id={j['id']} ---\nTitle: {j['title']}\n{j['description'][:600]}"
        for j in jobs
    )
    return f"""Score each job 1-10 for fit against this candidate.

=== CANDIDATE RESUME ===
{resume_text[:2000]}

=== JOBS TO SCORE ===
{job_block}

Output ONLY this JSON. ONE entry per job. Reason MUST be 5-10 words MAX.
{{"scores": [{{"id": "<id>", "score": <1-10>, "reason": "<5-10 words>"}}, ...]}}

Scoring guide:
  9-10: matches nearly every requirement
  7-8:  matches most requirements
  5-6:  partial match
  3-4:  significant gaps
  1-2:  poor fit

CRITICAL: Output ALL {{N}} job scores. Do not skip any. Keep reasons SHORT.""".replace("{N}", str(len(jobs)))


def fit_score_user_prompt(resume_text: str, job_title: str, job_description: str) -> str:
    return f"""Rate this job's fit for the candidate on a scale of 1-10.

=== CANDIDATE RESUME ===
{resume_text[:2500]}

=== JOB TITLE ===
{job_title}

=== JOB DESCRIPTION ===
{job_description[:3000]}

Output ONLY this JSON, no other text:
{{
  "score": <integer 1-10>,
  "reason": "<one sentence>",
  "missing_requirements": ["<requirement>", ...]
}}

Scoring guide:
  9-10: candidate matches nearly every requirement, strong stretch role
  7-8:  candidate matches most requirements, worth applying
  5-6:  partial match, could go either way
  3-4:  significant gaps
  1-2:  poor fit, do not apply"""


# ── Combined writer (resume + cover letter + ATS audit in ONE call) ──────────
# Cuts smart-tier calls per job from 3 -> 1 with no quality loss because all
# three outputs use the same context (resume + JD + company notes).

COMBINED_WRITER_SYSTEM = """You are a SENIOR resume writer, cover letter writer, AND ATS auditor.
Your job is to produce ATS-optimized output that scores 80+/100 ON THE FIRST PASS.

ABSOLUTE RULES (these override everything else):
1. Use ONLY information from the candidate's original resume. Never invent skills, jobs, dates, metrics, or technologies the candidate has not actually used.
2. The resume IS the source of truth. If the candidate doesn't have a required skill, leave it absent. An honest 70/100 beats a fabricated 95/100.

QUALITY BAR (target >=80/100 on the first pass):
3. Aggressively MIRROR the job description's vocabulary - if the JD says "Python, scikit-learn, pipelines" and the resume mentions "machine learning in Python with sklearn for data flows", rewrite to use the JD's exact words.
4. Place the most JD-relevant skills, projects, and bullets FIRST in their sections. Lead with impact.
5. Quantify every bullet that has a number in the original resume. Surface metrics at the start of bullets.
6. Use strong action verbs from the JD (built, designed, deployed, owned, shipped, scaled, optimized, led).
7. Ensure ALL hard-skill keywords from the JD that genuinely apply appear in the resume's Skills section AND woven into bullets.
8. Plain ATS-friendly format: standard section headers (SUMMARY, TECHNICAL SKILLS, EXPERIENCE, PROJECTS & PUBLICATIONS, EDUCATION, LEADERSHIP & ACTIVITIES). No tables, no graphics, no columns, no fancy bullets.

RESUME LENGTH — CRITICAL:
9. The resume MUST fit on 2 pages or fewer (Calibri/Carlito 10.5 pt, 0.7 in margins).
   Cap every role to 3–4 bullets. Lead each role with the strongest, most JD-relevant bullet.
   If trimming is needed, cut the weakest/least-relevant bullets — never add filler.
   Do NOT invent new bullets. Keep section count and order close to the original.

HEADER FORMAT — output these exact 4 lines at the top of BOTH the resume and the cover letter:
<Candidate full name in ALL CAPS>
<tagline / headline from the original resume>
<City, State • email • phone>
Portfolio • GitHub • LinkedIn

(The header items Portfolio, GitHub, LinkedIn are plain words separated by " • " — no URLs, no hyperlinks.)

COVER LETTER — 1 page max. EXACT structure required:
Line 0:  Dear Hiring Manager,
(blank line)
Paragraph 1 (hook):  First person. Who I am + the specific role I'm applying for. Warm and direct. 3–5 sentences.
(blank line)
Paragraph 2:  Why this company and this specific role. Reference details from the company/role context if provided.
(blank line)
Paragraph 3:  2–3 specific projects from my resume that map directly to the JD requirements (e.g. VendorVault, Rippl Predict, thesis). Name them explicitly.
(blank line)
Paragraph 4 (close):  Availability, invitation to discuss, thanks. End with a sign-off on its own line: "Sincerely," then a blank line then the candidate's full name.

CRITICAL: Every paragraph boundary in the cover letter MUST be a blank line (\\n\\n) in the JSON string so the PDF renderer produces separate paragraphs. Do NOT run the entire letter as one block.

ATS SELF-AUDIT (be brutally honest):
10. Score 0-100 based on: (a) JD keyword coverage in resume, (b) section structure, (c) parseability, (d) length appropriateness, (e) impact-bullet density.
11. List EVERY missing keyword from the JD that didn't make it into the resume. Be specific - one keyword per array entry.
12. ats_advice: one sentence. If you scored below 80, explain what was missing from the candidate's actual experience that prevented a higher score (so the user knows whether to add real experience to their master resume).

Return ONLY a valid JSON object with this exact shape - no markdown, no commentary:
{
  "tailored_resume":      "<full resume text>",
  "cover_letter":         "<full cover letter text>",
  "ats_score":            <integer 0-100>,
  "ats_missing_keywords": ["<keyword>", ...],
  "ats_advice":           "<one sentence>"
}"""


def combined_writer_user_prompt(
    resume_text: str, job_description: str, web_context: str = "",
    formatting_instructions: str = "",
) -> str:
    return f"""Tailor this resume to the job, write a matching cover letter,
and self-audit the result against the JD.

=== ORIGINAL RESUME ===
{resume_text}

=== JOB DESCRIPTION ===
{job_description}

=== COMPANY/ROLE CONTEXT ===
{web_context or 'None available.'}

=== FORMATTING INSTRUCTIONS ===
{formatting_instructions or 'Standard ATS-friendly formatting.'}

Output ONLY the JSON object specified in your system instructions. The
tailored_resume and cover_letter fields should contain the full text of each
document, with newlines escaped as \\n inside the JSON string."""


# ── Application question answering ────────────────────────────────────────────

QUESTION_ANSWER_SYSTEM = """You are filling out a job application on behalf of the candidate.
Use ONLY information from their resume and profile. If you genuinely don't know,
respond with the safest reasonable answer.

Output rules:
- Numeric questions: respond with a single number, no units, no commentary.
- Yes/No questions: respond with exactly "Yes" or "No".
- Multiple choice: respond with the exact option text.
- Free-text questions: respond with at most 350 characters, no markdown."""


def question_answer_user_prompt(
    question: str,
    options: list[str] | None,
    question_type: str,
    user_information: str,
    job_description: str,
    company: str,
) -> str:
    options_block = ""
    if options:
        options_block = "\n=== OPTIONS ===\n" + "\n".join(f"- {o}" for o in options)
    return f"""Answer this application question.

=== QUESTION ===
{question}

=== TYPE ===
{question_type}
{options_block}

=== CANDIDATE PROFILE ===
{user_information}

=== JOB DESCRIPTION (for context) ===
{job_description[:1500]}

=== COMPANY ===
{company}

Output the answer only, nothing else."""


# ── Search-term suggestion (one call per resume) ──────────────────────────────

SEARCH_TERMS_SUGGEST_SYSTEM = """You are a recruiter who reads a resume and outputs
the BEST 6-10 search queries this candidate should run on LinkedIn / Indeed.
Output ONLY a JSON object - no commentary, no markdown fences."""


def suggest_search_terms_user_prompt(resume_text: str) -> str:
    return f"""Suggest 6-10 highly-specific job search queries for this candidate.

Mix exact role titles AND a few broader fall-back queries. Skip generic queries
like just "Engineer". For interns/students, include "Intern" variants.

=== RESUME ===
{resume_text[:5000]}

Output ONLY this JSON:
{{
  "search_terms": ["<query 1>", "<query 2>", ...],
  "experience_level": "<internship | entry | associate | mid_senior_level | director | executive>",
  "rationale": "<one sentence on why these terms>"
}}"""


# ── Resume/cover-letter REWRITE pass (boost ATS score) ──────────────────────
# Used when the first pass scored below the user's target. Sends the previous
# attempt + missing keywords + advice back to the LLM and asks for a tighter
# version, while preserving anti-hallucination guarantees.

IMPROVE_WRITER_SYSTEM = """You are revising a tailored resume + cover letter to raise its ATS score.

ABSOLUTE RULES (these override everything else):
1. Use ONLY information from the candidate's original master resume. Never invent skills, jobs, dates, metrics, certifications, or technologies the candidate has not actually used.
2. You MAY weave missing keywords into the resume IF AND ONLY IF the candidate genuinely has that experience or skill. If a missing keyword refers to something the candidate has not done, do NOT add it - leave the score below target. An honest 65/100 is better than a fake 90/100.
3. You MAY rephrase, reorder, and emphasize existing bullets to better match the job description language.
4. Keep ATS-friendly formatting: plain text, standard headers (SUMMARY, TECHNICAL SKILLS, EXPERIENCE, PROJECTS & PUBLICATIONS, EDUCATION, LEADERSHIP & ACTIVITIES), no graphics or tables.

RESUME LENGTH — CRITICAL:
5. The resume MUST fit on 2 pages or fewer (Calibri/Carlito 10.5 pt, 0.7 in margins).
   Cap every role to 3–4 bullets. If the previous attempt is too long, cut the weakest bullets first.
   Do NOT add new bullets or roles not in the original master resume.

HEADER FORMAT — output these exact 4 lines at the top of BOTH the resume and the cover letter:
<Candidate full name in ALL CAPS>
<tagline / headline from the original resume>
<City, State • email • phone>
Portfolio • GitHub • LinkedIn

COVER LETTER — 1 page max. EXACT structure required:
Line 0:  Dear Hiring Manager,
(blank line)
Paragraph 1 (hook):  First person. Who I am + the specific role. Warm and direct.
(blank line)
Paragraph 2:  Why this company and role specifically.
(blank line)
Paragraph 3:  2–3 specific named projects from the resume that map to the JD.
(blank line)
Paragraph 4 (close):  Availability, thanks, sign-off — "Sincerely," then the candidate's full name.

CRITICAL: Every paragraph boundary MUST be a blank line (\\n\\n) in the JSON string. Do NOT run the letter as one block.

Return ONLY a valid JSON object with this exact shape - no markdown, no commentary:
{
  "tailored_resume": "<full revised resume text>",
  "cover_letter":    "<full revised cover letter text>",
  "ats_score":       <integer 0-100 - your honest re-score after revision>,
  "ats_missing_keywords": ["<keyword>", ...],
  "ats_advice":      "<one sentence on what can/can't be improved further>",
  "rewrite_notes":   "<one sentence: what you changed in this pass>"
}"""


def improve_writer_user_prompt(
    resume_text: str,
    job_description: str,
    previous_resume: str,
    previous_cover_letter: str,
    previous_score: int,
    missing_keywords: list[str],
    previous_advice: str,
    target_score: int,
    web_context: str = "",
    formatting_instructions: str = "",
) -> str:
    missing_block = ", ".join(missing_keywords[:20]) if missing_keywords else "(none reported)"
    return f"""The previous attempt scored {previous_score}/100 against the job
description. Target is {target_score}/100. Improve the resume and cover letter
by incorporating MISSING KEYWORDS that TRUTHFULLY apply to the candidate.

=== ORIGINAL MASTER RESUME (source of truth - never invent beyond this) ===
{resume_text}

=== JOB DESCRIPTION ===
{job_description}

=== COMPANY/ROLE CONTEXT ===
{web_context or 'None available.'}

=== PREVIOUS ATTEMPT - TAILORED RESUME ===
{previous_resume}

=== PREVIOUS ATTEMPT - COVER LETTER ===
{previous_cover_letter}

=== PREVIOUS SCORE: {previous_score}/100 ===
=== MISSING KEYWORDS FROM PREVIOUS PASS ===
{missing_block}

=== PREVIOUS ADVICE ===
{previous_advice or 'None.'}

=== FORMATTING INSTRUCTIONS ===
{formatting_instructions or 'Standard ATS-friendly formatting.'}

Instructions:
- Re-read the original master resume carefully. Find any place where a missing
  keyword genuinely applies (synonyms, related projects, transferable skills) and
  surface it in the revised resume.
- For each missing keyword: if the candidate truly has it, weave it in. If not,
  leave it out and accept the lower score - honesty over score.
- Tighten weak bullets, add measurable impact where the original master resume
  shows numbers, mirror the job description's vocabulary where truthful.
- Score honestly. Do NOT inflate the score to hit the target.

Output ONLY the JSON object specified in your system instructions. The
tailored_resume and cover_letter fields should contain the FULL text of each
document with newlines escaped as \n inside the JSON string."""


# ── AI Application Copilot ────────────────────────────────────────────────────
# Truthful, profile-grounded answers to job-application questions.
# Confidence scoring uses a composite of:
#   - cache_similarity  (0-1 from qa_memory.search)
#   - type_determinism  (yes_no=100, numeric=85, short_text=65, long_text=45)
#   - llm_self_confidence (0-100, returned in the JSON below)
# Final score formula is documented in core/copilot.py::_composite_confidence.

CLASSIFY_SYSTEM = (
    "Classify the following job application question into exactly one category. "
    "Return ONLY one of these four tokens, nothing else:\n"
    "  yes_no | short_text | long_text | numeric"
)


COPILOT_SYSTEM = """You are an AI Copilot answering job application questions truthfully on behalf of a candidate.

ABSOLUTE RULES (these override everything else):
1. Use ONLY facts present in the CANDIDATE PROFILE section. Never claim skills, certifications,
   years, projects, or technologies the candidate has not actually demonstrated.
2. If a required fact is absent from the profile, provide a truthful hedge
   (e.g. "I have not yet used X professionally, but I have worked with closely-related Y").
   Set self_confidence < 40 and needs_review = true in that case.
3. Yes/No questions: answer "Yes" or "No" first, then a ONE-sentence justification if helpful.
4. Numeric questions: answer with a number only (no units, no commentary).
5. Short-text / long-text: recruiter-friendly, ATS-safe plain text. No markdown.
6. Maximum length: short_text ≤ 80 words, long_text ≤ 200 words.

Return ONLY valid JSON — no markdown fences, no commentary:
{
  "answer": "<answer text>",
  "self_confidence": <integer 0-100>,
  "needs_review": <true|false>,
  "reasoning": "<one sentence explaining your confidence level>"
}"""


def copilot_user_prompt(
    profile: str,
    job_title: str,
    company: str,
    job_description: str,
    question: str,
    question_type: str,
    cached_answer: str | None = None,
    instructions: str = "",
) -> str:
    cached_section = ""
    if cached_answer:
        cached_section = (
            f"\n=== CACHED ANSWER (adapt if needed, verify against profile) ===\n"
            f"{cached_answer}\n"
        )
    extra = f"\n=== ADDITIONAL INSTRUCTIONS ===\n{instructions}\n" if instructions else ""
    return (
        f"=== CANDIDATE PROFILE ===\n{profile}\n\n"
        f"=== JOB ===\n"
        f"Title: {job_title}\nCompany: {company}\n"
        f"Description (excerpt):\n{job_description[:1500]}\n\n"
        f"=== QUESTION TYPE: {question_type} ===\n"
        f"=== QUESTION ===\n{question}\n"
        f"{cached_section}{extra}\n"
        "Answer using ONLY facts from the candidate profile above. "
        "If a needed fact is missing, hedge honestly and flag needs_review=true."
    )
