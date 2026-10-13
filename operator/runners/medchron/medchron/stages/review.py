"""`review`: the independent pre-delivery review of the chronology, after the
manifest and before anything is filed. Paid (one model call), then $0.

WHY (2026-10-13). The chronology's own gates (merge falsifiers, the claim
audit, the coverage gate) are the pipeline grading its own work, and three
kinds of defect have reached a firm past all of them: the system's own words
in the delivered document ("this chunk", eng#226; "Provider not identified in
this chunk" as an exhibit title, ss#2819) and a co-plaintiff's records inside
the client's chronology (ss#2893). Nothing read the package as the attorney
would before it was filed. This stage does, in two halves:

* **Code** settles what code can, from the rendered Word file (the text the
  attorney reads, tables included), the worksheet and the delivered file
  names: the system's own words and sentinels, an entry dated after today, an
  entry citing an exhibit that is not in the delivered set, an empty
  chronology (all MUST-FIX); an entry dated before the client's date of birth
  (SHOULD-FIX: the record may carry a parent's or a typo'd date, a person
  reads it). Words that a medical record may itself contain ("the model",
  "by code") are SHOULD-FIX, never a hold: a gate that refuses legitimate
  work is the defect class this review must not become.
* **A model** reads the chronology with its own adversarial prompt
  (``prompts/review-system.md``), on the firm's ``review`` tier when the
  config names one, else the ``audit`` tier, never the composer's. Its
  findings hold only on checks 1 to 4 (``HOLD_CHECKS``, the closed list in
  ``proof/review.py``).

A MUST-FIX exits 2 (held, resumable in the chronology's ledger) with a reason
starting ``review_held:``, which the daemon pages SMD on. ``review.json`` is
the record, so a resume holds again without paying again; it is forgotten when
the rendered package changes (``review-input.sha``) or SMD asks for a redo.

THE NOTES GO IN THEIR OWN FILE. The chronology has no attorney-notes surface:
its front section (Records Reviewed and Limitations) is part of the client
document, is read by whoever the firm hands the chronology to, and was audited
as it stands. So the SHOULD-FIX and NOTE findings (and released MUST-FIX ones)
are written to a short separate Word file, added to the upload manifest only
when there is something to say. Editing the audited chronology would reopen
its audit; a separate file does not.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .. import llm, prompts
from ..audit.claims import CITE
from ..proof import checks, review
from .base import StageRun

HOLD_CHECKS = (1, 2, 3, 4)
REVIEW_TEXT_MAX_CHARS = 400_000
INPUT_SHA = "review-input.sha"
REDO_SEEN = "review-redo.seen"
NOTES_TEMPLATE = "{CLIENT} - Chronology Review Notes {MM-DD-YY}.docx"
_NOTES_TAG = " - Chronology Review Notes "
_DATE_HEAD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
_EXHIBIT_NO = re.compile(r"^Exhibit (\d+)\b")
# The words that only the system writes. A hit is settled: no medical record
# says "this chunk" or names a placeholder provider.
_CHUNK = re.compile(r"(?i)\b(?:in )?this chunk\b|\bprovider not identified\b")
_SETTLED_SYSTEM = {
    "this chunk",
    "in this chunk",
    "the pipeline",
    "the auditor",
    "the drafter",
    "drafting gate",
    "compose stage",
    "job rule",
    "counted by code",
    "as an ai",
}


def docx_text(path: Path) -> str:
    """The text of a Word file as a reader meets it: body paragraphs and every
    table cell once (a merged cell repeats in python-docx's rows)."""
    from docx import Document

    d = Document(str(path))
    out = [p.text for p in d.paragraphs]
    # The elements themselves, held: lxml keeps one proxy per live element, so
    # identity is stable while referenced (an id() of a dropped proxy is reused
    # and silently skipped most of the table here once).
    seen: set[Any] = set()
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                if c._tc in seen:
                    continue
                seen.add(c._tc)
                out.append(c.text)
    return "\n".join(s for s in out if s.strip())


def entries(path: Path) -> list[tuple[str, str]]:
    """``(MM/DD/YYYY, provider)`` per chronology entry, off the rendered entry
    table (``render.body_table``: the left cell is the date over the provider)."""
    from docx import Document

    out: list[tuple[str, str]] = []
    for t in Document(str(path)).tables:
        for row in t.rows:
            paras = [p.text.strip() for p in row.cells[0].paragraphs]
            if paras and _DATE_HEAD.match(paras[0]) and len(row.cells) == 2:
                out.append((paras[0], paras[1] if len(paras) > 1 else ""))
    return out


def _as_date(mdy: str) -> date | None:
    m = _DATE_HEAD.match(mdy)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _system_findings(where: str, text: str) -> list[review.Finding]:
    out: list[review.Finding] = []
    for m in dict.fromkeys(x.group(0) for x in _CHUNK.finditer(text)):
        out.append(
            review.code(review.MUST, "3 system language", f"\"{m}\" is the system's wording, not the record's", where)
        )
    for w in checks.system_vocabulary(text):
        if _CHUNK.search(w):
            continue
        grade = review.MUST if w in _SETTLED_SYSTEM else review.SHOULD
        out.append(review.code(grade, "3 system language", f'"{w}" reads as the system\'s wording', where))
    for s in checks.sentinels(text):
        out.append(review.code(review.MUST, "3 system language", f'a system marker "{s[:60]}" is in the text', where))
    return out


def code_findings(
    chron: str,
    rows: list[tuple[str, str]],
    names: list[str],
    other_texts: dict[str, str],
    *,
    today: date,
    dob: date | None,
) -> list[review.Finding]:
    """What code settles about the package. Facts from ``proof.checks`` and the
    rendered entries; the grades are this lane's (see the module docstring)."""
    out = _system_findings("the chronology", chron)
    for label, text in other_texts.items():
        out += _system_findings(label, text)
    for n in names:
        out += _system_findings(f"file name {n}", n)
    if not rows:
        out.append(
            review.code(review.MUST, "empty chronology", "the chronology has no dated entries", "Medical Summary")
        )
    for mdy, prov in rows:
        d = _as_date(mdy)
        cite = f"entry {mdy} {prov}".strip()
        if d is not None and d > today:
            out.append(review.code(review.MUST, "5 future date", f"an entry is dated {mdy}, after today", cite))
        elif d is not None and dob is not None and d < dob:
            out.append(
                review.code(
                    review.SHOULD, "5 before birth", f"an entry is dated {mdy}, before the client's date of birth", cite
                )
            )
    delivered = {int(m.group(1)) for n in names if (m := _EXHIBIT_NO.match(n))}
    cited = sorted({int(m.group(1)) for m in CITE.finditer(chron)})
    for n in [c for c in cited if c not in delivered]:
        out.append(
            review.code(
                review.MUST,
                "5 missing exhibit",
                f"the chronology cites Exhibit {n}, which is not in the delivered set",
                f"Exhibit {n}",
            )
        )
    return out


def _model(sr: StageRun) -> str:
    tiers = sr.cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(sr.cfg, "audit"))


def _user(sr: StageRun, chron: str, names: list[str], found: list[review.Finding]) -> str:
    others = [u.client_name for u in sr.job.units if u.unit != sr.unit.unit]
    text = chron
    head = "CHRONOLOGY, as the attorney will read it"
    if len(chron) > REVIEW_TEXT_MAX_CHARS:
        text = chron[:REVIEW_TEXT_MAX_CHARS]
        head += (
            f" (TRUNCATED: the first {REVIEW_TEXT_MAX_CHARS:,} of {len(chron):,} characters; review what is here "
            "and add a NOTE that the rest was not reviewed)"
        )
    code_lines = [f"- {f.grade} | {f.check} | {f.finding} | {f.cite}" for f in found] or ["(none)"]
    return "\n".join(
        [
            f"CLIENT: {sr.unit.client_name}; date of birth {sr.unit.dob or 'not known'}; "
            f"incident {sr.job.incident_date}; today {date.today().isoformat()}",
            "OTHER CLIENTS ON THIS MATTER: " + (", ".join(others) or "none"),
            "",
            "RECORDS DELIVERED WITH THE CHRONOLOGY:",
            *[f"- {n}" for n in names],
            "",
            "CODE FINDINGS:",
            *code_lines,
            "",
            f"{head}:",
            text,
        ]
    )


def _input_sha(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for m in rows:
        h.update(f"{m['name']}|{m['sha256']}\n".encode())
    return h.hexdigest()


def _consume_redo(rd: Path, job_dir: Path) -> None:
    """SMD's redo, once per unit per request. ``review.gate`` unlinks the job's
    marker on first sight, which on a joint matter would redo the first
    client's review and not the second's; so this lane remembers the marker it
    acted on in each unit's own run dir instead, and never unlinks it (the next
    ``resume.start_run`` does)."""
    marker = job_dir / review.REDO_MARKER
    if not marker.is_file():
        return
    token = f"{marker.stat().st_mtime_ns}:{marker.read_text(encoding='utf-8')}"
    seen = rd / REDO_SEEN
    if seen.is_file() and seen.read_text(encoding="utf-8") == token:
        return
    review.forget(rd)
    seen.write_text(token, encoding="utf-8")


def _notes(sr: StageRun, out: Path, rec: dict[str, Any] | None, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The findings the attorney should see, in their own Word file, added to
    the manifest after the chronology; removed again when there are none."""
    for old in out.glob(f"*{_NOTES_TAG}*.docx"):
        old.unlink()
    lines = review.notes_section(rec)
    if not lines:
        return rows
    from docx import Document

    stamp = sr.date_stamp or date.today().strftime("%m-%d-%y")
    name = NOTES_TEMPLATE.replace("{CLIENT}", sr.unit.client_name).replace("{MM-DD-YY}", stamp)
    doc = Document()
    doc.add_paragraph(f"{sr.unit.client_name}: notes to the medical chronology", style="Title")
    for ln in lines:
        if ln.startswith("## "):
            doc.add_paragraph(ln[3:], style="Heading 1")
        elif ln.startswith("- "):
            doc.add_paragraph(ln[2:], style="List Bullet")
    p = out / name
    doc.save(str(p))
    data = p.read_bytes()
    row = {
        "name": name,
        "folder": rows[0]["folder"],
        "local_path": str(p),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    sr.log(f"review notes: {len(lines) - 3} finding(s) for the attorney in {name}")
    return [rows[0], row, *rows[1:]]


def run(sr: StageRun, *, today: date | None = None) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    rd = sr.slug_dir / "runs" / sr.unit.unit
    rd.mkdir(parents=True, exist_ok=True)
    mpath = out / "upload_manifest.json"
    rows = [m for m in json.loads(mpath.read_text(encoding="utf-8")) if _NOTES_TAG not in m["name"]]
    if not rows:
        sr.log("review: the upload manifest is empty")
        return 1
    sha = _input_sha(rows)
    if (rd / INPUT_SHA).is_file() and (rd / INPUT_SHA).read_text(encoding="utf-8") != sha:
        review.forget(rd)  # the package changed since it was reviewed
    _consume_redo(rd, sr.job.path.parent)
    status, rec = review.gate(rd, sr.job.path.parent, consume_redo=False)
    if status == "missing":
        chron_path = Path(rows[0]["local_path"])
        chron = docx_text(chron_path)
        names = [m["name"] for m in rows]
        others = {
            f"file {m['name']}": docx_text(Path(m["local_path"])) for m in rows[1:] if m["name"].endswith(".docx")
        }
        dob = _as_date(sr.unit.dob or "")
        found = code_findings(chron, entries(chron_path), names, others, today=today or date.today(), dob=dob)
        model = _model(sr)
        system = prompts.load("review-system", sr.cfg)
        try:
            found += review.run_model(
                sr.doorway, model, system, _user(sr, chron, names, found), f"{sr.slug}-{sr.unit.unit}", HOLD_CHECKS
            )
        except review.ReviewIncomplete as exc:
            sr.log(f"review: {exc}; nothing was filed, a rerun reviews again")
            return 1
        review.save(rd, found, model=model, lane="chronology")
        (rd / INPUT_SHA).write_text(sha, encoding="utf-8")
        status, rec = review.gate(rd, sr.job.path.parent, consume_redo=False)
    if status == "held":
        sr.log(review.held_reason(rec or {"findings": []}))
        return 2
    mpath.write_text(json.dumps(_notes(sr, out, rec, rows), indent=1), encoding="utf-8")
    sr.log(
        f"review {status}: {(rec or {}).get('must_fix', 0)} must-fix, {(rec or {}).get('should_fix', 0)} should-fix, {(rec or {}).get('notes', 0)} note(s); model {(rec or {}).get('model')}"
    )
    return 0
