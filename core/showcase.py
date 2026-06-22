"""
core/showcase.py - ShowcaseBuilder

Generates config/showcase.pdf — a clean one/two-page portfolio document
assembled from the user's GitHub public repos and profile data.

PackagerAgent already looks for config/showcase.pdf and attaches it to every
Ready-to-Apply card; no changes needed there.

Usage (called from the webapp route or CLI):
    from core.showcase import ShowcaseBuilder
    result = ShowcaseBuilder(root=Path(".")).build()
    # result = {"ok": True, "path": "config/showcase.pdf",
    #           "repo_count": 7, "generated_at": "2026-..."}

Network:  api.github.com only (no browser, no LLM).
Auth:     GITHUB_TOKEN env var used if set (increases rate limit 60→5000 req/hr).
Fallback: if GitHub is unreachable, builds from profile data only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

log = logging.getLogger(__name__)

# Maximum repos to include
_MAX_REPOS = 8


class ShowcaseBuilder:
    """Builds config/showcase.pdf from GitHub + profile data."""

    def __init__(self, root: Path = Path(".")):
        self.root        = Path(root)
        self.config_dir  = self.root / "config"
        self.output_path = self.config_dir / "showcase.pdf"

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self) -> dict[str, Any]:
        """Build showcase.pdf. Returns a status dict."""
        personal   = self._load_personal()
        repos      = self._fetch_github_repos(personal)
        story      = self._compose(personal, repos)
        self._render(story)

        result = {
            "ok":           True,
            "path":         str(self.output_path),
            "repo_count":   len(repos),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        log.info(
            f"showcase.pdf built: {len(repos)} repos, "
            f"{self.output_path.stat().st_size // 1024} KB"
        )
        return result

    @staticmethod
    def status(root: Path = Path(".")) -> dict[str, Any]:
        """Return current status without rebuilding."""
        p = Path(root) / "config" / "showcase.pdf"
        if not p.exists():
            return {"exists": False, "path": str(p)}
        st = p.stat()
        return {
            "exists":       True,
            "path":         str(p),
            "size_kb":      round(st.st_size / 1024, 1),
            "modified_at":  datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _load_personal(self) -> dict:
        p = self.config_dir / "personal.yaml"
        if not p.exists():
            return {}
        try:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning(f"ShowcaseBuilder: could not read personal.yaml: {e}")
            return {}

    def _github_username(self, personal: dict) -> str:
        """Extract GitHub username from the github URL in personal.yaml."""
        url = (
            (personal.get("contact") or {}).get("github", "")
            or os.environ.get("GITHUB_URL", "")
        ).strip().rstrip("/")
        if not url:
            return ""
        # https://github.com/username  or  github.com/username
        if "github.com/" in url:
            return url.split("github.com/", 1)[1].split("/")[0]
        return ""

    def _fetch_github_repos(self, personal: dict) -> list[dict]:
        """Fetch public repos via GitHub API. Returns sorted list."""
        username = self._github_username(personal)
        if not username:
            log.info("ShowcaseBuilder: no GitHub username found — skipping repo fetch")
            return []

        import urllib.request
        import urllib.error
        import json as _json

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {"User-Agent": "OneShot-ShowcaseBuilder/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = (
            f"https://api.github.com/users/{username}/repos"
            f"?sort=updated&per_page=100&type=public"
        )
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = _json.loads(resp.read().decode())
        except Exception as e:
            log.warning(
                f"ShowcaseBuilder: GitHub fetch failed for '{username}': {e} "
                "— building showcase from profile data only"
            )
            return []

        if not isinstance(raw, list):
            log.warning("ShowcaseBuilder: unexpected GitHub API response shape")
            return []

        repos = []
        for r in raw:
            if r.get("fork"):          # exclude forks
                continue
            repos.append({
                "name":        r.get("name", ""),
                "description": (r.get("description") or "").strip(),
                "language":    r.get("language") or "",
                "stars":       r.get("stargazers_count") or 0,
                "topics":      r.get("topics") or [],
                "url":         r.get("html_url", ""),
            })

        # Sort by stars descending, take top N
        repos.sort(key=lambda x: x["stars"], reverse=True)
        return repos[:_MAX_REPOS]

    # ── PDF composition ───────────────────────────────────────────────────────

    def _compose(self, personal: dict, repos: list[dict]) -> list:
        """Build the ReportLab story (list of Flowables)."""
        styles = self._styles()
        story  = []

        # ── Header ──────────────────────────────────────────────────────────
        name_parts = personal.get("name", {}) or {}
        full_name  = " ".join(
            p for p in [
                name_parts.get("first", ""),
                name_parts.get("middle", ""),
                name_parts.get("last", ""),
            ] if p
        ).strip() or "Portfolio"

        contact = personal.get("contact", {}) or {}
        email       = contact.get("email", "")
        phone       = contact.get("phone", "")
        linkedin    = contact.get("linkedin", "")
        github_url  = contact.get("github", "")
        portfolio   = contact.get("website", "")

        story.append(Paragraph(full_name, styles["name"]))

        contact_bits = [b for b in [email, phone, linkedin, github_url, portfolio] if b]
        if contact_bits:
            story.append(Paragraph("  |  ".join(contact_bits), styles["contact"]))

        story.append(Spacer(1, 6))
        story.append(HRFlowable(width="100%", thickness=1.0,
                                color=colors.black, spaceAfter=6))

        # ── GitHub Highlights ────────────────────────────────────────────────
        if repos:
            story.append(Paragraph("GITHUB HIGHLIGHTS", styles["section"]))
            story.append(HRFlowable(width="100%", thickness=0.4,
                                    color=colors.HexColor("#aaaaaa"), spaceAfter=4))
            for repo in repos:
                story += self._repo_flowables(repo, styles)
                story.append(Spacer(1, 6))

        # ── Selected Projects (from profile raw_questions / summary) ─────────
        # If personal.yaml has a "projects" list, render each entry
        projects = personal.get("projects") or []
        if projects:
            story.append(Spacer(1, 4))
            story.append(Paragraph("SELECTED PROJECTS", styles["section"]))
            story.append(HRFlowable(width="100%", thickness=0.4,
                                    color=colors.HexColor("#aaaaaa"), spaceAfter=4))
            for proj in projects:
                story += self._project_flowables(proj, styles)
                story.append(Spacer(1, 6))

        # Footer note
        story.append(Spacer(1, 14))
        story.append(Paragraph(
            f"Generated by OneShot · {datetime.now().strftime('%Y-%m-%d')}",
            styles["footer"],
        ))
        return story

    @staticmethod
    def _repo_flowables(repo: dict, styles: dict) -> list:
        """Render one GitHub repo as a compact entry."""
        parts: list = []

        # Title line: name • language • ★ stars
        meta_bits = []
        if repo.get("language"):
            meta_bits.append(repo["language"])
        if repo.get("stars"):
            meta_bits.append(f"★ {repo['stars']}")
        title = repo["name"]
        if meta_bits:
            title += f"  •  " + "  •  ".join(meta_bits)
        parts.append(Paragraph(title, styles["repo_title"]))

        # Description — clean filler but never LLM-polish (no facts context needed)
        desc = repo.get("description", "")
        if desc:
            try:
                from core.text_clean import clean as _clean
                desc = _clean(desc, kind="showcase")
            except Exception:
                pass
            parts.append(Paragraph(desc, styles["repo_desc"]))

        # URL
        url = repo.get("url", "")
        if url:
            parts.append(Paragraph(url, styles["repo_url"]))

        return parts

    @staticmethod
    def _project_flowables(proj: Any, styles: dict) -> list:
        """Render one project entry (dict or plain string)."""
        parts: list = []
        if isinstance(proj, dict):
            name = proj.get("name") or proj.get("title") or ""
            desc = proj.get("description") or proj.get("desc") or ""
            url  = proj.get("url") or proj.get("link") or ""
            if name:
                parts.append(Paragraph(name, styles["repo_title"]))
            if desc:
                try:
                    from core.text_clean import clean as _clean
                    desc = _clean(desc, kind="showcase")
                except Exception:
                    pass
                parts.append(Paragraph(desc, styles["repo_desc"]))
            if url:
                parts.append(Paragraph(url, styles["repo_url"]))
        elif isinstance(proj, str) and proj.strip():
            raw = proj.strip()
            try:
                from core.text_clean import clean as _clean
                raw = _clean(raw, kind="showcase")
            except Exception:
                pass
            parts.append(Paragraph(raw, styles["repo_desc"]))
        return parts

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, story: list) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(self.output_path),
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title="Portfolio Showcase",
        )
        doc.build(story)

    # ── Styles ────────────────────────────────────────────────────────────────

    @staticmethod
    def _styles() -> dict:
        base = getSampleStyleSheet()

        def _s(name, **kw):
            return ParagraphStyle(name, parent=base["Normal"], **kw)

        return {
            "name": _s(
                "ShowcaseName",
                fontSize=20, fontName="Helvetica-Bold",
                alignment=TA_CENTER, spaceAfter=4, leading=24,
            ),
            "contact": _s(
                "ShowcaseContact",
                fontSize=8.5, fontName="Helvetica",
                alignment=TA_CENTER, spaceAfter=2, leading=13,
                textColor=colors.HexColor("#444444"),
            ),
            "section": _s(
                "ShowcaseSection",
                fontSize=10, fontName="Helvetica-Bold",
                spaceBefore=12, spaceAfter=3, leading=13,
            ),
            "repo_title": _s(
                "ShowcaseRepoTitle",
                fontSize=10, fontName="Helvetica-Bold",
                spaceAfter=1, leading=14,
            ),
            "repo_desc": _s(
                "ShowcaseRepoDesc",
                fontSize=9.5, fontName="Helvetica",
                spaceAfter=1, leading=13,
                textColor=colors.HexColor("#333333"),
            ),
            "repo_url": _s(
                "ShowcaseRepoUrl",
                fontSize=8.5, fontName="Helvetica-Oblique",
                spaceAfter=0, leading=12,
                textColor=colors.HexColor("#0066cc"),
            ),
            "footer": _s(
                "ShowcaseFooter",
                fontSize=8, fontName="Helvetica",
                alignment=TA_CENTER,
                textColor=colors.HexColor("#999999"),
            ),
        }
