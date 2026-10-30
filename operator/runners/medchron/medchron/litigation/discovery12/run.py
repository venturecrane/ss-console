"""`medchron litigate <job_dir>` for the 12-section discovery breakdown
(``report: "discovery12"``): the status run's inventory, fetch, extract, text
cache, caps and filing, with its own plan, reads, gates and workbook.

    inventory -> diff -> fetch -> extract -> screen -> read1 -> read2 -> read3
      -> gates -> parity -> book -> review -> file -> report

* **diff** plans every litigated case: its discovery papers by name (all of
  them, newest first, uncapped) and its discovery-named emails. A case with
  none is not read and prints that no discovery paper was found by name.
* **read1/read2/read3** are the three passes on the twelve sections: a read,
  an independent verify of every date and every negative, an audit of every
  empty section and every "no" response. The job's cap and the month's budget
  stop the run before the call that would cross either (``RunBase._before_call``).
* **gates** hold the report while any date lacks a source on the matter or is
  not in its cited document's text, or any case could not be read.
* **screen** and **parity** do nothing here; **report** commits nothing to the
  lane's state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import proof_mode
from .. import deliver, gates, inventory, manifest, passes
from ..outcome import LitigationHold, Verdict
from ..run import STAGES, LitigationRun
from ..tools import dump
from . import book, sections

GROUP = "discovery12"
ROLE = "discovery_workbook"
#: The classes a breakdown reads: discovery papers, and recent emails named
#: for the case (``manifest.classify``).
CLASSES = ("discovery", "email")


def plan_for(files: list[dict[str, Any]], firm: Any, today: Any) -> dict[str, Any]:
    """One case's plan: the discovery papers and named emails, newest first."""
    picked = [f for f in files if manifest.classify(f, firm, today) in CLASSES]
    picked.sort(key=manifest.file_date, reverse=True)
    cands = [str(f["id"]) for f in picked]
    return {
        "reason": "discovery12",
        "read_groups": [GROUP] if cands else [],
        "audit": "all" if cands else "none",
        "trigger_files": [],
        "candidates": cands,
        "moved": [],
        "newest": manifest.newest_emails(files, int(firm.get("email_recent_n"))),
        "mode": "full",
    }


class Discovery12Run(LitigationRun):
    def _court_only(self) -> bool:
        return False

    def _diff(self) -> None:
        plan = {
            m["id"]: plan_for(inventory.files_of(self.data, m["id"]), self.firm, self.today) for m in self._matters()
        }
        dump(self.data / "plan.json", plan)

    def _screen(self) -> None:
        return None

    def _read1(self) -> None:
        def one(m: dict[str, Any], mid: str, p: dict[str, Any]) -> None:
            ctx = self._ctx(mid)
            raw = self._loop(ctx, "read", m, mid, "")
            dump(self._mfile(mid, "read1.json"), {"result": sections.to_result(ctx.source, raw)})

        passes._each(self, "read1", lambda p: bool(p["read_groups"]), one)

    def _check(self, stage: str, role: str) -> None:
        prev = {"read2": "read1.json", "read3": "read2.json"}[stage]

        def one(m: dict[str, Any], mid: str, p: dict[str, Any]) -> None:
            ctx = self._ctx(mid)
            before = self._mjson(mid, prev) or {}
            result = before["result"]
            got = self._loop(ctx, role, m, mid, "\nTHE RESULT TO CHECK:\n" + book.for_model(result, ctx))
            log = sections.apply_verdicts(ctx.source, result, got.get("verdicts"), role)
            rec = {"result": result, "overturns": list(before.get("overturns") or []) + log}
            dump(self._mfile(mid, f"{stage}.json"), rec)

        passes._each(self, stage, lambda p: bool(p["read_groups"]), one)

    def _read2(self) -> None:
        self._check("read2", "verify")

    def _read3(self) -> None:
        self._check("read3", "audit")

    def _loop(self, ctx: Any, role: str, m: dict[str, Any], mid: str, extra: str) -> dict[str, Any]:
        from ..tools import run_loop

        stage = {"read": "litigation_read", "verify": "litigation_verify", "audit": "litigation_audit"}[role]
        user = (
            f"MATTER: {self._header(m)}\nDETERMINE: the twelve discovery sections.\n\n"
            "Read the document texts below first; use the tools only for what they do not settle.\n\n"
            f"FILES (discovery papers and discovery emails, newest first):\n{self._listing(ctx, mid)}\n{extra}"
        )
        iters = int(self.firm.get("tool_iterations"))
        return run_loop(
            self.doorway,
            stage,
            model=self.firm.model(role),
            system=sections.system_for(role, self.today),
            user=user,
            ctx=ctx,
            final_tool=sections.RECORD_RESULT if role == "read" else sections.RECORD_VERDICTS,
            max_iterations=iters if role == "read" else max(4, iters // 2),
        )

    # ---- gates, book, file -----------------------------------------------------------------------
    def final(self, m: dict[str, Any]) -> dict[str, Any] | None:
        r3 = self._mjson(m["id"], "read3.json")
        return {
            "matter_id": m["id"],
            "number": m["number"],
            "title": m["title"],
            "responsible": m.get("responsible") or "",
            "read": r3 is not None,
            **(r3["result"] if r3 else {"sections": {}, "flags": []}),
        }

    def _finals(self) -> list[dict[str, Any]]:
        return [self.final(m) for m in self._matters()]  # type: ignore[misc]

    def _gates(self) -> None:
        ms = self._finals()
        dump(self.data / "final.json", ms)
        issues: dict[str, list[Any]] = {}
        for m in ms:
            on = {str(f["id"]) for f in inventory.files_of(self.data, m["matter_id"])}
            for n, path, why in sections.findings(
                m["number"], m, lambda fid, mid=m["matter_id"]: self._text_of(mid, fid), on
            ):
                issues.setdefault(why, []).append((n, path))
        unread = passes.unread_matters(self)
        if unread:
            issues["matter could not be read"] = [(u["number"], u["stage"]) for u in unread]
        dump(self.data / "gates.json", {"passed": not issues, "issues": issues})
        if issues:
            raise LitigationHold(
                "gate_refused: the gates refused the breakdown: "
                + "; ".join(f"{k} ({len(v)})" for k, v in issues.items())[:400]
            )

    def _parity(self) -> None:
        dump(self.data / "changes.json", {})

    def _out(self) -> Path:
        name = f"Discovery breakdown {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).xlsx"
        return self.data / "out" / name

    def _book(self) -> None:
        stats = book.build(self._json("final.json"), self._out(), self.today)
        gates.leak_gate(self._out(), self.data / "book.json", stats)

    def _review(self) -> None:
        built = self._json("book.json")
        problems = book.check(self._out().read_bytes(), self._json("final.json"))
        dump(self.data / "review.json", {"problems": problems, "rows": built.get("rows")})
        if problems:
            from ..outcome import LitigationFailed

            raise LitigationFailed(f"review_held: {len(problems)} findings in the built workbook; nothing was filed")
        self._put("review_status", "code_only")

    def _file(self) -> None:
        pause = deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause
        rec = deliver.file_workbook(
            self.seat, self.job.file_to_id, self.job.folder_name, self._out(), self.data, self.log, pause=pause
        )
        dump(self.data / "filed.json", rec)

    def _report(self) -> None:
        return None

    def _walk(self) -> Verdict:
        for name in STAGES:
            if name == "file" and proof_mode.active():
                out = self._out()
                return Verdict(
                    proof_mode.OUTCOME,
                    stage="file",
                    files=[{"role": ROLE, "name": out.name, "size": out.stat().st_size}],
                )
            self._stage(name, getattr(self, f"_{name}"))
            if name == "inventory" and not self._matters():
                raise LitigationHold("scope_empty: no open litigation matters are in this job's scope")
        f = self._json("filed.json")
        keep = ("name", "size", "sha256", "file_id")
        return Verdict(
            "delivered",
            stage="report",
            files=[{"role": ROLE, **{k: f[k] for k in keep}}],
            folder_id=f["folder_id"],
            proof={
                "readback": "confirmed",
                "files": [{k: f[k] for k in ("name", "file_id", "size", "sha256")}],
                "review": "code_only",
                "review_cents": 0,
                "review_model": "",
            },
        )
