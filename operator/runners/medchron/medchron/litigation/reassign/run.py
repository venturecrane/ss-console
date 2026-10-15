"""`medchron litigate <job_dir>` for a staff reassignment: one job, resumable.

    staff -> select -> matters -> settle -> tasks -> report

No model is called: ``cents`` is 0 by construction. ``staff`` and ``select``
only read; a proof run stops before ``matters``, the first write, and reports
what it would move. Every write is journaled (``write.py``) and read back.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ... import proof_mode
from ...arrivals import getter, paged
from .. import firm as firm_mod, job as job_mod
from ..deadlines.book import Rules
from ..outcome import REASON, LitigationFailed, LitigationHold, Verdict
from ..tools import dump
from . import select as select_mod, spec as spec_mod
from .write import ATTEMPTS, POLL_SECONDS, POLLS, Journal, Writer, converge

STAGES = ("staff", "select", "matters", "settle", "tasks", "report")
#: How long the practice system is given to move the person's tasks itself
#: after the matters move (it took under a minute on 2026-10-15).
SETTLE_SECONDS = 90.0


def swapped(ids: list[str], from_ids: set[str], to: str) -> list[str]:
    """The task's assignees with every FROM person replaced by TO, the other
    co-assignees kept in order, once each."""
    out: list[str] = []
    for i in ids:
        j = to if i in from_ids else i
        if j not in out:
            out.append(j)
    return out


class ReassignRun:
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        seat_factory: Callable[[], Any] | None = None,
        log: Callable[[str], None] = print,
        poll_seconds: float = POLL_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
        write_pause: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.job = job_mod.load(job_dir)
        if self.job.report != "reassign" or not self.job.reassign:
            raise job_mod.LitigationJobError("not a staff reassignment job")
        self.spec: dict[str, Any] = self.job.reassign
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._seat_factory, self._seat = seat_factory, None
        self.poll_seconds, self.settle_seconds, self.sleep = poll_seconds, settle_seconds, sleep
        self.journal = Journal(self.data)
        self.write_pause = write_pause

    # ---- plumbing --------------------------------------------------------------
    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is None:
                from ...seat import open_seat

                self._seat = open_seat(self.job.slug)
            else:
                self._seat = self._seat_factory()
        return self._seat

    @property
    def get(self) -> Callable[..., Any]:
        return getter(self.seat)

    def _writer(self) -> Writer:
        client = self.seat.client
        return (
            Writer(client, self.journal) if self.write_pause is None else Writer(client, self.journal, self.write_pause)
        )

    def _load(self, name: str) -> dict[str, Any]:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _state(self) -> dict[str, Any]:
        p = self.data / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _put(self, key: str, value: Any) -> None:
        st = self._state()
        st[key] = value
        dump(self.data / "state.json", st)

    def _converge(
        self, keys: list[str], write: Callable[[str], Any], check: Callable[[str], bool]
    ) -> tuple[list[str], int]:
        return converge(keys, write, check, attempts=ATTEMPTS, polls=POLLS, pause=self.poll_seconds, sleep=self.sleep)

    # ---- stages -------------------------------------------------------------------
    def _staff(self) -> None:
        rows = [r for r in paged(self.get, "/staff") if isinstance(r, dict)]
        try:
            r = spec_mod.resolve_all(self.spec, rows)
        except spec_mod.Unresolved as u:
            dump(self.data / "unresolved.json", {"role": u.role, "kind": u.kind, "name": u.name, "matches": u.matches})
            raise LitigationHold(str(u)) from u
        names = {str(s["id"]).strip().lower(): spec_mod.full_name(s) for s in rows if isinstance(s.get("id"), str)}
        dump(
            self.data / "resolve.json",
            {
                "from": [{"name": p.name, "ids": list(p.ids)} for p in r["from"]],
                "from_ids": sorted({i for p in r["from"] for i in p.ids}),
                "to_name": r["to"].name,
                "to_id": r["to_id"],
                "attorneys": [p.name for p in r["attorneys"]],
                "attorney_ids": sorted({i for p in r["attorneys"] for i in p.ids}),
                "names": names,
            },
        )

    def _select(self) -> None:
        # The litigation test reads no date; ``today`` only sizes the report's windows.
        rules = Rules({}, self.firm.court_rx, int(self.firm.get("min_court_hits")), dt.date.today())
        dump(
            self.data / "plan.json",
            select_mod.run(self.seat, self.data, self._load("resolve.json"), self.spec, rules, self.log),
        )

    def _assisting(self, mid: str) -> str:
        return str(self.get(f"/matters/{mid}").get("personAssistingStaffId") or "").lower()

    def _matters(self) -> None:
        res, plan = self._load("resolve.json"), self._load("plan.json")
        to, from_ids = res["to_id"], set(res["from_ids"])
        keys, skipped = [], []
        for m in plan["move"]:
            try:
                cur = self._assisting(m["id"])
            except Exception:  # noqa: BLE001 - unreadable now is still ours to move; converge reads it again
                keys.append(m["id"])
                continue
            if cur == to or cur in from_ids:
                keys.append(m["id"])
            else:  # someone else changed it since select, or a FROM person only in the plural list
                skipped.append(m["number"])
        writer = self._writer()
        not_moved, reads = self._converge(
            keys,
            lambda mid: writer.send("PATCH", f"/matters/{mid}", {"personAssistingStaffId": to}),
            lambda mid: self._assisting(mid) == to,
        )
        number = {m["id"]: m["number"] for m in plan["move"]}
        dump(self.data / "matters.json", {"moved": [k for k in keys if k not in not_moved],
                                          "not_moved": [number[k] for k in not_moved], "skipped": skipped,
                                          "reads": reads})  # fmt: skip

    def _settle(self) -> None:
        if self._load("matters.json")["moved"]:
            self.sleep(self.settle_seconds)

    def _task_writer(self, owner_of: dict[str, str], from_ids: set[str], to: str) -> Callable[[str], None]:
        from smokeball_connector.task_update import merge_task_update

        writer = self._writer()

        def write(tid: str) -> None:
            t = self.get(f"/tasks/{tid}")
            ids = select_mod.task_ids(t)
            if not set(ids) & from_ids or not owner_of.get(tid):
                return
            body, _ = merge_task_update(t, subject=None, note=None, due_date=None, is_completed=None,
                                        assignee_ids=swapped(ids, from_ids, to), staff_id=owner_of[tid],
                                        stamp=lambda s: s)  # fmt: skip
            if t.get("categories"):  # a full replace clears what it omits
                body["categories"] = t["categories"]
            writer.send("PUT", f"/tasks/{tid}", body)

        return write

    def _task_shows(self, from_ids: set[str], to: str) -> Callable[[str], bool]:
        def check(tid: str) -> bool:
            t = self.get(f"/tasks/{tid}")
            if t.get("isDeleted") or t.get("isCompleted"):
                return True  # closed by someone meanwhile: nothing left to move
            ids = select_mod.task_ids(t)
            return to in ids and not set(ids) & from_ids

        return check

    def _tasks(self) -> None:
        res, plan, moved = self._load("resolve.json"), self._load("plan.json"), self._load("matters.json")["moved"]
        to, from_ids = res["to_id"], set(res["from_ids"])
        by_id = {m["id"]: m for m in plan["move"]}
        stragglers, owner_of, matter_of, auto = [], {}, {}, 0
        for mid in moved:
            rows = select_mod.open_tasks(self.get, mid)
            now = {t["id"]: select_mod.task_ids(t) for t in rows}
            auto += sum(
                1 for tid in by_id[mid]["baseline"] if tid in now and to in now[tid] and not set(now[tid]) & from_ids
            )
            for tid, ids in now.items():
                if set(ids) & from_ids:
                    stragglers.append(tid)
                    owner_of[tid], matter_of[tid] = by_id[mid]["resp"], by_id[mid]["number"]
        not_moved, reads = self._converge(
            stragglers, self._task_writer(owner_of, from_ids, to), self._task_shows(from_ids, to)
        )
        left: dict[str, int] = {}
        for tid in not_moved:
            left[matter_of[tid]] = left.get(matter_of[tid], 0) + 1
        dump(self.data / "tasks.json", {"auto": auto, "swept": len(stragglers) - len(not_moved),
                                        "not_moved": dict(sorted(left.items())), "reads": reads})  # fmt: skip

    # ---- the walk and the verdict -------------------------------------------------
    def _walk(self) -> Verdict:
        fns = (self._staff, self._select, self._matters, self._settle, self._tasks, lambda: None)
        for name, fn in zip(STAGES, fns):
            if name == "matters" and proof_mode.active():  # a proof run stops before the first write
                return Verdict(proof_mode.OUTCOME, stage="matters", counts=self._counts(), detail=self._detail())
            if self._state().get(name, {}).get("status") != "done":
                self.log(f"[{name}] start")
                fn()
                self._put(name, {"status": "done"})
        return self._delivered()

    def _counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        if (self.data / "plan.json").is_file():
            plan = self._load("plan.json")
            c.update(matters_read=int(plan["matters_read"]), cases_to_move=len(plan["move"]),
                     tasks_naming_from=sum(len(m["baseline"]) for m in plan["move"]),
                     cases_outside_scope=sum(int(v) for v in plan["outside"].values()))  # fmt: skip
        if (self.data / "matters.json").is_file():
            m = self._load("matters.json")
            c.update(cases_moved=len(m["moved"]), cases_not_moved=len(m["not_moved"]), cases_skipped=len(m["skipped"]))
        if (self.data / "tasks.json").is_file():
            t = self._load("tasks.json")
            c.update(tasks_auto_moved=int(t["auto"]), tasks_swept=int(t["swept"]), tasks_moved=int(t["auto"]) + int(t["swept"]),
                     tasks_not_moved=sum(int(v) for v in t["not_moved"].values()))  # fmt: skip
        return c

    def _detail(self) -> dict[str, Any]:
        """What the wake names: staff names, matter numbers, counts. Never a
        client's name or a task's words."""
        d: dict[str, Any] = {}
        if (self.data / "unresolved.json").is_file():
            d["unresolved"] = self._load("unresolved.json")
        if (self.data / "resolve.json").is_file():
            r = self._load("resolve.json")
            d.update({"from": [p["name"] for p in r["from"]], "to": r["to_name"], "attorneys": r["attorneys"]})
        d.update({"litigation_only": self.spec["litigation_only"], "statuses": self.spec["statuses"]})
        if (self.data / "plan.json").is_file():
            plan, r = self._load("plan.json"), self._load("resolve.json")
            d["outside_by_attorney"] = plan["outside"]
            moved_from = set(plan["from_moved_ids"])
            d["from_with_nothing"] = [p["name"] for p in r["from"] if not set(p["ids"]) & moved_from]
        if (self.data / "matters.json").is_file():
            d["cases_not_moved"] = self._load("matters.json")["not_moved"]
        if (self.data / "tasks.json").is_file():
            d["tasks_not_moved"] = self._load("tasks.json")["not_moved"]
        return d

    def _delivered(self) -> Verdict:
        c = self._counts()
        reads = sum(int(self._load(n).get("reads") or 0) for n in ("matters.json", "tasks.json"))
        stuck = c.get("cases_not_moved", 0) + c.get("tasks_not_moved", 0)
        proof = {"readback": "confirmed", "rows": reads, "nothing_to_file": not c.get("cases_to_move"),
                 "review": "code_only", "review_cents": 0, "review_model": "",
                 **({"alerts": ["readback_mismatch"]} if stuck else {})}  # fmt: skip
        return Verdict("delivered", stage="report", counts=c, detail=self._detail(), proof=proof)

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "report")

    def reopen(self, stages: list[str]) -> None:
        """Reopen the named stages and every stage after the earliest. The
        write stages re-read before they write, so a reopened move is safe."""
        known = [x for x in stages if x in STAGES]
        if not known:
            return
        for x in STAGES[min(STAGES.index(k) for k in known) :]:
            self._put(x, {"status": "reopened"})
        if "select" in known or "staff" in known:
            import shutil

            shutil.rmtree(self.data / "select", ignore_errors=True)

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except LitigationHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h), counts=self._counts(), detail=self._detail())
        except LitigationFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc), counts=self._counts())
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict("failed", stage=self._current(),
                        reason=f"unexpected: {type(exc).__name__} at {self._current()}: {str(exc)[:300]}")  # fmt: skip
        if v.reason and not REASON.match(v.reason):
            v.reason = f"unexpected: {v.reason}"
        v.cents = 0
        v.writes = self.journal.results()
        v.matters_total, v.matters_reread = v.counts.get("matters_read", 0), v.counts.get("cases_moved", 0)
        return v
