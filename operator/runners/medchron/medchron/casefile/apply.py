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
     nothing), the contact linked, the values patched and read back;
   * a calendar entry: the matter's calendar read again (the appointment
     already there that day is ``already_present``, never written twice), then
     the connector's create-and-read-back (``event_update.create_event_proven``,
     the path ``create_event`` takes): stamped, the responsible attorney an
     attendee, read back by id.

Every write is journaled (``own_writes``) and rides the job's record to the
broker (``writes``), and every line ends with an outcome. A proof run (the
lane's ``.apply`` proof) does steps 1 and 2 and stops before any write.
Intent rows are written before each write, so a resumed job never writes a
line twice: a line with an intent and no outcome is decided by reading back.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .. import proof_mode
from ..arrivals import getter, paged
from ..stages.base import append_jsonl, read_jsonl
from . import job as job_mod, own_writes, propose, state as state_mod
from .outcome import CasefileHold, Verdict

READBACK_WAITS = (1, 2, 3, 5, 8, 13)
APPLIED = "applied.jsonl"
#: A line whose write landed and read back (or whose record already showed it).
DONE_OUTCOMES = ("completed", "written", "invoice_added", "created", "already_complete", "already_present")
#: A line whose write may or may not have landed: never reported as made.
UNCERTAIN_OUTCOMES = ("readback_mismatch", "unknown_after_stop", "error", "link_not_visible")


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
        owner = self._task_owner(cur, line.matter_id)
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
        if cur.get("categories"):  # a full replace clears what it omits
            body["categories"] = cur["categories"]
        self._note(line.n, intent="PUT")
        self.client.request("PUT", f"/tasks/{tid}", json=body)
        return self._task_readback(tid, line.n)

    def _task_owner(self, cur: dict[str, Any], matter_id: str) -> str:
        """The ``StaffId`` the full-replace PUT carries. Never the author
        (``createdBy``): the task's current assignee stays its owner, and with
        no assignee the matter's responsible staff member is (the staff
        reassignment's rule). ``assigneeIds`` are re-sent unchanged by
        ``merge_task_update``, so completing a task never reassigns it."""
        for a in cur.get("assignees") or []:
            sid = a.get("id") if isinstance(a, dict) else a
            if isinstance(sid, str) and sid:
                return sid
        matter = self.client.get(f"/matters/{matter_id}")
        return str(matter.get("personResponsibleStaffId") or "") if isinstance(matter, dict) else ""

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

    # ---- calendar entries ---------------------------------------------------------------
    def _calendar(self, matter_id: str) -> list[dict[str, Any]]:
        return [e for e in paged(getter(self.seat), "/events", MatterId=matter_id, ExcludeDeletedEvents=True)
                if isinstance(e, dict)]  # fmt: skip

    def _on_calendar(self, line: job_mod.Line, facts: dict[str, Any]) -> bool:
        return propose.on_calendar(self._calendar(line.matter_id), facts["provider"], facts["date"])

    def _event(self, line: job_mod.Line, facts: dict[str, Any]) -> dict[str, Any]:
        """The connector's create-and-read-back (``create_event_proven``, the
        path ``create_event`` takes): stamped, on the responsible attorney's
        calendar, read back by id. A calendar that already shows the
        appointment that day is not written again."""
        if self._on_calendar(line, facts):
            return {"outcome": "already_present"}
        matter = self.client.get(f"/matters/{line.matter_id}")
        owner = str(matter.get("personResponsibleStaffId") or "") if isinstance(matter, dict) else ""
        if not owner:
            return {"outcome": "refused_no_staff"}
        if proof_mode.active():
            return {"outcome": "would_create"}
        from smokeball_connector.event_update import create_event_proven
        from smokeball_connector.readback import is_shortfall

        source = self.job.apply.source_job_id if self.job.apply is not None else ""
        who = self.job.apply.approver if self.job.apply is not None else ""
        description = (
            f"Added on approval of change {line.n} (Case File Manager job {source}) by {who}. "
            f"{facts['specialty']} appointment with {facts['provider']}, {facts['how']}. "
            f"From the file: {'; '.join(facts.get('source_names') or [])[:300]}"
        )
        self._note(line.n, intent="POST")
        got = create_event_proven(
            self.client, subject=facts["subject"], start_time=facts["start"], end_time=facts["end"],
            attendees=[owner], time_zone=facts["time_zone"], stamp=_stamp, matter_id=line.matter_id,
            description=description, all_day=bool(facts["all_day"]),
        )  # fmt: skip
        eid = str(got.get("id") or got.get("eventId") or "") if isinstance(got, dict) else ""
        write = {
            "method": "POST",
            "path": f"/events/{eid}" if eid else f"/matters/{line.matter_id}",
            "ok": not is_shortfall(got),
        }
        if eid:  # ours whether or not it read back: never a firm change on the next re-check
            own_writes.record(self.state, self.job.job_id, line.matter_id, "event", eid)
        return {"outcome": "readback_mismatch" if is_shortfall(got) else "created", "write": write}

    def _resumed(self, line: job_mod.Line, proposals: dict[str, Any]) -> dict[str, Any]:
        """A line whose write may have landed before a stop: read back, never write again."""
        if line.kind == "task":
            return self._task_readback(line.source_id, line.n)
        facts = self._facts(line, proposals) if line.kind == "event" else None
        if facts is not None and self._on_calendar(line, facts):
            return {"outcome": "created"}
        return {"outcome": "unknown_after_stop"}

    # ---- the walk ----------------------------------------------------------------------
    def _one(self, line: job_mod.Line, proposals: dict[str, Any], staff: str | None) -> dict[str, Any]:
        facts = self._facts(line, proposals)
        if facts is None:
            return {"outcome": "refused_not_as_proposed"}
        if line.kind == "task":
            return self._task(line, facts, staff)
        if line.kind == "event":
            return self._event(line, facts)
        return self._medicals(line, facts)

    def _guarded(self, line: job_mod.Line, proposals: dict[str, Any], staff: str | None) -> dict[str, Any]:
        try:
            return self._one(line, proposals, staff)
        except Exception as exc:  # noqa: BLE001 - one line's failure is that line's outcome; the others go on
            self.log(f"[apply] change {line.n}: {type(exc).__name__}")
            return {"outcome": "error"}

    def run(self) -> Verdict:
        """Every ending carries the lines this job journaled (``intents``), so a
        hold or a failure never frees a line whose write may have landed."""
        try:
            return self._run()
        except CasefileHold as h:
            return Verdict("held", stage="apply", mode="apply", reason=str(h), intents=self.intents())
        except Exception as exc:  # noqa: BLE001 - a verdict, never a trace; the journal decides what was written
            self.log(traceback.format_exc())
            return Verdict(
                "failed",
                stage="apply",
                mode="apply",
                reason=f"unexpected: {type(exc).__name__}: {str(exc)[:300]}",
                intents=self.intents(),
            )

    def _run(self) -> Verdict:
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
                res = self._resumed(line, proposals)
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
        """The verdict the outcomes support, never more.

        * every line made, read back, or already so: delivered, read-back confirmed;
        * some lines refused (the record changed since the proposal, or the
          approver or the facts did not check out): delivered, read-back
          confirmed for the lines made only, each refused line named;
        * any line whose write may or may not have landed: HELD
          ``readback_mismatch:`` (SMD is paged), never reported as made."""
        if proof_mode.active():
            return Verdict(proof_mode.OUTCOME, stage="apply", mode="apply", line_outcomes=outcomes)
        dump_summary(self.data, outcomes)
        unsure = [o["n"] for o in outcomes if o["outcome"] in UNCERTAIN_OUTCOMES]
        if unsure:
            return Verdict(
                "held",
                stage="apply",
                mode="apply",
                line_outcomes=outcomes,
                writes=writes,
                intents=self.intents(),
                reason=f"readback_mismatch: changes {', '.join(map(str, unsure))} could not be confirmed in Smokeball",
            )
        made = sum(1 for o in outcomes if o["outcome"] in DONE_OUTCOMES)
        proof: dict[str, Any] = (
            {"readback": "confirmed", "rows": made} if made else {"readback": "confirmed", "nothing_to_file": True}
        )
        refused = [o["n"] for o in outcomes if o["outcome"] not in DONE_OUTCOMES]
        if refused:
            proof["not_made"] = refused
        return Verdict(
            "delivered",
            stage="report",
            mode="apply",
            line_outcomes=outcomes,
            writes=writes,
            proof=proof | {"review": "code_only", "review_cents": 0, "review_model": ""},
        )

    def intents(self) -> list[int]:
        """Lines with a journaled write intent or outcome: never released to another apply."""
        return sorted(n for n, r in self._journal().items() if r.get("intent") or r.get("outcome"))


def dump_summary(data: Path, outcomes: list[dict[str, Any]]) -> None:
    (data / "apply-summary.json").write_text(json.dumps(outcomes, indent=1), encoding="utf-8")


__all__ = ["ApplyRun", "DONE_OUTCOMES", "UNCERTAIN_OUTCOMES"]
