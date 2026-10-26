"""`medchron casefile <job_dir>`: one Case File Manager job, resumable.

    inventory -> screen -> matters -> rank -> propose -> book -> file
      -> workbook -> deliver -> report

* ``inventory`` ($0): the caseload and everything but document text.
* ``screen`` ($0 before paid): a matter whose inputs are unchanged since its
  last delivered record is ALREADY CURRENT (no model call); one that changed
  is read with its prior record and only the new or changed documents; one
  with no record, or a record older than ``full_reread_days``, is read whole.
* ``matters`` (paid, ``read_workers`` at once, each matter its own files):
  fetch, extract (no transcription of scans), pack, read, validate, audit.
* ``rank`` / ``propose`` (code): urgency, and the numbered lines.
* ``book``: a File Status PDF for each matter read this run.
* ``file``: each File Status into the matter's ``file_status_folder``, read back.
  A proof run stops here with a ``dry_run`` verdict.
* ``workbook`` / ``deliver``: the ranked caseload to the firm's library matter,
  read back.
* ``report``: the delivered records into the lane's state, and the verdict
  with cents and tokens per stage and per matter.

The run's "today" is frozen on the first attempt.
"""

from __future__ import annotations

import datetime as dt
import os
import traceback
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod, proof_mode, stop_request
from ..ledger import Ledger
from ..litigation import textcache
from ..litigation.runbase import RunBase
from ..litigation.tools import ReadIncomplete, dump
from ..litigation.workers import in_parallel
from ..llm import Doorway
from . import firm as firm_mod, inventory, job as job_mod, matter as matter_mod, metrics, pack, state as state_mod
from .deliver import DeliverMixin
from .outcome import REASON, CasefileFailed, CasefileHold, Verdict

STAGES = ("inventory", "screen", "matters", "rank", "propose", "book", "file", "workbook", "deliver", "report")


class CasefileCache(textcache.TextCache):
    """The litigation lane's shared text cache, read for every hit, written
    only with text that holds no unread scan page: a scan this job did not
    transcribe must never become the cached text the chronology lanes reuse."""

    def put_text(self, sha: str | None, text: str, *, scanned_pages: int, matter_id: str) -> bool:
        if pack.SCAN_MARK in text:
            return False
        return super().put_text(sha, text, scanned_pages=scanned_pages, matter_id=matter_id)


def no_ocr(_png: bytes) -> str:
    """The extract's page reader for this job: a scan page is marked, not read."""
    return pack.SCAN_MARK


class CasefileRun(DeliverMixin, RunBase):
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        pricing: str | None = None,
        state_dir: str | Path | None = None,
        seat_factory: Callable[[], Any] | None = None,
        client: Any = None,
        log: Callable[[str], None] = print,
        today: Callable[[], dt.date] | None = None,
        readback_pause: float | None = None,
        textcache_dir: str | Path | None = None,
    ) -> None:
        self.job = job_mod.load(job_dir)
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.state = state_mod.state_dir(state_dir)
        self.log = log
        cap = self.job.per_job_cap_usd or float(self.firm.data["per_job_cap_usd"])
        monthly = self.job.monthly_budget_usd or float(self.firm.data["monthly_budget_usd"])
        self.pricing = budget_mod.Pricing.load(
            Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        )
        self.budget = budget_mod.Budget(self.pricing, cap, [self.data / "usage-ledger.jsonl"], 1.0)
        self.limits = limits_mod.Limits(
            cap_usd=cap,
            monthly_budget_usd=monthly,
            usd_per_scanned_page=0.0,
            usd_per_audit_claim=0.0,
            month_cents_used=self.job.month_cents_used,
        )
        self.doorway = Doorway(
            ledger=Ledger(self.data / "usage-ledger.jsonl"), client=client, log=log, before_request=self._before_call
        )
        self._seat_factory, self._seat = seat_factory, None
        self.today = self._frozen_today(today)
        self.readback_pause = readback_pause
        self.no_ocr = no_ocr
        self.cache = CasefileCache(
            textcache_dir,
            method_version=textcache.method_version(self.firm.model("transcription")),
            job_id=self.job.job_id,
            log=log,
        )

    # ---- plumbing -------------------------------------------------------------
    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is None:
                from ..seat import open_seat

                self._seat = open_seat(self.job.slug)
            else:
                self._seat = self._seat_factory()
        return self._seat

    def _frozen_today(self, today: Callable[[], dt.date] | None) -> dt.date:
        stamp = self._state().get("today")
        if not stamp:
            if today is not None:
                d = today()
            else:
                from zoneinfo import ZoneInfo

                d = dt.datetime.now(ZoneInfo(str(self.firm.get("timezone")))).date()
            stamp = d.isoformat()
            self._put("today", stamp)
        return dt.date.fromisoformat(stamp)

    def stop_check(self) -> None:
        stop_request.check("casefile")

    def reopen(self, stages: list[str]) -> None:
        known = [s for s in stages if s in STAGES]
        if not known:
            return
        first = min(STAGES.index(s) for s in known)
        for s in STAGES[first:]:
            if s in self._state():
                self._put(s, {"status": "reopened"})

    def _matters(self) -> list[dict[str, Any]]:
        return list(self._json("inventory.json")["matters"])

    def _plan(self) -> dict[str, Any]:
        return self._json("plan.json")

    def prior(self, mid: str) -> dict[str, Any] | None:
        return state_mod.load_prior(self.state, mid)

    # ---- stages ---------------------------------------------------------------
    def _inventory(self) -> None:
        if self.job.mode != "full":
            raise CasefileHold(f"envelope_invalid: the {self.job.mode} kind is not available yet")
        inv = inventory.build(self.seat, self.job, self.firm, self.data, self.log)
        dump(self.data / "inventory.json", inv)
        for mid, why in inv["held"].items():
            matter_mod.held(self, mid, "inventory", why)

    def _screen(self) -> None:
        plan: dict[str, Any] = {}
        own = state_mod.own_ids(self.state)
        for m in self._matters():
            mid = m["id"]
            if self._mfile(mid, matter_mod.HELD).is_file():
                plan[mid] = {"mode": "held"}
                continue
            inv = inventory.inv_of(self.data, mid)
            fp = state_mod.fingerprint(inv, own)
            prior = self.prior(mid)
            plan[mid] = {"mode": "whole", "changed": None, "fingerprint": fp}
            if prior is None:
                continue
            age = (self.today - dt.date.fromisoformat(str(prior.get("delivered_at") or "1970-01-01")[:10])).days
            if prior.get("fingerprint") == fp:
                plan[mid]["mode"] = "current"
            elif age < int(self.firm.get("full_reread_days")):
                manifest = prior.get("manifest") or {}
                changed = [
                    str(f["id"])
                    for f in inv["files"]
                    if manifest.get(str(f["id"])) != str(f.get("modified") or f.get("created") or "")
                ]
                plan[mid].update(mode="delta", changed=sorted(set(changed) - own))
        dump(self.data / "plan.json", plan)

    def _paid(self) -> None:
        plan = self._plan()
        todo = [(m, plan[m["id"]]) for m in self._matters() if plan[m["id"]]["mode"] in ("whole", "delta")]
        self.log(
            f"[matters] {len(todo)} to read; {sum(1 for p in plan.values() if p['mode'] == 'current')} already current"
        )
        in_parallel(self, todo, lambda m, p: matter_mod.process(self, m, p), int(self.firm.get("read_workers")))

    def _walk(self) -> Verdict:
        steps = (
            self._inventory,
            self._screen,
            self._paid,
            self._rank,
            self._propose,
            self._book,
            self._file,
            self._workbook,
            self._deliver,
            self._report,
        )
        for name, fn in zip(STAGES, steps):
            if name == "file" and proof_mode.active():
                return self._dry_run()
            self._stage(name, fn)
            if name == "inventory" and not self._matters():
                raise CasefileHold("scope_empty: no open matter is in this job's scope")
            if name == "matters" and not self._rows_live():
                raise CasefileHold("all_matters_held: every matter in scope is held; nothing was filed")
        return self._delivered()

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "report")

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=self._current(), reason=f"limit: {hold.reason}")
        except CasefileHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except CasefileFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
        except ReadIncomplete as exc:
            v = Verdict("failed", stage=self._current(), reason=f"read_incomplete: {exc}")
        except inventory.InventoryError as exc:
            v = Verdict("failed", stage=self._current(), reason=f"inventory_unreadable: {exc}")
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict("failed", stage=self._current(), reason=f"unexpected: {type(exc).__name__}: {str(exc)[:300]}")
        if v.reason and not REASON.match(v.reason):
            v.reason = f"unexpected: {v.reason}"
        v.cents = int(round(self.budget.refresh() * 100))
        v.metrics = metrics.summarize(self.data / "usage-ledger.jsonl", self.pricing)
        self._counts(v)
        return v

    def _counts(self, v: Verdict) -> None:
        if not (self.data / "inventory.json").is_file():
            return
        v.matters_total = len(self._matters())
        if (self.data / "plan.json").is_file():
            plan = self._plan()
            v.matters_current = sum(1 for p in plan.values() if p["mode"] == "current")
            v.matters_read = sum(1 for mid in plan if self._mfile(mid, matter_mod.REVIEW).is_file())
        v.matters_held = sum(1 for m in self._matters() if self._mfile(m["id"], matter_mod.HELD).is_file())


__all__ = ["STAGES", "CasefileCache", "CasefileRun", "no_ocr"]
