"""`medchron draft <job_dir>`: one drafting job, pull to read-back, resumable.

The graph, per class::

    facts -> destination -> pull (privilege wall) -> preflight -> estimate
      -> transcribe -> digest (cited)
      -> [mediation brief: howell table] -> [court classes: caption read, record diff + corrections]
      -> per document (finish.py; one per set a discovery request names):
         compose -> audit -> repair -> reaudit -> final pass -> defined terms -> gate
         -> attach / reserve -> render -> [pleading paper, when asked] -> format check
      -> attorney notes from the final documents -> independent review (review.py)
      -> manifest -> file

    The attorney's OWN attached sets (placing.py): facts -> destination -> pull
      -> preflight -> caption -> each set placed verbatim in the caption
      -> [pleading paper] -> format check -> notes -> review -> manifest -> file

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
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod
from ..demand import deliver, facts as facts_mod, preflight, pull, summarize, transcribe
from ..demand.firm import DemandFirm
from ..ledger import Ledger
from ..llm import Doorway
from . import (
    caption as caption_mod,
    caption_stage,
    compose,
    firm as firm_mod,
    howell,
    job as job_mod,
)
from .placing import Placing
from .outcome import REASON, DraftingFailed, DraftingHold, Verdict


CHUNK_CHARS = 120_000
CONCURRENCY = 4
DIGEST_MAX_TOKENS = 64_000
#: The digest compose reads, condensed under this many characters (about 400K
#: tokens, under demand's 2M-character lever): with the house style, the
#: skeleton, one exemplar and the context, the compose request stays under
#: compose.COMPOSE_INPUT_MAX_CHARS, which compose asserts before any call.
DIGEST_BUDGET_CHARS = 1_600_000
COURT_CLASSES = ("mediation_brief", "discovery_set", "discovery_response")
CHARGES_CLASSES = ("mediation_brief", "memo")
TITLES = {
    "mediation_brief": "Mediation Brief",
    "discovery_set": "Discovery Set",
    "discovery_response": "Discovery Responses",
    "memo": "Memo",
    "depo_outline": "Deposition Outline",
}
CONDENSE_PROMPT = (
    "Condense these record digests to about the target length. Keep every citation exactly as written, every "
    "date, figure and name; drop repetition only. Never add a fact. Time window: {{WINDOW}}."
)


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


class DraftingRun(Placing):
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
        reexec: Callable[[], None] | None = None,
    ) -> None:
        #: Replaces this process with a fresh one on the same job (the CLI's
        #: os.execv); None in tests and dry runs. See ``_walk``.
        self.reexec = reexec
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

    def _json_text(self, name: str) -> str:
        return (self.data / name).read_text(encoding="utf-8")

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _atomic(self, name: str, text: str) -> None:
        """Every artifact a resume trusts is written whole or not at all."""
        tmp = self.data / f".{name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.data / name)

    def _write(self, name: str, obj: Any) -> None:
        self._atomic(name, json.dumps(obj, indent=1))

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

    # ---- free stages -----------------------------------------------------------------
    def _facts(self) -> None:
        self._write("facts.json", facts_mod.read(self.seat, self.job.matter_id))
        self._check_facts()

    def _check_facts(self) -> None:
        errs = facts_mod.blocking_errors(self._json("facts.json"))
        if errs:
            raise DraftingFailed(
                "record_unreadable: the matter record could not be read in full: " + "; ".join(errs)[:400]
            )

    def _destination(self) -> None:
        job = self.job
        if facts_mod.matter_number(self.seat, job.matter_id) != job.matter_number:
            raise DraftingHold(
                f"destination_mismatch: the matter id does not carry matter number {job.matter_number}; nothing was read"
            )
        if job.file_to_id == job.matter_id:
            return
        if job.file_to_number not in self.firm.rehearsal_matters:
            raise DraftingHold(
                f"destination_unauthored: the job files to matter {job.file_to_number}, which is neither the matter it reads nor an "
                "authored rehearsal matter; refused"
            )
        if facts_mod.matter_number(self.seat, job.file_to_id) != job.file_to_number:
            raise DraftingHold(
                f"destination_mismatch: the filing destination's id does not carry matter number {job.file_to_number}"
            )

    def _preflight(self) -> None:
        report = preflight.run(self.data, self.dview, self._json("facts.json"), self.log)
        if not report["documents"]:
            raise DraftingHold(
                "no_readable_documents: the matter file has no readable documents to draft from; nothing was spent"
            )

    def _estimate(self) -> None:
        if self.job.allowance_remaining is not None and self.job.allowance_remaining <= 0 and not self.budget.refresh():
            raise limits_mod.LimitHold(
                "drafting_allowance_per_cycle",
                "drafting_allowance_per_cycle: this cycle's drafting jobs are used; nothing was spent",
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
        self._atomic("digest.md", out)
        return out

    def _howell(self) -> None:
        try:
            rows, notes = howell.extract(self.data, self.doorway, self.firm.model("digest"), CONCURRENCY, self.log)
        except howell.ExtractionError as exc:
            raise compose.DraftingError(f"howell: {exc}") from None
        unmatched: list[str] = []
        table = howell.build(rows, self._json("facts.json").get("medicals") or [], unmatched)
        notes += [f"not on the Medicals tab, left out of the charges table: {u}" for u in unmatched]
        self._write("howell.json", {"table": table, "notes": notes})

    def _caption(self) -> None:
        try:
            out = caption_stage.run(
                self.data,
                self.doorway,
                self.firm,
                self.seat,
                self.job.matter_id,
                self._json("facts.json"),
                self.job.requester,
                self.log,
            )
        except caption_mod.RecordUnreadable as exc:
            raise DraftingFailed(f"record_unreadable: the matter record's caption fields: {exc}") from None
        self._write("caption.json", out)

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
            out += caption_stage.context(self._json("caption.json"))
        table = self._json("howell.json")["table"] if (self.data / "howell.json").is_file() else []
        if table:
            out.append(
                "THE CHARGES TABLE (computed by code from the bills, EOBs, liens, ledgers and the Medicals tab; "
                "each cell names its source document; paid is the Howell figure; place it and use its figures "
                "exactly; never compute a figure it does not carry; a {{NOT IN RECORD}} cell stays a marker):\n\n"
                + howell.markdown(table)
            )
        return out

    def _corpus_text(self) -> str:
        return "\n".join(
            Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            for r in summarize.corpus_files(self.data)
        )

    def _file(self) -> Verdict:
        manifest = json.loads((deliver.out_dir(self.data) / "upload_manifest.json").read_text(encoding="utf-8"))
        kw = {} if self.readback_pause is None else {"pause": self.readback_pause}
        rec = deliver.file_to_matter(self.data, self.seat, self.job.file_to_id, self.log, **kw)
        if rec["exit"] == 1:
            raise DraftingHold(f"filing_refused: {rec.get('said') or 'the upload stage refused'}")
        if rec["exit"] != 0:
            raise DraftingFailed(
                "stage_unfinished: file: the read-back is short after its retries; the files may still be materializing"
            )
        return Verdict(
            "delivered",
            stage="file",
            folder_id=str(rec.get("folder_id")),
            files=[{"name": m["name"], "size": m["bytes"], "role": m.get("role")} for m in manifest],
        )

    def _walk(self) -> Verdict:
        if self._is_done("manifest"):
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
        if self.placing():
            # The attorney's own sets: no transcription, no digest, no compose.
            # The caption is read off the court's paper; his text is placed as
            # it is (placing.py).
            self._stage("caption", self._caption)
            self._place_finish()
            return self._file()
        self._estimate()
        transcribing = not self._is_done("transcribe")
        self._stage(
            "transcribe",
            lambda: transcribe.run(self.data, self.doorway, self.firm.model("transcription"), self.log, CONCURRENCY),
        )
        if transcribing and self.reexec is not None:
            # Reading page images grows the heap far past what the rest of the
            # job needs, and the process does not give it back: a 1,083-page
            # file was killed at its 1 GiB cap while rendering, long after the
            # images were done with. Every stage so far is on disk, so a fresh process picks up
            # at the next one with none of that heap.
            self.log("  transcription done; continuing in a fresh process")
            self.reexec()
        client = set(f.get("client_emails") or [])
        self._stage("wall-transcribed", lambda: preflight.wall_printed_emails(self.data, self.dview, client, self.log))
        digest = self._digest()
        self._done("summarize")
        if self.cls in CHARGES_CLASSES:
            self._stage("howell", self._howell)
        if self.cls in COURT_CLASSES:
            self._stage("caption", self._caption)
        self._finish(digest)
        return self._file()

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=hold.setting, reason=f"limit: {hold.reason}")
        except DraftingHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except DraftingFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
        except compose.DraftingError as exc:
            v = Verdict("failed", stage=self._current(), reason=f"stage_unfinished: {self._current()}: {exc}")
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict(
                "failed",
                stage=self._current(),
                reason=f"unexpected: {type(exc).__name__} at {self._current()}: {str(exc)[:300]}",
            )
        if v.reason and not REASON.match(v.reason):  # a missed code is still never client text first
            v.reason = f"unexpected: {v.reason}"
        v.document_class = self.cls
        v.dollars = round(self.budget.refresh(), 4)
        if (self.data / "caption.json").is_file():
            cap = self._json("caption.json")
            v.caption_discrepancies = cap["discrepancies"]
            v.caption_corrections = list(cap.get("corrections") or [])
            v.caption_restore_incomplete = list(cap.get("restore_incomplete") or [])
            if v.caption_restore_incomplete and v.outcome != "held":
                # SMD's alarm: a delivered reply never relays the reason; a held
                # one does, so a held job carries it in its own field only.
                lost = "caption_restore_incomplete: " + "; ".join(v.caption_restore_incomplete)
                v.reason = f"{lost} | {v.reason}" if v.reason else lost
        v.markers = list(self._state().get("render", {}).get("markers") or [])
        if (self.data / "preflight.json").is_file():
            e = self._json("preflight.json")
            v.documents, v.pages = int(e["documents"]), int(e["estimate"]["pages"])
        return v

    def _current(self) -> str:
        head = ("facts", "destination", "pull", "preflight", "transcribe", "summarize", "howell", "caption")
        st = self._state()
        skip = {"howell"} if self.cls not in CHARGES_CLASSES else set()
        skip |= {"caption"} if self.cls not in COURT_CLASSES else set()
        order = [s for s in head if s not in skip]
        for u in self.units() if "units" in st else []:
            per_unit = ("placed",) if self.placing() else ("compose", "audit", "finalpass", "gate", "rendered")
            order += [u.stage(s) for s in per_unit]
        order += ["review", "manifest"]
        return next((s for s in order if st.get(s, {}).get("status") != "done"), "file")
