"""
agents/history.py — HistoryAgent

Snapshots pending_review.csv into outputs/history/<YYYY-MM-DD>/task_<N>/
at the start of every new run so each search task is archived separately.
The tailored/ folders are NOT moved — results.csv just records their paths.

Public API:
  agent = HistoryAgent(root)
  agent.archive_current(search_terms=[...])  → dict with path / count / skipped
  agent.list_runs()                           → [{date, task_number, started_at, count, search_terms}]
  agent.get_run(date, task_number)            → list[dict]  (the CSV rows)
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


class HistoryAgent:
    def __init__(self, root: Path) -> None:
        self.root        = Path(root)
        self.pending_csv = self.root / "outputs" / "pending_review.csv"
        self.history_dir = self.root / "outputs" / "history"

    # ── Public API ────────────────────────────────────────────────────────────

    def archive_current(self, search_terms: list[str] | None = None) -> dict[str, Any]:
        """Snapshot pending_review.csv and truncate it for the new run.

        Safe to call even when pending_review.csv is absent or empty — returns
        {skipped: True} in that case without creating an empty task folder.
        """
        rows = self._read_pending()
        if not rows:
            return {"skipped": True, "reason": "pending_review.csv is empty or absent"}

        task_dir, date_str, task_num = self._next_task_dir()
        task_dir.mkdir(parents=True, exist_ok=True)

        # Write results.csv (same columns as pending_review.csv)
        results_path = task_dir / "results.csv"
        fieldnames = list(rows[0].keys())
        with results_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

        # Write meta.json
        meta: dict[str, Any] = {
            "date":         date_str,
            "task_number":  task_num,
            "started_at":   datetime.now().isoformat(timespec="seconds"),
            "ended_at":     None,
            "count":        len(rows),
            "search_terms": search_terms or [],
        }
        (task_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Truncate pending_review.csv (keep header only)
        self._truncate_pending(fieldnames)

        return {
            "ok":          True,
            "task_dir":    str(task_dir),
            "date":        date_str,
            "task_number": task_num,
            "count":       len(rows),
        }

    def finish_run(self, date: str, task_number: int, search_terms: list[str] | None = None) -> None:
        """Update meta.json with ended_at and final search_terms (called at run end)."""
        meta_path = self.history_dir / date / f"task_{task_number}" / "meta.json"
        if not meta_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["ended_at"] = datetime.now().isoformat(timespec="seconds")
            if search_terms:
                meta["search_terms"] = search_terms
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def list_runs(self) -> list[dict[str, Any]]:
        """Return all archived runs sorted newest-first."""
        if not self.history_dir.exists():
            return []
        runs: list[dict[str, Any]] = []
        for date_dir in sorted(self.history_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            date_str = date_dir.name
            for task_dir in sorted(date_dir.iterdir(), key=lambda p: self._task_num(p.name)):
                if not task_dir.is_dir() or not task_dir.name.startswith("task_"):
                    continue
                meta = self._read_meta(task_dir)
                runs.append({
                    "date":         date_str,
                    "task_number":  meta.get("task_number", self._task_num(task_dir.name)),
                    "started_at":   meta.get("started_at", ""),
                    "ended_at":     meta.get("ended_at"),
                    "count":        meta.get("count", 0),
                    "search_terms": meta.get("search_terms", []),
                    "task_dir":     str(task_dir),
                })
        return runs

    def get_run(self, date: str, task_number: int) -> list[dict[str, Any]]:
        """Return the rows from a specific archived run."""
        results_path = self.history_dir / date / f"task_{task_number}" / "results.csv"
        if not results_path.exists():
            return []
        with results_path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def get_run_meta(self, date: str, task_number: int) -> dict[str, Any]:
        meta_path = self.history_dir / date / f"task_{task_number}" / "meta.json"
        return self._read_meta(meta_path.parent) if meta_path.exists() else {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _read_pending(self) -> list[dict]:
        if not self.pending_csv.exists():
            return []
        with self.pending_csv.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _truncate_pending(self, fieldnames: list[str]) -> None:
        with self.pending_csv.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    def _next_task_dir(self) -> tuple[Path, str, int]:
        date_str  = datetime.now().strftime("%Y-%m-%d")
        date_dir  = self.history_dir / date_str
        task_num  = 1
        if date_dir.exists():
            existing = [
                self._task_num(p.name)
                for p in date_dir.iterdir()
                if p.is_dir() and p.name.startswith("task_")
            ]
            if existing:
                task_num = max(existing) + 1
        return date_dir / f"task_{task_num}", date_str, task_num

    @staticmethod
    def _task_num(name: str) -> int:
        try:
            return int(name.replace("task_", ""))
        except ValueError:
            return 0

    @staticmethod
    def _read_meta(task_dir: Path) -> dict[str, Any]:
        p = task_dir / "meta.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
