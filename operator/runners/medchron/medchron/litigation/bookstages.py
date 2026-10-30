"""The list's build, review and filing stages of ``LitigationRun`` (``run.py``),
split out 2026-10-29 when the court-dates merge and the printable PDF took the
run past the module ceiling. A mixin: every attribute it reads (``data``,
``job``, ``today``, ``seat``, ``log``, ``readback_pause``, ``_json``,
``_matters``, ``_court_only``) is the run's own.

    book    the list, its one-sheet-per-case copy and the court-dates PDF, all
            from ``shown.json``: the final values with the calendar's court
            dates merged in (``courtdates.py``); the state keeps the papers'.
    review  each built file re-read from its bytes (``review.py``).
    file    the list, then the per-case copy, then the PDF, each read back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import book, courtdates, deliver, gates, percase, printout, review as review_mod
from .outcome import LitigationFailed
from .tools import dump

#: The verdict's role for the one-sheet-per-case copy (``percase.py``).
PER_CASE_ROLE = "per_case_workbook"
#: The verdict's role for the printable court-dates PDF (``printout.py``).
PRINTOUT_ROLE = "printout_pdf"
#: The PDF's discovery heading note on a court-only list, whose discovery
#: values were not read this run (``book.build(discovery=False)``).
DISCOVERY_AS_LAST_READ = "Discovery is shown as last read; it was not read again for this list."


class BookStages:
    # The run's own attributes and helpers (``run.py``, ``runbase.py``).
    data: Path
    job: Any
    today: Any
    seat: Any
    log: Any
    readback_pause: Any
    _json: Any
    _matters: Any
    _court_only: Any
    _put: Any

    def _out(self) -> Path:
        return self.data / "out" / f"Litigation status {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).xlsx"

    def _make_shown(self) -> list[dict[str, Any]]:
        """The values the list SHOWS: the final values with the calendar's
        court dates merged in (``courtdates.py``; a $0 read, resumable). The
        state keeps the papers' values; only the files carry the merge."""
        finals = self._json("final.json")
        try:
            seat = self.seat
        except Exception as exc:  # noqa: BLE001 - an offline replay has no seat: every case shows "not read"
            self.log(f"[calendar] no seat to read the calendar from ({type(exc).__name__})")
            seat = None
        cal = courtdates.pull(seat, [str(m["matter_id"]) for m in finals], self.data, self.log)
        shown = courtdates.shown(finals, cal, self.today)
        dump(self.data / "shown.json", shown)
        return shown

    def _shown(self) -> list[dict[str, Any]]:
        """shown.json, or final.json for a job whose book stage ran before the
        merge existed (its files were built from the final values)."""
        return self._json("shown.json" if (self.data / "shown.json").is_file() else "final.json")

    def _book(self) -> None:
        shown = self._make_shown()
        stats = book.build(
            shown,
            self._json("changes.json"),
            self._out(),
            self.today,
            discovery=not self._court_only(),
            tagged=self._tagged(),
        )
        gates.leak_gate(self._out(), self.data / "book.json", stats)
        self._build_per_case()
        self._build_printout()

    def _per_case_out(self) -> Path:
        return percase.path_for(self._out())

    def _printout_out(self) -> Path:
        return printout.path_for(self._out())

    def _build_per_case(self) -> None:
        """The one-sheet-per-case copy (``percase.py``), from the same shown
        values, under the same leak gate as the list."""
        stats = percase.build_per_case(self._shown(), self._per_case_out(), self.today, tagged=self._tagged())
        gates.leak_gate(self._per_case_out(), self.data / "book-percase.json", stats)

    def _build_printout(self) -> None:
        """The printable PDF (``printout.py``) from the same shown values; a
        leak in its text deletes it and fails the run, as for the workbooks."""
        note = DISCOVERY_AS_LAST_READ if self._court_only() else ""
        stats = printout.build(self._shown(), self._printout_out(), self.today, discovery_note=note)
        leaks = printout.scan(self._printout_out())
        dump(self.data / "book-printout.json", {**stats, "leaks": leaks})
        if leaks:
            self._printout_out().unlink(missing_ok=True)
            raise LitigationFailed(
                f"leak_found: the built print-out carries internal text in {len(leaks)} places: "
                + "; ".join(f"{c} {w}" for c, w, _ in leaks)[:300]
            )

    def _tagged(self) -> tuple[list[str], list[list[Any]]] | None:
        """A people scope's "Litigated files tagged on" (header, rows), built
        from the inventory's people and roles and the final values; None for
        every other scope."""
        if self.job.people is None:
            return None
        who = list(self._json("inventory.json").get("people") or [])
        return book.tagged_header(len(who) > 1), book.tagged_rows(self._json("final.json"), self._matters(), who)

    def _ensure_per_case(self) -> None:
        """A job whose book stage ran before the per-case copy (or the
        print-out) existed builds it on resume, through the same leak gate."""
        if self._out().is_file() and not self._per_case_out().is_file():
            self._build_per_case()
        if self._out().is_file() and not self._printout_out().is_file():
            self._build_printout()

    def _review(self) -> None:
        self._ensure_per_case()
        args = (self.data, self.job.job_dir, self._out(), self._shown(), self._matters())
        status = review_mod.run(
            *args, tagged=self._tagged(), per_case=self._per_case_out(), printout=self._printout_out()
        )
        self._put("review_status", status)

    def _file(self) -> None:
        pause = deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause
        args = (self.seat, self.job.file_to_id, self.job.folder_name)
        rec = deliver.file_workbook(*args, self._out(), self.data, self.log, pause=pause)
        dump(self.data / "filed.json", rec)
        # The per-case copy only after the list itself is filed and read back:
        # a held or refused list files neither.
        self._ensure_per_case()
        rec = deliver.file_workbook(
            *args, self._per_case_out(), self.data, self.log, pause=pause, record="delivery-percase.json"
        )
        dump(self.data / "filed-percase.json", rec)
        rec = deliver.file_workbook(
            *args, self._printout_out(), self.data, self.log, pause=pause, record="delivery-printout.json"
        )
        dump(self.data / "filed-printout.json", rec)

    def _filed(self) -> list[tuple[str, dict[str, Any]]]:
        """(role, filed record) for every file this job filed; a job filed
        before the print-out existed has none for it."""
        out = [("workbook", self._json("filed.json")), (PER_CASE_ROLE, self._json("filed-percase.json"))]
        if (self.data / "filed-printout.json").is_file():
            out.append((PRINTOUT_ROLE, self._json("filed-printout.json")))
        return out
