"""
agents/learner.py  —  LearningAgent

Runs automatically at the end of every pipeline run.
Does four things:

A. ATS gap analysis
   Reads every ats_audit.txt from the current run's tailored folders.
   Counts which keywords were missing most often.
   Writes the aggregated results to outputs/run_insights.json.

B. Dashboard insights
   run_insights.json is what the Dashboard Insights card reads via /api/insights.

C. Company blacklist flags
   If a company appears 2+ times in failed_jobs.csv it is surfaced in insights
   as a candidate for blacklisting (user still has to approve).

D. Question-answer memory
   Reads outputs/learned_qa.json (written by appliers during form filling).
   Promotes any answer that was used in a submitted application into
   config/questions.yaml under the  learned_answers  key.
   On future runs, _lookup_answer() in appliers checks this section first
   before calling the LLM — saving both tokens and latency.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agents.base import Agent

log = logging.getLogger(__name__)


class LearnerAgent(Agent):
    name = "learner"
    role = "Analyses completed runs, updates question memory, surfaces resume gaps."

    def __init__(self, root: Path):
        super().__init__(profile=None, dry_run=False)
        self.root = root
        self.tailored_dir  = root / "outputs" / "tailored"
        self.applied_csv   = root / "outputs" / "applied_jobs.csv"
        self.failed_csv    = root / "outputs" / "failed_jobs.csv"
        self.insights_json = root / "outputs" / "run_insights.json"
        self.learned_qa    = root / "outputs" / "learned_qa.json"
        self.questions_yaml = root / "config" / "questions.yaml"

    # ── Public ───────────────────────────────────────────────────────────────

    def learn(self) -> dict:
        """Run the full learning pass. Returns the insights dict."""
        self.info("learning from run data")

        keyword_counts  = self._aggregate_ats_gaps()
        ats_scores      = self._collect_ats_scores()
        flagged_co      = self._flag_repeat_failures()
        qa_promoted     = self._promote_learned_answers()

        insights = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "ats_average":  round(sum(ats_scores) / len(ats_scores), 1) if ats_scores else None,
            "ats_scores":   ats_scores,
            "top_missing_keywords": [
                {"keyword": kw, "count": c}
                for kw, c in keyword_counts.most_common(15)
            ],
            "advice": self._collect_advice(),
            "flagged_companies": flagged_co,
            "qa_promoted": qa_promoted,
            "jobs_analysed": len(ats_scores),
        }

        self.insights_json.parent.mkdir(parents=True, exist_ok=True)
        self.insights_json.write_text(
            json.dumps(insights, indent=2), encoding="utf-8"
        )
        self.info(
            f"insights: avg ATS={insights['ats_average']}, "
            f"{len(insights['top_missing_keywords'])} gap keywords, "
            f"{qa_promoted} Q&A pairs promoted"
        )
        return insights

    # ── ATS gap analysis ──────────────────────────────────────────────────────

    def _aggregate_ats_gaps(self) -> Counter:
        counter: Counter = Counter()
        if not self.tailored_dir.exists():
            return counter
        for audit in self.tailored_dir.glob("*/ats_audit.txt"):
            try:
                text = audit.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("missing keywords:"):
                    kws = stripped.split(":", 1)[1]
                    for k in kws.split(","):
                        k = k.strip().lower()
                        if k and 2 <= len(k) < 40:
                            counter[k] += 1
        return counter

    def _collect_ats_scores(self) -> list[int]:
        scores = []
        if not self.tailored_dir.exists():
            return scores
        for audit in self.tailored_dir.glob("*/ats_audit.txt"):
            try:
                text = audit.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("score:"):
                    try:
                        scores.append(int(stripped.split(":", 1)[1].strip().split("/")[0]))
                    except Exception:
                        pass
        return scores

    def _collect_advice(self) -> list[str]:
        seen: set[str] = set()
        advice = []
        if not self.tailored_dir.exists():
            return advice
        for audit in self.tailored_dir.glob("*/ats_audit.txt"):
            try:
                text = audit.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("advice:"):
                    a = stripped.split(":", 1)[1].strip()
                    if a and a not in seen:
                        seen.add(a)
                        advice.append(a)
        return advice[:5]

    # ── Repeat failure detection ──────────────────────────────────────────────

    def _flag_repeat_failures(self) -> list[dict]:
        if not self.failed_csv.exists():
            return []
        co_counts: Counter = Counter()
        try:
            with self.failed_csv.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    co = (row.get("company") or "").strip()
                    if co:
                        co_counts[co] += 1
        except Exception:
            return []
        return [
            {"company": co, "failures": n, "suggest_blacklist": True}
            for co, n in co_counts.most_common()
            if n >= 2
        ]

    # ── Q&A answer promotion ──────────────────────────────────────────────────

    def _promote_learned_answers(self) -> int:
        """
        Reads outputs/learned_qa.json.
        For each entry where submitted=True (answer was used in a submitted app),
        promotes it into config/questions.yaml under 'learned_answers'.
        Returns count of newly promoted pairs.
        """
        if not self.learned_qa.exists():
            return 0

        try:
            qa_list: list[dict] = json.loads(
                self.learned_qa.read_text(encoding="utf-8")
            )
        except Exception:
            return 0

        # Load existing questions.yaml
        cfg: dict = {}
        if self.questions_yaml.exists():
            try:
                cfg = yaml.safe_load(
                    self.questions_yaml.read_text(encoding="utf-8")
                ) or {}
            except Exception:
                cfg = {}

        learned: dict = cfg.get("learned_answers", {})
        promoted = 0

        for entry in qa_list:
            if not entry.get("submitted", False):
                continue
            question = (entry.get("question") or "").strip().lower()
            answer   = (entry.get("answer") or "").strip()
            if not question or not answer:
                continue
            # Normalise key: lowercase, collapse whitespace
            key = re.sub(r"\s+", " ", question)[:120]
            if key not in learned:
                learned[key] = {
                    "answer": answer,
                    "learned_at": datetime.now().isoformat(timespec="seconds"),
                    "company": entry.get("company", ""),
                    "confirmed": True,
                }
                promoted += 1

        if promoted > 0:
            cfg["learned_answers"] = learned
            self.questions_yaml.write_text(
                yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            self.info(f"promoted {promoted} Q&A pairs into questions.yaml")

        return promoted
