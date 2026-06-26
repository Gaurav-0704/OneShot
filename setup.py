"""
setup.py - One-command installer for OneShot.

Usage:
    python setup.py            # full install + first-run wizard + open the web UI
    python setup.py --no-run   # install only, don't launch the server

What it does:
  1. Verifies Python >= 3.10
  2. Creates a virtualenv (./venv) if not present
  3. Installs requirements.txt into it
  4. Copies .env.example -> .env if missing
  5. Walks you through a quick wizard:
       - pick LLM provider (Claude / OpenAI / Gemini-free)
       - paste API key
       - LinkedIn login (optional)
  6. Launches the web UI on http://127.0.0.1:5000

Re-running is safe: existing settings are preserved.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / "venv"
ENV  = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQS = ROOT / "requirements.txt"

# ── Pretty output ─────────────────────────────────────────────────────────

class C:
    R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"
    M = "\033[95m"; CY= "\033[96m"; W = "\033[97m"; D = "\033[2m"
    X = "\033[0m"; BOLD = "\033[1m"

def hdr(s):  print(f"\n{C.BOLD}{C.CY}── {s} ──{C.X}")
def ok(s):   print(f"  {C.G}✓{C.X} {s}")
def info(s): print(f"  {C.D}·{C.X} {s}")
def warn(s): print(f"  {C.Y}!{C.X} {s}")
def err(s):  print(f"  {C.R}✗{C.X} {s}")
def ask(q, default=""):
    suffix = f" [{C.D}{default}{C.X}]" if default else ""
    a = input(f"  {C.B}?{C.X} {q}{suffix}: ").strip()
    return a or default
def ask_secret(q):
    try:
        import getpass
        return getpass.getpass(f"  {C.B}?{C.X} {q}: ").strip()
    except Exception:
        return input(f"  {C.B}?{C.X} {q}: ").strip()


# ── Steps ─────────────────────────────────────────────────────────────────

def check_python():
    hdr("Checking Python")
    v = sys.version_info
    if v < (3, 10):
        err(f"Python 3.10+ required (found {v.major}.{v.minor}).")
        sys.exit(1)
    if v >= (3, 14):
        warn(f"Python {v.major}.{v.minor} is bleeding-edge.")
        warn("Some optional packages (selenium driver wheels, undetected-chromedriver)")
        warn("may not yet ship 3.14 wheels and could fail to compile from source.")
        warn("If you hit a build error, install Python 3.13 instead and re-run:")
        warn("  rmdir /s /q venv && py -3.13 setup.py")
        print()
        ans = input(f"  {C.B}?{C.X} Continue with {v.major}.{v.minor}? [y/N]: ").strip().lower()
        if ans not in {"y", "yes"}:
            sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


def venv_python() -> str:
    if platform.system() == "Windows":
        return str(VENV / "Scripts" / "python.exe")
    return str(VENV / "bin" / "python")


def venv_pip_cmd() -> list[str]:
    """Use 'python -m pip' instead of pip.exe directly. On Windows, pip.exe
    can't replace itself (file lock); this form sidesteps that."""
    return [venv_python(), "-m", "pip"]


def make_venv():
    hdr("Virtualenv")
    if VENV.exists() and Path(venv_python()).exists():
        ok("venv already exists")
        return
    info("creating ./venv (this is one-time)")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    ok("venv created")


def install_reqs():
    hdr("Installing dependencies")
    if not REQS.exists():
        err("requirements.txt missing - run from the OneShot folder")
        sys.exit(1)

    pip = venv_pip_cmd()

    # Upgrading pip is best-effort. If it fails (often on Windows because pip
    # can't replace itself), we just continue with whatever pip is installed.
    info("upgrading pip (best effort) ...")
    try:
        subprocess.check_call(pip + ["install", "--upgrade", "pip", "--quiet", "--disable-pip-version-check"])
        ok("pip upgraded")
    except subprocess.CalledProcessError:
        warn("pip upgrade skipped (not critical - continuing)")

    info("running pip install (1-2 min the first time) ...")
    try:
        subprocess.check_call(pip + ["install", "-r", str(REQS), "--disable-pip-version-check"])
    except subprocess.CalledProcessError as e:
        err(f"pip install failed (exit {e.returncode}).")
        py = sys.version_info
        if py >= (3, 13):
            warn(f"You're on Python {py.major}.{py.minor}. Some pinned packages may not yet")
            warn("have prebuilt wheels for that version. Try Python 3.11 or 3.12 if errors persist.")
        sys.exit(1)
    ok("dependencies installed")


def ensure_env_file():
    hdr("Configuration")
    if not ENV.exists():
        if not ENV_EXAMPLE.exists():
            err(".env.example missing - corrupt repo?")
            sys.exit(1)
        shutil.copy(ENV_EXAMPLE, ENV)
        ok(f"created {ENV.name} from .env.example")
    else:
        ok(f"{ENV.name} already present (settings preserved)")


def env_get(key: str) -> str:
    if not ENV.exists():
        return ""
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def env_set(key: str, value: str) -> None:
    """Update or insert KEY=VALUE in .env, preserving order."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    found = False
    out = []
    for line in lines:
        if line.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def wizard():
    hdr("Setup wizard")
    print(f"  {C.D}Existing settings are kept.{C.X}")

    # --- LLM provider ---
    have_claude = bool(env_get("ANTHROPIC_API_KEY"))
    have_openai = bool(env_get("OPENAI_API_KEY"))
    have_gemini = bool(env_get("GEMINI_API_KEY"))

    if have_claude or have_openai or have_gemini:
        info("API key already set - skipping wizard. Edit Settings tab in the UI to change.")
        return

    print()
    print(f"  {C.BOLD}Pick an LLM provider:{C.X}")
    print(f"    {C.CY}1{C.X}) Claude (Anthropic)  - best quality, $5 free trial credit")
    print(f"    {C.CY}2{C.X}) Gemini (Google)     - {C.G}FREE 1,500 requests/day{C.X}, recommended if you don't want to pay")
    print(f"    {C.CY}3{C.X}) OpenAI              - paid, no free tier")
    print(f"    {C.CY}4{C.X}) Skip - I'll add a key later via the UI Settings tab")
    choice = ask("Choice", "2")

    if choice == "1":
        env_set("LLM_PROVIDER", "claude")
        env_set("LLM_MODEL", "claude-sonnet-4-6")
        env_set("LLM_MODEL_CHEAP", "claude-haiku-4-5-20251001")
        print(f"\n  {C.D}Get a key at: {C.B}https://console.anthropic.com{C.X}")
        k = ask_secret("Paste your Claude API key (sk-ant-...)")
        if k: env_set("ANTHROPIC_API_KEY", k); ok("Claude key saved")
    elif choice == "2":
        env_set("LLM_PROVIDER", "gemini")
        env_set("LLM_MODEL", "gemini-2.5-pro")
        env_set("LLM_MODEL_CHEAP", "gemini-2.5-flash")
        print(f"\n  {C.D}Get a free key at: {C.B}https://aistudio.google.com/apikey{C.X}")
        k = ask_secret("Paste your Gemini API key (AIza...)")
        if k: env_set("GEMINI_API_KEY", k); ok("Gemini key saved")
    elif choice == "3":
        env_set("LLM_PROVIDER", "openai")
        env_set("LLM_MODEL", "gpt-4o")
        env_set("LLM_MODEL_CHEAP", "gpt-4o-mini")
        print(f"\n  {C.D}Get a key at: {C.B}https://platform.openai.com/api-keys{C.X}")
        k = ask_secret("Paste your OpenAI API key (sk-...)")
        if k: env_set("OPENAI_API_KEY", k); ok("OpenAI key saved")
    else:
        warn("Skipping API key - add one in the UI Settings tab before running the pipeline.")

    # --- LinkedIn (optional) ---
    print()
    if not env_get("LINKEDIN_USERNAME"):
        print(f"  {C.BOLD}Optional: save your LinkedIn login{C.X} (skip with Enter)")
        print(f"  {C.D}Otherwise you'll log in manually each session.{C.X}")
        u = ask("LinkedIn email (or Enter to skip)")
        if u:
            env_set("LINKEDIN_USERNAME", u)
            p = ask_secret("LinkedIn password")
            if p: env_set("LINKEDIN_PASSWORD", p); ok("LinkedIn login saved")


def make_resume_placeholder():
    hdr("Resume")
    cfg = ROOT / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    pdf = cfg / "master_resume.pdf"
    if pdf.exists():
        ok(f"{pdf.name} already in place")
        return
    warn("No resume yet - drop yours at config/master_resume.pdf, OR upload it from the Profile tab in the UI.")


def launch():
    hdr("Launching OneShot")
    print(f"  {C.D}The web UI will open at {C.B}http://127.0.0.1:5000{C.D} ...{C.X}\n")
    cmd = [venv_python(), str(ROOT / "run.py"), "serve"]
    try:
        subprocess.call(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        print()
        ok("server stopped")


def main() -> int:
    p = argparse.ArgumentParser(description="OneShot installer")
    p.add_argument("--no-run", action="store_true", help="Install but don't launch the web UI.")
    p.add_argument("--no-wizard", action="store_true", help="Skip the interactive API-key wizard.")
    args = p.parse_args()

    print(f"\n{C.BOLD}{C.M}OneShot - local setup{C.X}")
    print(f"  {C.D}cwd: {ROOT}{C.X}")

    check_python()
    make_venv()
    install_reqs()
    ensure_env_file()
    if not args.no_wizard:
        try:
            wizard()
        except KeyboardInterrupt:
            print(); warn("wizard skipped")
    make_resume_placeholder()

    print()
    ok("Setup complete.")
    if args.no_run:
        print(f"  {C.D}Run later with: {C.W}{venv_python()} {ROOT / 'run.py'}{C.X}")
        return 0
    launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
