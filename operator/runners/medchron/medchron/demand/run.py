"""`medchron demand <job_dir>`: one demand job, pull to read-back, resumable.

The one committed run command the 2026-09-24 incident asked for (build item 3),
in place of a session re-assembling the steps from memory notes. Every stage
records itself in ``data/state.json`` when it finishes; a resume skips the
finished ones, and every paid stage also resumes from its own artifacts, so
a stop costs only the call in flight. The run's dates are frozen in the state
file on the first attempt, and the files are rendered ONCE (a recorded stage
whose manifest a resume reuses), so a resume after a short read-back can never
send a second, differently-dated copy onto the firm's matter.

Outcomes, as the daemon records them in the demand ledger:

* ``delivered``  the files are on the matter and read back. Either the
  deliverables, or (premise failed) the coverage report.
* ``held``       a person must look, and nothing was filed: the matter record
  could not be read, the destination is not the matter (nor an authored
  rehearsal matter), the letter still carries invented or miscalculated facts
  after two repairs, its RE block contradicts the matter record, the drafting
  gate or the format check refused, or the upload refused. A held job is
  FINAL: the ledger has no held -> running edge, so a person reads the reason
  and asks again.
* ``failed``     resumable: a limit stopped it (the pre-paid estimate over the
  cap or the month's budget, or spend reaching either during a stage), the pull
  could not finish, a call failed, or the read-back was short.

Spend: the estimate is checked after the free preflight, BEFORE anything is
paid (on a resume, against what is left to spend), then every paid call checks
the cap and the month's budget through the doorway's hook (``limits.py``).
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
    crosscheck,
    deliver,
    draft,
    facts as facts_mod,
    finalpass,
    firm as firm_mod,
    gate,
    job as job_mod,
    preflight,
    premise,
    pull,
    quotefix,
    summarize,
    transcribe,
    vendor,
)

MAX_REPAIRS = 2
FINAL = "draft-final.md"  # the letter after the final audit pass; what is gated and rendered


def _vendor_from_env() -> Any:
    from smokeball_connector.records_vendor import client_from_env

    return client_from_env()


class GateQuotesLeft(RuntimeError):
    """Quote findings left after the deterministic repair: recorded failed, so a
    resume re-runs only repair and gate (the paid stages are on disk)."""


class DemandHold(RuntimeError):
    """Something about the FILE or the REQUEST that a person at the firm must
    decide (premise, variant, an unmapped attorney, a letter that contradicts
    the matter record, privileged text the wall caught): ``held``, final, and
    the firm is told. Nothing is filed."""


class DemandFailed(RuntimeError):
    """Something about OUR machinery (a read that did not complete, a draft
    the audit loop could not clean, a gate or format refusal of our own
    output): ``failed``, resumable, no client message, SMD
    alerted (ss#3085). A held job cannot resume, so a machinery fault must
    never hold."""


#: The drafting gate's privilege-and-wall gate. A refusal there means text from
#: behind the wall reached the letter: the attorney must see it, so it holds.
WALL_GATE = "[1]"


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
        vendor_factory: Callable[[], Any] | None = None,
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
        self.date_stamp, self.long_date = self._frozen_dates(today())
        self.readback_pause = readback_pause
        self._repairs: dict[str, list[str]] = {}
        self.vendor_factory = vendor_factory or _vendor_from_env

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

    def _put(self, key: str, value: Any) -> None:
        st = self._state()
        st[key] = value
        tmp = self.data / "state.json.tmp"
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(self.data / "state.json")

    def _done(self, stage: str, **info: Any) -> None:
        self._put(stage, {"status": "done", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **info})

    def _is_done(self, stage: str) -> bool:
        return self._state().get(stage, {}).get("status") == "done"

    def _frozen_dates(self, t: time.struct_time) -> tuple[str, str]:
        """The first attempt's dates, forever: a resume on another day must not
        rename the folder or the files it already sent (review of #3074)."""
        dates = self._state().get("dates")
        if not dates:
            dates = {
                "stamp": time.strftime("%m-%d-%y", t),
                "long": f"{time.strftime('%B', t)} {t.tm_mday}, {t.tm_year}",
            }
            self._put("dates", dates)
        return str(dates["stamp"]), str(dates["long"])

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _stage(self, name: str, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` unless the state file says it finished; log its wall time."""
        if self._is_done(name):
            return None
        t0 = time.time()
        self.log(f"[{name}] start")
        out = fn()
        secs = round(time.time() - t0, 1)
        self._done(name, seconds=secs)
        self.log(f"[{name}] done in {secs}s")
        return out

    @property
    def _workers(self) -> int:
        return int(self.firm.get("levers", "concurrency"))

    @property
    def _author(self) -> str:
        return str(self.firm.get("firm", "display_name"))

    # ---- free stages -----------------------------------------------------------------
    def _facts(self) -> None:
        f = facts_mod.read(self.seat, self.job.matter_id)
        (self.data / "facts.json").write_text(json.dumps(f, indent=1), encoding="utf-8")
        self._check_facts()  # before the stage is marked done, so a resume reads the matter again

    def _check_facts(self) -> None:
        errs = facts_mod.blocking_errors(self._json("facts.json"))
        if errs:
            raise DemandFailed(
                "the matter record could not be read in full, so the privilege wall and the "
                "letter's names cannot be checked: " + "; ".join(errs)[:400]
            )

    def _destination(self) -> None:
        """The matter read is the matter asked for, and the matter filed to is
        either it or an authored rehearsal matter, each by number read by id."""
        job = self.job
        if facts_mod.matter_number(self.seat, job.matter_id) != job.matter_number:
            raise DemandHold(
                f"the matter id does not carry matter number {job.matter_number}; nothing was read or filed"
            )
        if job.file_to_id == job.matter_id:
            return
        if job.file_to_number not in set(self.firm.get("delivery", "rehearsal_matters")):
            raise DemandHold(
                f"the job files to matter {job.file_to_number}, which is neither the matter it reads "
                "nor an authored rehearsal matter; refused"
            )
        if facts_mod.matter_number(self.seat, job.file_to_id) != job.file_to_number:
            raise DemandHold(f"the filing destination's id does not carry matter number {job.file_to_number}; refused")

    def _pull(self) -> None:
        f = self._json("facts.json")
        pull.run(self.seat, self.job.matter_id, self.firm, set(f.get("client_emails") or []), self.data, self.log)

    def _estimate(self) -> None:
        if self.job.allowance_remaining is not None and self.job.allowance_remaining <= 0 and not self.budget.refresh():
            raise limits_mod.LimitHold(
                "demand_allowance_per_cycle",
                "demand_allowance_per_cycle: this cycle's demands are used, counting the ones in progress; "
                "nothing was spent",
            )
        est = self._json("preflight.json")["estimate"]
        spent = self.budget.refresh()
        try:
            self.limits.check_before_paid(
                projected_usd=max(0.0, float(est["usd"]) - spent), spent_usd=spent, stage="the first paid stage"
            )
        except limits_mod.LimitHold as hold:
            raise limits_mod.LimitHold(
                hold.setting,
                f"{hold.reason} (estimate before anything was paid: {est['pages']:,} pages, "
                f"{est['characters']:,} characters, {est['transcription_pages']:,} pages to transcribe, "
                f"{est['usd']:.2f} USD)",
            ) from None

    # ---- paid stages -----------------------------------------------------------------
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

    def _matter_fields(self) -> list[str]:
        f = self._json("facts.json")
        fields = [
            f"{label}: {f.get(key)}"
            for key, label in (
                ("client_name", "client"),
                ("matter_number", "matter number"),
                ("insurer", "other side's insurer"),
                ("date_of_loss", "date of loss"),
                ("signer", "responsible attorney, the default signer"),
            )
            if f.get(key)
        ]
        return [*fields, f"today's date, the drafting date: {self.long_date}"]

    def _drafter(self) -> draft.Drafter:
        decision = self._json("premise.json")
        return draft.Drafter(
            self.data,
            self.doorway,
            self.firm,
            self.job.request_text,
            decision["premise_facts"],
            self.log,
            matter_fields=self._matter_fields(),
        )

    def _corpus_text(self) -> str:
        return "\n".join(
            Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            for r in summarize.corpus_files(self.data)
        )

    def _audit_loop(self, d: draft.Drafter, digest: str) -> tuple[int, dict[str, Any]]:
        """Audit, repair, re-audit, up to MAX_REPAIRS times while the letter
        still carries invented or miscalculated facts or an unfound quotation.
        Returns the final version and its audit."""
        corpus = self._corpus_text()
        version = 1
        result = d.audit(version, digest, corpus)
        while draft.blocking_findings(result) and version <= MAX_REPAIRS:
            d.repair(digest, version)
            version += 1
            result = d.audit(version, digest, corpus)
        return version, result

    def _demand_checks(self, version: int, result: dict[str, Any]) -> list[str]:
        """Every check between the final audit and the render. Findings the
        repairs left are settled in code (finalpass.py: an invented statement
        removed, a miscalculated figure flagged), never filed and never a stop;
        holds only on what a person at the firm must decide. Returns what the
        attorney notes must carry."""
        md, settled = self._final_pass(version)
        cc = crosscheck.check(md, self._json("facts.json"))
        if cc["mismatches"]:
            raise DemandHold("the letter's RE block contradicts the matter record: " + "; ".join(cc["mismatches"]))
        g, md = self._gate("gate.json", md, self.data / FINAL)
        audit_md = (self.data / f"audit-v{version}.md").read_text(encoding="utf-8")
        notes = [
            f"Left for the attorney by the auditor (DRIFTS): {x}" for x in draft.finding_lines(audit_md, ("DRIFTS",))
        ]
        notes += [f"Removed or flagged by the final audit: {x}" for x in settled]
        notes += [f"Not checked against the matter record: {x}" for x in cc["unchecked"]]
        notes += [f"Held out, never read (privilege wall: {w['reason']}): {w['name']}" for w in self._walled()]
        notes += [f"Drafting gate repair (the letter): {x}" for x in g.get("repairs") or []]
        return notes + [f"Drafting gate: {w}" for w in g["warnings"]]

    def _final_pass(self, version: int) -> tuple[str, list[str]]:
        """``draft-final.md`` from the last draft and its audit, once: a resume
        (or the gate's quote repair, which rewrites it) keeps the same file."""
        out, rec = self.data / FINAL, self.data / "final-pass.json"
        if out.is_file() and rec.is_file():
            return out.read_text(encoding="utf-8"), list(self._json("final-pass.json")["notes"])
        md = (self.data / f"draft-v{version}.md").read_text(encoding="utf-8")
        audit_md = (self.data / f"audit-v{version}.md").read_text(encoding="utf-8")
        try:
            md, notes = finalpass.settle(md, audit_md)
        except finalpass.Unlocated as exc:
            raise DemandFailed(f"the final audit pass could not settle a finding: {exc}") from None
        tmp = self.data / f".{FINAL}.tmp"
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(out)
        rec.write_text(json.dumps({"version": version, "notes": notes, **finalpass.summary(notes)}, indent=1))
        self.log(f"  final audit pass: {finalpass.summary(notes)}")
        return md, notes

    def _walled(self) -> list[dict[str, Any]]:
        p = self.data / "walled.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def _wall_section(self) -> str:
        """Code-authored, so over-walling is visible: every document the wall
        held out, by name and reason. The attorney can clear any of them."""
        rows = self._walled()
        head = f"\n\n## Held out behind the privilege wall: {len(rows)} document(s)\n\n"
        if not rows:
            return head + "None.\n"
        lines = ["| Document | Why it was held out |", "|---|---|"]
        lines += [f"| {str(w['name']).replace('|', '/')} | {w['reason']} |" for w in rows]
        return (
            head + "Never read by a model; clear any of them and ask again to include it.\n\n" + "\n".join(lines) + "\n"
        )

    def _gate(self, name: str, md: str, path: Path | None = None) -> tuple[dict[str, Any], str]:
        """Gate ``md``; on a quote finding, repair deterministically and gate
        again (``quotefix.py``). Returns the passing result and the text that
        passed (written back to ``path`` when given). Done only once PASSED, so
        a resume re-runs repair and gate and never a paid stage. A refusal of
        quotes alone that survives the repair is ``failed`` (resumable); any
        other refusal holds."""
        what = {"gate.json": "the letter", "gate-gap-audit.json": "the gap audit"}.get(name, "the coverage report")
        if self._is_done(name):
            done = self._json(name)
            self._repairs[what] = list(done.get("repairs") or [])
            return done, (path.read_text(encoding="utf-8") if path else md)
        g = gate.run(self.data, self.firm, md, name=name)
        log: list[str] = []
        if not g["passed"] and quotefix.quote_findings(g["refusals"]):
            md, log = quotefix.repair(md, g["refusals"], quotefix.source_texts(self.data, self.firm))
            if path is not None:
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_text(md, encoding="utf-8")
                tmp.replace(path)
            g = gate.run(self.data, self.firm, md, name=name)
        self._repairs.setdefault(what, []).extend(log)
        if not g["passed"]:
            if g["refusals"] and all(quotefix.quote_findings([r]) for r in g["refusals"]):
                raise GateQuotesLeft(
                    f"the drafting gate still finds quotations in {what} that are not in the record "
                    f"after repair: " + "; ".join(g["refusals"])[:400]
                )
            exc = DemandHold if any(str(r).startswith(WALL_GATE) for r in g["refusals"]) else DemandFailed
            raise exc(f"the drafting gate refused {what} ({g['disposition']}): " + "; ".join(g["refusals"])[:400])
        g["repairs"] = self._repairs.get(what, [])
        (self.data / name).write_text(json.dumps(g, indent=1), encoding="utf-8")
        self._done(name)
        return g, md

    # ---- render once, then file -----------------------------------------------------------
    def _render(self, build: Callable[[], list[tuple[str, Path]]], coverage: bool) -> None:
        """Render every file that will be filed, ONCE, and record the manifest.
        A resume finds the stage done and files the same bytes."""
        if self._is_done("render"):
            return
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        manifest = deliver.write_manifest(deliver.out_dir(self.data), nm["folder"], build())
        self._done("render", coverage=coverage, files=[{"role": m["role"], "name": m["name"]} for m in manifest])

    def _build_deliverables(self, version: int, notes: list[str]) -> list[tuple[str, Path]]:
        out = deliver.out_dir(self.data)
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        files: list[tuple[str, Path]] = []
        if self.job.wants("gap_audit"):
            gap = (self.data / "gap-audit.md").read_text(encoding="utf-8")
            gg, gap = self._gate("gate-gap-audit.json", gap, self.data / "gap-audit.md")
            if gg.get("repairs"):  # code-authored, so a changed quotation is visible
                gap += (
                    "\n\n## Quotations repaired before filing\n\n" + "\n".join(f"- {x}" for x in gg["repairs"]) + "\n"
                )
            # Code-authored, after the gate: directory facts and the wall's own
            # list, not record facts.
            if (self.data / "vendor.json").is_file():
                gap += vendor.section(self._json("vendor.json"))
            gap += self._wall_section()
            files.append(("gap_audit", deliver.render_plain(gap, out, nm["gap_audit"], self._author)))
        if self.job.wants("demand"):
            md = (self.data / FINAL).read_text(encoding="utf-8")
            client = crosscheck.matched_client(md, self._json("facts.json"))
            try:
                path, end_lists, fmt_notes = deliver.render_demand(self.firm, md, out, client)
            except deliver.FormatRefused as exc:
                raise DemandFailed("the format check refused the demand file: " + "; ".join(exc.fails)[:400]) from None
            gap_fixes = [f"Drafting gate repair (the gap audit): {x}" for x in self._repairs.get("the gap audit") or []]
            extra = "\n".join(f"- {n}" for n in [*notes, *gap_fixes, *fmt_notes])
            notes_md = f"# Attorney notes: {path.stem}\n\n{end_lists}\n" + (
                f"\n## For review\n\n{extra}\n" if extra else ""
            )
            files += [
                ("demand", path),
                (
                    "attorney_notes",
                    deliver.render_plain(notes_md, out, f"{path.stem} - attorney notes.docx", self._author),
                ),
            ]
        return files

    def _file(self) -> Verdict:
        manifest = json.loads((deliver.out_dir(self.data) / "upload_manifest.json").read_text(encoding="utf-8"))
        kw = {} if self.readback_pause is None else {"pause": self.readback_pause}
        rec = deliver.file_to_matter(self.data, self.seat, self.job.file_to_id, self.log, **kw)
        if rec["exit"] == 1:
            # The matter's own state refused it (a same-named folder already
            # there): a person at the firm decides, so it holds.
            raise DemandHold(f"filing refused: {rec.get('said') or 'the upload stage refused'}")
        if rec["exit"] != 0:
            raise RuntimeError("the read-back is short after its retries; the files may still be materializing")
        return Verdict(
            "delivered",
            stage="file",
            folder_id=str(rec.get("folder_id")),
            coverage_report=bool(self._state().get("render", {}).get("coverage")),
            files=[{"name": m["name"], "size": m["bytes"], "role": m.get("role")} for m in manifest],
        )

    def _coverage(self, md: str, *, gated: bool) -> Verdict:
        if gated:  # a model wrote it (compose's Section 0 path): it is checked like the letter
            _g, md = self._gate("gate-coverage.json", md)
        out = deliver.out_dir(self.data)
        nm = deliver.names(self.firm, self.job, self.date_stamp)
        self._render(lambda: [("coverage_report", deliver.render_plain(md, out, nm["coverage"], self._author))], True)
        return self._file()

    def _paid(self) -> Verdict:
        self._stage(
            "transcribe",
            lambda: transcribe.run(self.data, self.doorway, self.firm.model("transcription"), self.log, self._workers),
        )
        client = set(self._json("facts.json").get("client_emails") or [])
        self._stage("wall-transcribed", lambda: preflight.wall_printed_emails(self.data, self.firm, client, self.log))
        digest = self._digest()
        self._done("summarize")
        d = self._drafter()
        pre = self._json("preflight.json")
        walled = len(self._json("walled.json"))
        machine = self._json("transcribed.json") if (self.data / "transcribed.json").is_file() else []
        gap = d.gap_audit(digest, pre, walled, machine) if self.job.wants("gap_audit") else None
        version, notes = 0, []
        if self.job.wants("demand"):
            v1 = d.compose(digest, gap)
            if v1.lstrip().startswith(draft.COVERAGE_SENTINEL):
                return self._coverage(v1.lstrip()[len(draft.COVERAGE_SENTINEL) :], gated=True)
            version, result = self._audit_loop(d, digest)
            notes = self._demand_checks(version, result)
        if gap is not None:
            cap = int(self.firm.get("levers", "vendor_lookup_cap"))
            self._stage("vendor", lambda: vendor.run(self.data, gap, self.vendor_factory, cap, self.log))
        self._render(lambda: self._build_deliverables(version, notes), False)
        return self._file()

    def _walk(self) -> Verdict:
        if self._is_done("render"):  # a resume after rendering: file the same bytes
            return self._file()
        self._stage("facts", self._facts)
        self._check_facts()
        self._stage("destination", self._destination)
        self._stage("pull", self._pull)
        self._stage("preflight", lambda: preflight.run(self.data, self.firm, self._json("facts.json"), self.log))
        self._estimate()
        prem = {
            **self.firm.data["premise"],
            "_firm_signature": self.firm.data["format"]["firm_signature"],
            "_firm_domains": list(self.firm.firm_domains),
        }
        self._stage("premise", lambda: premise.run(self.data, self.job, self._json("facts.json"), prem, self.long_date))
        if not self._json("premise.json")["passed"]:
            return self._coverage((self.data / "coverage-report.md").read_text(encoding="utf-8"), gated=False)
        self._select_format()
        return self._paid()

    def _select_format(self) -> None:
        """The demand's variant, read off the file (premise.py: a filed action
        or defense counsel of record is litigation; a carrier's claim with
        neither is pre-suit; anything in between is unclear), and the
        responsible attorney's authored signature. Before anything is paid;
        each gap holds the job and names itself."""
        if not self.job.wants("demand"):
            return
        variant = self._json("premise.json").get("variant") or {}
        name = variant.get("variant")
        if name not in ("pre_suit", "litigation"):
            raise DemandHold(
                "the file does not say whether this is a pre-suit or a litigation demand: "
                + "; ".join(variant.get("evidence") or ["no evidence either way"])[:300]
                + ". Nothing was spent."
            )
        if name not in self.firm.data["variants"]:
            raise DemandHold(
                f"this is a {name.replace('_', '-')} demand and no {name.replace('_', '-')} format is "
                "authored for the firm. Nothing was spent."
            )
        who = self._json("facts.json").get("responsible_attorney")
        signs = self.firm.attorney_for(who)
        if not signs:
            raise DemandHold(
                f"no signature is authored for the responsible attorney ({who or 'none on the matter'}); "
                "a demand is never signed for an attorney who has not been mapped. Nothing was spent."
            )
        self.firm = self.firm.select(name, signs)

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=hold.setting, reason=hold.reason)
        except GateQuotesLeft as exc:
            v = Verdict("failed", stage="gate", reason=str(exc))
        except DemandFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
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
        order = ("facts", "destination", "pull", "preflight", "premise", "transcribe", "summarize", "render")
        st = self._state()
        return next((s for s in order if st.get(s, {}).get("status") != "done"), "file")
