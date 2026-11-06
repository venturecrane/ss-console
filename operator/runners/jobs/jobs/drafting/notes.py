"""The attorney notes filed beside the draft: the markers, the caption diff
and what was compared, the final pass, the gate, the format notes, the wall,
and the end tables.

The end tables are written from the FINAL documents (``review.final_notes``),
never copied from the drafter's or the auditor's intermediate output: on
2026-10-12 the delivered notes said terms were undefined that the document
defined, and carried auditor and drafter lines that contradicted each other.
The auditor's open items reach the attorney only through that rewrite, as the
final document still bears them out."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


#: How each marker kind reads in the notes: in words, never as a {{slot}}.
KIND_LABEL = {
    "ATTORNEY": "For your decision",
    "NOT IN RECORD": "Not in the file",
    "CLIENT": "From the client",
    "FILL": "To complete before or at service",
}


def _once(lines: list[str], labels: list[str]) -> list[str]:
    """A line every document carries, said once without its document's name."""
    out: list[str] = []
    for ln in lines:
        bare = next((ln[len(lb) + 2 :] for lb in labels if lb and ln.startswith(f"{lb}: ")), None)
        if bare is not None and len(labels) > 1 and all(f"{lb}: {bare}" in lines for lb in labels):
            ln = bare
        if ln not in out:
            out.append(ln)
    return out


_SCANS = re.compile(
    r"(?is)(\d+) of (\d+) sources are machine transcriptions of scanned documents[^:]*:\s*(.*?)(?:\.\s*Cite them\b.*)?$"
)


def _plain_warning(w: str) -> str:
    """The record check's warnings in the attorney's words, not the system's.

    Only what the check measured: how many of the documents it read have pages
    whose text software read from a scan, and which. Never "N of the file's M
    documents": the check's total counts every text it compared against (the
    firm's house style, skeleton and attachments, the walled emails), so on
    2026-11-04 a brief's notes stated a document count the file does not have."""
    m = _SCANS.match(str(w))
    if m:
        n = int(m.group(1))
        return (
            f"{n} {'document has' if n == 1 else 'documents have'} pages that were scanned images whose text was "
            f"read by software; check any figure or quotation taken from them against the scan: {m.group(3)}"
        )
    return str(w)


def _json(data: Path, name: str) -> Any:
    return json.loads((data / name).read_text(encoding="utf-8"))


def attorney_notes(
    data: Path,
    title: str,
    found: list[dict[str, str]],
    render_notes: list[str],
    end_tables: str = "",
    units: list[tuple[str, Path]] | None = None,
) -> str:
    """``units`` are (label, unit data dir) for a job that drafted several
    documents; a one-document job reads its records from ``data``."""
    lines = [f"# Attorney notes: {title}", "", "## Items for the attorney", ""]
    lines += [
        f"- {KIND_LABEL.get(m['kind'], m['kind'].title())}"
        + (f" ({m['where']})" if m.get("where") else "")
        + f": {m['text']}"
        for m in found
        if m["text"].strip()
    ]
    if lines[-1] == "":
        lines.append("- None.")
    cap = _json(data, "caption.json") if (data / "caption.json").is_file() else None
    if cap is not None:
        lines += ["", f"## Caption: source {cap['source'] or 'none (no court document in the file)'}", ""]
        lines += [
            f"- Corrected in Smokeball from the court's {c['source_document']}: {c['field'].replace('_', ' ')} "
            f'"{c["from"] or "nothing"}" -> "{c["to"]}"'
            for c in cap.get("corrections") or []
        ]
        if cap.get("scanned"):
            lines.append(
                "- Page 1 of the court's paper is a scan with no text layer: its caption was read from the image, "
                "so nothing was corrected in Smokeball from it."
            )
        if cap.get("kind") == "answer":
            lines.append(
                "- The caption is read from an answer; its attorney block is opposing counsel's and was not used."
            )
        said = [x for x in cap["discrepancies"] if str(x.get("document_value") or "").strip()]
        lines += [
            f'- {x["field"].replace("_", " ")}: the court\'s paper reads "{x["document_value"]}"; the matter record '
            f'reads "{x["record_value"] or "nothing"}" ({x["why"]})'
            + (f'. Line: "{x["quote"]}" ({x["source"]})' if str(x.get("quote") or "").strip() else f" ({x['source']})")
            for x in said
        ]
        compared = ", ".join(c.replace("_", " ") for c in cap.get("compared") or [])
        if not compared:
            lines.append(f"- Fields compared: none ({cap.get('none_because') or 'nothing comparable'}).")
        elif not said:
            lines.append(f"- Fields compared: {compared}. No discrepancy on those fields.")
        else:
            lines.append(f"- Fields compared: {compared}.")
    review: list[str] = []
    for label, d in units or [("", data)]:
        pre = f"{label}: " if label else ""
        fp = _json(d, "final-pass.json")
        review += [f"{pre}Removed or marked, not borne out by the file: {x}" for x in fp["settled"]]
        g = _json(d, "gate.json")
        review += [f"{pre}Corrected to match the record: {x}" for x in g.get("repairs") or []]
        review += [f"{pre}Check against the record: {_plain_warning(w)}" for w in g.get("warnings") or []]
        if (d / "caps.json").is_file():
            review += [f"{pre}Consistency: {x}" for x in _json(d, "caps.json")]
    if (data / "howell.json").is_file():
        review += [f"Howell table: {n}" for n in _json(data, "howell.json")["notes"]]
    review += list(render_notes)
    walled = _json(data, "walled.json") if (data / "walled.json").is_file() else []
    # Each is an EMAIL (a .msg with its attachments, or a printed one). Named as one:
    # an email's subject can equal a filed document's name ("LIEN - SIGNED" in
    # the mail and the signed lien in the liens folder), and on 2026-10-13 the
    # review read the line as the filed document excluded and then used.
    if walled:
        # One line, the emails named once each (the review: forty lines read
        # as the system talking). A filed document of the same name is a
        # separate paper and was read like any other.
        names = list(dict.fromkeys(str(w["name"]) for w in walled))
        review.append(
            f"Not used in drafting: {len(walled)} privileged or internal emails between the firm, its client and "
            f"its staff, with these {len(names)} subjects: " + "; ".join(f'"{n}"' for n in names) + ". Where a paper "
            "filed in the matter has the same name as one of these emails, it is a separate document and was read."
        )
    review = _once(review, [label for label, _d in units or []])
    tail = ["", "## Request basis, record gaps and your decisions", "", end_tables] if end_tables else []
    return "\n".join(lines + ["", "## For review", ""] + [f"- {x}" for x in review] + tail) + "\n"


# ---- SMD's own sections, carried through every rewrite ------------------------------
#
# SMD edits notes-final.md by hand before a job resumes ("Scope of this draft
# and SMD's edits", "Before you serve these"), and the notes are written again
# from the documents when a review holds on them: on 2026-11-05 that rewrite
# removed SMD's lines twice. Code carries them: a section SMD wrote is never
# sent to a model and comes through every rewrite byte for byte, where SMD put
# it. A section is SMD's when its heading names SMD, or when it is a top-level
# section (``#`` or ``##``) whose heading is not one code writes. Lines SMD
# adds inside a section code writes, without a heading of their own, are not
# kept: SMD gives them a heading.

#: The top-level headings ``attorney_notes`` writes (prefixes: the caption's names its source).
CODE_HEADINGS = (
    "Attorney notes:",
    "Items for the attorney",
    "Caption: source",
    "For review",
    "Request basis, record gaps and your decisions",
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _heading(line: str) -> tuple[int, str] | None:
    m = _HEADING.match(line.rstrip("\r\n"))
    return (len(m.group(1)), m.group(2)) if m else None


def _is_smd(level: int, text: str) -> bool:
    return bool(re.search(r"\bSMD\b", text)) or (level <= 2 and not text.startswith(CODE_HEADINGS))


def smd_sections(md: str) -> list[tuple[str, str | None]]:
    """(the section's exact text, the heading line of code's section that
    followed it, or None at the end) for every SMD section of ``md``."""
    lines = md.splitlines(keepends=True)
    out: list[tuple[str, str | None]] = []
    i = 0
    while i < len(lines):
        h = _heading(lines[i])
        if h is None or not _is_smd(*h):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and ((n := _heading(lines[j])) is None or n[0] > h[0]):
            j += 1
        after = (ln.rstrip() for ln in lines[j:] if (k := _heading(ln)) and not _is_smd(*k))
        out.append(("".join(lines[i:j]), next(after, None)))
        i = j
    return out


def strip_smd(md: str) -> str:
    """``md`` without its SMD sections (what a code edit of the notes may touch)."""
    for text, _ in smd_sections(md):
        md = md.replace(text, "", 1)
    return md


def carry_smd(old: str | None, new: str) -> str:
    """``new`` (notes code or a model just wrote) with every SMD section of
    ``old`` in it, verbatim, before the code heading that followed it there
    (at the end when none did, or that heading is gone). Idempotent."""
    for text, anchor in smd_sections(old or ""):
        if text in new:
            continue
        block = text if text.endswith("\n") else text + "\n"
        lines = new.splitlines(keepends=True)
        at = next((k for k, ln in enumerate(lines) if anchor is not None and ln.rstrip() == anchor), None)
        if at is None:
            new = new.rstrip("\n") + "\n\n" + block
        else:
            new = "".join(lines[:at]) + block + "".join(lines[at:])
    return new
