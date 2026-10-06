"""`medchron demand <job_dir>`: one demand job, pull to read-back, resumable.

The one committed run command the 2026-09-24 incident asked for (build item 3),
in place of a session re-assembling the steps from memory notes. Every stage
records itself in ``data/state.json`` when it finishes; a resume skips the
finished ones, and every paid stage also resumes from its own artifacts, so
a stop costs only the call in flight.

Outcomes, as the daemon records them in the demand ledger:

* ``delivered``  the files are on the matter and read back. Either the
  deliverables, or (premise failed) the coverage report.
* ``held``       a person must look: the drafting gate refused the letter, the
  format check refused the file, or a folder of the target name exists and is
  not this job's. Nothing was filed.
* ``failed``     resumable: a limit stopped it (the pre-paid estimate over the
  cap or the month's budget, or spend reaching either during a stage), the pull
  could not finish, or a call failed. Spent stages are kept for the resume.

Spend: the estimate is checked once after the free preflight, BEFORE anything is
paid, then every paid call checks the cap and the month's budget through the
doorway's hook (``limits.py``), so an overshoot is bounded to one call.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod
from ..ledger import Ledger
from ..llm import Doorway
from . import (
    deliver,
    draft,
    facts as facts_mod,
    firm as firm_mod,
    job as job_mod,
    preflight,
    premise,
    pull,
    summarize,
    transcribe,
)


class DemandHold(RuntimeError):
    """A person must act; nothing is filed."""


@dataclass
class Verdict:
    outcome: str
    stage: str | None = None
    reason: str | None = None
    dollars: float = 0.0
    documents: int = 0
    pages: int = 0
    folder_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    coverage_report: bool = False
    notes: list[str] = field(default_factory=list)

    def to_list(self) -> list[dict[str, Any]]:
        d = dict(self.__dict__)
        d["unit"] = "demand"
        d["kind"] = "demand"
        return [d]


class DemandRun:
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        pricing: str | None = None,
        seat_factory: Callable[[], Any] | None = None,
        client: Any = None,
        log: Callable[[str], None] = print,
        today: Callable[[], time.struct_time] = time.localtime,
        readback_pause: float | None = None,
    ) -> None:
        self.job = job_mod.load(job_dir)
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.pricing = budget_mod.Pricing.load(
            Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        )
        b = self.firm.data["budget"]
        self.budget = budget_mod.Budget(
            self.pricing,
            float(b["per_job_cap_usd"]),
            [self.data / "usage-ledger.jsonl"],
            float(b["usd_per_million_chars"]),
        )
        self.limits = limits_mod.Limits(
            cap_usd=float(b["per_job_cap_usd"]),
            monthly_budget_usd=float(b["monthly_budget_usd"]),
            usd_per_scanned_page=float(b["usd_per_scanned_page"]),
            usd_per_audit_claim=0.0,
            month_cents_used=self.job.month_cents_used,
        )
        self.doorway = Doorway(
            ledger=Ledger(self.data / "usage-ledger.jsonl"),
            client=client,
            log=log,
            before_request=lambda stage: self.limits.check_each_call(self.budget.refresh(), stage),
        )
        self._seat_factory = seat_factory
        self._seat: Any = None
        t = today()
        self.date_stamp = time.strftime("%m-%d-%y", t)
        self.long_date = time.strftime("%B ", t) + str(t.tm_mday) + time.strftime(", %Y", t)
        self.readback_pause = readback_pause

    # ---- plumbing ------------------------------------------------------------------
    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is None:
                from ..seat import open_seat

                self._seat = open_seat(self.job.slug)
            else:
                self._seat = self._seat_factory()
        return self._seat

    def _state(self) -> dict[str, Any]:
        p = self.data / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _done(self, stage: str, **info: Any) -> None:
        st = self._state()
        st[stage] = {"status": "done", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **info}
        tmp = self.data / "state.json.tmp"
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(self.data / "state.json")

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _stage(self, name: str, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` unless the state file says it finished; log its wall time."""
        if self._state().get(name, {}).get("status") == "done":
            return None
        t0 = time.time()
        self.log(f"[{name}] start")
        out = fn()
        secs = round(time.time() - t0, 1)
        self._done(name, seconds=secs)
        self.log(f"[{name}] done in {secs}s")
        return out

    # ---- the stages ------------------------------------------------------------------
    def _facts(self) -> None:
        f = facts_mod.read(self.seat, self.job.matter_id)
        (self.data / "facts.json").write_text(json.dumps(f, indent=1), encoding="utf-8")

    def _pull(self) -> None:
        f = self._json("facts.json")
        pull.run(self.seat, self.job.matter_id, self.firm, set(f.get("client_emails") or []), self.data, self.log)

    def _estimate(self) -> None:
        est = self._json("preflight.json")["estimate"]
        try:
            self.limits.check_before_paid(
                projected_usd=float(est["usd"]), spent_usd=self.budget.refresh(), stage="the first paid stage"
            )
        except limits_mod.LimitHold as hold:
            raise limits_mod.LimitHold(
                hold.setting,
                f"{hold.reason} (estimate before anything was paid: {est['pages']:,} pages, "
                f"{est['characters']:,} characters, {est['transcription_pages']:,} pages to transcribe, "
                f"{est['usd']:.2f} USD)",
            ) from None

    def _digest(self) -> str:
        p = self.data / "digest.md"
        if p.is_file():
            return p.read_text(encoding="utf-8")
        files = summarize.corpus_files(self.data)
        chunks = summarize.build_chunks(files, int(self.firm.get("levers", "chunk_chars")))
        s = summarize.Summarizer(
            self.data,
            self.doorway,
            self.firm.model("digest"),
            self.firm.text("prompt_digest"),
            int(self.firm.get("levers", "digest_max_tokens")),
            self._workers,
            self.log,
        )
        digests = s.run(chunks)
        out = summarize.condense(
            digests,
            int(self.firm.get("levers", "digest_budget_chars")),
            self.doorway,
            self.firm.model("digest"),
            self.firm.text("prompt_condense"),
            self._workers,
            self.log,
        )
        p.write_text(out, encoding="utf-8")
        return out

    @property
    def _workers(self) -> int:
        return int(self.firm.get("levers", "concurrency"))

    def _drafter(self) -> draft.Drafter:
        decision = self._json("premise.json")
        f = self._json("facts.json")
        fields = [
            f"matter field (Smokeball): {label} = {f.get(key)}"
            for key, label in (
                ("insurer", "other side's insurer"),
                ("date_of_loss", "date of loss"),
                ("signer", "responsible attorney, the default signer"),
            )
            if f.get(key)
        ]
        fields.append(f"today's date, the drafting date: {self.long_date}")
        return draft.Drafter(
            self.data, self.doorway, self.firm, self.job.request_text, decision["premise_facts"] + fields, self.log
        )

    def _corpus_text(self) -> str:
        return "\n".join(
            Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            for r in summarize.corpus_files(self.data)
        )

    def _write_deliverables(self) -> list[Path]:
        """Render everything that will be filed; refuse before filing on a
        gate or format failure."""
        out = deliver.out_dir(self.data)
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        author = self.firm.get("firm", "display_name")
        files: list[Path] = []
        if self.job.wants("gap_audit"):
            files.append(
                deliver.render_plain(
                    (self.data / "gap-audit.md").read_text(encoding="utf-8"), out, nm["gap_audit"], author
                )
            )
        if self.job.wants("demand"):
            v2 = (self.data / "draft-v2.md").read_text(encoding="utf-8")
            g = self._json("gate.json")
            if not g["passed"]:
                raise DemandHold(
                    f"the drafting gate refused the letter ({g['disposition']}): " + "; ".join(g["refusals"])[:400]
                )
            try:
                path, notes, fmt_notes = deliver.render_demand(self.firm, v2, out)
            except deliver.FormatRefused as exc:
                raise DemandHold("the format check refused the demand file: " + "; ".join(exc.fails)[:400]) from None
            files.append(path)
            extra = "\n".join(f"- {w}" for w in g["warnings"])
            notes_md = f"# Attorney notes: {path.stem}\n\n{notes}\n" + (
                f"\n## Drafting gate warnings\n\n{extra}\n" if extra else ""
            )
            files.append(deliver.render_plain(notes_md, out, f"{path.stem} - attorney notes.docx", author))
            self._done("format", notes=fmt_notes)
        return files

    def _file(self, files: list[Path], coverage: bool) -> Verdict:
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        out = deliver.out_dir(self.data)
        manifest = deliver.write_manifest(out, nm["folder"], files)
        kw = {} if self.readback_pause is None else {"pause": self.readback_pause}
        rec = deliver.file_to_matter(self.data, self.seat, self.job.file_to_id, self.log, **kw)
        if rec["exit"] == 1:
            raise DemandHold(
                "filing refused: a folder of the delivery name exists on the matter and this job did not create it"
            )
        if rec["exit"] != 0:
            raise RuntimeError("the read-back is short after its retries; the files may still be materializing")
        return Verdict(
            "delivered",
            stage="file",
            folder_id=str(rec.get("folder_id")),
            coverage_report=coverage,
            files=[{"name": m["name"], "size": m["bytes"]} for m in manifest],
        )

    def _coverage(self, md: str) -> Verdict:
        out = deliver.out_dir(self.data)
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        p = deliver.render_plain(md, out, nm["coverage"], self.firm.get("firm", "display_name"))
        return self._file([p], coverage=True)

    def _paid(self) -> Verdict:
        self._stage(
            "transcribe",
            lambda: transcribe.run(self.data, self.doorway, self.firm.model("transcription"), self.log, self._workers),
        )
        digest = self._digest()
        self._done("summarize")
        d = self._drafter()
        pre = self._json("preflight.json")
        walled = len(self._json("walled.json"))
        machine = self._json("transcribed.json") if (self.data / "transcribed.json").is_file() else []
        gap = d.gap_audit(digest, pre, walled, machine) if self.job.wants("gap_audit") else None
        if self.job.wants("demand"):
            v1 = d.compose(digest, gap)
            if v1.lstrip().startswith(draft.COVERAGE_SENTINEL):
                return self._coverage(v1.lstrip()[len(draft.COVERAGE_SENTINEL) :])
            corpus = self._corpus_text()
            d.audit(1, digest, corpus)
            d.repair(digest)
            d.audit(2, digest, corpus)
            from . import gate

            self._stage(
                "gate", lambda: gate.run(self.data, self.firm, (self.data / "draft-v2.md").read_text(encoding="utf-8"))
            )
        return self._file(self._write_deliverables(), coverage=False)

    def _walk(self) -> Verdict:
        self._stage("facts", self._facts)
        self._stage("pull", self._pull)
        self._stage("preflight", lambda: preflight.run(self.data, self.firm, self._json("facts.json"), self.log))
        self._estimate()
        prem = self.firm.data["premise"]
        self._stage("premise", lambda: premise.run(self.data, self.job, self._json("facts.json"), prem, self.long_date))
        if not self._json("premise.json")["passed"]:
            return self._coverage((self.data / "coverage-report.md").read_text(encoding="utf-8"))
        return self._paid()

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=hold.setting, reason=hold.reason)
        except DemandHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict("failed", stage=self._current(), reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        v.dollars = round(self.budget.refresh(), 4)
        pf = self.data / "preflight.json"
        if pf.is_file():
            e = self._json("preflight.json")
            v.documents, v.pages = int(e["documents"]), int(e["estimate"]["pages"])
        return v

    def _current(self) -> str:
        order = ("facts", "pull", "preflight", "premise", "transcribe", "summarize", "gate", "format")
        st = self._state()
        return next((s for s in order if st.get(s, {}).get("status") != "done"), "file")
