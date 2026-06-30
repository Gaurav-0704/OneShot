"""
webapp/pipeline_runner.py

Runs the 6-agent Orchestrator in a background thread so the Flask UI stays
responsive. Emits structured events via a queue so the UI can stream them
through Server-Sent Events.

Events are simple dicts:
  {"type": "stage", "agent": "discovery", "msg": "scraped 87 jobs", "ts": "..."}
  {"type": "job",   "stage": "writer", "company": "...", "title": "...", "score": 8}
  {"type": "done",  "submitted": 4, "failed": 1}

Only one run is allowed at a time; calling start() while one is active returns
the active run id without launching a new one.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class _LogToQueueHandler(logging.Handler):
    """Captures logger output to a queue (for live SSE) and a separate
    list of error records (for the Errors tab)."""

    def __init__(self, q: queue.Queue, error_log: list):
        super().__init__()
        self.q = q
        self.error_log = error_log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            agent = record.name.replace("agent.", "")
            msg = record.getMessage()
            ts = datetime.now().isoformat(timespec="seconds")
            self.q.put({
                "type": "log",
                "level": record.levelname,
                "agent": agent,
                "msg": msg,
                "ts": ts,
            })
            if record.levelno >= logging.WARNING:
                # Keep a rolling list of warnings/errors for the Errors tab
                self.error_log.append({
                    "ts": ts,
                    "level": record.levelname,
                    "agent": agent,
                    "msg": msg,
                    "summary": msg[:120],
                })
                # Cap to last 200 to bound memory
                if len(self.error_log) > 200:
                    del self.error_log[:len(self.error_log) - 200]
        except Exception:
            pass


class PipelineRunner:
    """Singleton-ish runner. One run at a time."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.run_id: Optional[str] = None
        self.events: queue.Queue = queue.Queue(maxsize=10000)
        self.status: str = "idle"        # idle | running | done | error | stopped
        self.summary: dict = {}
        self.errors: list[dict] = []     # rolling warning/error log
        self._stop_event = threading.Event()
        self._handler: Optional[_LogToQueueHandler] = None

    # ── Public ───────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> bool:
        """Ask the pipeline to stop at the next checkpoint. Returns whether
        a stop was actually scheduled (False if nothing was running)."""
        if not self.is_running():
            return False
        self._stop_event.set()
        try:
            self.events.put_nowait({
                "type": "stage", "agent": "client",
                "msg": "stop requested - finishing current step then exiting",
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
        return True

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def start(self, options: dict[str, Any]) -> str:
        with self._lock:
            if self.is_running():
                return self.run_id  # type: ignore[return-value]

            self.run_id = uuid.uuid4().hex[:8]
            self.events = queue.Queue(maxsize=10000)
            self.status = "running"
            self.summary = {}
            self.errors.clear()                 # fresh per run
            self._stop_event.clear()
            self._attach_log_handler()

            self._thread = threading.Thread(
                target=self._run,
                args=(options,),
                daemon=True,
                name=f"pipeline-{self.run_id}",
            )
            self._thread.start()
            return self.run_id

    def stream_events(self):
        """Generator yielding SSE-formatted strings. Stops when status is done/error."""
        # Send anything queued, then keep streaming until pipeline finishes
        while True:
            try:
                ev = self.events.get(timeout=1.0)
                yield ev
            except queue.Empty:
                # Auto-heal: thread died without cleanup (e.g. crash/disconnect)
                if self.status == "running" and not self.is_running():
                    self.status = "error"
                if self.status in {"done", "error", "stopped"} and self.events.empty():
                    yield {"type": "end", "status": self.status, "summary": self.summary}
                    return
                # Heartbeat
                yield {"type": "ping", "ts": datetime.now().isoformat(timespec="seconds")}

    # ── Internal ─────────────────────────────────────────────────────────────

    def _attach_log_handler(self) -> None:
        if self._handler is not None:
            try:
                logging.getLogger().removeHandler(self._handler)
            except Exception:
                pass
        self._handler = _LogToQueueHandler(self.events, self.errors)
        self._handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._handler)

    def _detach_log_handler(self) -> None:
        if self._handler is not None:
            try:
                logging.getLogger().removeHandler(self._handler)
            except Exception:
                pass
            self._handler = None

    def _emit(self, **kwargs: Any) -> None:
        kwargs.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        try:
            self.events.put_nowait(kwargs)
        except queue.Full:
            pass

    def _run(self, options: dict[str, Any]) -> None:
        # Late imports so Flask can boot without pulling browser deps until needed
        try:
            from agents.orchestrator import Orchestrator

            # Prevent any applier or agent from calling input() or driving the
            # browser interactively — both would block this daemon thread forever.
            os.environ["NONINTERACTIVE"] = "1"

            self._emit(type="stage", agent="orchestrator", msg="pipeline starting")

            orch = Orchestrator(
                self.root,
                master_resume=Path(options["resume"]) if options.get("resume") else None,
                dry_run=bool(options.get("dry_run", True)),
                pause=bool(options.get("pause_before_submit", True)),
                run_limit=options.get("limit"),
                score_jobs=bool(options.get("score_jobs", True)),
                do_research=bool(options.get("do_research", True)),
                run_ats_check=bool(options.get("run_ats_check", True)),
                require_min_ats=int(options.get("min_ats", 0)),
                headless=bool(options.get("headless", False)),
                should_stop=self.should_stop,
                use_cache=bool(options.get("use_cache", False)),
                on_event=self._emit,
            )
            rc = orch.run()

            self.summary = self._build_summary()
            self.summary["return_code"] = rc
            self.status = "stopped" if self.should_stop() else "done"
            self._emit(type="done", **self.summary, status=self.status)
        except Exception as e:
            log.exception("pipeline crashed")
            self.summary = {"error": str(e)}
            self.status = "error"
            self._emit(type="error", error=str(e))
        finally:
            # Guarantee status is never left as "running" after the thread exits
            if self.status == "running":
                self.status = "error"
            time.sleep(0.5)
            self._detach_log_handler()

    def _build_summary(self) -> dict:
        import csv
        out_dir = self.root / "outputs"
        applied_csv = out_dir / "applied_jobs.csv"
        failed_csv = out_dir / "failed_jobs.csv"
        pending_csv = out_dir / "pending_review.csv"
        today = datetime.now().date().isoformat()

        def _today_count(p: Path) -> int:
            if not p.exists():
                return 0
            n = 0
            with p.open("r", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    ts = (r.get("applied_at") or r.get("failed_at")
                          or r.get("pending_at") or "")
                    if ts.startswith(today):
                        n += 1
            return n

        def _total(p: Path) -> int:
            if not p.exists():
                return 0
            with p.open("r", newline="", encoding="utf-8") as f:
                return sum(1 for _ in csv.DictReader(f))

        return {
            "applied_today": _today_count(applied_csv),
            "applied_total": _total(applied_csv),
            "failed_today": _today_count(failed_csv),
            "failed_total": _total(failed_csv),
            "pending_today": _today_count(pending_csv),
            "pending_total": _total(pending_csv),
        }
