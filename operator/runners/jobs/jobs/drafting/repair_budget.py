"""Code owns a served set's repair budget, and what happens when it runs out.

WHY (2026-11-05). A District discovery set's repairs looped, each round paid,
until the set failed ``audit_unrepaired`` and SMD stopped it by hand. The
rounds had a ceiling and no price, and running out had one ending, a failure
somebody had to notice. Now:

* **The budget.** A fixed number of repair rounds and a dollar cap per job,
  from the firm's ``budget`` section (``repair_rounds``, ``repair_cap_usd``),
  with defaults (``DEFAULT_ROUNDS``; a quarter of ``per_job_cap_usd``). The
  dollars are what the job's own usage ledger priced between the first audit
  of each set and the end of its repairs, summed over the job's sets
  (``repair-budget.json``). Checked before every round: a round already
  started finishes, so the cap is overrun by at most one round.
* **Running out, in one clear state.** Code decides by the kinds of the
  findings left (``decide``):

  - a kind that changes what a request asks for or states (``HOLD_KINDS``:
    an invented or drifting premise, a misquotation, a wrong figure, a
    response that does not restate or skips the served item) HOLDS the job
    for SMD before anything is filed: ``audit_unrepaired``, failed and
    resumable, which alerts SMD as every drafting failure does;
  - a form defect only (``FILE_KINDS``: a compound request, a term the set
    does not define) FILES what passed: each item's own text and the finding
    go to the attorney notes under a heading code writes
    (``notes.OPEN_ITEMS_HEADING``), and SMD gets one alert
    (``REPAIR_ALERT``) naming the job, the set and the finding kinds.

  A kind this module does not know holds: the safer reading wins.

The same ending applies to findings parked because a repair was refused for
adding a fact (``repair_guard``): they are unresolved like any other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn

from .outcome import DraftingFailed
from .repair_guard import FindingId, Guard, kind_of, unit_text

DEFAULT_ROUNDS = 8
#: Without an authored ``repair_cap_usd``, repairs may spend this share of the job's cap.
CAP_SHARE = 0.25
HOLD_KINDS = frozenset({"DRIFTS", "INVENTED", "ARITHMETIC", "NOT_VERBATIM", "MISSING_ITEM", "MECHANICAL"})
FILE_KINDS = frozenset({"COMPOUND", "UNDEFINED"})
SPEND = "repair-budget.json"
OPEN = "repair-open.json"
EXHAUSTED = "repair-exhausted.json"
#: ``proof.gate.CODES``: the page SMD gets when a set filed with unresolved items.
REPAIR_ALERT = "repair_unconverged"
#: What the attorney reads for each kind that can be filed unresolved.
KIND_WORDS = {
    "COMPOUND": "asks more than one thing in one request",
    "UNDEFINED": "uses a capitalized term the set's definitions do not define",
}


def decide(kinds: set[str]) -> str:
    """``"file"`` when every kind is a form defect, else ``"hold"``."""
    return "file" if kinds and kinds <= FILE_KINDS else "hold"


def _read(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _write(p: Path, obj: Any) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(p)


@dataclass
class Plan:
    """One set's share of its job's repair budget."""

    rounds: int
    cap_usd: float
    spent: Callable[[], float]  # the job's priced ledger, now
    job_data: Path
    unit_data: Path
    key: str
    label: str
    job_id: str

    # ---- the dollars --------------------------------------------------------------------
    def _spend(self) -> dict[str, Any]:
        return _read(self.job_data / SPEND) or {}

    def begin(self) -> None:
        """The first audit is not a repair: the meter starts after it. Once
        per set, so a resume keeps what earlier runs spent."""
        st = self._spend()
        if self.key not in st:
            st[self.key] = {"start": round(self.spent(), 6)}
            _write(self.job_data / SPEND, st)

    def _end(self) -> None:
        st = self._spend()
        row = st.get(self.key) or {}
        if "end" not in row and "start" in row:
            row["end"] = round(self.spent(), 6)
            st[self.key] = row
            _write(self.job_data / SPEND, st)

    def repair_usd(self) -> float:
        now = self.spent()
        return sum(float(r.get("end", now)) - float(r["start"]) for r in self._spend().values() if "start" in r)

    def over(self, version: int) -> str | None:
        """Why no further round may start, or None."""
        if version > self.rounds:
            return f"after {self.rounds} repairs"
        usd = self.repair_usd()
        if usd >= self.cap_usd:
            return f"after {version - 1} repair(s), with the job's ${self.cap_usd:.2f} repair cap spent"
        return None

    # ---- the ending ---------------------------------------------------------------------
    def close(self, guard: Guard, audit_md: str, draft_md: str, version: int, why: str) -> int:
        """End the set's repairs: ``version`` when nothing is unresolved or
        every unresolved item may be filed with the notes; else the hold."""
        self._end()
        left = guard.open_rows(audit_md) + guard.parked_rows(audit_md)
        if not left:
            return version
        kinds = {kind_of(f) for f, _ in left}
        if decide(kinds) == "hold":
            self.hold(len(left), kinds, why, version)
        items = [self._item(f, ln, guard, draft_md) for f, ln in left]
        _write(self.unit_data / OPEN, items)
        rec = _read(self.job_data / EXHAUSTED) or []
        rec = [r for r in rec if r.get("set") != self.label] + [
            {"job_id": self.job_id, "set": self.label, "kinds": sorted(kinds), "count": len(left), "why": why}
        ]
        _write(self.job_data / EXHAUSTED, rec)
        return version

    def hold(self, n: int, kinds: set[str], why: str, version: int) -> NoReturn:
        # Counts and kinds only, never a claim or a request's words: a reason reaches SMD's alerts.
        raise DraftingFailed(
            f"audit_unrepaired: {n} audit finding(s) ({', '.join(sorted(kinds))}) in the {self.label} {why}; "
            f"see audit-v{version}.md"
        )

    def _item(self, fid: FindingId, line: str, guard: Guard, draft_md: str) -> dict[str, str]:
        head, n, kind = fid
        frozen = {(str(r["key"][0]), r["key"][1]): str(r["text"]) for r in guard.rejected}
        text = (frozen.get((head, n)) or frozen.get((head, None))) if guard.parked(fid) else None
        if not text:
            text = unit_text(draft_md, (head, n)) if n is not None else ""
        detail = line.split("|")[-1].strip() if "|" in line else ""
        return {
            "where": head + ("" if n is None else f", No. {n}"),
            "text": " ".join(text.split())[:600],
            "kind": KIND_WORDS.get(kind, kind.lower()),
            "finding": " ".join(detail.split())[:300],
        }


def plan(run: Any, unit: Any, default_rounds: int = DEFAULT_ROUNDS) -> Plan:
    """The firm's repair budget for one set of ``run`` (a ``DraftingRun``)."""
    b = run.firm.data["budget"]
    return Plan(
        rounds=int(b.get("repair_rounds", default_rounds)),
        cap_usd=float(b.get("repair_cap_usd", CAP_SHARE * float(b["per_job_cap_usd"]))),
        spent=run.budget.refresh,
        job_data=run.data,
        unit_data=unit.data,
        key=unit.key or "_",
        label=unit.label,
        job_id=str(run.job.job_id),
    )


def open_items(unit_data: Path) -> list[dict[str, str]]:
    """What a set filed unresolved (``notes.attorney_notes`` writes them)."""
    return list(_read(unit_data / OPEN) or [])


# ---- the daemon's half: the child cannot page, so root reads and pages ------------------
def alert_exhausted(lane: Any, job_id: str) -> None:
    """After the child runs: one alert per job when a set filed with
    unresolved items, the set and the kinds named, never their words."""
    from ..proof import watch

    p = lane.job_dir(job_id) / "data" / EXHAUSTED
    rec = _read(p) if p.is_file() else None
    if not rec:
        return
    detail = "; ".join(f"{r['set']}: {r['count']} unresolved ({','.join(r['kinds'])})" for r in rec)
    if watch.alert(lane, job_id, REPAIR_ALERT, f"job {job_id}: {detail}"):
        p.replace(p.with_name(EXHAUSTED + ".alerted"))


__all__ = [
    "CAP_SHARE",
    "DEFAULT_ROUNDS",
    "FILE_KINDS",
    "HOLD_KINDS",
    "Plan",
    "REPAIR_ALERT",
    "alert_exhausted",
    "decide",
    "open_items",
    "plan",
]
