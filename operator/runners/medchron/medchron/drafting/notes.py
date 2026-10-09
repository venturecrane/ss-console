"""The attorney notes filed beside the draft: the markers, the caption diff
and what was compared, the final pass, the gate, the format notes, the wall,
and the drafter's own end tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json(data: Path, name: str) -> Any:
    return json.loads((data / name).read_text(encoding="utf-8"))


def attorney_notes(
    data: Path, title: str, found: list[dict[str, str]], render_notes: list[str], end_tables: str = ""
) -> str:
    lines = [f"# Attorney notes: {title}", "", "## Items for the attorney", ""]
    lines += [f"- {{{{{m['kind']}}}}} {m['text']}".rstrip() for m in found] or ["- None."]
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
        lines += [
            f'- {x["field"].replace("_", " ")}: the court\'s paper reads "{x["document_value"]}"; the matter record '
            f'reads "{x["record_value"] or "nothing"}" ({x["why"]}). Line: "{x["quote"]}" ({x["source"]})'
            for x in cap["discrepancies"]
        ]
        compared = ", ".join(c.replace("_", " ") for c in cap.get("compared") or [])
        if not compared:
            lines.append(f"- Fields compared: none ({cap.get('none_because') or 'nothing comparable'}).")
        elif not cap["discrepancies"]:
            lines.append(f"- Fields compared: {compared}. No discrepancy on those fields.")
        else:
            lines.append(f"- Fields compared: {compared}.")
    fp = _json(data, "final-pass.json")
    review = [f"Removed or flagged by the final audit: {x}" for x in fp["settled"]]
    review += [f"Left for the attorney by the auditor: {x}" for x in fp["drifts"]]
    if (data / "howell.json").is_file():
        review += [f"Howell table: {n}" for n in _json(data, "howell.json")["notes"]]
    review += [f"Drafting gate repair: {x}" for x in _json(data, "gate.json").get("repairs") or []]
    review += [f"Drafting gate: {w}" for w in _json(data, "gate.json").get("warnings") or []]
    review += [f"Format: {n}" for n in render_notes]
    walled = _json(data, "walled.json") if (data / "walled.json").is_file() else []
    review += [f"Held out, never read (privilege wall: {w['reason']}): {w['name']}" for w in walled]
    tail = ["", "## The drafter's end tables", "", end_tables] if end_tables else []
    return "\n".join(lines + ["", "## For review", ""] + [f"- {x}" for x in review] + tail) + "\n"
