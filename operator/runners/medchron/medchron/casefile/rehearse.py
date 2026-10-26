"""`medchron casefile <job_dir> --rehearse <report.json>`: the real job on the
real seat at $0, to see what a run would cost before anything is paid.

What a rehearsal guarantees:

* **No write to the firm's system.** The seat is read-only (a folder or file
  write raises before any request), and the walk stops after the packs are
  built, long before ``file``.
* **No model call.** The doorway's client is a stub that raises: nothing is
  read by a model, so nothing is spent. The packs are built exactly as the paid
  run would build them, and their sizes ARE the measurement.
* **No touch of the lane's state or the shared text cache.** The state is read
  (so a matter that would be already current is counted as such) and never
  written; the cache is opened read-only.

The report (``report.json``) carries, per matter: the plan (``whole``,
``delta``, ``current`` or held), the pack's characters, documents readable and
packed, and the projected input tokens of one read (pack plus the fixed system
prompt, at four characters a token); and the totals. The verdict is always
``held`` with a ``rehearsal:`` reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..litigation.rehearse import ReadOnlySeat
from ..litigation.tools import dump
from ..litigation.workers import in_parallel
from . import matter as matter_mod, pack, read
from .inventory import inv_of
from .outcome import CasefileHold, Verdict
from .run import CasefileRun

CHARS_PER_TOKEN = 4


class NoModel:
    """A doorway client that refuses every call: a rehearsal never pays."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("rehearsal: no model call is made")


class RehearsalRun(CasefileRun):
    def __init__(self, job_dir: Path, *, report_path: Path, **kw: Any) -> None:
        super().__init__(job_dir, client=NoModel(), **kw)
        self.report_path = report_path
        self.cache.read_only = True

    @property
    def seat(self) -> Any:
        base = super().seat
        return base if isinstance(base, ReadOnlySeat) else ReadOnlySeat(base)

    def _measure(self, m: dict[str, Any], plan: dict[str, Any]) -> None:
        mid = m["id"]
        if self._mfile(mid, "rehearsal.json").is_file():
            return
        inv = inv_of(self.data, mid)
        changed = set(plan["changed"]) if plan.get("changed") is not None else None
        try:
            matter_mod._fetch(self, mid, inv, changed)
            matter_mod._extract(self, mid)
        except Exception as exc:  # noqa: BLE001 - a rehearsal records a matter it could not prepare, never stops on it
            dump(self._mfile(mid, "rehearsal.json"), {"mode": plan["mode"], "error": type(exc).__name__})
            return
        ctx = matter_mod.context(self, mid, inv)
        built = pack.build(ctx, inv, self.firm, self.prior(mid) if plan["mode"] == "delta" else None, changed)
        dump(
            self._mfile(mid, "rehearsal.json"),
            {
                "mode": plan["mode"],
                "pack_chars": len(built["text"]),
                "readable": built["readable"],
                "packed": built["packed"],
                "scale": round(built["scale"], 3),
                "projected_input_tokens": (len(built["text"]) + len(read.system_prompt(self.firm))) // CHARS_PER_TOKEN,
            },
        )

    def _paid(self) -> None:
        plan = self._plan()
        todo = [(m, plan[m["id"]]) for m in self._matters() if plan[m["id"]]["mode"] in ("whole", "delta")]
        in_parallel(self, todo, self._measure, int(self.firm.get("read_workers")))

    def _rehearsal_report(self) -> dict[str, Any]:
        plan, per = self._plan(), {}
        for m in self._matters():
            got = self._json_m(m["id"], "rehearsal.json") or {"mode": plan.get(m["id"], {}).get("mode", "held")}
            per[m["number"]] = got
        measured = [p for p in per.values() if "pack_chars" in p]
        return {
            "matters": per,
            "totals": {
                "matters": len(per),
                "to_read": len(measured),
                "already_current": sum(1 for p in per.values() if p.get("mode") == "current"),
                "pack_chars": sum(p["pack_chars"] for p in measured),
                "projected_input_tokens": sum(p["projected_input_tokens"] for p in measured),
            },
        }

    def run(self) -> Verdict:
        try:
            for name, fn in (("inventory", self._inventory), ("screen", self._screen), ("matters", self._paid)):
                self._stage(name, fn)
                if name == "inventory" and not self._matters():
                    raise CasefileHold("scope_empty: no open matter is in this job's scope")
            report = self._rehearsal_report()
            reason = f"rehearsal: {report['totals']['to_read']} matters measured; nothing was read by a model or filed"
        except CasefileHold as h:
            report, reason = {"held": str(h)}, f"rehearsal: {h}"
        self.report_path.write_text(json.dumps({"rehearsal": report}, indent=1), encoding="utf-8")
        v = Verdict("held", stage="matters", reason=reason)
        self._counts(v)
        return v


__all__ = ["NoModel", "RehearsalRun"]
