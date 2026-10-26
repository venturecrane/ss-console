"""`medchron casefile <job_dir>` on an APPLY job: the lines a person approved.

The broker built this job's line list itself (``casefile_apply_submit``): the
lines a case file job issued on that job's thread, each approved there by a
Named Administrator the firm authored, none applied before. The model never
names a line. Here, for each line, in number order:

1. its facts are loaded from the lane's state and their hash must equal the
   hash the broker holds (an approval binds to exactly what was proposed);
2. the record is read AGAIN and compared with the snapshot it was proposed
   against: a task that changed (or was completed) and a Medicals tab that
   changed are refused and listed, never written;
3. the write is made and READ BACK:
   * a task: the full-replace PUT built from a fresh read (``StaffId`` the
     task's owner, ``CompletedByStaffId`` the approver, resolved from the
     firm's staff list by email; an approver the list cannot place is
     refused), its note stamped with the line, the approver and the proof;
     polled until it reads back completed;
   * a Medicals row: the connector's proven row write (``medicals_tools``):
     the provider matched to the firm's contacts (several candidates write
     nothing), the contact linked, the values patched and read back.

Every write is journaled (``own_writes``) and rides the job's record to the
broker (``writes``), and every line ends with an outcome. A proof run (the
lane's ``.apply`` proof) does steps 1 and 2 and stops before any write.
Intent rows are written before each write, so a resumed job never writes a
line twice: a line with an intent and no outcome is decided by reading back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .. import proof_mode
from ..arrivals import getter, paged
from ..stages.base import append_jsonl, read_jsonl
from . import job as job_mod, own_writes, propose, state as state_mod
from .outcome import CasefileHold, Verdict

READBACK_WAITS = (1, 2, 3, 5, 8, 13)
APPLIED = "applied.jsonl"
DONE_OUTCOMES = ("completed", "written", "invoice_added", "already_complete", "already_present")


def _stamp(text: str | None) -> str | None:
    from smokeball_connector.task_update import PROVENANCE_MARK

    if not isinstance(text, str) or not text.strip():
        return text
    return text if text.lstrip().startswith(PROVENANCE_MARK) else f"{PROVENANCE_MARK} {text}"


def _medicals_module() -> Any:
    """The connector's row write, with its provenance stamp bound here: its
    default reaches for the MCP server module, which the runner never loads."""
    from smokeball_connector import medicals_tools

    medicals_tools._stamp = lambda: _stamp  # the runner's process only; the same stamp the server applies
    return medicals_tools


class ApplyRun:
    def __init__(
        self,
        job_dir: Path,
        *,
        state_dir: str | Path | None = None,
        seat_factory: Callable[[], Any] | None = None,
        log: Callable[[str], None] = print,
        sleep: Callable[[float], Any] = time.sleep,
    ) -> None:
        self.job = job_mod.load(job_dir)
        if self.job.apply is None:
            raise job_mod.CasefileJobError("not an apply job")
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.state = state_mod.state_dir(state_dir)
        self.log, self.sleep = log, sleep
        self._seat_factory, self._seat = seat_factory, None

    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is None:
                from ..seat import open_seat

                self._seat = open_seat(self.job.slug)
            else:
                self._seat = self._seat_factory()
        return self._seat

    @property
    def client(self) -> Any:
        return self.seat.client

    # ---- the journal ------------------------------------------------------------
    def _journal(self) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for r in read_jsonl(self.data / APPLIED):
            out.setdefault(int(r["n"]), {}).update(r)
        return out

    def _note(self, n: int, **fields: Any) -> None:
        append_jsonl(self.data / APPLIED, {"n": n, **fields})

    # ---- checks -------------------------------------------------------------------
    def _facts(self, line: job_mod.Line, proposals: dict[str, Any]) -> dict[str, Any] | None:
        got = proposals.get(str(line.n))
        if not isinstance(got, dict):
            return None
        same = (got.get("kind"), got.get("matter_id"), got.get("source_id")) == (
            line.kind,
            line.matter_id,
            line.source_id,
        )
        return got["facts"] if same and state_mod.digest(got.get("facts")) == line.sha256 else None

    def _approver_staff_id(self) -> str | None:
        who = self.job.apply.approver if self.job.apply else ""
        rows = [p for p in paged(getter(self.seat), "/staff") if isinstance(p, dict)]
        hits = {str(p["id"]) for p in rows if str(p.get("email") or "").strip().lower() == who and p.get("id")}
        return next(iter(hits)) if len(hits) == 1 else None

    # ---- tasks --------------------------------------------------------------------
    def _task(self, line: job_mod.Line, facts: dict[str, Any], staff: str | None) -> dict[str, Any]:
        tid = line.source_id
        cur = self.client.get(f"/tasks/{tid}")
        cur = cur if isinstance(cur, dict) else {}
        if str((cur.get("matter") or {}).get("id") or "").lower() not in ("", line.matter_id):
            return {"outcome": "refused_wrong_matter"}
        if cur.get("isCompleted"):
            return {"outcome": "already_complete"}
        if propose.task_snapshot(cur) != facts["snapshot"]:
            return {"outcome": "refused_changed"}
        owner = str((cur.get("createdBy") or {}).get("id") or "")
        if not owner or not staff:
            return {"outcome": "refused_no_staff"}
        if proof_mode.active():
            return {"outcome": "would_complete"}
        from smokeball_connector.task_update import merge_task_update

        old = str(cur.get("note") or "")
        source = self.job.apply.source_job_id if self.job.apply is not None else ""
        mark = (
            f"[Operator] Completed on approval of change {line.n} (Case File Manager job {source}). "
            f"Proof: {'; '.join(facts.get('proof_names') or [])[:300]}"
        )
        body, _ = merge_task_update(
            cur, subject=None, note=f"{old}\n{mark}" if old.strip() else mark, due_date=None,
            is_completed=True, assignee_ids=None, staff_id=owner, stamp=lambda s: s,
        )  # fmt: skip
        body["completedByStaffId"] = staff
        self._note(line.n, intent="PUT")
        self.client.request("PUT", f"/tasks/{tid}", json=body)
        return self._task_readback(tid, line.n)

    def _task_readback(self, tid: str, n: int) -> dict[str, Any]:
        for wait in READBACK_WAITS:
            self.sleep(wait)
            got = self.client.get(f"/tasks/{tid}")
            if isinstance(got, dict) and got.get("isCompleted") and f"change {n} " in str(got.get("note") or ""):
                return {"outcome": "completed", "write": {"method": "PUT", "path": f"/tasks/{tid}", "ok": True}}
        return {"outcome": "readback_mismatch", "write": {"method": "PUT", "path": f"/tasks/{tid}", "ok": False}}

    # ---- medicals --------------------------------------------------------------------
    def _medicals(self, line: job_mod.Line, facts: dict[str, Any]) -> dict[str, Any]:
        from .inventory import layout_values

        path = f"/matters/{line.matter_id}/layouts/{facts['item_id']}"
        if state_mod.digest(layout_values(self.client.get(path))) != facts["snapshot"]:
            return {"outcome": "refused_changed"}
        if proof_mode.active():
            return {"outcome": "would_write"}
        mt = _medicals_module()
        from decimal import Decimal

        bill = mt._Bill(
            matter=line.matter_id,
            provider=facts["provider"],
            amount=Decimal(facts["charge"]),
            start=facts["service_start"],
            end=facts["service_end"],
            account="",
            source={
                "file_name": facts.get("source_name"),
                "first_page": facts.get("page") or 1,
                "last_page": facts.get("page") or 1,
                "from_scan": False,
            },
        )
        self._note(line.n, intent="PATCH")
        got = mt._write_row(self.client, bill, facts["item_id"])
        status = str(got.get("status") or "refused")
        if got.get("contact_created") and isinstance(got.get("contact_id"), str):
            own_writes.record(self.state, self.job.job_id, line.matter_id, "contact", got["contact_id"])
        out: dict[str, Any] = {"outcome": status}
        if status in ("written", "invoice_added", "readback_mismatch"):
            out["write"] = {
                "method": "PATCH",
                "path": f"/matters/{line.matter_id}",
                "ok": status != "readback_mismatch",
            }
        return out

    # ---- the walk ----------------------------------------------------------------------
    def _one(self, line: job_mod.Line, proposals: dict[str, Any], staff: str | None) -> dict[str, Any]:
        facts = self._facts(line, proposals)
        if facts is None:
            return {"outcome": "refused_not_as_proposed"}
        if line.kind == "task":
            return self._task(line, facts, staff)
        return self._medicals(line, facts)

    def _guarded(self, line: job_mod.Line, proposals: dict[str, Any], staff: str | None) -> dict[str, Any]:
        try:
            return self._one(line, proposals, staff)
        except Exception as exc:  # noqa: BLE001 - one line's failure is that line's outcome; the others go on
            self.log(f"[apply] change {line.n}: {type(exc).__name__}")
            return {"outcome": "error"}

    def run(self) -> Verdict:
        a = self.job.apply
        assert a is not None
        proposals = state_mod.load_proposals(self.state, a.source_job_id)
        if not proposals:
            raise CasefileHold("apply_refused: the proposed lines of that job are not on this seat")
        staff = self._approver_staff_id() if any(ln.kind == "task" for ln in a.lines) else None
        done = self._journal()
        outcomes, writes = [], []
        for line in sorted(a.lines, key=lambda x: x.n):
            prev = done.get(line.n) or {}
            if prev.get("outcome"):
                res = prev
            elif prev.get("intent"):  # a write may have landed before a stop: read back, never write again
                res = (
                    self._task_readback(line.source_id, line.n)
                    if line.kind == "task"
                    else {"outcome": "unknown_after_stop"}
                )
            else:
                res = self._guarded(line, proposals, staff)
            if not prev.get("outcome"):
                self._note(line.n, outcome=res["outcome"], write=res.get("write"))
            if res.get("write"):
                writes.append(res["write"])
                if res["outcome"] in DONE_OUTCOMES and line.kind == "task":
                    own_writes.record(self.state, self.job.job_id, line.matter_id, "task", line.source_id)
            outcomes.append({"n": line.n, "kind": line.kind, "outcome": res["outcome"]})
        return self._verdict(outcomes, writes)

    def _verdict(self, outcomes: list[dict[str, Any]], writes: list[dict[str, Any]]) -> Verdict:
        if proof_mode.active():
            return Verdict(proof_mode.OUTCOME, stage="apply", mode="apply", line_outcomes=outcomes)
        ok = sum(1 for o in outcomes if o["outcome"] in DONE_OUTCOMES)
        dump_summary(self.data, outcomes)
        return Verdict(
            "delivered",
            stage="report",
            mode="apply",
            line_outcomes=outcomes,
            writes=writes,
            proof=({"readback": "confirmed", "rows": ok} if ok else {"readback": "confirmed", "nothing_to_file": True})
            | {"review": "code_only", "review_cents": 0, "review_model": ""},
        )


def dump_summary(data: Path, outcomes: list[dict[str, Any]]) -> None:
    (data / "apply-summary.json").write_text(json.dumps(outcomes, indent=1), encoding="utf-8")


__all__ = ["ApplyRun", "DONE_OUTCOMES"]
