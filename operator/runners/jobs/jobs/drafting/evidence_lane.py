"""The ledger path through the drafting job: every cite, quote, figure and date
in the filed brief written by code from verified facts.

Chosen per firm and class by ``evidence.mode: ledger`` (``evidence_config``);
every other job takes the digest path unchanged. The graph, after the shared
head (facts, pull, preflight, transcription)::

    evidence-extract -> evidence-verify -> evidence-images -> index
      -> [howell] -> per document:
         compose (markup) -> lint (one scoped repair, else markup_lint)
         -> source check (verify.check; two-of-two, planted controls;
            <= max_loops scoped repairs on the markup, each linted)

No digest audit, repair rounds or final pass on this path: they read the
brief against the digest, which is not evidence (and they were the largest
cost line: $37.30 measured on the job this was designed on). The source
check, against the cited pages, and its scoped repairs are this path's audit.
         -> render LAST -> gate (read-only) + span proof -> the shared tail

**Fixers off.** On this path the gate's quotation, cite and pin repairs and
the nested-quote repair never run: they edit text code wrote. The gate (pin
check included) only reads, and ``render.assert_spans`` proves every rendered
span is still exactly what code wrote and traces to a verified fact; a change
to one, by anything after render, is a gate failure.

This is a mixin ahead of ``Finish`` in ``DraftingRun``'s bases: ``_draft``,
``_gate`` and ``_notes`` fall through to the digest path's when the job is not
on the ledger path. Every paid call goes through the job's doorway, so the
budget checks every call as it does on the digest path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..demand import draft as d_mod
from ..demand.gapaudit import output_max
from ..demand.summarize import sha
from . import caption_stage, compose, evidence_config, gate, render
from .evidence import extract, images, index, lint as lint_mod, prompts as ev_prompts, render as ev_render, verify
from .evidence.shapes import Candidate, DocPages, Ledger, Span
from .outcome import WALL_SENTENCE, DraftingFailed, DraftingHold
from .verify import authorities as auth, check as check_mod, config as verify_config, controls_adapter
from .verify.sources import Sources

if TYPE_CHECKING:
    from .finish import Unit

EV = "evidence"
FINAL = "draft-final.md"
SPANS = "evidence-spans.json"
CHECK_NOTES = "check-notes.md"
CHECK_MAX_TOKENS = 16_000
CHARGES_LINE = (
    "THE CHARGES TABLE is computed by code from the bills, EOBs, liens, ledgers and the Medicals tab. Place it with "
    "{{T:charges}} where the skeleton places it; never retype a figure from it."
)
#: The audit and the repairs read the document in markup: what a token is.
READING_MARKUP = (
    "THE DOCUMENT IS WRITTEN IN MARKUP. Code fills every token after the audit: {{F:F0001}} is the cite of fact "
    "F0001, {{Q:F0001}} its excerpt quoted, {{N:F0001}} its figure, {{D:F0001}} its date, {{SUM:...}} / "
    "{{COUNT:...}} / {{SPAN:...}} a total, count or interval code computes, {{T:charges}} the charges table, "
    "{{A:...}} a question for the attorney, {{L:...}} a legal authority. Read each token as the fact the digest line "
    "[F0001] carries; a token is never itself a finding. A repair keeps tokens as tokens and never types a cite, "
    "quotation, figure or date."
)


class WindowExtractor(extract.Extractor):
    """``extract.Extractor`` at the firm's window size (``evidence.page_window``)."""

    def __init__(self, *a: Any, size: int = extract.WINDOW_PAGES, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.size = size

    def run(self) -> list[Candidate]:
        from concurrent.futures import ThreadPoolExecutor

        ws = extract.windows(self.corpus, self.model, size=self.size, prompt=self.prompt)
        fresh = sum(1 for w in ws if not (self.cache / f"{w.sha}.json").is_file())
        self.log(f"  evidence: {len(ws)} window(s) over {len(self.corpus)} document(s); {fresh} to read")
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            return [c for rs in ex.map(self.read, ws) for c in rs]


class MarkupDrafter(compose.Drafter):
    """The digest path's drafter, composing over the fact index in markup:
    the MARKUP block is appended to every compose and repair system prompt,
    after whatever the firm authored, because lint and render depend on it."""

    compose_prompt: str | None = None

    def compose_system(self) -> str:
        blocks = [
            "## THE HOUSE STYLE (the attorney's own drafting instructions; binding)\n\n" + self.firm.style,
            "## THE SKELETON (structure is fixed; fill it)\n\n" + self.skeleton(),
            self.compose_prompt or self.firm.prompt(self.cls, "compose"),
        ]
        ex = self.exemplars()
        if ex:
            blocks.append("## EXEMPLARS (their FORMAT and VOICE; their cites are not this brief's)\n\n" + ex)
        return "\n\n---\n\n".join([*blocks, ev_prompts.MARKUP_BLOCK])

    def compose(self, digest: str) -> str:
        """``digest`` here is the FACT INDEX (``index.build``)."""
        if (self.data / "draft-v1.md").is_file():
            return self._read("draft-v1.md")
        user = (
            self.context
            + "\n\n---\n\nTHE FACT INDEX (the only source; name each fact by its id, in a token):\n\n"
            + digest
        )
        system = self.compose_system()
        if len(system) + len(user) > compose.COMPOSE_INPUT_MAX_CHARS:
            raise compose.DraftingError(
                f"compose input is {len(system) + len(user):,} characters, over {compose.COMPOSE_INPUT_MAX_CHARS:,}"
            )
        r = self._call("compose", system, user, f"compose-{sha(user)[:8]}")
        self._commit("draft-v1.md", r.text)
        return r.text

    def scoped(self, md: str, heads: list[str], brief: str, tag: str, lead: str) -> str:
        """One scoped repair of the named sections of the document part, told
        exactly what was refused; the notes part is kept as it was."""
        doc, notes = render.split_notes(md)
        secs = [(h, b) for h, b in d_mod.sections(doc) if h in heads]
        if not secs:
            return md
        user = lead + "\n## FINDINGS\n" + brief + "\n\n## SECTIONS TO REPAIR\n\n" + "\n\n".join(b for _h, b in secs)
        system = self.compose_system() + "\n\n---\n\n" + self.firm.prompt(self.cls, "repair")
        r = self._call("repair", system, user, f"{tag}-{sha(user)[:8]}")
        merged, missing = d_mod.merge_repair(doc, r.text, [h for h, _b in secs])
        if missing:
            raise compose.DraftingError(f"{tag} repair did not return {len(missing)} section(s)")
        return merged.rstrip() + "\n" + (f"\n{render.NOTES_MARK}\n\n{notes}\n" if notes else "")


def heads_for(doc: str, sentences: list[str]) -> list[str]:
    """The sections of ``doc`` whose text carries any of the sentences."""
    want = [" ".join(s.split()) for s in sentences if s and s.strip()]
    out = []
    for h, body in d_mod.sections(doc):
        flat = " ".join(body.split())
        if any(w[:200] in flat for w in want):
            out.append(h)
    return out


class EvidenceLane:
    """Mixin: ``DraftingRun`` supplies data, job, firm, cls, doorway, log,
    _stage/_done/_is_done/_json/_atomic/_write, _context, _corpus_text, and
    Finish's _drafter/_shared/units."""

    if TYPE_CHECKING:
        data: Path
        job: Any
        firm: Any
        cls: str
        doorway: Any
        log: Callable[[str], None]

        def _stage(self, name: str, fn: Callable[[], Any]) -> None: ...
        def _done(self, stage: str, **info: Any) -> None: ...
        def _is_done(self, stage: str) -> bool: ...
        def _put(self, key: str, value: Any) -> None: ...
        def _atomic(self, name: str, text: str) -> None: ...
        def _context(self) -> list[str]: ...
        def _corpus_text(self) -> str: ...
        def _shared(self, u: Unit) -> list[str]: ...

        def units(self) -> list[Unit]: ...

    # ---- settings and job-wide state ------------------------------------------------------
    @property
    def evidence(self) -> evidence_config.Settings:
        got = getattr(self, "_ev_settings", None)
        if got is None:
            got = evidence_config.settings(self.firm.data, self.cls, self.firm.read)
            self._ev_settings = got
        return got

    def _ev_write(self, name: str, text: str) -> None:
        """An evidence artifact a resume trusts: written whole or not at all."""
        p = self.data / EV
        p.mkdir(parents=True, exist_ok=True)
        (p / f".{name}.tmp").write_text(text, encoding="utf-8")
        (p / f".{name}.tmp").replace(p / name)

    def _ev_corpus(self) -> dict[str, DocPages]:
        got = getattr(self, "_ev_docs", None)
        if got is None:
            got = extract.load_corpus(self.data)
            self._ev_docs = got
        return got

    def ledger(self) -> Ledger:
        got = getattr(self, "_ev_ledger", None)
        if got is None:
            got = Ledger.from_jsonl((self.data / EV / "ledger.jsonl").read_text(encoding="utf-8"))
            self._ev_ledger = got
        return got

    def store(self) -> auth.Store:
        got = getattr(self, "_ev_store", None)
        if got is None:
            s = verify_config.settings(self.firm.data, self.cls)
            paths = verify_config.authority_paths(s.authorities, self.firm.data["inputs"]) if s.authorities else []
            got = auth.Store.from_inputs(self.firm.root, paths)
            self._ev_store = got
        return got

    def charges(self) -> list[dict[str, Any]] | None:
        p = self.data / "howell.json"
        return json.loads(p.read_text(encoding="utf-8"))["table"] if p.is_file() else None

    # ---- the job-wide stages -------------------------------------------------------------
    def evidence_digest(self) -> str:
        """The four stages, then the index's digest-compatible text."""
        self._stage("evidence-extract", self._ev_extract)
        self._stage("evidence-verify", self._ev_verify)
        self._stage("evidence-images", self._ev_images)
        self._stage("index", self._ev_index)
        return (self.data / "digest.md").read_text(encoding="utf-8")

    def _ev_extract(self) -> None:
        ev = self.evidence
        try:
            cands = WindowExtractor(
                self.data,
                self.doorway,
                self.firm.model("digest"),
                self._ev_corpus(),
                prompt=ev.extract_prompt or ev_prompts.EXTRACT_PROMPT,
                workers=4,
                log=self.log,
                size=ev.page_window,
            ).run()
        except extract.ExtractError as exc:
            raise compose.DraftingError(f"evidence-extract: {exc}") from None
        self._ev_write("candidates.jsonl", "".join(json.dumps(c.to_json()) + "\n" for c in cands))

    def _ev_verify(self) -> None:
        rows = (self.data / EV / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        cands = [Candidate.from_json(json.loads(r), json.loads(r).get("window") or "") for r in rows if r.strip()]
        led = verify.verify(cands, self._ev_corpus())
        self._ev_write("ledger-verified.jsonl", led.to_jsonl())
        self._put("evidence-stats", verify.stats(led))

    def _ev_images(self) -> None:
        led = Ledger.from_jsonl((self.data / EV / "ledger-verified.jsonl").read_text(encoding="utf-8"))
        left = [self.evidence.image_pages]

        def capped(src: str | None, page: int, log: Callable[[str], None], name: str) -> str | None:
            # Past the cap a page is not read: its fact goes to the attorney as unconfirmed.
            if left[0] <= 0:
                return None
            left[0] -= 1
            return images._render(src, page, log, name)

        out = images.confirm(
            led, self._ev_corpus(), self.data, self.doorway, self.firm.model("transcription"), self.log, capped
        )
        self._ev_write("ledger.jsonl", out.to_jsonl())
        self._ev_ledger = out

    def _ev_index(self) -> None:
        from .run import DIGEST_BUDGET_CHARS

        idx = index.build(self.ledger(), DIGEST_BUDGET_CHARS, self.log)
        self._ev_write("index.md", idx.text)
        self._ev_write("index.json", json.dumps({"kept": len(idx.kept), "trimmed": list(idx.trimmed)}, indent=1))
        self._atomic("digest.md", idx.digest)

    # ---- per document ---------------------------------------------------------------------
    def _markup_drafter(self, u: Unit) -> MarkupDrafter:
        context = [READING_MARKUP, *([u.instruction] if u.instruction else []), *self._shared(u), *self._context()]
        d = MarkupDrafter(
            u.data,
            self.doorway,
            self.firm,
            self.cls,
            self.job.request_text,
            context,
            4,
            self.log,
            skeleton=self._skeleton(),
        )
        d.compose_prompt = self.evidence.compose_prompt
        return d

    def _skeleton(self) -> str:
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else None
        return caption_stage.fill_skeleton(self.firm.skeleton(self.cls), cap)

    def exempt(self) -> list[str]:
        """The firm's own words and the code-read record fields: lint never
        refuses a sentence that stands in them."""
        out = [p for p in self._skeleton().split("\n\n") if p.strip()]
        for block in self._context():
            out += [ln for ln in block.splitlines() if ln.strip()]
        return out

    def lint_doc(self, md: str) -> list[Any]:
        return lint_mod.lint(render.split_notes(md)[0], self.ledger(), self.exempt(), self.store())

    def lint_fix(self, u: Unit, d: MarkupDrafter, md: str, tag: str) -> str:
        """Lint; on findings, ONE scoped repair; findings left fail the job."""
        found = self.lint_doc(md)
        if not found:
            return md
        cache = u.data / f"lint-{tag}.md"
        if cache.is_file():
            fixed = cache.read_text(encoding="utf-8")
        else:
            heads = heads_for(render.split_notes(md)[0], [f.sentence or f.snippet for f in found])
            fixed = d.scoped(md, heads, lint_mod.repair_brief(found), f"lint-{tag}", ev_prompts.LINT_REPAIR)
            (u.data / f".lint-{tag}.md.tmp").write_text(fixed, encoding="utf-8")
            (u.data / f".lint-{tag}.md.tmp").replace(cache)
        left = self.lint_doc(fixed)
        (u.data / f"lint-{tag}.json").write_text(
            json.dumps({"before": lint_mod.counts(found), "after": lint_mod.counts(left)}, indent=1), encoding="utf-8"
        )
        if left:
            # Counts and codes only: a reason reaches SMD's alerts, never brief text.
            raise DraftingFailed(
                f"markup_lint: {len(left)} hand-typed or invalid item(s) remain after one repair at {tag}: "
                + json.dumps(lint_mod.counts(left))
            )
        return fixed

    def _draft(self, u: Unit, digest: str) -> str:
        if not self.evidence.ledger:
            return super()._draft(u, digest)  # type: ignore[misc]
        out = u.data / FINAL
        if out.is_file():
            return out.read_text(encoding="utf-8")
        d = self._markup_drafter(u)
        first = d.compose((self.data / EV / "index.md").read_text(encoding="utf-8"))
        refused = render.needs_attorney(first)
        if refused:
            raise DraftingHold(f"request_incomplete: the request is missing what the draft needs: {refused}")
        self._done(u.stage("compose"))
        md = self.lint_fix(u, d, first, "compose")
        if md != first:
            (u.data / "draft-v1.md").replace(u.data / "draft-v1.unlinted.md")
            (u.data / "draft-v1.md").write_text(md, encoding="utf-8")
        # No digest audit on this path: it reads the brief against the digest,
        # which is not evidence. The source check (against the cited pages)
        # and its scoped repairs are this path's audit.
        md = self._source_check(u, d, md)
        rendered = self._render(u, md)
        body = {"version": 1, "settled": [], "drifts": [], "path": "ledger"}
        (u.data / "final-pass.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
        self._done(u.stage("finalpass"))
        return rendered

    # ---- the source check ------------------------------------------------------------------
    def _asker(self, model: str, stage: str) -> Callable[[str, str], str]:
        mx = output_max(model, CHECK_MAX_TOKENS)

        def ask(system: str, user: str) -> str:
            r = self.doorway.call(
                stage,
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=min(mx, CHECK_MAX_TOKENS),
                stream=True,
                custom_id=f"{stage[:6]}-{sha(system + user)[:16]}",
            )
            return str(r.text or "")

        return ask

    def _second_model(self, first: str) -> str:
        m = self.firm.data["models"]
        return m["audit"] if first != m["audit"] else str(m.get("review") or first)

    def _source_check(self, u: Unit, d: MarkupDrafter, md: str) -> str:
        """The check over the markup; a two-of-two wrong sentence gets a scoped
        repair (linted), up to ``max_loops``; what survives is disclosed in
        the attorney notes. INVALID controls fail the job."""
        s = verify_config.settings(self.firm.data, self.cls)
        done = u.data / "markup-checked.md"
        if done.is_file():
            return done.read_text(encoding="utf-8")
        if not s.enabled:
            self._done(u.stage("check"), skipped="verify.enabled is false for this class")
            return md
        sources = Sources.from_docs(self._ev_corpus().values(), self.log)
        ask, second = self._asker(s.model, "source_check"), self._asker(self._second_model(s.model), "source_check_2")
        report = None
        for loop in range(s.max_loops + 1):
            try:
                report = check_mod.run(
                    render.split_notes(md)[0],
                    self.ledger(),
                    sources,
                    ask,
                    store=self.store(),
                    second=second,
                    cache_dir=u.data / EV / "check-cache",
                    parallelism=s.parallelism,
                    seed=0,
                    log=self.log,
                    out_dir=u.data,
                    job={"job_id": self.job.job_id, "class": self.cls},
                )
            except controls_adapter.ControlsInvalid:
                raise DraftingFailed(
                    "check_invalid: the source check's planted controls did not discriminate; nothing was filed; "
                    "see check-controls.json"
                ) from None
            wrong = report.blocking()
            if not wrong or loop == s.max_loops:
                break
            heads = heads_for(render.split_notes(md)[0], [i.sentence for i in wrong])
            md = d.scoped(md, heads, report.repair_brief(), f"check{loop}", ev_prompts.LINT_REPAIR)
            md = self.lint_fix(u, d, md, f"check{loop}")
        assert report is not None
        (u.data / CHECK_NOTES).write_text(report.notes_md(), encoding="utf-8")
        done.write_text(md, encoding="utf-8")
        c = report.counts()
        self._done(u.stage("check"), **{k: c[k] for k in ("confirmed", "wrong", "framing", "disclosed")})
        return md

    # ---- render, last ----------------------------------------------------------------------
    def _render(self, u: Unit, md: str) -> str:
        doc, notes = render.split_notes(md)
        try:
            r = ev_render.render(doc, self.ledger(), self.charges(), None, self.store())
        except ev_render.RenderError as exc:
            raise DraftingFailed(
                f"markup_lint: render refused {len(exc.findings)} token(s): "
                + json.dumps(lint_mod.counts(exc.findings))
            ) from None
        try:
            end = ev_render.render(notes, self.ledger(), self.charges(), None, self.store()).text if notes else ""
        except ev_render.RenderError:
            end = ""  # the drafter's own end tables are written again from the final documents
        (u.data / SPANS).write_text(json.dumps([s.to_json() for s in r.spans], indent=1), encoding="utf-8")
        out = r.text.rstrip() + "\n" + (f"\n{render.NOTES_MARK}\n\n{end}\n" if end else "")
        (u.data / f".{FINAL}.tmp").write_text(out, encoding="utf-8")
        (u.data / f".{FINAL}.tmp").replace(u.data / FINAL)
        return out

    def spans(self, u: Unit) -> list[Span]:
        return [Span.from_json(x) for x in json.loads((u.data / SPANS).read_text(encoding="utf-8"))]

    # ---- the gate, read-only ---------------------------------------------------------------
    def _gate(self, u: Unit, md: str) -> str:
        if not self.evidence.ledger:
            return super()._gate(u, md)  # type: ignore[misc]
        if self._is_done(u.stage("gate")):
            return (u.data / FINAL).read_text(encoding="utf-8")
        doc, _notes = render.split_notes(md)
        name = f"{u.key}/gate.json" if u.key else "gate.json"
        g = gate.run(self.data, self.firm, self.cls, doc, name=name)
        if not g["passed"]:
            if any(str(r).startswith("[1]") for r in g["refusals"]):
                raise DraftingHold(f"gate_refused: {WALL_SENTENCE}")
            raise DraftingFailed(
                f"gate_refused: the drafting gate refused the document ({g['disposition']}): "
                + "; ".join(g["refusals"])[:400]
            )
        rep = ev_render.assert_spans(
            doc, self.spans(u), self.ledger(), self.charges(), None, self.exempt(), self.store()
        )
        if not rep.ok:
            codes = lint_mod.counts(rep.problems)
            raise DraftingFailed(
                f"gate_refused: {len(rep.problems)} code-written span(s) changed or untraced after render: "
                + json.dumps(codes)
            )
        g["spans"] = {"spans": rep.spans, "traced": rep.traced}
        (u.data / "gate.json").write_text(json.dumps({**g, "repairs": []}, indent=1), encoding="utf-8")
        self._done(u.stage("gate"))
        return md

    # ---- the notes carry the check ---------------------------------------------------------
    def _notes(self, digest: str, review_findings: list[str] | None = None) -> str:
        md = super()._notes(digest, review_findings)  # type: ignore[misc]
        if not self.evidence.ledger or "## Source check" in md:
            return md
        parts = []
        for u in self.units():
            p = u.data / CHECK_NOTES
            if p.is_file():
                parts.append((f"### {u.label}\n\n" if u.key else "") + p.read_text(encoding="utf-8").strip())
        if not parts:
            return md
        md = md.rstrip("\n") + "\n\n" + "\n\n".join(parts) + "\n"
        self._atomic("notes-final.md", md)
        return md
