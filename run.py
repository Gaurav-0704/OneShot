"""
run.py - single CLI for the 6-agent OneShot pipeline.

OneShot discovers jobs and writes a tailored resume + cover letter for each.
It never submits applications — you review and apply yourself.

Commands:
    python run.py serve                   #  recommended: opens the web UI
    python run.py run                     # CLI pipeline (search + tailor)
    python run.py run --limit 5           # cap to 5 applications this run
    python run.py run --no-score          # skip Claude fit-scoring (cheaper)
    python run.py run --no-research       # skip ResearchAgent (faster)
    python run.py run --site linkedin     # restrict to one platform
    python run.py run --resume my.pdf     # different master resume

    python run.py profile                 # build & print the UserProfile, no scraping
    python run.py status                  # print today's + lifetime application stats
    python run.py history --limit 20      # last N applied jobs
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agents.orchestrator import Orchestrator      # noqa: E402
from agents.profile import ProfileAgent           # noqa: E402


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    log_dir = ROOT / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    for noisy in ("httpx", "urllib3", "selenium", "WDM"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_serve(args) -> int:
    """Launch the Flask web UI."""
    from webapp.app import run_server
    run_server(
        ROOT,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def cmd_run(args) -> int:
    # OneShot only discovers + tailors; it never submits. dry_run stays True.
    # Optional --site override → patch preferences.yaml in memory via env? Simplest:
    # we let DiscoveryAgent read prefs as-is and we modify it post-build via a temp env hack.
    # Cleaner: patch the loaded YAML. We'll read & rewrite into ProfileAgent's flow by
    # passing through the orchestrator's profile (it reads raw_preferences directly).
    if args.site:
        os.environ["JOBAPPLIER_SITE_OVERRIDE"] = args.site  # consumed below

    orch = Orchestrator(
        ROOT,
        master_resume=Path(args.resume) if args.resume else None,
        dry_run=True,
        pause=False,
        run_limit=args.limit,
        score_jobs=not args.no_score,
        do_research=not args.no_research,
        run_ats_check=not args.no_ats,
        require_min_ats=args.min_ats,
        headless=True,
    )

    # Apply --site override by patching prefs.yaml in memory. We do this by
    # subclassing orchestrator's profile build, but simpler: monkey-patch the YAML loader.
    if args.site:
        _patch_site_override(args.site)

    return orch.run()


def cmd_profile(args) -> int:
    """Build the UserProfile and print it without running the pipeline."""
    agent = ProfileAgent(ROOT / "config",
                         master_resume=Path(args.resume) if args.resume else None)
    profile = agent.build()
    print()
    print("═" * 64)
    print(f"  USER PROFILE - {profile.full_name}")
    print("═" * 64)
    print(f"  Email:    {profile.email}")
    print(f"  Phone:    {profile.phone}")
    print(f"  Location: {profile.city}, {profile.state}, {profile.country}")
    print(f"  LinkedIn: {profile.linkedin_url}")
    print(f"  GitHub:   {profile.github_url}")
    print(f"  Resume:   {profile.master_resume_path} ({len(profile.master_resume_text)} chars)")
    print()
    print(f"  Years exp:   {profile.years_of_experience}")
    print()
    if profile.github_repos:
        print(f"  GitHub: top {len(profile.github_repos)} repos")
        for r in profile.github_repos[:5]:
            print(f"    - {r['name']:<25} ★{r['stars']:<5} {r.get('language') or ''}  {r.get('description','')[:50]}")
        print(f"  GitHub languages: {', '.join(profile.github_languages[:10])}")
    print("═" * 64)
    return 0


def cmd_status(args) -> int:
    """Print application stats (today + lifetime)."""
    applied = ROOT / "outputs" / "applied_jobs.csv"
    failed = ROOT / "outputs" / "failed_jobs.csv"

    def _counts(path: Path):
        if not path.exists():
            return 0, 0
        today = datetime.now().date().isoformat()
        n_total = n_today = 0
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                n_total += 1
                if row.get("applied_at", "").startswith(today) or row.get("failed_at", "").startswith(today):
                    n_today += 1
        return n_total, n_today

    a_total, a_today = _counts(applied)
    f_total, f_today = _counts(failed)

    print()
    print(f"  Applied  - today: {a_today}    lifetime: {a_total}")
    print(f"  Failed   - today: {f_today}    lifetime: {f_total}")
    print()
    print(f"  Files:")
    print(f"    {applied}")
    print(f"    {failed}")
    print()
    return 0


def cmd_history(args) -> int:
    applied = ROOT / "outputs" / "applied_jobs.csv"
    if not applied.exists():
        print("no applications yet")
        return 0

    with applied.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    rows = rows[-args.limit:][::-1]   # most recent first
    print()
    print(f"{'date':<19}  {'site':<11}  {'company':<25}  {'title':<35}  {'sub'}")
    print("─" * 100)
    for r in rows:
        d = (r.get("applied_at") or "")[:19]
        site = (r.get("site") or "")[:11]
        co = (r.get("company") or "")[:25]
        ti = (r.get("title") or "")[:35]
        sub = "✓" if str(r.get("submitted", "")).lower() in {"true", "1"} else "·"
        print(f"{d:<19}  {site:<11}  {co:<25}  {ti:<35}  {sub}")
    print()
    return 0


# ── --site override helper ────────────────────────────────────────────────────

def _patch_site_override(site: str) -> None:
    """Rewrite preferences.yaml in memory so DiscoveryAgent only scrapes one site.
    Implemented by replacing yaml.safe_load briefly; simpler than threading a
    parameter through ProfileAgent."""
    pref_path = ROOT / "config" / "preferences.yaml"
    if not pref_path.exists():
        return
    data = yaml.safe_load(pref_path.read_text(encoding="utf-8")) or {}
    data["sites"] = [site]

    # Save the patched copy under config/.runtime.preferences.yaml and
    # set an env var that ProfileAgent could read - but for now the simplest
    # path is to overwrite. We won't because that mutates user state.
    # Instead, monkey-patch:
    import yaml as _y
    _orig = _y.safe_load
    def _patched(stream, *a, **kw):
        result = _orig(stream, *a, **kw)
        # Identify by content shape - preferences has "search_terms"
        if isinstance(result, dict) and "search_terms" in result and "sites" in result:
            result["sites"] = [site]
        return result
    _y.safe_load = _patched


# ── Argparse ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description="OneShot - 6-agent job application pipeline.")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging.")
    sub = p.add_subparsers(dest="command", required=False)

    # serve
    ps = sub.add_parser("serve", help="Launch the web UI (recommended).")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=5001)
    ps.add_argument("--no-browser", action="store_true", help="Don't auto-open browser.")

    # run
    pr = sub.add_parser("run", help="Run the full pipeline (search + tailor, no submit).")
    pr.add_argument("--no-score", action="store_true", help="Skip Claude fit scoring.")
    pr.add_argument("--no-research", action="store_true", help="Skip ResearchAgent enrichment.")
    pr.add_argument("--no-ats", action="store_true", help="Skip ATS self-check in WriterAgent.")
    pr.add_argument("--min-ats", type=int, default=0, help="Reject applications with ATS score below this.")
    pr.add_argument("--limit", type=int, default=None, help="Max applications this run.")
    pr.add_argument("--site", choices=["linkedin", "indeed", "greenhouse", "glassdoor", "zip_recruiter"],
                    default=None, help="Restrict to one site.")
    pr.add_argument("--resume", default=None, help="Path to master resume (overrides config).")

    # profile
    pp = sub.add_parser("profile", help="Print the assembled UserProfile.")
    pp.add_argument("--resume", default=None)

    # status / history
    sub.add_parser("status", help="Show today's + lifetime application stats.")
    ph = sub.add_parser("history", help="Show recent applications.")
    ph.add_argument("--limit", type=int, default=20)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(getattr(args, "verbose", False))

    cmd = args.command or "serve"  # default to web UI if no subcommand
    if cmd == "serve":
        if args.command is None:
            args = parser.parse_args(["serve"])
        return cmd_serve(args)
    if cmd == "run":
        if args.command is None:
            args = parser.parse_args(["run"])
        return cmd_run(args)
    if cmd == "profile":
        return cmd_profile(args)
    if cmd == "status":
        return cmd_status(args)
    if cmd == "history":
        return cmd_history(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
