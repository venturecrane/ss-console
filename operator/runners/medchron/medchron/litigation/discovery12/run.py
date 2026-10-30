"""`medchron litigate <job_dir>` for the 12-section discovery breakdown
(``report: "discovery12"``): the status run's inventory, fetch, extract, text
cache, caps and filing, with its own plan, reads, gates and files.

    inventory -> diff -> fetch -> extract -> screen -> read1 -> read2 -> read3
      -> gates -> parity -> book -> review -> file -> report

* **diff** plans every litigated case: its discovery papers by name (all of
  them, newest first, uncapped) and its discovery-named emails. A case with
  none is not read and prints that no discovery paper was found by name.
* **read1/read2/read3** are the three passes (read, verify, Opus audit) as
  single-shot requests through the Batch API at half price (``batch.py``):
  each case's papers in chunks, every request answered by one forced tool
  call. Before each batch is submitted its projected cost at the batch rate
  is checked against the job's cap and the month's budget; the ledger meters
  every row at the batch rate it was billed.
* **gates** hold the report while any date lacks a source on the matter or is
  not in its cited document's text, or any case could not be read.
* **book** builds the workbook and the print-out PDF: every case's court
  dates (the papers' values from the lane's state, the calendar read now),
  its defendants, and the twelve sections, every date naming its paper.
* **screen** and **parity** do nothing here; **report** commits nothing to the
  lane's state.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ... import proof_mode
from ...llm import Item, Result
from .. import courtdates, deliver, gates, inventory, manifest, passes, printout
from ..outcome import LitigationFailed, LitigationHold, Verdict
from ..run import STAGES, LitigationRun
from ..tools import dump
from . import batch, book, complete, sections

GROUP = "discovery12"
ROLE = "discovery_workbook"
PRINTOUT_ROLE = "printout_pdf"
#: What the Batch API reports for an answer the model declined to give
#: (llm.py batch_call: stop_reason "refusal", after its own one retry).
REFUSAL = "refusal"
#: The unread marker of a case declined twice: printed for a hand check, no hold.
DECLINED_REASON = "declined: the reading service declined this file twice (refusal)"
#: A refused chunk is asked once more on this model before its case is declined
#: (Captain, 2026-10-30: three cases of 230 refused twice on the read model).
REFUSAL_RETRY_MODEL = "claude-opus-5-5"
#: The classes a breakdown reads: discovery papers, and recent emails named
#: for the case (``manifest.classify``).
CLASSES = ("discovery", "email")


def _paper(f: dict[str, Any], firm: Any) -> bool:
    """A discovery PAPER as the quote counted it (part 4, 2026-10-29): not an
    email, and its name matches a firm discovery pattern, whatever its class
    (a motion to compel classes as court)."""
    name = manifest.full_name(f)
    return not manifest.is_email(f) and any(r.search(name) for r in firm.discovery_rx)


def plan_for(files: list[dict[str, Any]], firm: Any, today: Any) -> dict[str, Any]:
    """One case's plan: the discovery papers and named emails, newest first."""
    picked = [f for f in files if manifest.classify(f, firm, today) in CLASSES or _paper(f, firm)]
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
    def __init__(self, job_dir: Path, *, use_batch: bool = True, only: list[str] | None = None, **kw: Any) -> None:
        super().__init__(job_dir, **kw)
        #: Matter numbers this run is limited to (the proof's five); empty is all.
        self.only = [str(x) for x in only or []]
        if use_batch:
            self.doorway.batch_stages = frozenset(batch.STAGES.values())
            self.doorway.before_batch = self._before_batch
            self.doorway.log = self.log

    def _before_batch(self, stage: str, items: int, chars: int) -> None:
        """A batch is one commitment: its projected cost at the batch rate,
        against the job's cap and the month's budget, before it is submitted."""
        role = next(r for r, s in batch.STAGES.items() if s == stage)
        rate = self.pricing.rate_for(getattr(self, "_model_override", None) or self.firm.model(role))
        projected = batch.project_usd(rate, chars, items, self.pricing.batch_multiplier)
        self.limits.check_before_paid(projected_usd=projected, spent_usd=self.budget.refresh(), stage=stage, batch=True)

    def _matters(self) -> list[dict[str, Any]]:
        ms = super()._matters()
        if not self.only:
            return ms
        return [m for m in ms if str(m.get("number")) in self.only or str(m.get("id")) in self.only]

    def _court_only(self) -> bool:
        return False

    def _diff(self) -> None:
        plan = {
            m["id"]: plan_for(inventory.files_of(self.data, m["id"]), self.firm, self.today) for m in self._matters()
        }
        dump(self.data / "plan.json", plan)

    def _screen(self) -> None:
        return None

    # ---- the three passes, batched ---------------------------------------------------------------
    def _chunks(self, mid: str) -> tuple[Any, list[list[tuple[int, str]]]]:
        """The case's discovery papers, then the discovery attachments of its
        emails that are not already one of those papers; no email body."""
        ctx = self._ctx(mid)
        by = {str(f["id"]): f for f in inventory.files_of(self.data, mid)}
        docs: list[tuple[int, str]] = []
        seen: set[str] = set()
        hashes: set[str] = set()
        mail: list[tuple[int, str]] = []
        for fid in self._plan()[mid]["candidates"]:
            n = ctx.by_id.get(fid)
            text = self._text_of(mid, fid)
            if n is None or not text:
                continue
            if manifest.is_email(by.get(fid) or {}):
                mail.append((n, text))
                continue
            # An exact copy of a paper already in the set (a served copy, a
            # re-save) is read once: the first, newest, is the one cited.
            whole = batch.text_hash(text)
            if whole in hashes:
                continue
            hashes.add(whole)
            seen.add(batch.paper_key(text))
            docs.append((n, text))
        for n, text in mail:
            kept = batch.attachments_only(text, self.firm.discovery_rx, seen)
            if kept:
                docs.append((n, kept))
        return ctx, batch.chunk(docs)

    def _prev(self, mid: str, role: str, k: int) -> dict[str, Any] | None:
        before = {"verify": "read", "audit": "verify"}[role]
        return self._mjson(mid, batch.FILE.format(p=before, k=k))

    def _items(self, role: str, only: set[str] | None = None) -> tuple[list[Item], dict[str, Any], dict[str, int]]:
        """(the pass's requests still to answer, each case's context, its chunk
        count); ``only`` limits it to those cases."""
        items: list[Item] = []
        ctxs: dict[str, Any] = {}
        want: dict[str, int] = {}
        for m in self._matters():
            mid = m["id"]
            if only is not None and mid not in only:
                continue
            if not self._plan()[mid]["read_groups"] or passes.unread(self, mid):
                continue
            ctx, parts = self._chunks(mid)
            ctxs[mid], want[mid] = ctx, len(parts)
            for k, docs in enumerate(parts):
                if self._mfile(mid, batch.FILE.format(p=role, k=k)).is_file():
                    continue
                check = ""
                if role != "read":
                    prev = self._prev(mid, role, k)
                    if prev is None:
                        continue
                    check = "\nTHE RESULT TO CHECK:\n" + book.for_model(prev["result"], ctx)
                text = batch.user_text(self._header(m), batch.papers(ctx.line, docs), k, len(parts), check)
                items.append(
                    Item(
                        f"{mid}-{k}",
                        [{"role": "user", "content": [{"type": "text", "text": text}]}],
                        {"mid": mid, "k": k},
                    )
                )
        return items, ctxs, want

    def _record(
        self, role: str, ctxs: dict[str, Any], it: Item, r: Result | None, err: str | None, last: bool,
        refused: list[Item] | None = None,
    ) -> bool:  # fmt: skip
        """Write one answered chunk. False when the answer did not call the
        pass's tool and this is not the last round (the caller asks again). A
        refusal with ``refused`` given is collected for the retry model."""
        mid, k = it.meta["mid"], it.meta["k"]
        if r is None:
            if str(err or "").strip() == REFUSAL and refused is not None:
                refused.append(it)
                return True
            if str(err or "").strip() == REFUSAL:
                # The doorway already asked once more (llm.py batch_call's retry
                # round): refused twice, the case is DECLINED, printed for a hand
                # check, and never holds every other case (2026-10-30 job).
                dump(self._mfile(mid, passes.UNREAD), {"stage": role, "reason": DECLINED_REASON, "declined": True})
                return True
            dump(self._mfile(mid, passes.UNREAD), {"stage": role, "reason": f"read_error (batch): {str(err)[:200]}"})
            return True
        name = sections.RECORD_RESULT["name"] if role == "read" else sections.RECORD_VERDICTS["name"]
        got = batch.tool_input(r.message, name)
        if got is None:
            if not last:
                return False
            dump(self._mfile(mid, passes.UNREAD), {"stage": role, "reason": "read_incomplete: no recorded answer"})
            return True
        ctx = ctxs[mid]
        if role == "read":
            rec = {"result": sections.to_result(ctx.source, got), "overturns": []}
        else:
            prev = self._prev(mid, role, k) or {}
            result = copy.deepcopy(prev["result"])
            log = sections.apply_verdicts(ctx.source, result, got.get("verdicts"), role)
            rec = {"result": result, "overturns": list(prev.get("overturns") or []) + log}
        dump(self._mfile(mid, batch.FILE.format(p=role, k=k)), rec)
        return True

    def _submit(self, role: str, todo: list[Item], where: str, on_result: Any, model: str | None = None) -> None:
        self._model_override = model  # _before_batch projects the batch at this model's rate
        summary = self.doorway.batch_call(
            batch.STAGES[role],
            todo,
            on_result,
            model=model or self.firm.model(role),
            max_tokens=batch.MAX_TOKENS,
            batch_dir=self.data / where,
            system=sections.system_for(role, self.today) + batch.only_by_tool(role),
            **batch.shape(role),
        )
        if summary.timed_out:
            raise LitigationFailed(
                f"stage_unfinished: {role}: {len(summary.timed_out)} requests are still processing; resume later"
            )

    def _pass(self, role: str) -> None:
        """One pass over every chunk still to answer. A chunk answered without
        the pass's tool is asked ONCE more, in its own batch dir (the doorway
        resumes a dir's batches by custom id, so the same dir would hand back the
        same answer), before its case is marked unread (2026-10-29 review: one
        such reply marked a whole case unread, and an unread case holds the
        report). An ERRORED item the doorway already retries once."""
        self._run_pass(role)

    def _run_pass(self, role: str, only: set[str] | None = None, where: str = "batches") -> None:
        items, ctxs, want = self._items(role, only)
        notool: list[Item] = []
        refused: list[Item] = []
        if items:
            self._submit(
                role,
                items,
                where,
                lambda it, r, e: self._record(role, ctxs, it, r, e, False, refused) or notool.append(it),
            )
        if notool:
            self.log(f"[{role}] {len(notool)} answers did not call the tool; asking those once more")
            self._submit(
                role, notool, f"{where}-notool", lambda it, r, e: self._record(role, ctxs, it, r, e, True, refused)
            )
        if refused:
            self.log(f"[{role}] {len(refused)} answers were refused; asking those once on {REFUSAL_RETRY_MODEL}")
            self._submit(
                role,
                refused,
                f"{where}-opus",
                lambda it, r, e: self._record(role, ctxs, it, r, e, True),
                model=REFUSAL_RETRY_MODEL,
            )
        path = self.data / f"d12-{role}-chunks.json"
        if only is not None and path.is_file():  # a subset redo keeps every other case's count
            want = {**self._json(path.name), **want}
        dump(path, want)

    def _redo_refused(self) -> None:
        """Before the gates: every case whose unread marker is a REFUSAL from a
        run before this code (no ``declined`` decision yet) is asked again,
        ONLY its missing chunks, pass by pass, in a fresh batch dir (the old
        dirs would resume the same refused answer by custom id). The kept chunk
        results of every other case are not asked again. Refused again, the
        case is declined (``_record``), never unread (2026-10-30: three refused
        cases held a 230-case report after every other read was paid for)."""
        mids = set()
        for m in self._matters():
            mark = passes.unread(self, m["id"]) or {}
            if not mark.get("declined") and REFUSAL in str(mark.get("reason") or ""):
                mids.add(m["id"])
        if not mids:
            return
        self.log(f"[gates] {len(mids)} cases refused before; asking only their missing chunks once more")
        for mid in mids:
            self._mfile(mid, passes.UNREAD).unlink(missing_ok=True)
        for role in batch.PASSES:
            self._run_pass(role, only=mids, where="batches-refused")

    def _read1(self) -> None:
        self._pass("read")

    def _read2(self) -> None:
        self._pass("verify")

    def _read3(self) -> None:
        self._pass("audit")

    # ---- gates, book, file -----------------------------------------------------------------------
    def final(self, m: dict[str, Any]) -> dict[str, Any] | None:
        mid = m["id"]
        p = self._plan().get(mid) or {}
        planned = self._json("d12-audit-chunks.json") if (self.data / "d12-audit-chunks.json").is_file() else {}
        n = int(planned.get(mid) or 0)
        got = [self._mjson(mid, batch.FILE.format(p="audit", k=k)) for k in range(n)]
        read = bool(p.get("read_groups")) and n > 0 and all(g is not None for g in got)
        # 2026-10-29 proof: a case whose candidates hold no discovery paper (only
        # emails without a discovery attachment) packs into ZERO chunks. That is
        # nothing to read, not a read that failed: it prints "not read" and does
        # not hold every other case. A case the passes skipped (unread) is absent.
        nothing = mid in planned and n == 0
        declined = bool((passes.unread(self, mid) or {}).get("declined"))
        merged = batch.merge([g["result"] for g in got if g]) if read else {"sections": {}, "flags": []}
        return {
            "matter_id": mid,
            "number": m["number"],
            "title": m["title"],
            "responsible": m.get("responsible") or "",
            "read": read,
            "nothing_to_read": nothing,
            "declined": declined,
            **merged,
        }

    def _finals(self) -> list[dict[str, Any]]:
        return [self.final(m) for m in self._matters()]  # type: ignore[misc]

    def _gates(self) -> None:
        """A date the gates cannot confirm (not ISO, no source on the matter,
        not in its cited paper) is printed as unconfirmed, never as fact, and
        counted. The report HOLDS on a case that could not be read, or when
        more than ``MAX_UNCONFIRMED_SHARE`` of its dates are unconfirmed (the
        reads, not one value, are then in doubt). 2026-10-29: one value used to
        hold every case, with no way for SMD to accept it."""
        self._redo_refused()
        ms = self._finals()
        found: dict[str, list[Any]] = {}
        holds: dict[str, list[Any]] = {}
        declined: list[Any] = []
        dated = 0
        for m in ms:
            on = {str(f["id"]) for f in inventory.files_of(self.data, m["matter_id"])}
            dated += len(sections.dated_values(m))
            bad = sections.findings(m["number"], m, lambda fid, mid=m["matter_id"]: self._text_of(mid, fid), on)
            for n, path, why in bad:
                found.setdefault(why, []).append((n, path))
            sections.unconfirm(m, {path for _n, path, _w in bad})
            if m.get("declined"):
                declined.append(m["number"])
            elif self._plan()[m["matter_id"]]["read_groups"] and not m["read"] and not m.get("nothing_to_read"):
                holds.setdefault("matter could not be read", []).append((m["number"], "audit"))
        unconfirmed = sum(len(v) for v in found.values())
        if dated and unconfirmed / dated > sections.MAX_UNCONFIRMED_SHARE:
            holds[f"more than {sections.MAX_UNCONFIRMED_SHARE:.0%} of dates unconfirmed"] = [(unconfirmed, dated)]
        dump(self.data / "final.json", ms)
        dump(
            self.data / "gates.json",
            {
                "passed": not holds,
                "issues": holds,
                "unconfirmed": found,
                "dates": dated,
                "unconfirmed_count": unconfirmed,
                "declined": declined,
                "declined_count": len(declined),
            },
        )
        if holds:
            raise LitigationHold(
                "gate_refused: the gates refused the breakdown: "
                + "; ".join(f"{k} ({len(v)})" for k, v in holds.items())[:400]
            )

    def _parity(self) -> None:
        dump(self.data / "changes.json", {})

    def _out(self) -> Path:
        name = f"Discovery breakdown {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).xlsx"
        return self.data / "out" / name

    def _pdf(self) -> Path:
        name = f"Litigation print-out with discovery {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).pdf"
        return self.data / "out" / name

    def _status_values(self, finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each case as the status list holds it (the lane's state), with its
        twelve sections attached, and the calendar's court dates merged."""
        out = []
        for f in finals:
            prior = manifest.load_prior(self.state, f["matter_id"]) or {}
            out.append(
                {
                    **prior,
                    "matter_id": f["matter_id"],
                    "number": f["number"],
                    "title": f["title"],
                    "case_name": prior.get("case_name") or f["title"],
                    "responsible": f["responsible"],
                    "discovery12": {
                        "read": f["read"],
                        "declined": bool(f.get("declined")),
                        "sections": f.get("sections") or {},
                        "flags": f.get("flags") or [],
                        "status_rows": f.get("status_rows") or [],
                        "kind_hits": f.get("kind_hits") or {},
                    },
                }
            )
        try:
            seat = self.seat
        except Exception:  # noqa: BLE001 - no seat (an offline replay): the papers' dates stand alone
            seat = None
        cal = courtdates.pull(seat, [str(m["matter_id"]) for m in out], self.data, self.log)
        return courtdates.shown(out, cal, self.today)

    def _completed(self) -> list[dict[str, Any]]:
        """final.json completed from the status list's own values (``complete.py``),
        kept as final-complete.json: the workbook, the print-out and the review
        all read this one."""
        out = []
        # kind_hits.json (optional): sections whose kind a paper's title names
        hits = self._json("kind_hits.json") if (self.data / "kind_hits.json").is_file() else {}
        for f in self._json("final.json"):
            d12 = {"read": f["read"], "declined": bool(f.get("declined")), "sections": f.get("sections") or {}}
            got = complete.merge(manifest.load_prior(self.state, f["matter_id"]) or {}, d12, hits.get(f["matter_id"]))
            out.append({**f, **{k: got[k] for k in ("sections", "status_rows", "kind_hits", "completed")}})
        dump(self.data / "final-complete.json", out)
        return out

    def _book(self) -> None:
        finals = self._completed()
        stats = book.build(finals, self._out(), self.today)
        gates.leak_gate(self._out(), self.data / "book.json", stats)
        shown = self._status_values(finals)
        dump(self.data / "shown.json", shown)
        pstats = printout.build(shown, self._pdf(), self.today)
        leaks = printout.scan(self._pdf())
        dump(self.data / "book-printout.json", {**pstats, "leaks": leaks})
        if leaks:
            self._pdf().unlink(missing_ok=True)
            raise LitigationFailed(f"leak_found: the built print-out carries internal text in {len(leaks)} places")

    def _review(self) -> None:
        built = self._json("book.json")
        problems = book.check(self._out().read_bytes(), self._json("final-complete.json"))
        problems += printout.check(self._pdf(), self._json("shown.json"))
        dump(self.data / "review.json", {"problems": problems, "rows": built.get("rows")})
        if problems:
            raise LitigationFailed(f"review_held: {len(problems)} findings in the built files; nothing was filed")
        self._put("review_status", "code_only")

    def _file(self) -> None:
        pause = deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause
        args = (self.seat, self.job.file_to_id, self.job.folder_name)
        dump(self.data / "filed.json", deliver.file_workbook(*args, self._out(), self.data, self.log, pause=pause))
        rec = deliver.file_workbook(
            *args, self._pdf(), self.data, self.log, pause=pause, record="delivery-printout.json"
        )
        dump(self.data / "filed-printout.json", rec)

    def _report(self) -> None:
        return None

    # ---- the per-chunk results: kept, listed, reopened only on purpose ----------------------------
    def _kept(self) -> dict[str, int]:
        """Every per-chunk result on disk, listed in ``data/d12-results.json``
        (case id -> pass -> files, relative to ``data``) so a later list can
        reuse them: discovery12 has no incremental carry, and nothing in the
        lane prunes a job dir. Returns the verdict's counts per pass."""
        listing: dict[str, dict[str, list[str]]] = {}
        counts = {f"d12_{p}_chunks": 0 for p in batch.PASSES}
        root = self.data / "m"
        for mdir in sorted(root.iterdir()) if root.is_dir() else []:
            for p in batch.PASSES:
                got = sorted(x.name for x in mdir.glob(batch.FILE.format(p=p, k="*")))
                if got:
                    listing.setdefault(mdir.name, {})[p] = [f"m/{mdir.name}/{x}" for x in got]
                    counts[f"d12_{p}_chunks"] += len(got)
        dump(self.data / "d12-results.json", listing)
        g = self._json("gates.json") if (self.data / "gates.json").is_file() else {}
        counts["d12_declined"] = int(g.get("declined_count") or 0)  # the wake states it (litigation_lane)
        return counts

    def reopen(self, stages: list[str]) -> None:
        """The run's reopen, plus each reopened pass's per-chunk results: the
        pass skips a chunk whose file exists, so a redo that left them would
        silently re-use the answers it was asked to redo."""
        super().reopen(stages)
        order: list[str] = list(STAGES)
        known = [s for s in stages if s in order]
        if not known:
            return
        redo = {"read1": "read", "read2": "verify", "read3": "audit"}
        passes_ = [redo[s] for s in order[min(order.index(s) for s in known) :] if s in redo]
        root = self.data / "m"
        for mdir in root.iterdir() if root.is_dir() else []:
            for p in passes_:
                for x in mdir.glob(batch.FILE.format(p=p, k="*")):
                    x.unlink(missing_ok=True)

    def _walk(self) -> Verdict:
        for name in STAGES:
            if name == "file" and proof_mode.active():
                return Verdict(
                    proof_mode.OUTCOME,
                    stage="file",
                    files=[
                        {"role": role, "name": p.name, "size": p.stat().st_size}
                        for role, p in ((ROLE, self._out()), (PRINTOUT_ROLE, self._pdf()))
                    ],
                    counts=self._kept(),
                )
            self._stage(name, getattr(self, f"_{name}"))
            if name == "inventory" and not self._matters():
                raise LitigationHold("scope_empty: no open litigation matters are in this job's scope")
        keep = ("name", "size", "sha256", "file_id")
        both = ((ROLE, self._json("filed.json")), (PRINTOUT_ROLE, self._json("filed-printout.json")))
        return Verdict(
            "delivered",
            stage="report",
            files=[{"role": role, **{k: f[k] for k in keep}} for role, f in both],
            folder_id=both[0][1]["folder_id"],
            counts=self._kept(),
            proof={
                "readback": "confirmed",
                "files": [{k: f[k] for k in ("name", "file_id", "size", "sha256")} for _r, f in both],
                "review": "code_only",
                "review_cents": 0,
                "review_model": "",
            },
        )
