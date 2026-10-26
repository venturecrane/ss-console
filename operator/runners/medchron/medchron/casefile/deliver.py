"""The code half of a case file job after the reads: rank, propose, book, file,
workbook, deliver, report. Split from ``run.py`` (the stage walk) to keep each
module under the size ceiling.

Filing reuses the litigation lane's proven filing (``litigation/deliver.py``):
a standing folder found or created, an intent row before ``add_file``, a name
already in the folder that this run did not send refused, and the read-back a
download compared by sha256. Each File Status files into its own matter's
folder; a matter whose filing is refused keeps its review in the workbook and
is not committed to the lane's state, so the next job reads it again.

Every write is journaled (``own_writes``) as it is proven, so a later job can
tell the Operator's own activity from the firm's.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import proof_mode
from ..litigation import deliver as lit_deliver, fetch as fetch_mod
from ..litigation.outcome import LitigationFailed, LitigationHold
from ..litigation.tools import dump
from . import book, matter as matter_mod, own_writes, propose, rank as rank_mod, state as state_mod, workbook
from .inventory import inv_of
from .outcome import CasefileFailed, CasefileHold, Verdict

STATUS_READ = "Read this run"


class DeliverMixin:
    data: Path
    state: Path
    firm: Any
    job: Any
    log: Any
    today: dt.date
    readback_pause: float | None

    seat: Any

    if TYPE_CHECKING:  # provided by the run (``run.CasefileRun`` / ``RunBase``); never defined here at runtime

        def _matters(self) -> list[dict[str, Any]]: ...
        def _plan(self) -> dict[str, Any]: ...
        def _json(self, name: str) -> Any: ...
        def _mfile(self, mid: str, name: str) -> Path: ...
        def prior(self, mid: str) -> dict[str, Any] | None: ...
        def _library(self) -> str: ...

    # ---- rows --------------------------------------------------------------------
    def _row(self, m: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
        mid = m["id"]
        if plan["mode"] == "current":
            prior = self.prior(mid) or {}
            when = str(prior.get("delivered_at") or "")[:10]
            return {
                "review": prior.get("review") or {},
                "fresh": False,
                "status_word": f"Already current (nothing new since {when})",
            }
        got = matter_mod.load_review(self, mid)
        if got is None:
            return None
        word = STATUS_READ if plan["mode"] == "whole" else "Updated from what changed since the last review"
        return {**got, "fresh": True, "status_word": word}

    def _rows_live(self) -> list[dict[str, Any]]:
        plan, out = self._plan(), []
        for m in self._matters():
            r = self._row(m, plan[m["id"]]) if plan[m["id"]]["mode"] != "held" else None
            if r is not None:
                out.append({**r, "matter_id": m["id"], "number": m["number"], "responsible": m.get("responsible")})
        return out

    def _held_list(self) -> list[dict[str, Any]]:
        out = []
        for m in self._matters():
            p = self._mfile(m["id"], matter_mod.HELD)
            if p.is_file():
                out.append({"number": m["number"], "why": json.loads(p.read_text(encoding="utf-8")).get("why")})
        return out

    def _text_of(self, mid: str) -> Any:
        return lambda fid: fetch_mod.cached_text(self.data, mid, fid)

    # ---- rank and propose ------------------------------------------------------------
    def _rank(self) -> None:
        own = state_mod.own_ids(self.state)
        rows = []
        for r in self._rows_live():
            mid = r["matter_id"]
            inv = inv_of(self.data, mid)
            lines: list[dict[str, Any]] = []
            if r["fresh"]:
                meta = self._json_m(mid, "meta.json")
                lines = propose.task_lines(r["review"], inv, meta, r.get("verdicts") or {})
                med, skipped = propose.medicals_lines(r["review"], inv, meta, self.firm, self._text_of(mid))
                lines += med
                dump(self._mfile(mid, "lines.json"), {"lines": lines, "medicals_skipped": skipped})
            r["rank"] = rank_mod.rank(r["review"], inv, self.firm, self.today, own, len(lines))
            rows.append(r)
        rows.sort(key=rank_mod.order_key)
        dump(self.data / "rows.json", rows)

    def _json_m(self, mid: str, name: str) -> Any:
        p = self._mfile(mid, name)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _propose(self) -> None:
        per = [
            (r["matter_id"], (self._json_m(r["matter_id"], "lines.json") or {}).get("lines") or [])
            for r in self._json("rows.json")
        ]
        lines = propose.number_lines(per)
        state_mod.save_proposals(self.state, self.job.job_id, {str(ln["n"]): ln for ln in lines})
        dump(self.data / "lines.json", lines)

    def _plan_for(self, mid: str, lines: list[dict[str, Any]], verdicts: dict[str, Any]) -> dict[str, Any]:
        mine = [ln for ln in lines if ln["matter_id"] == mid]
        return {
            "tasks": {ln["source_id"]: ln["n"] for ln in mine if ln["kind"] == "task"},
            "medicals": {ln["row_index"]: ln["n"] for ln in mine if ln["kind"] == "medicals"},
            "verdicts": verdicts,
        }

    # ---- book and file ---------------------------------------------------------------
    def _pdf(self, r: dict[str, Any]) -> Path:
        return self.data / "out" / r["matter_id"] / book.file_status_name(str(r["number"]), self.today.isoformat())

    def _book(self) -> None:
        lines = self._json("lines.json")
        for r in self._json("rows.json"):
            if not r["fresh"] or self._pdf(r).is_file():
                continue
            meta = self._json_m(r["matter_id"], "meta.json")
            html = book.render_html(
                r["review"],
                self.today.isoformat(),
                meta,
                r["rank"],
                self._plan_for(r["matter_id"], lines, r.get("verdicts") or {}),
            )
            out = self._pdf(r)
            out.parent.mkdir(parents=True, exist_ok=True)
            src = out.with_suffix(".html")
            src.write_text(html, encoding="utf-8")
            book.print_pdf(src, out)

    def _pause(self) -> float:
        return lit_deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause

    def _file(self) -> None:
        folder = str(self.firm.get("file_status_folder"))
        for r in self._json("rows.json"):
            mid = r["matter_id"]
            if not r["fresh"] or self._mfile(mid, "filed.json").is_file():
                continue
            try:
                rec = lit_deliver.file_workbook(
                    self.seat,
                    mid,
                    folder,
                    self._pdf(r),
                    fetch_mod.matter_dir(self.data, mid),
                    self.log,
                    pause=self._pause(),
                )
            except LitigationHold as exc:
                dump(self._mfile(mid, "filed.json"), {"refused": str(exc)[:300]})
                continue
            except LitigationFailed as exc:
                raise CasefileFailed(str(exc)) from exc
            own_writes.record(self.state, self.job.job_id, mid, "folder", rec["folder_id"])
            own_writes.record(self.state, self.job.job_id, mid, "file", rec["file_id"])
            dump(self._mfile(mid, "filed.json"), rec)

    # ---- the caseload workbook -------------------------------------------------------
    def _xlsx(self) -> Path:
        return self.data / "out" / f"Case File Manager {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).xlsx"

    def _workbook(self) -> None:
        lines = self._json("lines.json")
        rows = self._json("rows.json")
        counts = workbook.build(self._xlsx(), rows, lines, self._held_list())
        dump(self.data / "workbook.json", counts)

    def _deliver(self) -> None:
        wd = self.data / "workbook-delivery"
        wd.mkdir(parents=True, exist_ok=True)
        try:
            rec = lit_deliver.file_workbook(
                self.seat, self._library(), self.job.folder_name, self._xlsx(), wd, self.log, pause=self._pause()
            )
        except LitigationHold as exc:
            raise CasefileHold(str(exc)) from exc
        except LitigationFailed as exc:
            raise CasefileFailed(str(exc)) from exc
        own_writes.record(self.state, self.job.job_id, self._library(), "file", rec["file_id"])
        dump(self.data / "delivered.json", rec)

    # ---- report ---------------------------------------------------------------------
    def _report(self) -> None:
        plan = self._plan()
        for r in self._json("rows.json"):
            mid = r["matter_id"]
            filed = self._json_m(mid, "filed.json")
            if not r["fresh"] or not filed.get("file_id"):
                continue
            inv = inv_of(self.data, mid)
            state_mod.commit(
                self.state,
                mid,
                {
                    "review": r["review"],
                    "rank": r["rank"],
                    "fingerprint": state_mod.fingerprint(inv, state_mod.own_ids(self.state)),
                    "manifest": {
                        str(f["id"]): str(f.get("modified") or f.get("created") or "")
                        for f in inv["files"]
                        if f.get("id")
                    },
                    "job_id": self.job.job_id,
                    "delivered_at": self.today.isoformat(),
                    "read": plan[mid]["mode"],
                    "file_status": filed["file_id"],
                },
            )

    def _filed_so_far(self) -> list[dict[str, Any]]:
        """What a held job already filed, read back: the broker's held wording
        is chosen from it (a File Status in a matter is not "nothing new")."""
        return self._filed() if (self.data / "rows.json").is_file() else []

    def _filed(self) -> list[dict[str, Any]]:
        out = []
        for r in self._json("rows.json"):
            f = self._json_m(r["matter_id"], "filed.json")
            if f.get("file_id"):
                out.append(
                    {
                        "role": "file_status",
                        "name": f["name"],
                        "size": f["size"],
                        "sha256": f["sha256"],
                        "file_id": f["file_id"],
                    }
                )
        return out

    def _dry_run(self) -> Verdict:
        files = [
            {"role": "file_status", "name": self._pdf(r).name, "size": self._pdf(r).stat().st_size}
            for r in self._json("rows.json")
            if r["fresh"]
        ]
        lines = self._json("lines.json")
        return Verdict(proof_mode.OUTCOME, stage="file", files=files, lines_proposed=len(lines))

    def _delivered(self) -> Verdict:
        wb = self._json("delivered.json")
        lines = self._json("lines.json")
        files = [
            {
                "role": "workbook",
                "name": wb["name"],
                "size": wb["size"],
                "sha256": wb["sha256"],
                "file_id": wb["file_id"],
            },
            *self._filed(),
        ]
        return Verdict(
            "delivered",
            stage="report",
            files=files,
            folder_id=wb["folder_id"],
            lines=propose.broker_view(lines),
            lines_proposed=len(lines),
            proof={
                "readback": "confirmed",
                "files": [{k: f[k] for k in ("name", "file_id", "size", "sha256")} for f in files],
                "review": "code_only",
                "review_cents": 0,
                "review_model": "",
            },
        )


__all__ = ["DeliverMixin"]
