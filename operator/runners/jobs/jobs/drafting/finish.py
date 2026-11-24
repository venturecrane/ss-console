"""From the cited digest to the filed files: per document, then the whole job.

The second half of ``DraftingRun`` (``run.py`` holds the first: the record,
the pull, the digest, the caption), split out at the module-size ceiling.

**Units.** A job drafts one document, except a propounded-discovery request
that names several sets: each set is its own UNIT (``sets.variants``), with
its own data directory (``data/set-<k>/``), its own compose, audit, repair,
final pass and gate, its own attachments (a section 2030.050 declaration only
on a special-interrogatory set over the PROVEN limit, and a proof of service
on every set), its own rendered Word file and format check. A one-document job
is the unit ``""``: the root data directory and the stage names it always had,
so a job in flight across a deploy resumes unchanged.

**Parties.** A request that serves its sets on more than one party
(``parties.plan``: "RFA, RFPD and SROG to the City and the School District")
plans one unit per (party, set), party by party, each with the party in its
label, its file name, its identification block, its title and its definition
of YOU (``parties.bind``). Every unit reads the same digest: the record is
paid for once. A request served on one party plans exactly as before, and a
plan already frozen in state.json (no ``party``) resumes as it was.

**The finish, in order.** Every unit rendered and format-checked; then the
attorney notes written from the FINAL documents (``review.final_notes``); then
the independent pre-delivery review over the documents and the notes as they
would be filed (``review.py``); only then the manifest and the filing. A
MUST-FIX anywhere holds the whole job for SMD before anything is filed.
"""

from __future__ import annotations

import io
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import firm_wording
from ..demand import deliver, finalpass, quotefix, summarize
from . import caption_stage, citefix, compose, converge, format_check, gate, nestquote, page_limits, pincheck, quote_end
from . import base_lay, counts, library_pick, pleading, prerender, render
from . import review as review_mod, sets
from . import grounding, notes as notes_mod, parties as parties_mod, settle as settle_mod
from . import amended, exhibit_index, exhibit_plan, repair_budget, repair_guard, served as served_mod
from .meetconfer import memo as mc_memo
from .forms import lane_hook as form_hook
from .rulebook import hook as rule_hook
from .finish_review import FINAL, FinishReview
from .outcome import WALL_SENTENCE, DraftingFailed, DraftingHold, fills_located, markers, markers_located

MAX_REPAIRS = 2
#: A served paper's audit findings are repaired or the job fails (compose.STRICT_CLASSES).
#: A served set's repair rounds when the firm authors no ``budget.repair_rounds``
#: (``repair_budget``); ``Finish._converge`` stops on non-convergence well before it in practice.
REPAIR_CEILING = 8
CONCURRENCY = 4
#: Units drafted at once after the first of each party (``Finish._draft_units``).
UNITS_AT_ONCE = 4
#: Audit workers shared by the units drafting at once: in-flight model calls stay
#: bounded (4 units x 2 workers), never 4 x ``CONCURRENCY``.
CALLS_AT_ONCE = 8
WALL_GATE = "[1]"


class Unit:
    """One document the job drafts."""

    def __init__(
        self, key: str, label: str, variant: str | None, instruction: str | None, data: Path, party: str | None = None
    ) -> None:
        self.key, self.label, self.variant, self.instruction, self.data = key, label, variant, instruction, data
        #: The party this set is served on, when the request serves several (``parties.py``).
        self.party = party

    def stage(self, name: str) -> str:
        return f"{self.key}:{name}" if self.key else name


class SetDoorway:
    """The job's doorway for one set's drafter: every call's ledger row carries
    the set's key at the front of its ``custom_id``, so the set's own spend is
    read from the ledger (``Finish.unit_spent``). Everything else is the job's."""

    def __init__(self, inner: Any, key: str) -> None:
        self._inner, self._key = inner, key

    @staticmethod
    def tag(key: str) -> str:
        return f"{key}-"

    def call(self, stage: str, **kw: Any) -> Any:
        kw["custom_id"] = self.tag(self._key) + str(kw.get("custom_id") or "")
        return self._inner.call(stage, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class Finish(FinishReview):
    """Mixin: ``DraftingRun`` supplies data, job, firm, cls, doorway, log,
    seat, _state/_put/_done/_is_done/_json/_atomic/_write, _context,
    _corpus_text, _title, date_stamp."""

    if TYPE_CHECKING:  # what DraftingRun (run.py) supplies
        data: Path
        job: Any
        firm: Any
        cls: str
        doorway: Any
        budget: Any
        log: Callable[[str], None]
        date_stamp: str
        rule_layout: dict[str, Any]
        rule_notes: list[str]
        _units: list[Unit] | None
        _corpus: dict[str, str] | None

        @property
        def _title(self) -> str: ...
        def _state(self) -> dict[str, Any]: ...
        def _put(self, key: str, value: Any) -> None: ...
        def _done(self, stage: str, **info: Any) -> None: ...
        def _is_done(self, stage: str) -> bool: ...
        def _json(self, name: str) -> Any: ...
        def _atomic(self, name: str, text: str) -> None: ...
        def _context(self) -> list[str]: ...
        def _corpus_text(self) -> str: ...
        def _caption(self) -> None: ...
        def _plan(self) -> list[dict[str, Any]]: ...
        def _freeze_plan(self, plan: list[dict[str, Any]]) -> None: ...
        def served_review(self) -> dict[str, dict[str, Any]] | None: ...

    # ---- units ----------------------------------------------------------------------
    def units(self) -> list[Unit]:
        if getattr(self, "_units", None) is not None:
            return self._units  # type: ignore[has-type]
        plan = self._state().get("units")
        if plan is None:
            plan = self._plan()  # served_plan.py: the sets, the parties, the served sets
            self._freeze_plan(plan)
        by_op = form_hook.by_operator(self.firm.data)  # the firm's form policy (forms/lane_hook.py)
        served = served_mod.frozen(self.data, self.job.job_dir, by_op) if served_mod.has_plan(plan) else []
        out = []
        titles = [parties_mod.unit_title(p) for p in plan]
        for p, title in zip(plan, titles):
            d = self.data / p["key"] if p["key"] else self.data
            d.mkdir(parents=True, exist_ok=True)
            others = [x for x in titles if x and x != title]
            if not p["key"]:
                instr = None
            elif isinstance(p.get("served"), int):
                # Every other served set, SMD's included, is answered separately.
                rest = [s.label for i, s in enumerate(served) if i != p["served"]]
                s = served[p["served"]]
                # An amended set: only its challenged numbers (amended.py).
                instr = (
                    amended.instruction(self.data, s, p["numbers"])
                    if p.get("numbers")
                    else served_mod.instruction(s, rest)
                )
            else:
                instr = sets.Unit(p["variant"], p["label"], p.get("party")).instruction(others)
            instr = counts.instruction(self, p.get("variant"), instr)  # the firm's number for the set (counts.py)
            out.append(Unit(p["key"], title or self._title, p["variant"], instr, d, p.get("party")))
        self._units = out
        return out

    def _served_on(self) -> list[str]:
        """``parties.served_on``; a name that is none of the matter's parties
        stops the job for SMD before anything is drafted."""
        try:
            return parties_mod.served_on(self.data, self.job.job_dir, self.job.request_text)
        except parties_mod.PartyUnknown as exc:
            raise DraftingFailed(f"party_unknown: {exc}") from None

    def paper(self) -> str:
        """``pleading`` when the request asks for it and the firm authored its
        pleading paper; otherwise plain (house style D1, the default). A
        pleading request the firm cannot honor is the review's MUST-FIX. A class
        the firm files on pleading paper by default is asked for (``pleading.paper_for``)."""
        return pleading.paper_for(self.firm.data["format"], self.cls, self.job.request_text)

    def corpus(self) -> dict[str, str]:
        got = getattr(self, "_corpus", None)
        if got is None:
            got = {
                str(r["name"]): Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
                for r in summarize.corpus_files(self.data)
            }
            self._corpus = got
        return got

    def _uwrite(self, u: Unit, name: str, obj: Any) -> None:
        tmp = u.data / f".{name}.tmp"
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        tmp.replace(u.data / name)

    def _uatomic(self, u: Unit, name: str, text: str) -> None:
        tmp = u.data / f".{name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(u.data / name)

    # ---- compose, audit, repair, final pass, per unit ---------------------------------
    def _repair_rounds(self, d: compose.Drafter, digest: str, corpus: str) -> int:
        """Every other class: audit, repair while findings block, up to
        ``MAX_REPAIRS``; what survives is settled and noted (``settle``)."""
        version = 1
        result = d.audit(version, digest, corpus)
        while compose.blocking_findings(result, self.cls) and version <= MAX_REPAIRS:
            repair_guard.repaired(d, digest, version, repair_guard.Guard(d.data, digest + "\n" + corpus))
            version += 1
            result = d.audit(version, digest, corpus)
        return version

    def _converge(self, u: Unit, d: compose.Drafter, digest: str, corpus: str) -> int:
        """A served set (``compose.STRICT_CLASSES``) is clean, files with open
        items, or holds (``converge.served``, its rules and their history). A
        production unit with an exhibit plan also has every audit carry the
        exhibit findings code finds (``exhibit_index.watch``)."""
        if exhibit_index.applies(self.data, u.variant):
            exhibit_index.watch(d, self.data)
        return converge.served(d, self.cls, u.label, digest, corpus, repair_budget.plan(self, u, REPAIR_CEILING))

    def _canon(self, u: Unit) -> Unit | None:
        """The set the others align to: the first one gated, never ``u``."""
        done = [o for o in self.units() if o.key and o.key != u.key and self._is_done(o.stage("gate"))]
        same = [o for o in done if o.party == u.party]
        return (same or done or [None])[0]

    def _shared(self, u: Unit) -> list[str]:
        c = self._canon(u) if u.key else None
        if c is None:
            return []
        doc, _ = render.split_notes((c.data / FINAL).read_text(encoding="utf-8"))
        front, served = parties_mod.shared_front(sets.front_matter(doc), c.party, u.party)
        return [
            f"THE {c.label.upper()} SET ALREADY DRAFTED FOR THIS REQUEST (it binds how SHARED words read: print its "
            "attorney block and signature block exactly, and define every term both sets define in the same words. "
            "It is not this set's whole list of terms: this set's own DEFINITIONS define every further term its "
            "requests use)." + served + "\n\n" + front
        ]

    def unit_spent(self, key: str) -> float:
        """One set's priced model calls so far: the ledger rows its drafter
        tagged (``SetDoorway``). The repair budget meters each set by this."""
        tag, total = SetDoorway.tag(key), 0.0
        for path in self.budget.ledgers:
            for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get("custom_id") or "").startswith(tag):
                    total += self.budget.pricing.price_row(row)
        return total

    def _drafter(self, u: Unit) -> compose.Drafter:
        context = ([u.instruction] if u.instruction else []) + self._shared(u) + self._context()
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else None
        d = compose.Drafter(
            u.data,
            SetDoorway(self.doorway, u.key) if u.key else self.doorway,
            self.firm,
            self.cls,
            self.job.request_text,
            context,
            getattr(self, "_unit_workers", CONCURRENCY),  # its share while units draft at once
            self.log,
            skeleton=caption_stage.fill_skeleton(self.firm.skeleton(self.cls), cap),
        )
        return firm_wording.attach(d, self.data)

    def _draft(self, u: Unit, digest: str) -> str:
        if form_hook.takes(self, u):
            return form_hook.draft(self, u)  # a served form set: answered in code (forms/lane_hook.py)
        out = u.data / FINAL
        if out.is_file():
            return out.read_text(encoding="utf-8")
        d = self._drafter(u)
        refused = render.needs_attorney(d.compose(digest))
        if refused:
            raise DraftingHold(f"request_incomplete: the request is missing what the draft needs: {refused}")
        self._done(u.stage("compose"))
        if self.cls == "discovery_set":
            # The attorney block, the Dated line and the first set's front
            # matter set by code BEFORE the audit, so it never spends repairs
            # on what code fixes anyway (2026-10-14: an "Attorneys for" line
            # the audit flagged and the gate later rewrote). Idempotent.
            first = u.data / "draft-v1.md"
            doc, end = render.split_notes(first.read_text(encoding="utf-8"))
            fronted, _ = self._front(u, doc)
            if fronted != doc:
                self._uatomic(u, "draft-v1.md", self._join(fronted, end))
            counts.after_compose(self, u, d, digest)  # one "more requests" pass below the firm's minimum
        corpus = self._corpus_text()
        strict = self.cls in compose.STRICT_CLASSES
        version = self._converge(u, d, digest, corpus) if strict else self._repair_rounds(d, digest, corpus)
        audit_md = (u.data / f"audit-v{version}.md").read_text(encoding="utf-8")
        md = (u.data / f"draft-v{version}.md").read_text(encoding="utf-8")
        self._done(u.stage("audit"))
        try:
            md, settled = settle_mod.settle(md, audit_md)
        except finalpass.Unlocated as exc:
            raise DraftingFailed(f"audit_unsettled: the final audit pass could not settle a finding: {exc}") from None
        # A strict class forwards nothing: its findings were repaired, removed with
        # their request, or failed the job (the case-only rows code settles).
        drifts = [] if strict else compose.finding_lines(audit_md, ("DRIFTS", *compose.EXTRA_VERDICTS))
        self._uwrite(u, "final-pass.json", {"version": version, "settled": settled, "drifts": drifts})
        self._uatomic(u, FINAL, md)
        self._done(u.stage("finalpass"))
        return md

    # ---- the gate, after the defined-term pass ----------------------------------------
    def _join(self, doc: str, notes: str) -> str:
        return doc + ("\n" + render.NOTES_MARK + "\n\n" + notes + "\n" if notes else "")

    def _block_lines(self) -> list[str]:
        """The firm's authored attorney block this job carries, or none."""
        cp = self.data / "caption.json"
        chosen = (json.loads(cp.read_text(encoding="utf-8")).get("attorney_block") if cp.is_file() else None) or {}
        return list(chosen.get("lines") or [])

    def _front(self, u: Unit, doc: str) -> tuple[str, list[str]]:
        """The firm's authored attorney block, the draft's date on the Dated
        line, then the first set's front matter (``sets.align``): one wording
        across the sets of one request."""
        doc, _system = sets.strip_system_lines(doc)  # the pipeline's own mess: not the attorney's news
        doc, notes = sets.authored_head(doc, self._block_lines())
        doc, dated = sets.dated(doc, self._long_date())
        doc, numbered = sets.set_number(doc, self.job.request_text)
        doc, counsel_for = sets.attorneys_for(doc)
        doc, court = sets.particulars(doc, sets.court_particulars(self._digest_text()))
        doc, _alias = sets.alias_wording(doc)
        notes += dated + numbered + counsel_for + court
        c = self._canon(u) if u.key else None
        if c is not None:
            canon, _ = render.split_notes((c.data / FINAL).read_text(encoding="utf-8"))
            doc, more = sets.align(doc, canon, f"{c.label} set", keep=parties_mod.keep_terms(c.party, u.party))
            notes += more
        doc, bound = parties_mod.bound(doc, u.party, u.label, self.data, self.job.job_dir, self.job.request_text)
        return doc, notes + bound

    def _digest_text(self) -> str:
        p = self.data / "digest.md"
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def _long_date(self) -> str:
        import datetime as dt

        d = dt.datetime.strptime(self.date_stamp, "%m-%d-%y")
        return f"{d:%B} {d.day}, {d.year}"

    def _gate(self, u: Unit, md: str) -> str:
        if self._is_done(u.stage("gate")):
            return (u.data / FINAL).read_text(encoding="utf-8")
        doc, notes = render.split_notes(md)
        doc, log = nestquote.repair(doc)
        doc, trimmed = quote_end.trim(doc)
        log += trimmed
        if self.cls == "discovery_set":
            doc, caps = sets.enforce_caps(doc)
            doc, fronted = self._front(u, doc)
            doc, marked = sets.compound_markers(doc)
            doc = counts.at_gate(self, u, doc)  # numbered in order; counts.json (counts.py)
            self._uwrite(u, "caps.json", caps + fronted + marked)
        if amended.is_unit(u.label):
            doc, _titled = amended.retitle(doc)  # the title reads AMENDED, by code (amended.py)
        md = self._join(doc, notes)
        self._uatomic(u, FINAL, md)
        if log:
            self.log("  " + log[0])
        name = f"{u.key}/gate.json" if u.key else "gate.json"
        g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"] and quotefix.quote_findings(g["refusals"]):
            named = gate.named_sources(self.data, self.firm, self.cls)
            doc, log2 = quotefix.repair(doc, g["refusals"], [t for _n, t in named], pincheck.cited_text(named))
            log += log2
            md = self._join(doc, notes)
            self._uatomic(u, FINAL, md)
            g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"] and citefix.findings(g["refusals"]):
            doc, fixed = citefix.repair(doc, g["refusals"])
            if fixed:
                log += fixed
                md = self._join(doc, notes)
                self._uatomic(u, FINAL, md)
                g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"] and any(str(r).startswith(f"[{pincheck.GATE}]") for r in g["refusals"]):
            # A quotation pinned to a page that does not carry it, on exactly
            # one other page of the same document: the pin moves (pincheck.py).
            doc, moved = pincheck.repair(doc, gate.named_sources(self.data, self.firm, self.cls))
            if moved:
                log += moved
                md = self._join(doc, notes)
                self._uatomic(u, FINAL, md)
                g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"]:
            if any(str(r).startswith(WALL_GATE) for r in g["refusals"]):
                raise DraftingHold(f"gate_refused: {WALL_SENTENCE}")
            raise DraftingFailed(
                f"gate_refused: the drafting gate refused the document ({g['disposition']}): "
                + "; ".join(g["refusals"])[:400]
            )
        staged = rule_hook.at_gate(self, u, md)  # the firm's rules stage: a no-op unless phrase_book switches it on
        if exhibit_index.applies(self.data, u.variant):
            # The Index of Exhibits, by code from the firm's exhibit plan (exhibit_index.py).
            body, end = render.split_notes(staged)
            body, rec = exhibit_index.write(body, exhibit_plan.load(self.data) or {})
            exhibit_index.keep(u.data, rec)
            staged = self._join(body, end)
        if staged != md:
            md = staged
            self._uatomic(u, FINAL, md)
        self._uwrite(u, "gate.json", {**g, "repairs": log})
        self._done(u.stage("gate"))
        return md

    # ---- render once per unit -------------------------------------------------------
    def _names(self, u: Unit | None = None) -> dict[str, str]:
        base = f"{self._title} - {self.job.matter_number} - {self.date_stamp}"
        label = re.sub(r'[\\/:*?"<>|]', "", u.label) if u else ""
        draft = f"{self._title} - {label} - {self.job.matter_number} - {self.date_stamp}" if u and u.key else base
        return {
            "folder": f"{self._title} {self.date_stamp} (Operator {self.job.job_id[-6:]})",
            "draft": f"{draft}.docx",
            "notes": f"{base} - attorney notes.docx",
        }

    def _render_unit(self, u: Unit, md: str, digest: str) -> None:
        if self._is_done(u.stage("rendered")):
            return
        doc, _end = render.split_notes(md)
        lines = self._block_lines()
        address = [
            x for x in lines[1:] if not re.search(r"(?i)@|\(\d{3}\)|telephone|facsimile|fax|\bLLP\b|\bLLC\b|\bPC\b", x)
        ]
        body_fills = fills_located(doc)  # the set's own body, before the job's attachments
        doc, attached = render.attach(
            doc, self.cls, self.firm, digest, self.corpus(), render.declarant_name(lines), address, counts.facts(u)
        )
        doc, reserved = render.reserve_judgment(doc, self.cls)
        doc, cited, cites = prerender.prepare(doc, self.firm.data, self.cls, self.corpus())
        fmt, paper, made_in = library_pick.ranked(self)  # the file, and the order of layout precedence in it
        dest = deliver.out_dir(self.data) / self._names(u)["draft"]
        path, report, laid, checked = base_lay.lay_out(
            doc, dest, self.cls, fmt, paper, made_in.held, made_in.rules, styles=made_in.styles
        )
        attachments = self.firm.data["attachments"]
        res = format_check.check(path, self.cls, checked, digest, self.corpus(), paper, cites, attachments)
        if self.cls == "discovery_set":
            # A capitalized term the set uses and never defines: the code check
            # that blocks, behind the audit's TERM verdict (compose.py).
            set_text = render.split_notes(md)[0]
            undefined = sets.undefined_caps(set_text, self.corpus())
            if undefined:
                res.model(sets.undefined_caps_advice(undefined, set_text))
        page_limits.step(self, u, path, fmt, res)
        if res.content and not res.ours and not self._is_done(u.stage("format_repair")):
            self.log(f"  format check: {len(res.content)} content finding(s); one repair pass")
            model_doc, end = render.split_notes(md)
            fixed = self._drafter(u).repair_format(model_doc, res.content)
            fixed = repair_guard.vet_format(u.data, model_doc, fixed, digest + "\n" + self._corpus_text())
            self._uatomic(
                u, FINAL, fixed.rstrip() + ("\n\n" + render.NOTES_MARK + "\n\n" + end + "\n" if end else "\n")
            )
            self._done(u.stage("format_repair"))
            self._put(u.stage("gate"), {"status": "reopened"})
            self._render_unit(u, self._gate(u, (u.data / FINAL).read_text(encoding="utf-8")), digest)
            return
        if not res.ok:
            raise DraftingFailed(
                "format_check: the format check refused the rendered document: " + "; ".join(res.fails)[:400]
            )
        self._uwrite(
            u,
            "rendered.json",
            {
                "path": str(path),
                "paper": paper,
                "markers": markers(doc),
                "located": markers_located(doc) + body_fills,
                # The renderer's own report stays here, never in the attorney
                # notes ("named styles defined on the starter base", 2026-10-13).
                "notes": [
                    *attached,
                    *reserved,
                    *cited,
                    *laid,
                    *self.rule_notes,
                    *exhibit_index.notes(u.data),
                    *(amended.unit_notes(self.data, u.key) if amended.is_unit(u.label) else []),
                ],
                "render_report": report.get("notes", []),
            },
        )
        self._done(u.stage("rendered"))

    def _rendered(self, u: Unit) -> dict[str, Any]:
        return json.loads((u.data / "rendered.json").read_text(encoding="utf-8"))

    def _papers(self) -> list[review_mod.Paper]:
        import docx

        out = []
        for u in self.units():
            r = self._rendered(u)
            d = docx.Document(io.BytesIO(Path(r["path"]).read_bytes()))
            out.append(review_mod.Paper(u.key, u.label, format_check.doc_text(d), r["paper"]))
        return out

    def _one(self, u: Unit, digest: str) -> None:
        self._render_unit(u, self._gate(u, self._draft(u, digest)), digest)

    def _draft_units(self, digest: str) -> None:
        """Every unit drafted, gated and rendered (2026-11-23: a 12-set request ran
        12 rounds one after another). The first unit served on each party goes
        first, alone, as before: the others align to it (``_canon``). The rest
        draft ``UNITS_AT_ONCE`` at a time, each with its share of
        ``CALLS_AT_ONCE`` audit workers, so the model calls in flight stay
        bounded and the work (so the cost) is the same. Each unit's stages are
        recorded as they finish (a resume skips them). When units fail, the
        units not yet started are cancelled, the running ones finish, and the
        job ends with the failure of the earliest failed unit in plan order."""
        units = self.units()
        self.corpus()  # the shared caches, filled once before any thread reads them
        parties: set[str | None] = set()
        first, rest = [], []
        for u in units:
            (rest if u.party in parties else first).append(u)
            parties.add(u.party)
        for u in first:
            self._one(u, digest)
        if not rest:
            return
        at_once = min(UNITS_AT_ONCE, len(rest))
        self._unit_workers = max(1, CALLS_AT_ONCE // at_once)
        failed: dict[str, BaseException] = {}
        try:
            with ThreadPoolExecutor(max_workers=at_once, thread_name_prefix="unit") as pool:
                futures = {pool.submit(self._one, u, digest): u for u in rest}
                for f in as_completed(futures):
                    exc = None if f.cancelled() else f.exception()
                    if exc is None:
                        continue
                    failed[futures[f].key] = exc
                    for g in futures:
                        g.cancel()  # not yet started; the running ones finish and record their stages
        finally:
            self._unit_workers = CONCURRENCY
        if failed:
            raise next(failed[u.key] for u in rest if u.key in failed)

    def _finish(self, digest: str) -> None:
        """Every unit rendered, then notes, review, manifest."""
        self._draft_units(digest)
        notes_md = self._notes(digest)
        rec = self._review(digest, notes_md)
        notes_md = self._notes(digest)  # as reviewed (a notes rewrite replaced it)
        flagged = grounding.flagged_rows(rec.get("findings") or [])
        if flagged and not self._is_done("basis_dropped"):
            # A basis row the review says does not carry its request: dropped
            # and said, like a row that failed verification. No rewrite: the
            # review read the rest, and removing a row adds nothing to check.
            dropped, n = grounding.drop_rows(notes_mod.strip_smd(notes_md), flagged)  # never inside SMD's sections
            notes_md = notes_mod.carry_smd(notes_md, dropped)
            self._atomic("notes-final.md", notes_md)
            self._done("basis_dropped", rows=n)
        if self._is_done("manifest"):
            return
        out, nm = deliver.out_dir(self.data), self._names()
        notes_md = notes_md.rstrip("\n") + "\n" + "\n".join(review_mod.notes_section(rec)) + "\n"
        notes = deliver.render_plain(notes_md, out, nm["notes"], str(self.firm.get("firm", "display_name")))
        files = [("draft", Path(self._rendered(u)["path"])) for u in self.units()]
        memo = mc_memo.build(self)  # an amended job's meet-and-confer memo (meetconfer/memo.py); None otherwise
        files += ([("draft", memo)] if memo else []) + [("attorney_notes", notes)]
        deliver.write_manifest(out, nm["folder"], files)
        self._done("manifest")
        self._done("render", markers=self._all_markers())
