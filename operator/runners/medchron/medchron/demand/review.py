"""The demand job's independent pre-delivery review: the last stage before
anything is filed, over the files exactly as they would be filed.

WHY (2026-10-13). Twice a coverage report went onto a firm's matter in place
of the demand that was asked for, each time on a premise verdict that did not
hold: on 2026-10-07 G2 read a denial out of the Operator's own draft and a
policy's boilerplate, and on 2026-10-09 G4 read "once a settlement has been
reached" (a med-pay carrier's standard notice) as a settlement. Both were
marked delivered and nobody was alerted; SMD found them by reading the work.
The premise gate is code and its phrase lists will always have a next case,
so the report it produces is now read, before filing, by a reviewer whose
only question is whether the quoted source actually establishes each failure
the report states.

Two documents, two reviews, one rule (``proof.review``):

* **The demand package** (the letter, the gap audit, the attorney notes).
  Code first, on the text of the rendered files: the system's own vocabulary
  or a sentinel line in anything the firm reads is MUST-FIX; a requested
  deliverable with no file is MUST-FIX; the RE block disagreeing with the
  matter record (``crosscheck``, already a hold before render, re-read here
  over the final text) is MUST-FIX; a dollar figure in the letter that is in
  neither the record nor the figures code computed from it is only SHOULD-FIX
  (a demand's totals are sums the record never states, so code cannot settle
  it); a date in the letter the record never states is a NOTE. Then a model
  with its own adversarial prompt, never the drafter's.
* **The coverage report.** Code checks its wording; the model reads every
  premise failure the report states against the source the premise stage
  quoted, and a failure the source does not establish holds the job.

A hold is ``failed`` with ``review_held:`` (a demand ``held`` is final; a
review hold must stay resumable), nothing is filed, and SMD is paged by the
daemon. ``redo`` reviews again after a fix; SMD's release files as it stands
with every finding, the released MUST-FIX ones included, in the attorney
notes (or, on a coverage report, in the report's own review section).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from ..proof import checks, review as R
from . import crosscheck

HOLD_DEMAND = (1, 2, 3, 4, 5)
HOLD_COVERAGE = (1, 2, 3)
REVIEW_MAX_TOKENS = 32_000
SOURCE_CHARS_PER_DOCUMENT = 40_000
SOURCE_CHARS_TOTAL = 240_000
REDO_MARKER = R.REDO_MARKER
#: The role labels a filed file carries (``deliver.write_manifest``), and which
#: requested deliverable each answers.
ROLES_FOR = {"demand": ("demand", "attorney_notes"), "gap_audit": ("gap_audit",)}
#: Where the review's own findings ride, in order of preference.
NOTES_ROLES = ("attorney_notes", "coverage_report", "gap_audit")

DEMAND_PROMPT = """## INDEPENDENT PRE-DELIVERY REVIEW: A DEMAND PACKAGE

You review a policy-limits demand package before it is filed on a personal-injury firm's matter. A drafting system wrote it; nothing has been delivered; your review decides whether it may be. You did not write it and you owe it nothing. Assume it carries at least one serious error until you have checked.

Read the documents as delivered (the demand letter, the gap audit, the attorney notes) against the sources given (the attorney's request, the matter record, the record digest, the figures code computed). Check each of these:

1. A STATEMENT THAT CONTRADICTS THE RECORD: an amount, a date, an injury, a diagnosis, a course of treatment, a provider, a party, as the digest or the matter record states it. MUST-FIX.
2. THE REQUEST NOT CARRIED OUT: a deliverable or an instruction in the attorney's words with nothing answering it. MUST-FIX. The firm's OPEN-ITEM MARKERS are not this: `[TO BE SUPPLIED: ...]`, `[ATTORNEY: ...]`, `[CONFIRM ... BEFORE TRANSMITTAL]`, and, when the request names them, `{{NOT IN RECORD: ...}}`, `{{FILL: ...}}`, `{{ATTORNEY: ...}}`. The firm asked for demands that go out with what the file supports, the markers say what it does not, and the attorney resolves each one before the letter is sent. A letter carrying markers IS the deliverable; their presence, their number or where they stand is never a finding. A marker may still be wrong in what it SAYS (it asks for something the record states: check 1).
3. WRONG ARITHMETIC: a total that is not the sum of its rows, a figure carried between sections that changes, a demand figure that disagrees with the specials it rests on. MUST-FIX.
4. THE WRONG PARTY OR CLIENT: a client, an insured, a carrier, a claim number or a provider attributed to the wrong person or matter. MUST-FIX.
5. THE SYSTEM'S OWN VOCABULARY in anything the firm reads: a job, a pipeline, a model, an auditor or drafter, a gate, a rule of the system, a chunk, a sentinel line ("=== ... ==="). MUST-FIX.
6. TONE AND CLARITY: a sentence an attorney would rewrite. SHOULD-FIX or NOTE, never MUST-FIX.

The CODE FINDINGS block lists what code already found; do not repeat them, and do not contradict them.

GRADES. MUST-FIX: the firm must not receive this as it is. SHOULD-FIX: a real defect the attorney can correct in review. NOTE: worth the attorney's eye. Every finding cites where it is: the document and its section, and the source (FILE: name, p. N, the digest line, or the matter record). A finding you cannot cite is not a finding.

OUTPUT, and nothing else: one line per finding, exactly

| <MUST-FIX or SHOULD-FIX or NOTE> | <check number and name> | <the finding, one or two sentences> | <citation> |

then a final line exactly `VERDICT: <n> MUST-FIX, <n> SHOULD-FIX, <n> NOTE`. No preamble, no summary, no em dashes. If you find nothing, write only the VERDICT line with zeros."""

COVERAGE_PROMPT = """## INDEPENDENT PRE-DELIVERY REVIEW: A COVERAGE POSTURE REPORT

A personal-injury firm asked for a demand to a liability carrier. A rule-based premise check read the matter file and decided the demand must NOT be written; the report below tells the firm so, and it will be filed in place of the demand. Twice such a report was wrong: once it read a denial out of the firm's own draft letter and a policy's boilerplate, once it read a carrier's "once a settlement has been reached" as a settlement. Your review decides whether this one may be filed. Assume the check misread its source until the source proves otherwise.

For EACH premise failure the report states, read the quoted text inside the source document given below and decide whether the source actually establishes it:

1. A FAILURE THE SOURCE DOES NOT ESTABLISH. It is established only if the quoted text is (a) the CARRIER'S OWN letter or email on THIS claim, not a letter or draft the firm or the Operator wrote, not a policy form, a benefit booklet, an endorsement or other boilerplate, not a provider's or a lienholder's document; (b) a present, affirmative statement, not a conditional or future one ("once", "if", "when", "until", "upon", "in the event"), not negated ("this is not a denial"), not a question, not a quotation of someone else's words; (c) about THIS matter, this client and this loss, not another claim, another claimant or another coverage (med-pay, health, workers' compensation). A document NAME alone (a file called "acceptance") establishes nothing until its text does. A failure that rests on something ABSENT from the file (no carrier identified) cannot be checked against a quote: grade it NOTE. Each failure stated that its source does not establish is MUST-FIX.
2. THE SYSTEM'S OWN VOCABULARY in the report: a job, a pipeline, a model, a rule of the system, a sentinel line. MUST-FIX.
3. A FACT IN THE REPORT THAT ITS SOURCE CONTRADICTS: a document named that is not the one quoted, a date or a party misstated. MUST-FIX.
4. TONE AND CLARITY. SHOULD-FIX or NOTE, never MUST-FIX.

The CODE FINDINGS block lists what code already found; do not repeat them.

Every finding cites the gate and the source document by name. A finding you cannot cite is not a finding.

OUTPUT, and nothing else: one line per finding, exactly

| <MUST-FIX or SHOULD-FIX or NOTE> | <check number and name> | <the finding, one or two sentences> | <citation> |

then a final line exactly `VERDICT: <n> MUST-FIX, <n> SHOULD-FIX, <n> NOTE`. No preamble, no em dashes. If every failure is established and nothing else is wrong, write only the VERDICT line with zeros."""


class ReviewHeld(RuntimeError):
    """A MUST-FIX held the job: nothing filed, ``failed`` with ``review_held``."""


def model_for(firm: Any) -> str:
    """The firm's authored ``review`` model, else its ``audit`` model: never
    the drafter's ``compose`` model grading its own work."""
    models = firm.data["models"]
    return str(models.get("review") or models["audit"])


def docx_text(path: Path) -> str:
    """A rendered Word file as text, IN DOCUMENT ORDER: each paragraph, and each
    table where it stands, its rows as ``| a | b |``. Tables appended after
    every paragraph read as a heading with nothing under it: on 2026-10-15 the
    review held a demand whose Damages section is a table on "a bare header
    with no text"."""
    import io

    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(Path(path).read_bytes()))
    out: list[str] = []
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            out.append(Paragraph(el, d).text)
        elif el.tag == qn("w:tbl"):
            out += _table_rows(Table(el, d))
    return "\n".join(out)


def _table_rows(t: Any) -> list[str]:
    out = []
    for row in t.rows:
        cells, seen = [], set()
        for c in row.cells:  # a merged cell repeats across the span; read it once
            if id(c._tc) not in seen:
                seen.add(id(c._tc))
                cells.append(" ".join(c.text.split()))
        out.append("| " + " | ".join(cells) + " |")
    return out


def delivered(manifest: list[dict[str, Any]]) -> dict[str, str]:
    """``{role: text}`` of every file the manifest would file."""
    return {str(m.get("role") or m["name"]): docx_text(Path(m["local_path"])) for m in manifest}


def _what(role: str) -> str:
    return role.replace("_", " ")


# ---- code findings ---------------------------------------------------------------
#: What only this lane writes into a firm's files and must never: the seat's
#: tool names, the stages and check labels of the demand job, its template's
#: internal name. The shared list (``checks.system_vocabulary``) carries the
#: general words; these are added here, not there, because the chronology and
#: litigation lanes file medical and court text this list would misread
#: ("the axial skeleton", a G2 lesion). Each term is a phrase, not a bare word,
#: where a record could carry the word ("symptoms manifest", "the skeleton").
#: Case matters for the check labels: they are capitals in the audit's rows.
FIRM_BANNED = (
    re.compile(r"\bsmd_[a-z_]+\b", re.I),
    re.compile(r"\b(?:root[- ]owned|root|upload|spec)[- ]manifest\b", re.I),
    re.compile(r"\bfinal[- ]audit\b|\baudit (?:loop|pass|stage)\b", re.I),
    re.compile(r"\bpremise (?:gate|check)s?\b|\b(?:drafting|format) gate\b", re.I),
    # a gate's label: "G1 passed", "G2.", "(G3)", "G1 identified carrier"; never "G40.9" or "G1P0"
    re.compile(r"\bG[1-4]\b(?=[.:)]|\s+[a-z])"),
    re.compile(r"\b(?:DRIFTS|INVENTED|ARITHMETIC)\b|\|\s*SUPPORTED\s*\||\bSUPPORTED(?=[=:])"),
    re.compile(r"\bpipeline\b", re.I),
    re.compile(
        r"\bskeleton(?:'s)? (?:marker|text|slot|section|comment|boilerplate|language)s?\b"
        r"|\b(?:fixed|firm'?s?|demand|house) skeleton\b|\bthe skeleton(?:'s)? (?:calls|says|requires)\b",
        re.I,
    ),
    re.compile(r"\bauthored[- ]spec\b|\bpointer block\b|\bdelivery note\b", re.I),
    # the system's name for the record (2026-10-15 re-run: "no cite in digest"
    # in the letter); never "digestive", never a record's own "digest" heading
    re.compile(
        r"\b(?:the|per|in|from|no cite in) (?:record )?digest\b|\bdigest(?:'s)? (?:states|says|shows|entry|entries|line|lines|cites?)\b",
        re.I,
    ),
)


def firm_banned(text: str) -> list[str]:
    """Distinct hits of ``FIRM_BANNED`` in ``text``, in order of first appearance."""
    hits: list[tuple[int, str]] = []
    for rx in FIRM_BANNED:
        hits += [(m.start(), m.group(0)) for m in rx.finditer(text or "")]
    out: list[str] = []
    for _pos, w in sorted(hits):
        if w.lower() not in (x.lower() for x in out):
            out.append(w)
    return out


def wording(texts: dict[str, str]) -> list[R.Finding]:
    """System vocabulary and sentinels in anything the firm reads: settled by
    code, before the model reads a line, and graded MUST-FIX so the job HOLDS
    with the words named (``review_held: ... says "..."``). Never rewritten
    in place: a hit is a defect at its source, which a hold makes someone fix."""
    out: list[R.Finding] = []
    for role, text in texts.items():
        said = checks.system_vocabulary(text)
        said += [w for w in firm_banned(text) if w.lower() not in said]
        for w in said:
            out.append(R.code(R.MUST, "5 system vocabulary", f'the {_what(role)} says "{w}"', _what(role)))
        for s in checks.sentinels(text):
            out.append(R.code(R.MUST, "5 system vocabulary", f'the {_what(role)} carries "{s}"', _what(role)))
    return out


def computed_figures(facts: dict[str, Any], letter_md: str) -> set[int]:
    """The figures code can derive, in cents: every charge on the matter
    record, each provider's sum and the grand sum, and each table's own column
    sum in the letter (its Total row is a sum, not a record fact)."""
    out: set[int] = set()
    grand = 0
    for row in facts.get("medicals") or []:
        each = [checks.money_in_sources(str(c.get("amount") or "")) for c in row.get("charges") or []]
        cents = [max(s) for s in each if s]
        out.update(cents)
        out.add(sum(cents))
        grand += sum(cents)
    out.add(grand)
    block: list[int] = []
    for ln in [*letter_md.splitlines(), ""]:
        if ln.lstrip().startswith("|"):
            block.append(sum(checks.money(ln)))
            continue
        if len(block) > 1:
            out.add(sum(block[:-1]))
            out.add(sum(block))
        block = []
    return out


#: An open-item marker opened and never closed on its line, a curly one the
#: same, an empty one, or the firm form's own fill-in instruction left in.
_MARK_OPEN = re.compile(r"\[(TO BE SUPPLIED|ATTORNEY|CONFIRM)\b[^\]\n]*(?=\n|$)")
_MARK_EMPTY = re.compile(
    r"\[(TO BE SUPPLIED|ATTORNEY|CONFIRM)\s*:?\s*\]|\{\{\s*(NOT IN RECORD|FILL|ATTORNEY)\s*:?\s*\}\}"
)
_CURLY_OPEN = re.compile(r"\{\{(?![^}\n]*\}\})[^\n]*")
_ABOUT_MARKERS = re.compile(
    r"\[(TO BE SUPPLIED|ATTORNEY|CONFIRM)|\{\{|placeholder|open[- ]item|\bmarkers?\b|bracket", re.I
)


def malformed_markers(letter: str) -> list[str]:
    """Open-item markers that are not well formed. A well-formed marker is the
    firm's by design and never a finding (``markers_by_design``)."""
    out = [m.group(0)[:60] for m in _MARK_OPEN.finditer(letter or "")]
    out += [m.group(0) for m in _MARK_EMPTY.finditer(letter or "")]
    out += [m.group(0)[:60] for m in _CURLY_OPEN.finditer(letter or "")]
    if "[INSERT" in (letter or ""):
        out.append("[INSERT")
    return out


def markers_by_design(found: list[R.Finding]) -> list[R.Finding]:
    """A model finding under check 2 that rests on the letter carrying open-item
    markers is graded NOTE: the markers are the deliverable working as the firm
    asked (2026-10-15 re-run: a MUST-FIX "unresolved placeholders ... no
    complete, sendable letter" held a correct demand). Code grades a malformed
    marker itself."""
    out = []
    for f in found:
        if f.by == "model" and f.check.strip().startswith("2") and _ABOUT_MARKERS.search(f.finding):
            f = R.Finding(R.NOTE, f.check, f.finding, f.cite, f.by)
        out.append(f)
    return out


def demand_code(
    texts: dict[str, str],
    *,
    wanted: Iterable[str],
    sources: str,
    computed: Iterable[int],
    facts: dict[str, Any],
    letter_md: str | None,
    drafted: date | None,
    front_keys: tuple[str, ...] | None = None,
) -> list[R.Finding]:
    """``front_keys``: the selected format's front matter (deliver.front_keys);
    absent, the firm's house format's."""
    out = wording(texts)
    for want in wanted:
        for role in ROLES_FOR.get(want, ()):
            if role not in texts:
                out.append(R.code(R.MUST, "2 deliverables", f"the request asks for the {_what(role)}; no file", role))
    letter = texts.get("demand")
    if letter is not None:
        for m in malformed_markers(letter):
            out.append(R.code(R.MUST, "2 open items", f'an open-item marker is not well formed: "{m}"', "the demand"))
        for c in checks.money_not_in(letter, sources, also=computed):
            out.append(
                R.code(
                    R.SHOULD,
                    "3 figures",
                    f"{checks.fmt_cents(c)} is in neither the record nor a total computed from it; confirm it",
                    "the demand",
                )
            )
        for d in checks.dates_not_in(letter, sources):
            if drafted is None or d < drafted:
                out.append(
                    R.code(R.NOTE, "1 dates", f"{d:%B} {d.day}, {d.year} is not a date the record states", "the demand")
                )
    if letter_md is not None:
        keys = front_keys or crosscheck.FRONT_KEYS
        for m in crosscheck.check(letter_md, facts, keys)["mismatches"]:
            out.append(R.code(R.MUST, "4 parties", f"the RE block disagrees with the matter record: {m}", "RE block"))
    return out


# ---- the model's material --------------------------------------------------------
def _block(findings: list[R.Finding]) -> str:
    return "\n".join(f"- {f.grade} | {f.check} | {f.finding}" for f in findings) or "(none)"


def signature_block(firm: Any) -> str:
    """The signature block as the firm authored it (``attorneys`` in
    demand-firm.yaml), for the reviewer: code writes it from that row, so its
    shape is the firm's, not the drafter's. On 2026-10-15 the review flagged
    the numbered format's script line followed by the printed name as "the
    signature repeats the name twice", and the authored full name as an
    expansion of the matter record's initial."""
    who = getattr(firm, "attorney", None) or {}
    if not who.get("signer"):
        return "(no signature row selected)"
    numbered = (getattr(firm, "variant", None) or {}).get("layout") == "numbered"
    shape = (
        "a script signature line in the signer's name, then the same name printed, then the title"
        if numbered
        else "the signer's printed name, then the title"
    )
    return (
        f"Signer: {who['signer']}. Title: {who.get('title', '')}. The block is {shape}"
        + (" (the name appearing twice is the script line and the printed name, by design)" if numbered else "")
        + ". The name is the firm's own authored form of the attorney's name; it is correct even where the "
        "matter record abbreviates it. Do not report the signature block's shape or its name as a finding."
    )


def demand_user(
    request: str,
    matter: list[str],
    premise_facts: list[str],
    computed: set[int],
    texts: dict[str, str],
    code: list,
    signature: str = "",
) -> str:
    docs = "\n\n".join(f"=== {_what(role).upper()} (as it would be filed) ===\n{t}" for role, t in texts.items())
    figures = ", ".join(checks.fmt_cents(c) for c in sorted(computed) if c) or "(none)"
    return (
        "THE ATTORNEY'S REQUEST (verbatim; the instruction, never a source of facts):\n\n"
        + request
        + "\n\n---\n\nTHE MATTER RECORD (structured fields):\n"
        + ("\n".join(matter) or "(none)")
        + "\n\n---\n\nPREMISE FACTS FROM THE FILE:\n"
        + ("\n".join(f"- {x}" for x in premise_facts) or "(none)")
        + "\n\n---\n\nFIGURES CODE COMPUTED FROM THE MATTER RECORD AND THE LETTER'S OWN TABLES: "
        + figures
        + "\n\n---\n\nTHE SIGNATURE BLOCK, AS THE FIRM AUTHORED IT (written by code from the firm's row, not "
        "drafted):\n"
        + (signature or "(not given)")
        + "\n\n---\n\nCODE FINDINGS:\n"
        + _block(code)
        + "\n\n---\n\n"
        + docs
    )


def _cited(evidence: str, extracted: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    for r in extracted:
        name = str(r.get("name") or "")
        if name and evidence.startswith(name) and (best is None or len(name) > len(str(best.get("name")))):
            best = r
    return best


def cited_sources(premise: dict[str, Any], extracted: list[dict[str, Any]], report: str) -> str:
    """Every document a failed gate quotes, whole up to a cap, with what the
    pull knows of it (kind, folder); and, for a report the drafter wrote,
    every document it names."""
    rows: list[dict[str, Any]] = []
    for g in premise.get("gates") or []:
        if g.get("passed"):
            continue
        for ev in g.get("evidence") or []:
            r = _cited(str(ev), extracted)
            if r is not None and r not in rows:
                rows.append(r)
    for r in extracted:
        if r not in rows and str(r.get("name") or "") and str(r["name"]) in report:
            rows.append(r)
    parts, size = [], 0
    for r in rows:
        p = Path(str(r.get("text_path") or ""))
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "(the text could not be read)"
        head = (
            f"=== SOURCE: {r.get('name')} (kind: {r.get('kind') or 'document'}; folder: {r.get('folder') or '?'}) ==="
        )
        piece = f"{head}\n{text[:SOURCE_CHARS_PER_DOCUMENT]}"
        if size + len(piece) > SOURCE_CHARS_TOTAL:
            parts.append(f"{head}\n(not included: over the review's size cap)")
            continue
        parts.append(piece)
        size += len(piece)
    return "\n\n".join(parts) or "(no source document could be matched to the report's evidence)"


def coverage_user(report: str, premise: dict[str, Any], sources: str, code: list[R.Finding]) -> str:
    gates = json.dumps(premise.get("gates") or [], indent=1)
    return (
        "THE PREMISE CHECK'S VERDICT (each gate, its result and the evidence it quoted):\n\n"
        + gates
        + "\n\n---\n\nCODE FINDINGS:\n"
        + _block(code)
        + "\n\n---\n\n=== THE COVERAGE POSTURE REPORT (as it would be filed) ===\n"
        + report
        + "\n\n---\n\nTHE SOURCE DOCUMENTS THE EVIDENCE QUOTES (whole, up to a cap):\n\n"
        + sources
    )


def model_review(
    doorway: Any, model: str, system: str, user: str, key: str, hold_checks: Iterable[int]
) -> list[R.Finding]:
    from .gapaudit import output_max

    return R.run_model(doorway, model, system, user, key, hold_checks, max_tokens=output_max(model, REVIEW_MAX_TOKENS))


# ---- the decision, shared with the drafting job ----------------------------------
def decide(
    data: Path, job_dir: Path, fresh: Callable[[], tuple[list[R.Finding], str]], *, lane: str
) -> tuple[str, dict[str, Any]]:
    """``(status, record)`` for this attempt: a redo applied, a release
    applied (``proof.review.gate``), and the review run when there is no
    record. ``status`` is ``passed``, ``released`` or ``held``. A pending
    redo (``REDO_MARKER``) is consumed by ``gate``: the record is forgotten
    and the review runs afresh."""
    status, rec = R.gate(data, job_dir)
    if status == "missing" or rec is None:
        found, model = fresh()
        rec = R.save(data, found, model=model, lane=lane)
        status = R.status(rec)
    return status, rec


def summary(rec: dict[str, Any]) -> str:
    return f"{rec['must_fix']} must-fix, {rec['should_fix']} should-fix, {rec['notes']} note(s)"


__all__ = [
    "COVERAGE_PROMPT",
    "DEMAND_PROMPT",
    "HOLD_COVERAGE",
    "HOLD_DEMAND",
    "ReviewHeld",
    "cited_sources",
    "computed_figures",
    "coverage_user",
    "decide",
    "delivered",
    "demand_code",
    "demand_user",
    "docx_text",
    "firm_banned",
    "malformed_markers",
    "markers_by_design",
    "signature_block",
    "model_for",
    "model_review",
    "summary",
    "wording",
]
