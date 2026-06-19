"""
core/pdf_generator.py
Converts tailored resume and cover letter text into clean, ATS-friendly PDFs
using ReportLab, styled to match the Calibri/Carlito reference format.

Font chain: Calibri (Windows) → Carlito (Linux) → Helvetica (built-in).
Sizes (reference-matched): name 16 pt Bold, contact/body/bullets 10.5 pt,
section headers 11 pt Bold UPPERCASE, cover-letter body 11 pt.
Header items (Portfolio • GitHub • LinkedIn) are PLAIN TEXT — no href, no color.
"""

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


# ── Font registration ─────────────────────────────────────────────────────────

def _register_body_font() -> tuple[str, str, str]:
    """Try Calibri (Win) → Carlito (Linux) → Helvetica (built-in).
    Returns (normal, bold, italic) font name strings."""

    win = Path("C:/Windows/Fonts")
    if (win / "calibri.ttf").exists():
        try:
            pdfmetrics.registerFont(TTFont("Calibri",        str(win / "calibri.ttf")))
            pdfmetrics.registerFont(TTFont("Calibri-Bold",   str(win / "calibrib.ttf")))
            pdfmetrics.registerFont(TTFont("Calibri-Italic", str(win / "calibrii.ttf")))
            pdfmetrics.registerFontFamily(
                "Calibri",
                normal="Calibri", bold="Calibri-Bold", italic="Calibri-Italic",
            )
            return "Calibri", "Calibri-Bold", "Calibri-Italic"
        except Exception:
            pass

    for base_dir in [
        Path("/usr/share/fonts/truetype/crosextra"),
        Path("/usr/share/fonts/carlito"),
        Path("/usr/share/fonts/truetype/carlito"),
    ]:
        reg    = base_dir / "Carlito-Regular.ttf"
        bold   = base_dir / "Carlito-Bold.ttf"
        italic = base_dir / "Carlito-Italic.ttf"
        if reg.exists():
            try:
                pdfmetrics.registerFont(TTFont("Carlito", str(reg)))
                b_name = "Carlito-Bold"   if bold.exists()   else "Carlito"
                i_name = "Carlito-Italic" if italic.exists() else "Carlito"
                if bold.exists():
                    pdfmetrics.registerFont(TTFont("Carlito-Bold", str(bold)))
                if italic.exists():
                    pdfmetrics.registerFont(TTFont("Carlito-Italic", str(italic)))
                pdfmetrics.registerFontFamily(
                    "Carlito", normal="Carlito", bold=b_name, italic=i_name,
                )
                return "Carlito", b_name, i_name
            except Exception:
                pass

    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


_FONT, _FONT_BOLD, _FONT_ITALIC = _register_body_font()


# ── Known section header keywords ─────────────────────────────────────────────

SECTION_KEYWORDS = {
    "summary", "profile", "objective", "about",
    "experience", "work experience", "employment", "career history",
    "education", "academic background", "qualifications",
    "skills", "technical skills", "core competencies", "competencies",
    "certifications", "certificates", "awards", "honours", "honors",
    "projects", "publications", "projects & publications",
    "languages", "interests", "volunteering",
    "references", "professional development", "training",
    "leadership", "leadership & activities", "activities",
}


# ── Style definitions ──────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()

    name_style = ParagraphStyle(
        "CandidateName",
        parent=base["Normal"],
        fontSize=16,
        fontName=_FONT_BOLD,
        alignment=TA_CENTER,
        spaceAfter=2,
        leading=20,
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=base["Normal"],
        fontSize=10.5,
        fontName=_FONT,
        alignment=TA_CENTER,
        spaceAfter=1,
        leading=13,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=base["Normal"],
        fontSize=11,
        fontName=_FONT_BOLD,
        spaceBefore=10,
        spaceAfter=2,
        textColor=colors.black,
        leading=13,
    )
    job_role_style = ParagraphStyle(
        "JobRole",
        parent=base["Normal"],
        fontSize=10.5,
        fontName=_FONT_BOLD,
        spaceAfter=0,
        leading=13,
    )
    job_meta_style = ParagraphStyle(
        "JobMeta",
        parent=base["Normal"],
        fontSize=10.5,
        fontName=_FONT_ITALIC,
        spaceAfter=2,
        leading=13,
        textColor=colors.HexColor("#444444"),
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=base["Normal"],
        fontSize=10.5,
        fontName=_FONT,
        leading=13,
        leftIndent=12,
        firstLineIndent=0,
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=base["Normal"],
        fontSize=10.5,
        fontName=_FONT,
        leading=13,
        spaceAfter=3,
    )
    cover_body_style = ParagraphStyle(
        "CoverBody",
        parent=base["Normal"],
        fontSize=11,
        fontName=_FONT,
        leading=14,
        spaceAfter=0,
    )

    return {
        "name":       name_style,
        "contact":    contact_style,
        "section":    section_style,
        "job_role":   job_role_style,
        "job_meta":   job_meta_style,
        "bullet":     bullet_style,
        "body":       body_style,
        "cover_body": cover_body_style,
    }


# ── Line classification helpers ───────────────────────────────────────────────

def _strip_markdown(line: str) -> str:
    line = re.sub(r"^#{1,3}\s*", "", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    line = re.sub(r"\*(.*?)\*", r"\1", line)
    line = re.sub(r"_(.*?)_", r"\1", line)
    return line.strip()


def _is_section_header(raw: str) -> bool:
    stripped = raw.strip()
    if not stripped or len(stripped) > 60:
        return False
    clean = _strip_markdown(stripped)
    if not clean:
        return False
    if clean.isupper() and 2 < len(clean) < 55 and not clean.startswith("•"):
        return True
    lower = clean.lower().strip(":").strip()
    if lower in SECTION_KEYWORDS:
        return True
    if raw.strip().startswith("#"):
        return True
    return False


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^\s*[•\-\*–·]\s+\S", line))


def _is_job_entry(line: str) -> bool:
    stripped = line.strip()
    has_separator = bool(re.search(r"\s*[|–-]\s*", stripped))
    has_alpha = bool(re.search(r"[A-Za-z]{3,}", stripped))
    return has_separator and has_alpha and 10 < len(stripped) < 120


def _is_date_only(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d{4})"
        r".{0,30}(Present|Current|\d{4})$",
        stripped, re.IGNORECASE
    ))


def _split_job_entry(line: str):
    parts = re.split(r"\s*[|–-]\s*", line.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return line.strip(), ""


# ── Shared header-block parser ────────────────────────────────────────────────

def _parse_header_block(lines: list[str], styles: dict) -> tuple[list, int]:
    """Extract name + contact lines from the top of a document.
    Returns (flowables, next_line_index)."""
    story = []

    # Skip leading blank lines
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return story, i

    # Name (first non-empty line)
    name_line = _strip_markdown(lines[i])
    story.append(Paragraph(name_line, styles["name"]))
    i += 1

    # Contact block: consecutive non-empty lines until blank or section header
    while i < len(lines):
        l = lines[i].strip()
        if not l:
            i += 1
            break
        if _is_section_header(lines[i]) or l.lower().startswith("dear "):
            break
        # Each contact line gets its own centered paragraph (plain text, no links)
        story.append(Paragraph(_strip_markdown(l), styles["contact"]))
        i += 1

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceAfter=4))
    return story, i


# ── Resume parser ─────────────────────────────────────────────────────────────

def _parse_resume_to_flowables(text: str, styles: dict) -> list:
    story = []
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return story

    header_flowables, i = _parse_header_block(lines, styles)
    story.extend(header_flowables)

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not stripped:
            story.append(Spacer(1, 2))
            i += 1
            continue

        if _is_section_header(raw):
            clean_header = _strip_markdown(stripped).upper()
            story.append(Spacer(1, 6))
            story.append(Paragraph(clean_header, styles["section"]))
            story.append(HRFlowable(
                width="100%", thickness=0.4,
                color=colors.HexColor("#999999"), spaceAfter=3,
            ))
            i += 1
            continue

        if _is_bullet(raw):
            clean = re.sub(r"^\s*[•\-\*–·]\s*", "", stripped)
            clean = _strip_markdown(clean)
            story.append(Paragraph(f"• {clean}", styles["bullet"]))
            i += 1
            continue

        if _is_date_only(stripped):
            story.append(Paragraph(stripped, styles["job_meta"]))
            i += 1
            continue

        if _is_job_entry(raw):
            role, meta = _split_job_entry(stripped)
            story.append(Paragraph(_strip_markdown(role), styles["job_role"]))
            if meta:
                story.append(Paragraph(_strip_markdown(meta), styles["job_meta"]))
            i += 1
            continue

        if re.match(r"^\*\*.+\*\*$", stripped) or re.match(r"^__.+__$", stripped):
            clean = _strip_markdown(stripped)
            story.append(Paragraph(clean, styles["job_role"]))
            i += 1
            continue

        clean = _strip_markdown(stripped)
        if clean:
            story.append(Paragraph(clean, styles["body"]))
        i += 1

    return story


# ── Cover letter parser ───────────────────────────────────────────────────────

def _parse_letter_to_flowables(text: str, styles: dict) -> list:
    story = []
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return story

    # If the first non-blank line looks like a name header (not "Dear ..."),
    # render the header block the same way as the resume.
    first_content = lines[0].strip()
    # A name line is short (≤6 words), not "Dear ...", and not a known section keyword.
    # Do NOT exclude on isupper() — names are often all-caps in these templates.
    has_header = (
        first_content
        and not first_content.lower().startswith("dear ")
        and len(first_content.split()) <= 6
        and first_content.strip().lower().strip(":") not in SECTION_KEYWORDS
    )
    if has_header:
        header_flowables, start_i = _parse_header_block(lines, styles)
        story.extend(header_flowables)
        remaining_text = "\n".join(lines[start_i:])
    else:
        remaining_text = text

    # Split remaining on blank lines → paragraphs
    raw_paras = re.split(r"\n{2,}", remaining_text.strip())
    for para in raw_paras:
        para = para.strip()
        if not para:
            continue
        # Collapse internal single newlines
        para = re.sub(r"\n", " ", para)
        clean = _strip_markdown(para)
        if clean:
            story.append(Paragraph(clean, styles["cover_body"]))
            story.append(Spacer(1, 10))

    return story


# ── Public API ────────────────────────────────────────────────────────────────

def generate_resume_pdf(text: str, output_path: str) -> str:
    """Render resume text to a PDF file. Returns the output path."""
    styles = _build_styles()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Tailored Resume",
    )

    story = _parse_resume_to_flowables(text, styles)
    doc.build(story)
    return output_path


def generate_cover_letter_pdf(text: str, output_path: str) -> str:
    """Render cover letter text to a PDF file. Returns the output path."""
    styles = _build_styles()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Cover Letter",
    )

    story = _parse_letter_to_flowables(text, styles)
    doc.build(story)
    return output_path
