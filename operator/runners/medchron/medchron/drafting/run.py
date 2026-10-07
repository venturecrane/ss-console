"""`medchron draft <job_dir>`: one drafting job, pull to read-back, resumable.

The graph, per class::

    facts -> destination -> pull (privilege wall) -> preflight -> estimate
      -> transcribe -> digest (cited)
      -> [mediation brief: howell table] -> [court classes: caption + record diff]
      -> compose -> audit -> repair -> reaudit -> final pass -> gate
      -> attach / reserve -> render -> format check -> file -> manifest

Every stage records itself in ``data/state.json``; a resume skips finished
stages and every paid stage resumes from its own artifacts. The run's dates are
frozen on the first attempt and the files rendered ONCE, so a resume after a
short read-back files the same bytes.

Reused from the demand job, read-only, where the code is kind-agnostic (and
pinned by ``tests/test_drafting_demand_contract.py``): ``facts``, ``pull``,
``preflight``, ``transcribe``, ``summarize``, ``finalpass``, ``quotefix``,
``gate.strip_names``, ``deliver`` (manifest, plain render, upload), and the
draft helpers in ``compose.py``. The demand-shaped parts (its job envelope, its
firm config, its drafter, its gate sources) have drafting's own copies here.

Outcomes (the drafting ledger, the same edges as demand's):

* ``delivered``: the document and its attorney notes are on the matter, read back.
* ``held``: the FILE or the REQUEST needs a person, nothing was filed: the
  matter id does not carry the number asked for, the destination is neither
  the matter nor an authored rehearsal matter, the file has nothing to draft
  from, privileged text reached the draft, or a same-named folder is already
  on the matter. Final; the reply relays the reason.
* ``failed``: OUR machinery (a read that did not finish, a limit, a truncated
  or unfinished stage, a gate or format refusal of our own output, a short
  read-back): resumable, no client message, SMD alerted.
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod
from ..demand import deliver, facts as facts_mod, finalpass, preflight, pull, quotefix, summarize, transcribe
from ..demand.firm import DemandFirm
from ..ledger import Ledger
from ..llm import Doorway
from . import caption as caption_mod, compose, firm as firm_mod, format_check, gate, howell, job as job_mod, render

MAX_REPAIRS = 2
FINAL = "draft-final.md"
CHUNK_CHARS = 120_000
CONCURRENCY = 4
DIGEST_MAX_TOKENS = 64_000
DIGEST_BUDGET_CHARS = 600_000
COURT_CLASSES = ("mediation_brief", "discovery_set", "discovery_response")
CHARGES_CLASSES = ("mediation_brief", "memo")
TITLES = {
    "mediation_brief": "Mediation Brief",
    "discovery_set": "Discovery Set",
    "discovery_response": "Discovery Responses",
    "memo": "Memo",
    "depo_outline": "Deposition Outline",
}
WALL_GATE = "[1]"
MARKERS = re.compile(r"\{\{\s*(ATTORNEY|NOT IN RECORD|CLIENT)\s*(?::\s*([^{}]*))?\}\}")
CONDENSE_PROMPT = (
    "Condense these record digests to about the target length. Keep every citation exactly as written, every "
    "date, figure and name; drop repetition only. Never add a fact. Time window: {{WINDOW}}."
)


class DraftingHold(RuntimeError):
    """The file or the request needs a person: held, final, the firm is told."""


class DraftingFailed(RuntimeError):
    """Our machinery: failed, resumable, no client message, SMD alerted."""


def demand_view(firm: firm_mod.DraftingFirm) -> DemandFirm:
    """The drafting inputs in the shape demand's kind-agnostic stages read
    (pull's selection and privilege wall, preflight's estimate). The levers are
    drafting's own constants; premise scanning is a demand concept and is
    empty; condense is estimated at the digest rate (an over-estimate)."""
    b = firm.data["budget"]
    return DemandFirm(
        root=firm.root,
        data={
            "firm": firm.data["firm"],
            "privilege": firm.data["privilege"],
            "selection": firm.data["selection"],
            "budget": {**b, "usd_per_million_condense_chars": b["usd_per_million_chars"]},
            "premise": {"scan": {}},
            "levers": {
                "chunk_chars": CHUNK_CHARS,
                "concurrency": CONCURRENCY,
                "digest_max_tokens": DIGEST_MAX_TOKENS,
                "compose_max_tokens": DIGEST_MAX_TOKENS,
                "digest_budget_chars": DIGEST_BUDGET_CHARS,
            },
        },
    )


def markers(md: str) -> list[dict[str, str]]:
    seen, out = set(), []
    for m in MARKERS.finditer(md):
        item = (m.group(1), " ".join((m.group(2) or "").split()))
        if item not in seen:
            seen.add(item)
            out.append({"kind": item[0], "text": item[1]})
    return out


@dataclass
class Verdict:
    outcome: str
    stage: str | None = None
    reason: str | None = None
    dollars: float = 0.0
    documents: int = 0
    pages: int = 0
    document_class: str | None = None
    folder_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    caption_discrepancies: list[dict[str, str]] = field(default_factory=list)
    markers: list[dict[str, str]] = field(default_factory=list)

    def to_list(self) -> list[dict[str, Any]]:
        return [{**self.__dict__, "unit": "drafting", "kind": "drafting"}]


class DraftingRun:
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
        self.dview = demand_view(self.firm)
        self.cls = self.job.document_class
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
        self._seat_factory, self._seat = seat_factory, None
        self.date_stamp = self._frozen_date(today())
        self.readback_pause = readback_pause
        self.notes: list[str] = []

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

    def _frozen_date(self, t: time.struct_time) -> str:
        stamp = self._state().get("date")
        if not stamp:
            stamp = time.strftime("%m-%d-%y", t)
            self._put("date", stamp)
        return str(stamp)

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _write(self, name: str, obj: Any) -> None:
        (self.data / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")

    def _stage(self, name: str, fn: Callable[[], Any]) -> None:
        if self._is_done(name):
            return
        t0 = time.time()
        self.log(f"[{name}] start")
        fn()
        self._done(name, seconds=round(time.time() - t0, 1))

    @property
    def _title(self) -> str:
        return TITLES[self.cls]

    def _names(self) -> dict[str, str]:
        base = f"{self._title} - {self.job.matter_number} - {self.date_stamp}"
        return {
            "folder": f"{self._title} {self.date_stamp} (Operator {self.job.job_id[-6:]})",
            "draft": f"{base}.docx",
            "notes": f"{base} - attorney notes.docx",
        }

    # ---- free stages -----------------------------------------------------------------
    def _facts(self) -> None:
        self._write("facts.json", facts_mod.read(self.seat, self.job.matter_id))
        self._check_facts()

    def _check_facts(self) -> None:
        errs = facts_mod.blocking_errors(self._json("facts.json"))
        if errs:
            raise DraftingFailed("the matter record could not be read in full: " + "; ".join(errs)[:400])

    def _destination(self) -> None:
        job = self.job
        if facts_mod.matter_number(self.seat, job.matter_id) != job.matter_number:
            raise DraftingHold(f"the matter id does not carry matter number {job.matter_number}; nothing was read")
        if job.file_to_id == job.matter_id:
            return
        if job.file_to_number not in self.firm.rehearsal_matters:
            raise DraftingHold(
                f"the job files to matter {job.file_to_number}, which is neither the matter it reads nor an "
                "authored rehearsal matter; refused"
            )
        if facts_mod.matter_number(self.seat, job.file_to_id) != job.file_to_number:
            raise DraftingHold(f"the filing destination's id does not carry matter number {job.file_to_number}")

    def _preflight(self) -> None:
        report = preflight.run(self.data, self.dview, self._json("facts.json"), self.log)
        if not report["documents"]:
            raise DraftingHold("the matter file has no readable documents to draft from; nothing was spent")

    def _estimate(self) -> None:
        if self.job.allowance_remaining is not None and self.job.allowance_remaining <= 0 and not self.budget.refresh():
            raise limits_mod.LimitHold(
                "drafting_allowance_per_cycle",
                "drafting_allowance_per_cycle: this cycle's drafting jobs are used; nothing was spent",
            )
        est = self._json("preflight.json")["estimate"]
        spent = self.budget.refresh()
        self.limits.check_before_paid(
            projected_usd=max(0.0, float(est["usd"]) - spent), spent_usd=spent, stage="the first paid stage"
        )

    # ---- paid stages -----------------------------------------------------------------
    def _digest(self) -> str:
        p = self.data / "digest.md"
        if p.is_file():
            return p.read_text(encoding="utf-8")
        chunks = summarize.build_chunks(summarize.corpus_files(self.data), CHUNK_CHARS)
        s = summarize.Summarizer(
            self.data,
            self.doorway,
            self.firm.model("digest"),
            self.firm.prompt(self.cls, "digest"),
            DIGEST_MAX_TOKENS,
            CONCURRENCY,
            self.log,
        )
        out = summarize.condense(
            s.run(chunks),
            DIGEST_BUDGET_CHARS,
            self.doorway,
            self.firm.model("digest"),
            CONDENSE_PROMPT,
            CONCURRENCY,
            self.log,
        )
        p.write_text(out, encoding="utf-8")
        return out

    def _howell(self) -> None:
        rows, notes = howell.extract(self.data, self.doorway, self.firm.model("digest"), CONCURRENCY, self.log)
        table = howell.build(rows, self._json("facts.json").get("medicals") or [])
        self._write("howell.json", {"table": table, "notes": notes})

    def _caption(self) -> None:
        doc = caption_mod.source_document(self.data)
        fields = caption_mod.extract(doc, self.firm.firm_domains) if doc else {}
        record = caption_mod.read_record(self.seat, self.job.matter_id, self._json("facts.json"))
        name = str(doc.get("name")) if doc else None
        diffs = caption_mod.compare(fields, record, name or "") if doc else []
        self._write("caption.json", {"source": name, "fields": fields, "discrepancies": diffs})

    def _context(self) -> list[str]:
        f = self._json("facts.json")
        rec = [
            f"{label}: {f.get(key)}"
            for key, label in (
                ("client_name", "client"),
                ("matter_number", "matter number"),
                ("date_of_loss", "date of loss"),
                ("responsible_attorney", "responsible attorney"),
            )
            if f.get(key)
        ]
        out = [
            "THE MATTER RECORD (structured fields read by code; cite as 'matter record'):\n"
            + "\n".join(f"- {r}" for r in rec)
        ]
        if (self.data / "caption.json").is_file():
            c = self._json("caption.json")
            out.append(caption_mod.block(c["fields"], c["source"]))
        table = self._json("howell.json")["table"] if (self.data / "howell.json").is_file() else []
        if table:
            out.append(
                "THE CHARGES TABLE (computed by code from the bills, EOBs, liens, ledgers and the Medicals tab; "
                "each cell names its source document; paid is the Howell figure; place it and use its figures "
                "exactly; never compute a figure it does not carry; a {{NOT IN RECORD}} cell stays a marker):\n\n"
                + howell.markdown(table)
            )
        return out

    def _drafter(self) -> compose.Drafter:
        return compose.Drafter(
            self.data, self.doorway, self.firm, self.cls, self.job.request_text, self._context(), CONCURRENCY, self.log
        )

    def _corpus_text(self) -> str:
        return "\n".join(
            Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            for r in summarize.corpus_files(self.data)
        )

    def _draft(self, digest: str) -> str:
        """compose, audit, repair, re-audit, then the final pass settles what
        the repairs left (never a hold): ``draft-final.md``."""
        out = self.data / FINAL
        if out.is_file():
            return out.read_text(encoding="utf-8")
        d = self._drafter()
        refused = render.needs_attorney(d.compose(digest))
        if refused:
            raise DraftingHold(f"the request is missing what the draft needs: {refused}")
        corpus, version = self._corpus_text(), 1
        result = d.audit(version, digest, corpus)
        while compose.blocking_findings(result) and version <= MAX_REPAIRS:
            d.repair(digest, version)
            version += 1
            result = d.audit(version, digest, corpus)
        md = (self.data / f"draft-v{version}.md").read_text(encoding="utf-8")
        audit_md = (self.data / f"audit-v{version}.md").read_text(encoding="utf-8")
        try:
            md, settled = finalpass.settle(md, audit_md)
        except finalpass.Unlocated as exc:
            raise DraftingFailed(f"the final audit pass could not settle a finding: {exc}") from None
        drifts = compose.finding_lines(audit_md, ("DRIFTS", *compose.EXTRA_VERDICTS))
        self._write("final-pass.json", {"version": version, "settled": settled, "drifts": drifts})
        tmp = self.data / f".{FINAL}.tmp"
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(out)
        return md

    def _gate(self, md: str) -> str:
        if self._is_done("gate"):
            return (self.data / FINAL).read_text(encoding="utf-8")
        doc, notes = render.split_notes(md)
        g = gate.run(self.data, self.firm, self.cls, doc)
        log: list[str] = []
        if not g["passed"] and quotefix.quote_findings(g["refusals"]):
            doc, log = quotefix.repair(doc, g["refusals"], gate.source_texts(self.data, self.firm, self.cls))
            md = doc + ("\n" + render.NOTES_MARK + "\n\n" + notes + "\n" if notes else "")
            (self.data / FINAL).write_text(md, encoding="utf-8")
            g = gate.run(self.data, self.firm, self.cls, doc)
        if not g["passed"]:
            exc = DraftingHold if any(str(r).startswith(WALL_GATE) for r in g["refusals"]) else DraftingFailed
            raise exc(f"the drafting gate refused the document ({g['disposition']}): " + "; ".join(g["refusals"])[:400])
        self._write("gate.json", {**g, "repairs": log})
        self._done("gate")
        return md

    # ---- render once, then file -----------------------------------------------------------
    def _render(self, md: str, digest: str) -> None:
        if self._is_done("render"):
            return
        doc, end_tables = render.split_notes(md)
        doc, attached = render.attach_decl(doc, self.cls, self.firm, digest)
        doc, reserved = render.reserve_judgment(doc, self.cls)
        out, nm = deliver.out_dir(self.data), self._names()
        path, report = render.render(doc, self.cls, out / nm["draft"])
        res = format_check.check(path, self.cls, self.firm.data["format"], digest)
        if not res.ok:
            raise DraftingFailed("the format check refused the rendered document: " + "; ".join(res.fails)[:400])
        found = markers(doc)
        notes_md = self._notes_md(found, [*attached, *reserved, *report.get("notes", [])], end_tables)
        notes = deliver.render_plain(notes_md, out, nm["notes"], str(self.firm.get("firm", "display_name")))
        deliver.write_manifest(out, nm["folder"], [("draft", path), ("attorney_notes", notes)])
        self._done("render", markers=found)

    def _notes_md(self, found: list[dict[str, str]], render_notes: list[str], end_tables: str = "") -> str:
        lines = [f"# Attorney notes: {self._title}", "", "## Items for the attorney", ""]
        lines += [f"- {{{{{m['kind']}}}}} {m['text']}".rstrip() for m in found] or ["- None."]
        cap = self._json("caption.json") if (self.data / "caption.json").is_file() else None
        if cap is not None:
            lines += ["", f"## Caption: source {cap['source'] or 'none (no court document in the file)'}", ""]
            lines += [
                f'- {x["field"].replace("_", " ")}: the court\'s paper reads "{x["document_value"]}"; the matter record '
                f'reads "{x["record_value"] or "nothing"}" ({x["why"]}). Line: "{x["quote"]}" ({x["source"]})'
                for x in cap["discrepancies"]
            ] or ["- The matter record agrees with the court's paper on every field checked."]
        fp = self._json("final-pass.json")
        review = [f"Removed or flagged by the final audit: {x}" for x in fp["settled"]]
        review += [f"Left for the attorney by the auditor: {x}" for x in fp["drifts"]]
        if (self.data / "howell.json").is_file():
            review += [f"Howell table: {n}" for n in self._json("howell.json")["notes"]]
        review += [f"Drafting gate repair: {x}" for x in self._json("gate.json").get("repairs") or []]
        review += [f"Drafting gate: {w}" for w in self._json("gate.json").get("warnings") or []]
        review += [f"Format: {n}" for n in render_notes]
        walled = self._json("walled.json") if (self.data / "walled.json").is_file() else []
        review += [f"Held out, never read (privilege wall: {w['reason']}): {w['name']}" for w in walled]
        tail = ["", "## The drafter's end tables", "", end_tables] if end_tables else []
        return "\n".join(lines + ["", "## For review", ""] + [f"- {x}" for x in review] + tail) + "\n"

    def _file(self) -> Verdict:
        manifest = json.loads((deliver.out_dir(self.data) / "upload_manifest.json").read_text(encoding="utf-8"))
        kw = {} if self.readback_pause is None else {"pause": self.readback_pause}
        rec = deliver.file_to_matter(self.data, self.seat, self.job.file_to_id, self.log, **kw)
        if rec["exit"] == 1:
            raise DraftingHold(f"filing refused: {rec.get('said') or 'the upload stage refused'}")
        if rec["exit"] != 0:
            raise RuntimeError("the read-back is short after its retries; the files may still be materializing")
        return Verdict(
            "delivered",
            stage="file",
            folder_id=str(rec.get("folder_id")),
            files=[{"name": m["name"], "size": m["bytes"], "role": m.get("role")} for m in manifest],
        )

    def _walk(self) -> Verdict:
        if self._is_done("render"):
            return self._file()
        self._stage("facts", self._facts)
        self._check_facts()
        self._stage("destination", self._destination)
        f = self._json("facts.json")
        self._stage(
            "pull",
            lambda: pull.run(
                self.seat, self.job.matter_id, self.dview, set(f.get("client_emails") or []), self.data, self.log
            ),
        )
        self._stage("preflight", self._preflight)
        self._estimate()
        self._stage(
            "transcribe",
            lambda: transcribe.run(self.data, self.doorway, self.firm.model("transcription"), self.log, CONCURRENCY),
        )
        client = set(f.get("client_emails") or [])
        self._stage("wall-transcribed", lambda: preflight.wall_printed_emails(self.data, self.dview, client, self.log))
        digest = self._digest()
        self._done("summarize")
        if self.cls in CHARGES_CLASSES:
            self._stage("howell", self._howell)
        if self.cls in COURT_CLASSES:
            self._stage("caption", self._caption)
        md = self._gate(self._draft(digest))
        self._render(md, digest)
        return self._file()

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=hold.setting, reason=hold.reason)
        except DraftingHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except (DraftingFailed, compose.DraftingError) as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict("failed", stage=self._current(), reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        v.document_class = self.cls
        v.dollars = round(self.budget.refresh(), 4)
        if (self.data / "caption.json").is_file():
            v.caption_discrepancies = self._json("caption.json")["discrepancies"]
        v.markers = list(self._state().get("render", {}).get("markers") or [])
        if (self.data / "preflight.json").is_file():
            e = self._json("preflight.json")
            v.documents, v.pages = int(e["documents"]), int(e["estimate"]["pages"])
        return v

    def _current(self) -> str:
        order = (
            "facts",
            "destination",
            "pull",
            "preflight",
            "transcribe",
            "summarize",
            "howell",
            "caption",
            "gate",
            "render",
        )
        st = self._state()
        skip = {"howell"} if self.cls not in CHARGES_CLASSES else set()
        skip |= {"caption"} if self.cls not in COURT_CLASSES else set()
        return next((s for s in order if s not in skip and st.get(s, {}).get("status") != "done"), "file")
