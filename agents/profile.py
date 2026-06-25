"""
agents/profile.py - ProfileAgent

Owns the user's data. Other agents read from this.

Capabilities:
  - build()                   -> assemble UserProfile from YAML + resume + GitHub
  - parse_resume_to_yaml()    -> Claude extracts fields from the resume,
                                 auto-fills BLANK fields in personal.yaml + questions.yaml
  - validate_profile()        -> returns missing required + recommended fields
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

from agents.base import Agent
from core.parser import extract_text
from llm.client import complete_cheap
from models import UserProfile

log = logging.getLogger(__name__)


# Only the fields the engine truly needs to search jobs and write a tailored
# resume + cover letter. Everything auto-apply (work authorization, salary,
# demographics, sponsorship) has been removed.
REQUIRED_FIELDS = [
    ("personal.name.first",                 "First name",            "Identity",     "personal.yaml"),
    ("personal.name.last",                  "Last name",             "Identity",     "personal.yaml"),
    ("personal.contact.email",              "Email",                 "Identity",     "personal.yaml"),
    ("personal.address.city",               "City",                  "Address",      "personal.yaml"),
    ("personal.address.country",            "Country",               "Address",      "personal.yaml"),
    ("questions.years_of_experience",       "Years of experience",   "Experience",   "questions.yaml"),
    ("questions.user_information_summary",  "Profile summary for AI","About you",    "questions.yaml"),
    ("master_resume",                       "Master resume PDF",     "Resume",       "config/master_resume.pdf"),
]

# Nice to have — improves tailoring quality, never blocks a run.
RECOMMENDED_FIELDS = [
    ("personal.contact.phone",      "Phone",                "Identity",     "personal.yaml"),
    ("personal.contact.linkedin",   "LinkedIn URL",         "Links",        "personal.yaml"),
]


def _get_dotted(d: dict, dotted: str):
    for k in dotted.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return d


def _set_dotted(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[parts[-1]] = value


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        return not s or s.upper().startswith("REPLACE_ME")
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, list):
        return not v
    return False


class ProfileAgent(Agent):
    name = "profile"
    role = "Owns user identity. Builds UserProfile from configs, the master resume, and public sources like GitHub."

    _EXTRACT_SYSTEM = (
        "You are extracting structured personal data from a candidate's resume. "
        "Return ONLY valid JSON, no commentary, no markdown fences. Use null when "
        "a field is not clearly stated. Never invent or guess."
    )

    def __init__(self, config_dir: Path, *, master_resume: Optional[Path] = None):
        super().__init__(profile=None, dry_run=True)
        self.config_dir = config_dir
        self.master_resume = master_resume

    def _find_resume_file(self):
        """Find the user's resume in any supported extension."""
        if self.master_resume and self.master_resume.exists():
            return self.master_resume
        for ext in (".pdf", ".docx", ".doc", ".txt", ".md"):
            p = self.config_dir / f"master_resume{ext}"
            if p.exists():
                return p
        return self.config_dir / "master_resume.pdf"  # fallback path (won't exist)

    def build(self) -> UserProfile:
        self.info("building user profile")
        personal = self._load_yaml("personal.yaml")
        questions = self._load_yaml("questions.yaml")
        preferences = self._load_yaml("preferences.yaml")
        profile = self._from_yaml(personal, questions, preferences)

        resume_path = self._find_resume_file()
        if resume_path.exists():
            try:
                profile.master_resume_path = resume_path
                profile.master_resume_text = extract_text(str(resume_path), resume_path.name)
                self.info(f"loaded resume: {resume_path.name} ({len(profile.master_resume_text)} chars)")
            except Exception as e:
                self.warn(f"failed to parse resume: {e}")

        if profile.github_url:
            self._enrich_github(profile)
        return profile

    def parse_resume_to_yaml(self, *, overwrite: bool = False) -> dict:
        """Run LLM extractor on master resume; auto-fill BLANK fields by default."""
        resume_path = self._find_resume_file()
        if not resume_path.exists():
            return {"ok": False, "error": "no resume found at config/master_resume.{pdf,docx,doc,txt,md}"}
        try:
            resume_text = extract_text(str(resume_path), resume_path.name)
        except Exception as e:
            return {"ok": False, "error": f"could not read resume: {e}"}
        if not resume_text.strip():
            return {"ok": False, "error": "resume parsed to empty text"}

        try:
            extracted = self._extract_with_llm(resume_text)
        except Exception as e:
            self.warn(f"LLM extraction failed: {e}")
            return {"ok": False, "error": f"LLM extraction failed: {e}"}

        personal = self._load_yaml("personal.yaml")
        questions = self._load_yaml("questions.yaml")
        personal.setdefault("name", {})
        personal.setdefault("contact", {})
        personal.setdefault("address", {})

        written: list[str] = []
        skipped: list[str] = []

        def _maybe_set(yaml_doc: dict, dotted: str, val):
            if val is None or val == "":
                return
            existing = _get_dotted(yaml_doc, dotted)
            if overwrite or _is_blank(existing):
                _set_dotted(yaml_doc, dotted, val)
                written.append(dotted)
            else:
                skipped.append(dotted)

        _maybe_set(personal, "name.first",       extracted.get("first_name"))
        _maybe_set(personal, "name.middle",      extracted.get("middle_name"))
        _maybe_set(personal, "name.last",        extracted.get("last_name"))
        _maybe_set(personal, "contact.email",    extracted.get("email"))
        _maybe_set(personal, "contact.phone",    extracted.get("phone"))
        _maybe_set(personal, "contact.linkedin", extracted.get("linkedin_url"))
        _maybe_set(personal, "contact.github",   extracted.get("github_url"))
        _maybe_set(personal, "contact.website",  extracted.get("website_url"))
        _maybe_set(personal, "address.city",     extracted.get("city"))
        _maybe_set(personal, "address.state",    extracted.get("state"))
        _maybe_set(personal, "address.country",  extracted.get("country"))

        if extracted.get("years_of_experience") is not None:
            existing = questions.get("years_of_experience", 0) or 0
            if overwrite or existing == 0:
                questions["years_of_experience"] = int(extracted["years_of_experience"])
                written.append("questions.years_of_experience")
            else:
                skipped.append("questions.years_of_experience")
        _maybe_set(questions, "linkedin_headline",        extracted.get("headline"))
        _maybe_set(questions, "linkedin_summary",         extracted.get("summary"))
        _maybe_set(questions, "user_information_summary", extracted.get("user_information_summary"))

        self._save_yaml("personal.yaml", personal)
        self._save_yaml("questions.yaml", questions)
        return {
            "ok": True,
            "extracted": extracted,
            "written": written,
            "skipped_existing": skipped,
            "resume_chars": len(resume_text),
        }


    def suggest_search_terms(self) -> dict:
        """Run an LLM call on the master resume and suggest 6-10 specific job
        search queries plus an experience-level guess. One call total."""
        from llm.prompts import SEARCH_TERMS_SUGGEST_SYSTEM, suggest_search_terms_user_prompt
        resume_path = self._find_resume_file()
        if not resume_path.exists():
            return {"ok": False, "error": "no resume"}
        try:
            resume_text = extract_text(str(resume_path), resume_path.name)
        except Exception as e:
            return {"ok": False, "error": f"resume read failed: {e}"}
        if not resume_text.strip():
            return {"ok": False, "error": "resume empty after parse"}
        try:
            raw = complete_cheap(
                SEARCH_TERMS_SUGGEST_SYSTEM,
                suggest_search_terms_user_prompt(resume_text),
                max_tokens=2000, json_mode=True,
            )
        except Exception as e:
            return {"ok": False, "error": f"LLM call failed: {e}"}

        # Use the same loose parser the fit-scorer uses - Gemini sometimes
        # wraps JSON in fences, adds preamble, or appends trailing commas
        # even with json_mode=True. Salvage as much as we can.
        from core.filter import _parse_json_loose
        data = _parse_json_loose(raw or "")

        # Last-resort regex salvage: pull every COMPLETE string out of the
        # search_terms array, even when Gemini truncates mid-array (no closing
        # `]`). We just locate the array opener, then greedy-grab quoted strings
        # until either the closing `]` or the end of the response.
        if not data or "search_terms" not in data:
            import re
            opener = re.search(r'"search_terms"\s*:\s*\[', raw or "")
            if opener:
                tail = raw[opener.end():]
                # Stop at the first unbalanced ] if present
                end = tail.find("]")
                segment = tail if end < 0 else tail[:end]
                items = re.findall(r'"((?:[^"\\]|\\.)+)"', segment)
                # Drop the last item if the response was truncated mid-string
                # (i.e. no closing `]` AND segment doesn't end with `"` or `,`)
                if end < 0 and items:
                    last_quote = segment.rfind('"')
                    after_last = segment[last_quote+1:].strip()
                    if after_last and not after_last.startswith(","):
                        items = items[:-1]
                if items:
                    data = {"search_terms": items, "experience_level": "", "rationale": ""}

        if not data or "search_terms" not in data:
            snippet = (raw or "")[:200].replace("\n", " ")
            self.log.warning(f"suggest_search_terms got unparseable response: {snippet!r}")
            return {
                "ok": False,
                "error": f"LLM returned unparseable JSON. Raw start: {snippet[:120]!r}",
            }

        terms = [t for t in (data.get("search_terms") or []) if t and isinstance(t, str)][:10]
        level = data.get("experience_level", "") or ""
        rationale = data.get("rationale", "") or ""
        if not terms:
            return {"ok": False, "error": "LLM returned no search terms"}
        return {"ok": True, "search_terms": terms, "experience_level": level, "rationale": rationale}

    def validate_profile(self) -> dict:
        personal = self._load_yaml("personal.yaml")
        questions = self._load_yaml("questions.yaml")

        def _resume_exists() -> bool:
            if self.master_resume and self.master_resume.exists():
                return True
            # Accept any common resume format the user might have uploaded
            for ext in (".pdf", ".docx", ".doc", ".txt", ".md"):
                if (self.config_dir / f"master_resume{ext}").exists():
                    return True
            return False

        def _check(field_path: str) -> bool:
            if field_path == "master_resume":
                return _resume_exists()
            if field_path.startswith("personal."):
                v = _get_dotted(personal, field_path[len("personal."):])
            elif field_path.startswith("questions."):
                v = _get_dotted(questions, field_path[len("questions."):])
            else:
                return True
            return not _is_blank(v)

        missing_required = [
            {"field": p, "label": l, "section": s, "file": f}
            for p, l, s, f in REQUIRED_FIELDS if not _check(p)
        ]
        missing_recommended = [
            {"field": p, "label": l, "section": s, "file": f}
            for p, l, s, f in RECOMMENDED_FIELDS if not _check(p)
        ]
        return {
            "is_complete": len(missing_required) == 0,
            "missing_required": missing_required,
            "missing_recommended": missing_recommended,
            "completeness_pct": int(100 * (1 - len(missing_required) / max(1, len(REQUIRED_FIELDS)))),
        }

    def _extract_user_prompt(self, resume_text: str) -> str:
        return (
            "Extract these fields from the resume below. Return ONLY this JSON shape:\n\n"
            "{\n"
            '  "first_name": "...", "middle_name": null, "last_name": "...",\n'
            '  "email": "...", "phone": "...",\n'
            '  "linkedin_url": "...", "github_url": "...", "website_url": null,\n'
            '  "city": "...", "state": null, "country": "...",\n'
            '  "headline": "<professional headline / current title>",\n'
            '  "summary": "<2-3 sentence professional summary>",\n'
            '  "years_of_experience": <integer estimate; null if unclear>,\n'
            '  "skills": ["<skill>", "..."],\n'
            '  "user_information_summary": "<3-5 sentence factual summary other AI agents will use as the candidate identity. Cover current role, years, key skills, work auth if mentioned, languages.>"\n'
            "}\n\n"
            "If a field is not present, use null. Never fabricate.\n\n"
            "=== RESUME TEXT ===\n"
            f"{resume_text[:8000]}\n"
        )

    def _extract_with_llm(self, resume_text: str) -> dict:
        raw = complete_cheap(
            self._EXTRACT_SYSTEM,
            self._extract_user_prompt(resume_text),
            max_tokens=4096,        # headroom so the JSON doesn't truncate mid-field
            json_mode=True,
        )
        # Robust JSON: try strict, then loose extraction, then regex fallback.
        parsed = _parse_json_loose(raw)
        if parsed is not None:
            return parsed
        # Last-resort: regex extract a few fields directly from the resume text
        # so the user still gets something even if the LLM output is mangled.
        log.warning("LLM JSON unparseable, falling back to regex extraction")
        return _regex_extract_resume(resume_text)

    def _load_yaml(self, name: str) -> dict[str, Any]:
        path = self.config_dir / name
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _save_yaml(self, name: str, data: dict) -> None:
        path = self.config_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _from_yaml(self, personal: dict, questions: dict, preferences: dict) -> UserProfile:
        name = personal.get("name", {}) or {}
        contact = personal.get("contact", {}) or {}
        address = personal.get("address", {}) or {}
        demo = personal.get("demographics", {}) or {}
        auth = personal.get("work_authorization", {}) or {}

        return UserProfile(
            first_name=name.get("first", "") or "",
            middle_name=name.get("middle", "") or "",
            last_name=name.get("last", "") or "",
            email=contact.get("email", "") or "",
            phone=contact.get("phone", "") or "",
            linkedin_url=contact.get("linkedin", "") or "",
            github_url=contact.get("github", "") or "",
            website_url=contact.get("website", "") or "",
            city=address.get("city", "") or "",
            state=address.get("state", "") or "",
            country=address.get("country", "") or "",
            zipcode=address.get("zipcode", "") or "",
            gender=demo.get("gender", "") or "",
            ethnicity=demo.get("ethnicity", "") or "",
            veteran_status=demo.get("veteran_status", "") or "",
            disability_status=demo.get("disability_status", "") or "",
            auth_us=str(auth.get("us", "No")),
            auth_eu=str(auth.get("eu", "No")),
            auth_uk=str(auth.get("uk", "No")),
            auth_canada=str(auth.get("canada", "No")),
            requires_sponsorship=str(auth.get("requires_sponsorship", "No")),
            years_of_experience=int(questions.get("years_of_experience", 0) or 0),
            desired_salary_usd=int(questions.get("desired_salary", 0) or 0),
            notice_period_days=int(questions.get("notice_period_days", 30) or 30),
            summary=questions.get("linkedin_summary", "") or "",
            headline=questions.get("linkedin_headline", "") or "",
            user_information_summary=questions.get("user_information_summary", "") or "",
            raw_personal=personal,
            raw_questions=questions,
            raw_preferences=preferences,
        )

    def _enrich_github(self, profile: UserProfile) -> None:
        username = self._extract_github_username(profile.github_url)
        if not username:
            return
        # Phase 4: cache GitHub enrichment by username (12-hour TTL) so repeat
        # profile builds don't re-hit the API or block on a slow response.
        from core import cache as _cache
        cached = _cache.get("github", username, ttl_seconds=12 * 3600)
        if cached is not None:
            profile.github_bio       = cached.get("bio", "")
            profile.github_repos     = cached.get("repos", [])
            profile.github_languages = cached.get("languages", [])
            return
        try:
            with httpx.Client(timeout=8) as client:
                u = client.get(f"https://api.github.com/users/{username}")
                if u.status_code == 200:
                    data = u.json()
                    profile.github_bio = data.get("bio", "") or ""
                r = client.get(
                    f"https://api.github.com/users/{username}/repos",
                    params={"sort": "updated", "per_page": 50},
                )
                if r.status_code == 200:
                    repos = r.json() or []
                    repos = sorted(repos, key=lambda x: x.get("stargazers_count", 0), reverse=True)[:10]
                    profile.github_repos = [
                        {"name": r.get("name"), "description": r.get("description") or "",
                         "language": r.get("language"), "stars": r.get("stargazers_count", 0),
                         "url": r.get("html_url")}
                        for r in repos
                    ]
                    langs = sorted(
                        {r.get("language") for r in repos if r.get("language")},
                        key=lambda l: -sum(1 for x in repos if x.get("language") == l),
                    )
                    profile.github_languages = [l for l in langs if l]
                    self.info(f"github: pulled {len(profile.github_repos)} top repos")
            _cache.set("github", username, {
                "bio": profile.github_bio,
                "repos": profile.github_repos,
                "languages": profile.github_languages,
            })
        except Exception as e:
            self.warn(f"github enrichment failed: {e}")

    @staticmethod
    def _extract_github_username(url: str) -> str:
        if not url:
            return ""
        url = url.rstrip("/")
        if "github.com/" in url:
            return url.split("github.com/", 1)[1].split("/", 1)[0]
        return ""


# ── JSON parsing helpers (kept module-level so they're easy to test) ─────────

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)


def _parse_json_loose(raw: str):
    """Try a sequence of repair strategies before giving up. Returns dict on
    success, None on total failure."""
    if not raw:
        return None
    text = _JSON_FENCE_RE.sub("", raw.strip())

    # Strategy 1: strict parse on the cleaned-up text
    try:
        return json.loads(text)
    except Exception:
        pass

    # Strategy 2: extract from first '{' to last '}' (drop preamble/postamble)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        substr = text[first:last + 1]
        try:
            return json.loads(substr)
        except Exception:
            pass

        # Strategy 3: escape unescaped newlines inside string values, fix
        # trailing commas. Common LLM output bugs.
        repaired = _repair_json(substr)
        try:
            return json.loads(repaired)
        except Exception:
            pass

    # Strategy 4: field-level salvage. When the JSON is truncated (e.g. the model
    # ran out of tokens mid-way), the EARLY fields — name, email, links, location
    # — are usually intact even though the object never closes. Pull every
    # complete "key": value pair out so we keep what we can instead of nothing.
    salvaged = _salvage_json_fields(text)
    if salvaged:
        return salvaged

    return None


_JSON_STR_FIELD  = re.compile(r'"([A-Za-z_]\w*)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_JSON_NULL_FIELD = re.compile(r'"([A-Za-z_]\w*)"\s*:\s*null')
_JSON_NUM_FIELD  = re.compile(r'"([A-Za-z_]\w*)"\s*:\s*(-?\d+(?:\.\d+)?)')


def _salvage_json_fields(text: str):
    """Extract every complete key/value pair from a broken/truncated JSON object."""
    out: dict = {}
    for m in _JSON_STR_FIELD.finditer(text):
        v = m.group(2).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        out.setdefault(m.group(1), v)
    for m in _JSON_NULL_FIELD.finditer(text):
        out.setdefault(m.group(1), None)
    for m in _JSON_NUM_FIELD.finditer(text):
        if m.group(1) not in out:
            num = m.group(2)
            out[m.group(1)] = int(num) if num.lstrip("-").isdigit() else float(num)
    return out or None


def _repair_json(s: str) -> str:
    """Best-effort JSON repair. Handles unescaped newlines inside strings and
    trailing commas before } or ]."""
    out = []
    in_str = False
    escape = False
    for ch in s:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch == "\n":
            out.append("\\n")
            continue
        if in_str and ch == "\r":
            continue
        if in_str and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    repaired = "".join(out)
    # Drop trailing commas: ",}" -> "}", ",]" -> "]"
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


_EMAIL_RE = re.compile(r"[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
# Accept links with OR without the https:// scheme (resumes often write the
# bare domain, e.g. "linkedin.com/in/jane").
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_/]+", re.I)
_GITHUB_RE   = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9\-_/]+", re.I)

# US state abbreviations + a few common country names, used to validate that a
# "City, X" segment really is a location (and not e.g. "Python, SQL").
_US_STATE_ABBR = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_COUNTRY_WORDS = {
    "usa", "u.s.a", "u.s.", "us", "united states", "united states of america",
    "india", "canada", "uk", "u.k.", "united kingdom", "england", "australia",
    "germany", "france", "ireland", "singapore", "netherlands", "spain",
}
_LOC_SEG_RE = re.compile(
    r"^([A-Za-z][A-Za-z.\-'' ]{1,28}),\s*([A-Za-z.\-'' ]{2,28})"
    r"(?:,\s*([A-Za-z.\-'' ]{2,28}))?$"
)


def _norm_link(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _extract_location(resume_text: str):
    """Best-effort (city, state, country) from the first lines of a resume.
    Validates the second/third token against US states / known countries so we
    don't mistake 'Python, SQL' for a location. Returns (city, state, country)."""
    for line in resume_text.splitlines()[:12]:
        for seg in re.split(r"[|•·]", line):
            m = _LOC_SEG_RE.match(seg.strip())
            if not m:
                continue
            city = m.group(1).strip()
            t2 = m.group(2).strip()
            t3 = (m.group(3) or "").strip()
            if t2.upper() in _US_STATE_ABBR:
                return city, t2, (t3 or "United States")
            if t2.lower() in _COUNTRY_WORDS:
                return city, None, t2
            if t3 and t3.lower() in _COUNTRY_WORDS:
                return city, t2, t3
    return None, None, None


def _regex_extract_resume(resume_text: str) -> dict:
    """Regex fallback when the LLM returns unparseable JSON.
    Pulls contact basics + location + links from the raw resume text."""
    out = {
        "first_name": None, "middle_name": None, "last_name": None,
        "email": None, "phone": None,
        "linkedin_url": None, "github_url": None, "website_url": None,
        "city": None, "state": None, "country": None,
        "headline": None, "summary": None,
        "years_of_experience": None, "skills": [],
        "user_information_summary": None,
    }
    m = _EMAIL_RE.search(resume_text)
    if m:
        out["email"] = m.group(0)
    m = _PHONE_RE.search(resume_text)
    if m:
        out["phone"] = m.group(0).strip()
    m = _LINKEDIN_RE.search(resume_text)
    if m:
        out["linkedin_url"] = _norm_link(m.group(0))
    m = _GITHUB_RE.search(resume_text)
    if m:
        out["github_url"] = _norm_link(m.group(0))
    city, state, country = _extract_location(resume_text)
    out["city"], out["state"], out["country"] = city, state, country
    # Best-guess name: first non-empty line of the resume that doesn't contain
    # an email or "@" and is short.
    for line in resume_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if 2 <= len(line.split()) <= 4 and "@" not in line and not _PHONE_RE.search(line):
            parts = line.split()
            if all(p[0].isupper() for p in parts if p and p[0].isalpha()):
                out["first_name"] = parts[0]
                if len(parts) >= 2:
                    out["last_name"] = parts[-1]
                break
    return out
