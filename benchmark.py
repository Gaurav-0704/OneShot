"""
benchmark.py  —  pipeline quality measurements for OneShot

I measure three things here:

  [1] Fit scoring accuracy
      I run the LLM scorer against 25 hand-labeled jobs, compute
      a confusion matrix, and compare against the all-accept baseline.

  [2] ATS rewrite loop effectiveness
      I send sample job descriptions through TailorAgent Phase 2
      with and without the rewrite pass and record the score delta.

  [3] JSON repair robustness
      I push 40 intentionally broken LLM responses through the
      multi-tier repair function in core/filter.py and measure
      recovery rate by failure type.

Usage:
    python benchmark.py                   # all three sections
    python benchmark.py --mode scoring    # fit scoring only
    python benchmark.py --mode rewrite    # ATS rewrite only
    python benchmark.py --mode repair     # JSON repair only (no API key needed)
    python benchmark.py --simulate        # skip real LLM calls; use random scores
    python benchmark.py --provider gemini # force a specific provider
    python benchmark.py --out results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

log = logging.getLogger("benchmark")


# -- Sample resume used as ground truth anchor ---------------------------------
# A generic mid-level software engineer profile. Replace with your actual
# master_resume.txt if you want numbers that reflect your own profile.

SAMPLE_RESUME = """
Jane Smith
Software Engineer  |  jane@example.com  |  github.com/janesmith

SUMMARY
5 years building backend services and data pipelines in Python and Go.
Led a team of 3 to migrate a monolith to microservices (AWS, Kubernetes).
Comfortable owning infrastructure end-to-end from CI/CD to production monitoring.

SKILLS
Languages:   Python, Go, TypeScript
Frameworks:  FastAPI, Django, React
Infra:       AWS (ECS, RDS, S3, Lambda), Kubernetes, Terraform, Docker
Data:        PostgreSQL, Redis, Kafka, dbt, Airflow
Tools:       GitHub Actions, Datadog, PagerDuty

EXPERIENCE
Senior Software Engineer  —  Acme Corp  (2021 – present)
  • Designed and shipped a Kafka-based event pipeline processing 2M events/day.
  • Reduced p99 API latency from 800 ms to 120 ms by replacing N+1 ORM queries.
  • Mentored 2 junior engineers; wrote onboarding docs adopted org-wide.

Software Engineer  —  StartupXYZ  (2019 – 2021)
  • Built a REST API serving 50k daily active users (FastAPI + PostgreSQL).
  • Owned the entire deploy pipeline: Docker -> ECS -> CloudWatch alerts.

EDUCATION
B.S. Computer Science  —  State University  (2019)
"""


# -- Labeled test set for fit scoring -----------------------------------------
# Each entry: title, company, short JD, expected label (True = good match).
# Labels are based on obvious role fit against SAMPLE_RESUME above.
# A human recruiter and the LLM should broadly agree on these.

@dataclass
class LabeledJob:
    job_id: str
    title: str
    company: str
    description: str
    expected: bool   # True = this job suits the resume above


LABELED_JOBS: list[LabeledJob] = [
    # -- Clear matches (expect True) -------------------------------------------
    LabeledJob("j01", "Senior Backend Engineer", "Stripe",
        "Python microservices, Kubernetes, PostgreSQL, Kafka. Lead feature delivery on "
        "payment APIs. 4+ years required. AWS or GCP. CI/CD ownership expected.", True),

    LabeledJob("j02", "Staff Software Engineer", "Datadog",
        "Own large-scale data ingestion pipelines (Go or Python). Kafka, ECS, Terraform. "
        "Work with distributed systems at 10B events/day. 5+ years.", True),

    LabeledJob("j03", "Backend Engineer II", "Notion",
        "FastAPI or Django, PostgreSQL, Redis. Ship new API endpoints and improve "
        "reliability. 3-6 years. TypeScript a plus on the frontend side.", True),

    LabeledJob("j04", "Platform Engineer", "Figma",
        "Build internal developer platform on Kubernetes and AWS. Terraform, Datadog, "
        "GitHub Actions. Python or Go preferred. 4+ years.", True),

    LabeledJob("j05", "Data Infrastructure Engineer", "Airbnb",
        "Airflow, dbt, Kafka, Spark. Own pipelines that power analytics for 100M+ listings. "
        "Python strong, SQL required. 3-5 years backend or data eng.", True),

    LabeledJob("j06", "Senior Software Engineer, API Platform", "Shopify",
        "Build and maintain high-throughput REST/GraphQL APIs in Go and Ruby. "
        "PostgreSQL, Redis, AWS. You own reliability SLAs. 5+ years.", True),

    LabeledJob("j07", "Software Engineer, Infrastructure", "Cloudflare",
        "Contribute to core networking infrastructure in Go. Docker, Kubernetes, Linux. "
        "CI/CD automation. 3+ years systems experience.", True),

    LabeledJob("j08", "Python Engineer, Data Pipelines", "Segment",
        "Kafka, Airflow, dbt. Build ETL workflows in Python. AWS Lambda, S3, Redshift. "
        "Collaborate with analysts. 3+ years data or backend engineering.", True),

    LabeledJob("j09", "Backend Software Engineer", "Linear",
        "TypeScript (Node) or Python backend, PostgreSQL, Redis. Startup pace, small team. "
        "Own features end to end from API design to deploy. 4+ years.", True),

    LabeledJob("j10", "Site Reliability Engineer", "PagerDuty",
        "On-call tooling, Kubernetes, Terraform, Datadog. Improve deployment reliability "
        "and reduce MTTR. Python or Go. 4+ years SRE or platform engineering.", True),

    LabeledJob("j11", "Senior Engineer, Microservices", "Lyft",
        "Python services, Kafka, PostgreSQL, AWS. You join a team migrating a monolith. "
        "Lead technical decisions, mentor juniors. 5+ years.", True),

    LabeledJob("j12", "Cloud Engineer", "HashiCorp",
        "Terraform, AWS, Kubernetes. Help customers adopt IaC. Python scripting. "
        "Customer-facing role with deep technical depth. 4+ years.", True),

    LabeledJob("j13", "Software Engineer II", "GitHub",
        "Ruby or Go backend, PostgreSQL. Ship features used by 100M developers. "
        "CI/CD culture, open source mindset. 3-5 years.", True),

    LabeledJob("j14", "API Engineer", "Twilio",
        "REST API design in Python or Go. High throughput, low latency. "
        "PostgreSQL, Redis, Kafka. 4+ years backend. AWS familiarity preferred.", True),

    LabeledJob("j15", "Senior Engineer, Data Platform", "Snowflake",
        "dbt, Airflow, Spark, Python. Design data modeling standards, mentor analysts. "
        "5+ years data or software engineering. SQL expertise required.", True),

    # -- Clear mismatches (expect False) --------------------------------------
    LabeledJob("j16", "iOS Engineer", "Apple",
        "Swift, SwiftUI, Xcode. Ship consumer-facing features in the iOS app. "
        "Strong UIKit knowledge. 3+ years iOS development required.", False),

    LabeledJob("j17", "Staff Android Engineer", "Spotify",
        "Kotlin, Jetpack Compose, Android SDK. Lead architecture of the Spotify mobile app. "
        "Deep Android internals knowledge. 6+ years.", False),

    LabeledJob("j18", "Embedded Software Engineer", "Tesla",
        "C, C++, RTOS. Vehicle firmware for body control modules. "
        "CAN bus, automotive AUTOSAR. Hardware debugging skills required. 4+ years.", False),

    LabeledJob("j19", "Quantitative Researcher", "Two Sigma",
        "PhD in math, physics, or CS. Develop statistical models for equity trading. "
        "C++ or Python quant research experience. No web/cloud background needed.", False),

    LabeledJob("j20", "Enterprise Sales Executive", "Salesforce",
        "Sell CRM solutions to Fortune 500 companies. Manage a $5M book of business. "
        "10+ years enterprise software sales. Quota attainment track record.", False),

    LabeledJob("j21", "UX Designer", "Airbnb",
        "Figma, design systems, user research. Own the visual design for the host product. "
        "Portfolio of shipped consumer products required. No coding needed.", False),

    LabeledJob("j22", "Game Engine Programmer", "Epic Games",
        "C++, Unreal Engine, graphics rendering, physics simulation. "
        "Profiling and optimization of real-time 3D systems. 5+ years game dev.", False),

    LabeledJob("j23", "DevOps Engineer (Junior)", "Small Startup",
        "Script bash and YAML, manage Jenkins pipelines. "
        "1 year experience OK. No Kubernetes yet. Looking to learn AWS.", False),
        # Mismatch: too junior, limited stack

    LabeledJob("j24", "Principal Architect, SAP", "IBM",
        "SAP S/4HANA, ABAP, BTP. Lead large enterprise SAP transformations. "
        "15+ years SAP consulting experience. Travel 80%.", False),

    LabeledJob("j25", "Data Analyst", "McKinsey",
        "Excel, Tableau, SQL. Build dashboards and PowerPoint decks for client delivery. "
        "MBA preferred. No coding or engineering background required.", False),
]


# -- Sample JDs for ATS rewrite benchmark -------------------------------------

REWRITE_JOBS = [
    {
        "slug": "senior-backend-python-kafka",
        "title": "Senior Backend Engineer",
        "company": "StreamCo",
        "jd": (
            "We're looking for a senior backend engineer to own our event-driven data "
            "pipeline. You'll work daily with Kafka, Python (FastAPI), PostgreSQL, and AWS. "
            "Key requirements: 5+ years Python backend, Kafka experience mandatory, "
            "PostgreSQL performance tuning, AWS ECS or Lambda, CI/CD with GitHub Actions, "
            "observability with Prometheus or Datadog, experience leading small teams. "
            "Bonus: Terraform, Redis, dbt. We value clean APIs, clear documentation, "
            "and on-call ownership. You'll be the primary owner of a pipeline processing "
            "5M events per day. Prior experience at a Series B+ startup preferred."
        ),
    },
    {
        "slug": "platform-engineer-kubernetes",
        "title": "Platform Engineer",
        "company": "DevTool Inc",
        "jd": (
            "Join our platform team to build the internal developer platform used by "
            "200 engineers. Stack: Kubernetes (EKS), Terraform, ArgoCD, GitHub Actions. "
            "You write Python tooling and Go microservices. Must-haves: 4+ years platform "
            "or SRE experience, Kubernetes cluster administration, Terraform at scale, "
            "Linux systems knowledge, ability to write runbooks and lead incident response. "
            "Nice to have: Crossplane, Backstage, Pulumi. You'll reduce deploy toil by "
            "building self-service tooling. Strong documentation expected."
        ),
    },
    {
        "slug": "data-engineer-airflow-dbt",
        "title": "Data Engineer",
        "company": "Analytics Co",
        "jd": (
            "Own our data warehouse and transformation layer. You'll build Airflow DAGs, "
            "write dbt models, and maintain our Kafka -> S3 -> Redshift pipeline. "
            "Required: 3+ years data engineering, Python, SQL (advanced), dbt, Airflow. "
            "Nice to have: Spark, Kafka, Great Expectations for data quality. "
            "You'll partner with analysts to ship weekly reporting. Stakeholder "
            "communication skills matter here as much as technical depth. "
            "The team is fully remote and async-first."
        ),
    },
]


# -- JSON repair test fixtures -------------------------------------------------

def _make_repair_cases() -> list[dict]:
    """40 test cases: 10 per category."""
    clean = [
        '{"id": "1", "score": 8, "reason": "Strong match on Python and Kafka"}',
        '{"id": "2", "score": 3, "reason": "No iOS experience listed"}',
        '{"id": "3", "score": 7, "reason": "Good AWS background"}',
        '[{"id": "1", "score": 9, "reason": "Perfect fit"}, {"id": "2", "score": 2, "reason": "Wrong domain"}]',
        '{"score": 6, "reason": "Partial match", "id": "5"}',
        '{"id": "6", "score": 10, "reason": "Exact match"}',
        '{"id": "7", "score": 1, "reason": "Sales role, not engineering"}',
        '[{"id": "8", "score": 5, "reason": "Some overlap"}, {"id": "9", "score": 8, "reason": "Good fit"}]',
        '{"id": "10", "score": 4, "reason": "Junior level only"}',
        '{"id": "11", "score": 7, "reason": "Kubernetes experience matches"}',
    ]

    truncated = [
        '{"id": "1", "score": 8, "reason": "Strong match on Python',            # cut mid-string
        '[{"id": "1", "score": 9, "reason": "Perfect"}, {"id": "2", "score":',  # cut in number
        '{"id": "3", "score": 7, "reason": "Good match on AWS and',
        '[{"id": "1", "score": 6, "reason": "ok"}, {"id"',                       # cut in key
        '{"id": "5", "score": 5',                                                 # no closing brace
        '[{"id": "1", "score": 8, "reason": "matches"}, {"id": "2", "score": 3, "reason": "no',
        '{"id": "7", "score": 2, "reas',
        '[{"id": "1", "score": 7, "reason": "yes"}, {"id": "2"',
        '{"id": "9", "score": 9, "reason": "perfect fit for the',
        '{"id": "10", "score": 4',
    ]

    unescaped_newlines = [
        '{"id": "1", "score": 8, "reason": "Python experience\nKafka experience"}',
        '{"id": "2", "score": 5, "reason": "Some skills match\nbut missing Terraform"}',
        '[{"id": "1", "score": 9, "reason": "Excellent\nAll requirements met"}, {"id": "2", "score": 2, "reason": "No match"}]',
        '{"id": "4", "score": 7, "reason": "Good backend\nand some cloud work"}',
        '{"id": "5", "score": 3, "reason": "Wrong domain\nneeds iOS"}',
        '{"id": "6", "score": 6, "reason": "Decent\nmissing Spark\nbut close"}',
        '[{"id": "7", "score": 8, "reason": "Strong\nKafka and Python"}, {"id": "8", "score": 1, "reason": "Sales"}]',
        '{"id": "9", "score": 9, "reason": "Perfect\nAll boxes checked"}',
        '{"id": "10", "score": 4, "reason": "Junior\nnot senior level"}',
        '{"id": "11", "score": 7, "reason": "Good infra experience\nterraform present"}',
    ]

    trailing_commas = [
        '{"id": "1", "score": 8, "reason": "Good fit",}',
        '[{"id": "1", "score": 9, "reason": "Yes",}, {"id": "2", "score": 2, "reason": "No",},]',
        '{"id": "3", "score": 7, "reason": "Matches",}',
        '{"id": "4", "score": 5, "reason": "Partial",}',
        '[{"id": "5", "score": 3, "reason": "Nope",},]',
        '{"id": "6", "score": 10, "reason": "Perfect",}',
        '{"id": "7", "score": 1, "reason": "Wrong",}',
        '[{"id": "8", "score": 6, "reason": "ok",}, {"id": "9", "score": 8, "reason": "good",},]',
        '{"id": "10", "score": 4, "reason": "Junior",}',
        '{"id": "11", "score": 7, "reason": "Infra match",}',
    ]

    cases = []
    for cat, samples in [
        ("clean", clean),
        ("truncated", truncated),
        ("unescaped_newlines", unescaped_newlines),
        ("trailing_commas", trailing_commas),
    ]:
        for raw in samples:
            cases.append({"category": cat, "raw": raw})
    return cases


# -- Confusion matrix helpers --------------------------------------------------

@dataclass
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def total(self):
        return self.tp + self.fp + self.fn + self.tn

    @property
    def accuracy(self):
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self):
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self):
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self):
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _print_confusion_matrix(cm: ConfusionMatrix, label: str = "") -> None:
    if label:
        print(f"\n  {label}")
    print()
    print(f"               {'Predicted YES':>14}   {'Predicted NO':>12}")
    print(f"  Actual YES      {cm.tp:>5}  (TP)       {cm.fn:>5}  (FN)")
    print(f"  Actual NO       {cm.fp:>5}  (FP)       {cm.tn:>5}  (TN)")
    print()
    print(f"  Accuracy   :  {cm.accuracy * 100:>5.1f}%")
    print(f"  Precision  :  {cm.precision * 100:>5.1f}%   "
          f"(when it says apply, it's right this often)")
    print(f"  Recall     :  {cm.recall * 100:>5.1f}%   "
          f"(catches this share of relevant jobs)")
    print(f"  F1 Score   :  {cm.f1 * 100:>5.1f}%")


# -- Section 1: Fit scoring benchmark -----------------------------------------

def run_scoring_benchmark(
    simulate: bool = False,
    provider: str = "",
    threshold: int = 6,
) -> dict:
    print("\n" + "-" * 62)
    print("  [1/3]  FIT SCORING  —  LLM vs all-accept baseline")
    print("-" * 62)

    jobs = LABELED_JOBS
    n_pos = sum(1 for j in jobs if j.expected)
    n_neg = len(jobs) - n_pos
    print(f"\n  Test set : {len(jobs)} jobs  "
          f"({n_pos} relevant, {n_neg} irrelevant)")
    print(f"  Threshold: score >= {threshold}/10 -> accept")
    if simulate:
        print("  Mode     : simulated (--simulate flag)")
    else:
        active = provider or os.environ.get("LLM_PROVIDER", "gemini")
        print(f"  Provider : {active}")

    predicted_scores: dict[str, int] = {}

    if simulate:
        # Produce plausible fake scores — good matches score high, bad ones low.
        # Add realistic noise so the numbers aren't suspiciously perfect.
        rng = random.Random(42)
        for job in jobs:
            if job.expected:
                score = rng.randint(6, 10)
                # ~12% chance of a false negative
                if rng.random() < 0.12:
                    score = rng.randint(3, 5)
            else:
                score = rng.randint(1, 5)
                # ~8% chance of a false positive
                if rng.random() < 0.08:
                    score = rng.randint(6, 8)
            predicted_scores[job.job_id] = score
    else:
        from llm.client import complete_cheap
        from llm.prompts import BATCH_FIT_SYSTEM, batch_fit_score_user_prompt

        resume_text = _load_resume()
        batch_size = 10

        print(f"\n  Scoring {len(jobs)} jobs in batches of {batch_size}…")
        for start in range(0, len(jobs), batch_size):
            chunk = jobs[start: start + batch_size]
            job_dicts = [
                {
                    "id": str(i + 1),
                    "title": j.title,
                    "company": j.company,
                    "description": j.description,
                }
                for i, j in enumerate(chunk)
            ]
            prompt = batch_fit_score_user_prompt(resume_text, job_dicts)
            try:
                raw = complete_cheap(BATCH_FIT_SYSTEM, prompt)
                entries = _parse_score_response(raw, chunk)
                for job, entry in zip(chunk, entries):
                    predicted_scores[job.job_id] = entry.get("score", 0)
            except Exception as exc:
                log.warning("scoring batch failed: %s", exc)
                for job in chunk:
                    predicted_scores[job.job_id] = 0
            # Respect free-tier rate limits between batches
            if start + batch_size < len(jobs):
                time.sleep(12)

    # Build confusion matrix
    llm_cm = ConfusionMatrix()
    baseline_cm = ConfusionMatrix()

    detail_rows = []
    for job in jobs:
        score = predicted_scores.get(job.job_id, 0)
        predicted_yes = score >= threshold
        actual_yes = job.expected

        if actual_yes and predicted_yes:
            llm_cm.tp += 1
        elif not actual_yes and predicted_yes:
            llm_cm.fp += 1
        elif actual_yes and not predicted_yes:
            llm_cm.fn += 1
        else:
            llm_cm.tn += 1

        # Baseline: accept everything that passes rule filters (no LLM)
        if actual_yes:
            baseline_cm.tp += 1
        else:
            baseline_cm.fp += 1

        detail_rows.append({
            "job_id": job.job_id,
            "title": job.title,
            "score": score,
            "predicted": predicted_yes,
            "expected": actual_yes,
            "correct": predicted_yes == actual_yes,
        })

    _print_confusion_matrix(llm_cm, "LLM scorer (gemini-2.5-flash / claude-haiku)")
    _print_confusion_matrix(baseline_cm, "Baseline  (accept all post-rule-filter)")

    wasted_baseline = baseline_cm.fp
    wasted_llm = llm_cm.fp
    reduction = (wasted_baseline - wasted_llm) / wasted_baseline if wasted_baseline else 0
    print()
    print(f"  Wasted applications prevented by LLM scoring:")
    print(f"    Baseline accepts {wasted_baseline} irrelevant jobs "
          f"(all {n_neg} non-matches pass through)")
    print(f"    LLM rejects   {wasted_baseline - wasted_llm} of them  "
          f"({reduction * 100:.0f}% reduction in bad applications)")
    print(f"    At the cost of missing {llm_cm.fn} relevant job(s)  "
          f"(recall = {llm_cm.recall * 100:.1f}%)")

    return {
        "section": "scoring",
        "test_set_size": len(jobs),
        "n_relevant": n_pos,
        "n_irrelevant": n_neg,
        "threshold": threshold,
        "llm": asdict(llm_cm),
        "llm_accuracy": round(llm_cm.accuracy * 100, 1),
        "llm_precision": round(llm_cm.precision * 100, 1),
        "llm_recall": round(llm_cm.recall * 100, 1),
        "llm_f1": round(llm_cm.f1 * 100, 1),
        "baseline_accuracy": round(baseline_cm.accuracy * 100, 1),
        "wasted_applications_prevented": wasted_baseline - wasted_llm,
        "detail": detail_rows,
    }


def _parse_score_response(raw: str, jobs: list[LabeledJob]) -> list[dict]:
    """Parse per-job scores from a batch LLM response, falling back through the repair chain."""
    import re, json as _json

    fence = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)
    text = fence.sub("", (raw or "").strip())

    # Try direct parse first
    try:
        parsed = _json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "results" in parsed:
            return parsed["results"]
    except Exception:
        pass

    # Try extracting the array
    first, last = text.find("["), text.rfind("]")
    if first != -1 and last > first:
        try:
            return _json.loads(text[first: last + 1])
        except Exception:
            pass

    # Regex salvage — pull individual scored entries
    entry_re = re.compile(
        r'"id"\s*:\s*"?(\d+)"?.*?"score"\s*:\s*(\d+)', re.DOTALL
    )
    found = {m.group(1): int(m.group(2)) for m in entry_re.finditer(text)}
    if found:
        return [{"id": str(i + 1), "score": found.get(str(i + 1), 0)}
                for i in range(len(jobs))]

    # Give up — return zeros
    return [{"id": str(i + 1), "score": 0} for i in range(len(jobs))]


def _load_resume() -> str:
    """Use my real resume if it's on disk; otherwise fall back to the sample profile above."""
    txt_path = ROOT / "config" / "master_resume.txt"
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8").strip()

    pdf_path = ROOT / "config" / "master_resume.pdf"
    if pdf_path.exists():
        try:
            from core.parser import parse_resume
            return parse_resume(str(pdf_path))
        except Exception:
            pass

    return SAMPLE_RESUME.strip()


# -- Section 2: ATS rewrite benchmark -----------------------------------------

def run_rewrite_benchmark(simulate: bool = False) -> dict:
    print("\n" + "-" * 62)
    print("  [2/3]  ATS REWRITE LOOP  —  before vs after scores")
    print("-" * 62)

    target = int(os.environ.get("ATS_TARGET_MIN", "80") or 80)
    max_rw = int(os.environ.get("ATS_MAX_REWRITES", "1") or 1)
    print(f"\n  Target ATS : {target}/100")
    print(f"  Max rewrites per job : {max_rw}")

    rows = []

    if simulate:
        print("  Mode       : simulated (--simulate flag)\n")
        sim_data = [
            ("senior-backend-python-kafka",   72, 87, 2),
            ("platform-engineer-kubernetes",  65, 81, 2),
            ("data-engineer-airflow-dbt",     61, 79, 2),
        ]
        for slug, before, after, attempts in sim_data:
            rows.append({
                "slug": slug,
                "score_before": before,
                "score_after": after,
                "attempts": attempts,
                "gain": after - before,
                "reached_target": after >= target,
            })
    else:
        try:
            from agents.tailor import TailorAgent
            from agents.profile import ProfileAgent
            from models import JobApplication
        except ImportError as exc:
            print(f"  Cannot import pipeline: {exc}")
            print("  Run with --simulate to see example output.")
            return {"section": "rewrite", "error": str(exc)}

        profile_agent = ProfileAgent(ROOT)
        try:
            profile = profile_agent.build()
        except Exception as exc:
            print(f"  Could not build profile: {exc}")
            print("  Fill in config/personal.yaml and config/master_resume.pdf first.")
            return {"section": "rewrite", "error": str(exc)}

        out_dir = ROOT / "outputs" / "benchmark_rewrite"
        out_dir.mkdir(parents=True, exist_ok=True)

        for job_def in REWRITE_JOBS:
            job = JobApplication(
                job_id=job_def["slug"],
                title=job_def["title"],
                company=job_def["company"],
                raw_description=job_def["jd"],
                enriched_description=job_def["jd"],
                site="benchmark",
            )
            job.folder = out_dir / job_def["slug"]
            job.folder.mkdir(exist_ok=True)

            print(f"  -> {job_def['title']} @ {job_def['company']} …", end="", flush=True)
            agent = TailorAgent(profile, do_research=False, run_ats_check=True)
            try:
                agent.tailor(job)
                score_after = job.ats_score or 0
                # Score before is logged by TailorAgent but not exposed on the object.
                # We re-run Phase 2 without the rewrite loop by temporarily zeroing
                # the target to capture just the first-pass score.
                os.environ["ATS_MAX_REWRITES"] = "0"
                job_nr = JobApplication(
                    job_id=job_def["slug"] + "-no-rewrite",
                    title=job_def["title"],
                    company=job_def["company"],
                    raw_description=job_def["jd"],
                    enriched_description=job_def["jd"],
                    site="benchmark",
                )
                job_nr.folder = out_dir / (job_def["slug"] + "-nr")
                job_nr.folder.mkdir(exist_ok=True)
                agent2 = TailorAgent(profile, do_research=False, run_ats_check=True)
                agent2.tailor(job_nr)
                score_before = job_nr.ats_score or 0
                os.environ["ATS_MAX_REWRITES"] = str(max_rw)

                attempts = 2 if score_after > score_before else 1
                rows.append({
                    "slug": job_def["slug"],
                    "score_before": score_before,
                    "score_after": score_after,
                    "attempts": attempts,
                    "gain": score_after - score_before,
                    "reached_target": score_after >= target,
                })
                print(f" {score_before} -> {score_after}")
            except Exception as exc:
                print(f" FAILED ({exc})")
                os.environ["ATS_MAX_REWRITES"] = str(max_rw)

    if not rows:
        print("  No results collected.")
        return {"section": "rewrite", "rows": []}

    print()
    col_w = max(len(r["slug"]) for r in rows) + 2
    header = f"  {'job':<{col_w}} {'before':>7}  {'after':>6}  {'gain':>5}  {'attempts':>9}  target"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        flag = "[ok]" if r["reached_target"] else "[--]"
        print(
            f"  {r['slug']:<{col_w}} {r['score_before']:>7}  "
            f"{r['score_after']:>6}  {r['gain']:>+5}  {r['attempts']:>9}      {flag}"
        )

    mean_before = sum(r["score_before"] for r in rows) / len(rows)
    mean_after = sum(r["score_after"] for r in rows) / len(rows)
    hit = sum(1 for r in rows if r["reached_target"])
    hit_before = sum(1 for r in rows if r["score_before"] >= target)
    print()
    print(f"  Mean ATS before rewrite :  {mean_before:.1f}/100")
    print(f"  Mean ATS after  rewrite :  {mean_after:.1f}/100   (+{mean_after - mean_before:.1f})")
    print(f"  Jobs reaching {target}+ target :  {hit}/{len(rows)}"
          f"  ({hit / len(rows) * 100:.0f}%)  "
          f"<- was {hit_before}/{len(rows)} without rewrite")

    return {
        "section": "rewrite",
        "target": target,
        "rows": rows,
        "mean_before": round(mean_before, 1),
        "mean_after": round(mean_after, 1),
        "mean_gain": round(mean_after - mean_before, 1),
        "jobs_hitting_target_before": hit_before,
        "jobs_hitting_target_after": hit,
        "hit_rate_before": round(hit_before / len(rows) * 100, 1),
        "hit_rate_after": round(hit / len(rows) * 100, 1),
    }


# -- Section 3: JSON repair robustness ----------------------------------------

def run_repair_benchmark() -> dict:
    print("\n" + "-" * 62)
    print("  [3/3]  JSON REPAIR  —  recovery rate by failure type")
    print("-" * 62)
    print()

    # Import the same repair function used in production
    from core.filter import _parse_json_loose

    cases = _make_repair_cases()
    by_category: dict[str, dict] = {}

    for case in cases:
        cat = case["category"]
        raw = case["raw"]
        result = _parse_json_loose(raw)
        recovered = result is not None

        if cat not in by_category:
            by_category[cat] = {"total": 0, "recovered": 0, "failed": []}
        by_category[cat]["total"] += 1
        if recovered:
            by_category[cat]["recovered"] += 1
        else:
            by_category[cat]["failed"].append(raw[:60])

    total = sum(v["total"] for v in by_category.values())
    total_ok = sum(v["recovered"] for v in by_category.values())

    print(f"  {'Type':<22} {'Recovered':>10}  {'Failed':>7}  {'Rate':>6}")
    print("  " + "-" * 50)
    for cat, stats in by_category.items():
        rate = stats["recovered"] / stats["total"] * 100
        label = cat.replace("_", " ")
        print(
            f"  {label:<22} {stats['recovered']:>4}/{stats['total']:<5} "
            f"{stats['total'] - stats['recovered']:>7}  {rate:>5.0f}%"
        )
    print("  " + "-" * 50)
    overall = total_ok / total * 100
    print(f"  {'Overall':<22} {total_ok:>4}/{total:<5} "
          f"{total - total_ok:>7}  {overall:>5.0f}%")

    print()
    if total - total_ok > 0:
        print("  Failed cases (first 60 chars):")
        for cat, stats in by_category.items():
            for f in stats["failed"]:
                print(f"    [{cat}] {f!r}")
    else:
        print("  All cases recovered successfully.")

    return {
        "section": "repair",
        "total": total,
        "recovered": total_ok,
        "overall_rate": round(overall, 1),
        "by_category": {
            cat: {
                "total": s["total"],
                "recovered": s["recovered"],
                "rate": round(s["recovered"] / s["total"] * 100, 1),
            }
            for cat, s in by_category.items()
        },
    }


# -- Entry point ---------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OneShot pipeline quality benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--mode", choices=["scoring", "rewrite", "repair", "full"],
                   default="full", help="Which benchmark to run (default: full)")
    p.add_argument("--simulate", action="store_true",
                   help="Skip real LLM calls; use deterministic simulated scores")
    p.add_argument("--provider", default="",
                   help="LLM provider override: claude | openai | gemini")
    p.add_argument("--threshold", type=int, default=6,
                   help="Fit score threshold for accept/reject (default 6)")
    p.add_argument("--out", default="",
                   help="JSON output file path (default: outputs/benchmark_<ts>.json)")
    p.add_argument("--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # Ensure Unicode output works on Windows consoles
    import io as _io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = _io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    print()
    print("=" * 62)
    print("  OneShot  --  pipeline quality benchmark")
    print(f"  {datetime.now().strftime('%Y-%m-%d  %H:%M')}")
    print("=" * 62)

    results: list[dict] = []

    if args.mode in ("scoring", "full"):
        results.append(run_scoring_benchmark(
            simulate=args.simulate,
            provider=args.provider,
            threshold=args.threshold,
        ))

    if args.mode in ("rewrite", "full"):
        results.append(run_rewrite_benchmark(simulate=args.simulate))

    if args.mode in ("repair", "full"):
        results.append(run_repair_benchmark())

    # Summary
    print("\n" + "=" * 62)
    print("  Summary")
    print("=" * 62)
    for r in results:
        s = r.get("section", "?")
        if s == "scoring" and "llm_f1" in r:
            print(f"  Fit scoring   F1 {r['llm_f1']}%  |  "
                  f"accuracy {r['llm_accuracy']}%  |  "
                  f"recall {r['llm_recall']}%")
        elif s == "rewrite" and "mean_gain" in r:
            print(f"  ATS rewrite   mean gain +{r['mean_gain']} pts  |  "
                  f"jobs hitting target "
                  f"{r['jobs_hitting_target_before']} -> {r['jobs_hitting_target_after']} "
                  f"of {len(r['rows'])}")
        elif s == "repair" and "overall_rate" in r:
            print(f"  JSON repair   recovery {r['overall_rate']}%  "
                  f"({r['recovered']}/{r['total']} cases)")

    # Save results
    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.out)
        if args.out
        else out_dir / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    payload = {
        "run_at": datetime.now().isoformat(),
        "simulated": args.simulate,
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  Full results -> {out_path.relative_to(ROOT)}")
    print()


if __name__ == "__main__":
    main()
