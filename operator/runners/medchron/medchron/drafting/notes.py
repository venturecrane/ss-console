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
from pathlib import Path
from typing import Any


#: How each marker kind reads in the notes: in words, never as a {{slot}}.
KIND_LABEL = {"ATTORNEY": "For your decision", "NOT IN RECORD": "Not in the file", "CLIENT": "From the client"}


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
    lines += [f"- {KIND_LABEL.get(m['kind'], m['kind'].title())}: {m['text']}" for m in found if m["text"].strip()]
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
        review += [f"{pre}Check against the record: {w}" for w in g.get("warnings") or []]
        if (d / "caps.json").is_file():
            review += [f"{pre}Consistency: {x}" for x in _json(d, "caps.json")]
    if (data / "howell.json").is_file():
        review += [f"Howell table: {n}" for n in _json(data, "howell.json")["notes"]]
    review += list(render_notes)
    walled = _json(data, "walled.json") if (data / "walled.json").is_file() else []
    review += [f"Held out, never read (privilege wall: {w['reason']}): {w['name']}" for w in walled]
    tail = ["", "## End tables (from the final document)", "", end_tables] if end_tables else []
    return "\n".join(lines + ["", "## For review", ""] + [f"- {x}" for x in review] + tail) + "\n"
