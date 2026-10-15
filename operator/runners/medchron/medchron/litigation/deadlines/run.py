"""`medchron litigate <job_dir>` for a deadlines report: one job, resumable.

    pull -> book -> check -> file -> report

No model is called and nothing is metered: ``cents`` is 0 by construction.
The filing reuses the status list's ``deliver.file_workbook`` (intent row
before the send, a name this run did not send is refused, the far end
downloaded and its sha256 compared), once per workbook, each with its own
record directory so the two filings cannot share a delivery row.

``check`` is the pre-delivery review, code only (the output is data read
from the practice system, not prose): each built file re-read from its BYTES
must hold exactly the rows the build counted, sheet by sheet, and no cell may
carry an internal id; and the kit's falsifier must find no matter outside the
litigation test carrying a future LawToolBox date (a rule date the workbook
would leave out). Either failure holds the job before anything is filed.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import re
import traceback
from pathlib import Path
from typing import Any, Callable

from ... import proof_mode
from .. import deliver, firm as firm_mod, job as job_mod
from ..outcome import REASON, LitigationFailed, LitigationHold, Verdict
from ..tools import dump
from . import book, pull

STAGES = ("pull", "book", "check", "file", "report")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def reread_sheets(blob: bytes) -> dict[str, list[list[Any]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    try:
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)][1:] for ws in wb.worksheets}
    finally:
        wb.close()


def review(blob: bytes, expected: dict[str, int]) -> list[str]:
    """Findings for one built file: a sheet whose rows differ from the build's
    count, a missing or extra sheet, a cell carrying an internal id."""
    got = reread_sheets(blob)
    out = [
        f"sheet {t!r}: {len(got.get(t, []))} rows, built {n}" for t, n in expected.items() if len(got.get(t, [])) != n
    ]
    out += [f"sheet {t!r} was not built" for t in got if t not in expected]
    for t, rows in got.items():
        if any(isinstance(c, str) and _UUID.search(c) for r in rows for c in r):
            out.append(f"sheet {t!r} carries an internal id")
    return out


class DeadlinesRun:
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        seat_factory: Callable[[], Any] | None = None,
        log: Callable[[str], None] = print,
        today: Callable[[], dt.date] | None = None,
        readback_pause: float | None = None,
    ) -> None:
        self.job = job_mod.load(job_dir)
        if self.job.report != "deadlines":
            raise job_mod.LitigationJobError("not a deadlines report job")
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._seat_factory, self._seat = seat_factory, None
        self.today = self._frozen_today(today)
        self.readback_pause = readback_pause

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

    def _state(self) -> dict[str, Any]:
        p = self.data / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _put(self, key: str, value: Any) -> None:
        st = self._state()
        st[key] = value
        dump(self.data / "state.json", st)

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

    def _pulled(self) -> dict[str, Any]:
        with gzip.open(self.data / pull.PULL_NAME, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def rules(self, d: dict[str, Any]) -> book.Rules:
        return book.Rules(d["staff"], self.firm.court_rx, int(self.firm.get("min_court_hits")), self.today)

    def names(self) -> dict[str, str]:
        return book.file_names(self.today, self.job.job_id[-6:])

    # ---- stages -------------------------------------------------------------------
    def _pull(self) -> None:
        pull.run(self.seat, self.job, self.data, self.log)

    def _book(self) -> None:
        d = self._pulled()
        if not d["matters"]:
            raise LitigationHold("scope_empty: no Open or Pending matters are in this job's scope")
        built = book.build(d, self.rules(d), self.data / "out", self.names(), self.job.workbooks)
        dump(self.data / "book.json", {"sheets": built["sheets"], "counts": built["counts"],
                                        "files": {k: p.name for k, p in built["files"].items()}})  # fmt: skip

    def _check(self) -> None:
        d = self._pulled()
        built = json.loads((self.data / "book.json").read_text(encoding="utf-8"))
        findings: list[str] = []
        for key, name in built["files"].items():
            findings += [f"{key}: {f}" for f in review((self.data / "out" / name).read_bytes(), built["sheets"][key])]
        misses = book.coverage_misses(d["matters"], self.rules(d)) if "deadlines" in self.job.workbooks else []
        dump(self.data / "check.json", {"findings": findings, "coverage_misses": misses})
        if findings:
            raise LitigationFailed(f"review_held: {len(findings)} findings in the built workbooks; nothing was filed")
        if misses:
            raise LitigationHold(
                f"gate_refused: coverage: {len(misses)} matters outside the litigation test carry a future "
                "LawToolBox date; nothing was filed"
            )

    def _file(self) -> None:
        built = json.loads((self.data / "book.json").read_text(encoding="utf-8"))
        pause = deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause
        filed = {}
        for key in self.job.workbooks:
            rec_dir = self.data / f"file-{key}"
            rec_dir.mkdir(exist_ok=True)
            local = self.data / "out" / built["files"][key]
            filed[key] = deliver.file_workbook(
                self.seat, self.job.file_to_id, self.job.folder_name, local, rec_dir, self.log, pause=pause
            )
        dump(self.data / "filed.json", filed)

    def _walk(self) -> Verdict:
        for name, fn in zip(STAGES, (self._pull, self._book, self._check, self._file, lambda: None)):
            if name == "file" and proof_mode.active():  # a proof run stops here (proof_mode.py)
                return self._dry_run()
            if self._state().get(name, {}).get("status") != "done":
                self.log(f"[{name}] start")
                fn()
                self._put(name, {"status": "done"})
        return self._delivered()

    def _roles(self) -> dict[str, str]:
        """The file the status projection shows is role ``workbook``: the
        deadlines workbook when built, else the statute report."""
        first = self.job.workbooks[0]
        return {k: ("workbook" if k == first else k) for k in self.job.workbooks}

    def _dry_run(self) -> Verdict:
        built = json.loads((self.data / "book.json").read_text(encoding="utf-8"))
        files = [
            {"role": self._roles()[k], "name": n, "size": (self.data / "out" / n).stat().st_size}
            for k, n in built["files"].items()
        ]
        return Verdict(proof_mode.OUTCOME, stage="file", files=files, counts=built["counts"])

    def _delivered(self) -> Verdict:
        filed = json.loads((self.data / "filed.json").read_text(encoding="utf-8"))
        built = json.loads((self.data / "book.json").read_text(encoding="utf-8"))
        keep = ("name", "size", "sha256", "file_id")
        files = [{"role": self._roles()[k], **{x: filed[k][x] for x in keep}} for k in self.job.workbooks]
        return Verdict(
            "delivered",
            stage="report",
            files=files,
            folder_id=filed[self.job.workbooks[0]]["folder_id"],
            counts=built["counts"],
            proof={
                "readback": "confirmed",
                "files": [{x: f[x] for x in keep} for f in files],
                "review": "code_only",
                "review_cents": 0,
                "review_model": "",
            },
        )

    def reopen(self, stages: list[str]) -> None:
        """Reopen the named stages and every stage after the earliest (the
        resume's ``--redo``). Reopening ``pull`` re-reads every matter."""
        known = [x for x in stages if x in STAGES]
        if not known:
            return
        for x in STAGES[min(STAGES.index(k) for k in known) :]:
            self._put(x, {"status": "reopened"})
        if "pull" in known:
            import shutil

            shutil.rmtree(self.data / "pull", ignore_errors=True)

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "report")

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except LitigationHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except LitigationFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict("failed", stage=self._current(),
                        reason=f"unexpected: {type(exc).__name__} at {self._current()}: {str(exc)[:300]}")  # fmt: skip
        if v.reason and not REASON.match(v.reason):
            v.reason = f"unexpected: {v.reason}"
        v.cents = 0
        if (self.data / "book.json").is_file():
            c = json.loads((self.data / "book.json").read_text(encoding="utf-8"))["counts"]
            v.matters_total, v.matters_reread = int(c["matters_read"]), int(c["litigation"])
            v.counts = v.counts or c
        return v
