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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NoReturn

from ..demand import deliver, finalpass, quotefix, summarize
from . import citefix, compose, format_check, gate, nestquote, pleading, render, review as review_mod, sets
from . import grounding, notes as notes_mod, wording
from . import settle as settle_mod
from .outcome import WALL_SENTENCE, DraftingFailed, DraftingHold, markers, markers_located

MAX_REPAIRS = 2
#: A served paper's audit findings are repaired or the job fails (compose.STRICT_CLASSES).
#: The runaway guard on a served set's repairs; ``Finish._converge`` stops on
#: non-convergence well before it in practice.
REPAIR_CEILING = 8
FINAL = "draft-final.md"
CONCURRENCY = 4
WALL_GATE = "[1]"


class Unit:
    """One document the job drafts."""

    def __init__(self, key: str, label: str, variant: str | None, instruction: str | None, data: Path) -> None:
        self.key, self.label, self.variant, self.instruction, self.data = key, label, variant, instruction, data

    def stage(self, name: str) -> str:
        return f"{self.key}:{name}" if self.key else name


class Finish:
    """Mixin: ``DraftingRun`` supplies data, job, firm, cls, doorway, log,
    seat, _state/_put/_done/_is_done/_json/_atomic/_write, _context,
    _corpus_text, _title, date_stamp."""

    if TYPE_CHECKING:  # what DraftingRun (run.py) supplies
        data: Path
        job: Any
        firm: Any
        cls: str
        doorway: Any
        log: Callable[[str], None]
        date_stamp: str
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

    # ---- units ----------------------------------------------------------------------
    def units(self) -> list[Unit]:
        if getattr(self, "_units", None) is not None:
            return self._units  # type: ignore[has-type]
        plan = self._state().get("units")
        if plan is None:
            found = sets.variants(self.job.request_text) if self.cls == "discovery_set" else []
            plan = [{"key": f"set-{i}", "variant": u.key, "label": u.label} for i, u in enumerate(found, 1)]
            if len(plan) < 2:
                plan = [{"key": "", "variant": plan[0]["variant"] if plan else None, "label": ""}]
            self._put("units", plan)
        out = []
        labels = [p["label"] for p in plan]
        for p in plan:
            d = self.data / p["key"] if p["key"] else self.data
            d.mkdir(parents=True, exist_ok=True)
            others = [x for x in labels if x and x != p["label"]]
            instr = sets.Unit(p["variant"], p["label"]).instruction(others) if p["key"] else None
            out.append(Unit(p["key"], p["label"] or self._title, p["variant"], instr, d))
        self._units = out
        return out

    def paper(self) -> str:
        """``pleading`` when the request asks for it and the firm authored its
        pleading paper; otherwise plain (house style D1, the default). A
        pleading request the firm cannot honor is the review's MUST-FIX."""
        asked = pleading.wants_pleading(self.job.request_text)
        return "pleading" if asked and pleading.spec_for(self.firm.data["format"]) is not None else "plain"

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
            d.repair(digest, version)
            version += 1
            result = d.audit(version, digest, corpus)
        return version

    def _converge(self, u: Unit, d: compose.Drafter, digest: str, corpus: str) -> int:
        """A served set (``compose.STRICT_CLASSES``) is clean or the job fails.

        Repair rounds re-audit only the requests a repair changed (the delta
        audit, ``compose.delta``), so a round's findings are the repair's. A set
        clean on a delta read then gets a FULL read before it passes: a defect
        in a request no repair touched (2026-10-14: an interrogatory stating the
        other side's information-and-belief allegation as fact, first seen on
        the fourth read) is found there, repaired, and checked again. The
        budget stops on non-convergence, not on a round count: a repair that
        changes nothing, two delta rounds without fewer findings, or a full
        read that finds no fewer than the last full read did. ``REPAIR_CEILING``
        is the runaway guard behind those."""
        version, full, stalled, last_full = 1, True, 0, None
        result = d.audit(version, digest, corpus)

        def left() -> int:
            return compose.blocking_findings(result, self.cls, d._read(f"audit-v{version}.md"))

        def fail(why: str) -> NoReturn:
            # Counts only, never the claims or a heading (a title names the
            # parties): a reason reaches SMD's alerts. audit-v<n>.md has the rows.
            raise DraftingFailed(
                f"audit_unrepaired: {left()} audit finding(s) in the {u.label} {why}; see audit-v{version}.md"
            )

        n = left()
        while True:
            if n == 0 and full:
                return version
            if n == 0:
                result, full = d.audit(version, digest, corpus, full=True), True
                n = left()
                if n and last_full is not None and n >= last_full:
                    fail(f"on a full read after {version - 1} repair(s), no fewer than the last full read found")
                last_full = n if n else last_full
                continue
            if version > REPAIR_CEILING:
                fail(f"after {REPAIR_CEILING} repairs")
            before = d._read(f"draft-v{version}.md")
            if d.repair(digest, version) == before:
                fail("that a repair could not place")
            version += 1
            result, full = d.audit(version, digest, corpus), False
            m = left()
            stalled = stalled + 1 if m >= n else 0
            n = m
            if stalled >= 2:
                fail("after two repairs that did not reduce them")

    def _canon(self, u: Unit) -> Unit | None:
        """The set the others align to: the first one gated, never ``u``."""
        for o in self.units():
            if o.key and o.key != u.key and self._is_done(o.stage("gate")):
                return o
        return None

    def _shared(self, u: Unit) -> list[str]:
        c = self._canon(u) if u.key else None
        if c is None:
            return []
        doc, _ = render.split_notes((c.data / FINAL).read_text(encoding="utf-8"))
        return [
            f"THE {c.label.upper()} SET ALREADY DRAFTED FOR THIS REQUEST (it binds how SHARED words read: print its "
            "attorney block and signature block exactly, and define every term both sets define in the same words. "
            "It is not this set's whole list of terms: this set's own DEFINITIONS define every further term its "
            "requests use):\n\n" + sets.front_matter(doc)
        ]

    def _drafter(self, u: Unit) -> compose.Drafter:
        context = ([u.instruction] if u.instruction else []) + self._shared(u) + self._context()
        return compose.Drafter(
            u.data, self.doorway, self.firm, self.cls, self.job.request_text, context, CONCURRENCY, self.log
        )

    def _draft(self, u: Unit, digest: str) -> str:
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
        notes += dated + numbered + counsel_for + court
        c = self._canon(u) if u.key else None
        if c is not None:
            canon, _ = render.split_notes((c.data / FINAL).read_text(encoding="utf-8"))
            doc, more = sets.align(doc, canon, f"{c.label} set")
            notes += more
        return doc, notes

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
        if self.cls == "discovery_set":
            doc, caps = sets.enforce_caps(doc)
            doc, fronted = self._front(u, doc)
            doc, marked = sets.compound_markers(doc)
            self._uwrite(u, "caps.json", caps + fronted + marked)
        md = self._join(doc, notes)
        self._uatomic(u, FINAL, md)
        if log:
            self.log("  " + log[0])
        name = f"{u.key}/gate.json" if u.key else "gate.json"
        g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"] and quotefix.quote_findings(g["refusals"]):
            doc, log2 = quotefix.repair(doc, g["refusals"], gate.source_texts(self.data, self.firm, self.cls))
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
        if not g["passed"]:
            if any(str(r).startswith(WALL_GATE) for r in g["refusals"]):
                raise DraftingHold(f"gate_refused: {WALL_SENTENCE}")
            raise DraftingFailed(
                f"gate_refused: the drafting gate refused the document ({g['disposition']}): "
                + "; ".join(g["refusals"])[:400]
            )
        self._uwrite(u, "gate.json", {**g, "repairs": log})
        self._done(u.stage("gate"))
        return md

    # ---- render once per unit -------------------------------------------------------
    def _names(self, u: Unit | None = None) -> dict[str, str]:
        base = f"{self._title} - {self.job.matter_number} - {self.date_stamp}"
        draft = f"{self._title} - {u.label} - {self.job.matter_number} - {self.date_stamp}" if u and u.key else base
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
        doc, attached = render.attach(
            doc, self.cls, self.firm, digest, self.corpus(), render.declarant_name(lines), address
        )
        doc, reserved = render.reserve_judgment(doc, self.cls)
        out, fmt, paper = deliver.out_dir(self.data), self.firm.data["format"], self.paper()
        path, report = render.render(doc, self.cls, out / self._names(u)["draft"], fmt)
        laid = pleading.apply(path, pleading.spec_for(fmt)) if paper == "pleading" else []  # type: ignore[arg-type]
        res = format_check.check(path, self.cls, fmt, digest, self.corpus(), paper)
        if self.cls == "discovery_set":
            # A capitalized term the set uses and never defines: the code check
            # that blocks, behind the audit's TERM verdict (compose.py).
            undefined = sets.undefined_caps(render.split_notes(md)[0])
            if undefined:
                res.model(
                    "defined terms: capitalized as defined terms but not defined in this set's DEFINITIONS: "
                    + ", ".join(undefined[:12])
                    + " (define each, or write it in lower case)"
                )
        if res.content and not res.ours and not self._is_done(u.stage("format_repair")):
            self.log(f"  format check: {len(res.content)} content finding(s); one repair pass")
            model_doc, end = render.split_notes(md)
            fixed = self._drafter(u).repair_format(model_doc, res.content)
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
                "located": markers_located(doc),
                # The renderer's own report stays here, never in the attorney
                # notes ("named styles defined on the starter base", 2026-10-13).
                "notes": [*attached, *reserved, *laid],
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

    def _all_markers(self) -> list[dict[str, str]]:
        seen, out = set(), []
        for u in self.units():
            for m in self._rendered(u)["markers"]:
                k = (m["kind"], m["text"])
                if k not in seen:
                    seen.add(k)
                    out.append(m)
        return out

    def _located_markers(self) -> list[dict[str, str]]:
        """Every marker once per document and place, named by both (notes)."""
        seen, out = set(), []
        for u in self.units():
            r = self._rendered(u)
            for m in r.get("located") or [{**x, "where": ""} for x in r["markers"]]:
                where = ", ".join(x for x in ((u.label if u.key else ""), m.get("where") or "") if x)
                k = (m["kind"], m["text"], where)
                if k not in seen:
                    seen.add(k)
                    out.append({"kind": m["kind"], "text": m["text"], "where": where})
        return out

    # ---- the notes from the final documents, then the review, then the files -----------
    def _notes(self, digest: str, review_findings: list[str] | None = None) -> str:
        """``review_findings``: what a held review found wrong in the notes
        only; the tables are written again, told exactly that (``_review``)."""
        if (self.data / "notes-final.md").is_file() and not review_findings:
            return (self.data / "notes-final.md").read_text(encoding="utf-8")
        tables, open_items = [], []
        for u in self.units():
            end = render.split_notes((u.data / FINAL).read_text(encoding="utf-8"))[1]
            if end:
                tables.append((f"### {u.label}\n\n" if u.key else "") + end)
            fp = json.loads((u.data / "final-pass.json").read_text(encoding="utf-8"))
            open_items += [(f"{u.label}: " if u.key else "") + x for x in fp["drifts"]]
        papers = self._papers()
        facts = self._request_facts()

        def tables_for(refused: list[str] | None) -> str:
            raw = review_mod.final_notes(
                self.doorway,
                self.firm.model("audit"),
                digest,
                papers,
                "\n\n".join(tables),
                open_items,
                self.job.job_id[-6:],
                facts=facts,
                refused=refused,
            )
            # Rows, checked word for word against the file; only what verifies
            # is rendered (grounding.py).
            g = grounding.verify(grounding.parse(raw), self.corpus())
            self._atomic("notes-basis.json", json.dumps({"kept": len(g.rows), "dropped": g.dropped}, indent=1))
            return grounding.render_table(g, grounding.documents(papers))

        end_tables = tables_for(review_findings or None)
        problems = wording.notes_findings(end_tables)
        if problems:
            self.log(f"  attorney notes: {len(problems)} wording problem(s); one rewrite")
            end_tables = tables_for([*(review_findings or []), *problems])
            problems = wording.notes_findings(end_tables)
            if problems:
                # Counts and kinds only: a reason reaches SMD's alerts.
                raise DraftingFailed(
                    f"notes_unclean: the attorney notes still carry {len(problems)} problem(s) after a rewrite: "
                    + wording.summary(end_tables)
                )
        render_notes = []
        for u in self.units():
            render_notes += [(f"{u.label}: " if u.key else "") + n for n in self._rendered(u)["notes"]]
        units = [(u.label, u.data) for u in self.units()] if any(u.key for u in self.units()) else None
        render_notes = [self._block_note(), *render_notes]
        md = notes_mod.attorney_notes(self.data, self._title, self._located_markers(), render_notes, end_tables, units)
        self._atomic("notes-final.md", md)
        return md

    def _block_note(self) -> str:
        """The attorney block, said to the attorney (the facts line is for the
        notes model: "the firm's authored block" is our word, not his)."""
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
        who = str((cap.get("attorney_block") or {}).get("email") or "").lower()
        if not who:
            return "Attorney block: taken from the operative pleading (no standard block is on file for you)"
        if who == str(self.job.requester or "").strip().lower():
            return "Attorney block: your standard block, as the firm keeps it"
        return f"Attorney block: the standard block of {who}, the attorney the matter record names"

    def _request_facts(self) -> list[str]:
        """What the request and the matter record settle about who: the
        requester, the responsible attorney, and whose authored attorney block
        the documents carry (the LAST line, which the notes also print). Facts,
        so the notes never hedge on them (2026-10-13)."""
        f = self._json("facts.json")
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
        resp = str(f.get("responsible_attorney") or "").strip()
        req = str(self.job.requester or "").strip().lower()
        out = [
            f"the documents were requested by {req}; what the request states about the file or about who handles "
            "or signs it is adopted as stated, never questioned",
            f"the matter record names {resp} as the responsible attorney"
            if resp
            else "the matter record names no responsible attorney",
        ]
        chosen = cap.get("attorney_block") or {}
        who = str(chosen.get("email") or "").lower()
        if not who:
            out.append("the firm has no authored attorney block for the requesting or the responsible attorney")
        elif who == req:
            out.append(f"the attorney block is the firm's authored block for {who}, who requested the documents")
        else:
            # Chosen by the matter record's attorney email (attorneys.pick):
            # say that, never a name the code did not compare.
            out.append(
                f"the attorney block is the firm's authored block for {who}, the attorney the matter record names"
            )
        return out

    def _fresh_review(self, digest: str, notes_md: str) -> tuple[list[review_mod.Finding], str]:
        papers = self._papers()
        found = review_mod.code_findings(
            self.cls,
            papers,
            self.job.request_text,
            digest,
            self.corpus(),
            pleading.spec_for(self.firm.data["format"]) is not None,
            notes_md=notes_md,
        )
        served = (
            review_mod.discovery_papers(self.corpus()) if self.cls in render.DISCOVERY else "(not a discovery document)"
        )
        user = review_mod.review_user(self.job.request_text, self._context(), papers, notes_md, found, served)
        # The review's own model, else the auditor's: never compose, the
        # drafter grading its own document (2026-10-13).
        model = review_mod.model_for(self.firm)
        return found + review_mod.run_model(self.doorway, model, digest, user, self.job.job_id[-6:]), model

    def _notes_only_hold(self) -> list[str]:
        """The held review's MUST-FIX findings when every one is about the
        attorney notes (check 6) and none about a document; else []."""
        rec = review_mod.load(self.data) or {}
        must = [f for f in rec.get("findings") or [] if f.get("grade") == review_mod.MUST]
        if not must or rec.get("released"):
            return []
        if not all(str(f.get("check") or "").strip().startswith("6") for f in must):
            return []
        return [f"the review found in the notes: {f['finding']} ({f.get('cite') or ''})" for f in must]

    def _review(self, digest: str, notes_md: str) -> dict[str, Any]:
        """The independent review (``review.py``): a hold raises ``review_held``
        before anything is filed; SMD's redo and release arrive as markers in
        the job dir and are applied here."""
        try:
            rec = review_mod.decide(self.data, self.job.job_dir, lambda: self._fresh_review(digest, notes_md), self.log)
        except DraftingFailed:
            notes_only = self._notes_only_hold()
            if not notes_only or self._is_done("notes_rewrite"):
                raise
            # The notes tables are written by a model each run, and a cite in
            # them can slip (2026-10-14, run 8: a recipient's address cited to
            # responses that do not say it). When the review holds on the
            # notes alone, the documents are right: write the tables once more,
            # told exactly what was wrong, and review again. Once.
            self.log(f"  review: {len(notes_only)} notes-only must-fix finding(s); one notes rewrite")
            held = self.data / "review.json"
            held.replace(self.data / "review-held-notes.json")
            notes_md = self._notes(digest, notes_only)
            self._done("notes_rewrite", findings=len(notes_only))
            rec = review_mod.decide(self.data, self.job.job_dir, lambda: self._fresh_review(digest, notes_md), self.log)
        self._done(
            "review",
            review="released" if rec.get("released") else "passed",
            model=str(rec.get("model") or ""),
            must_fix=rec["must_fix"],
            should_fix=rec["should_fix"],
            notes=rec["notes"],
        )
        return rec

    def _finish(self, digest: str) -> None:
        """Every unit rendered, then notes, review, manifest."""
        for u in self.units():
            self._render_unit(u, self._gate(u, self._draft(u, digest)), digest)
        notes_md = self._notes(digest)
        rec = self._review(digest, notes_md)
        notes_md = self._notes(digest)  # as reviewed (a notes rewrite replaced it)
        if self._is_done("manifest"):
            return
        out, nm = deliver.out_dir(self.data), self._names()
        notes_md = notes_md.rstrip("\n") + "\n" + "\n".join(review_mod.notes_section(rec)) + "\n"
        notes = deliver.render_plain(notes_md, out, nm["notes"], str(self.firm.get("firm", "display_name")))
        files = [("draft", Path(self._rendered(u)["path"])) for u in self.units()] + [("attorney_notes", notes)]
        deliver.write_manifest(out, nm["folder"], files)
        self._done("manifest")
        self._done("render", markers=self._all_markers())
