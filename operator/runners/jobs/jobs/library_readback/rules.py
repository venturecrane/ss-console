"""Test (e): every recorded rule in force that code can check holds in the filed document.

THE RULES A JOB RECORDS, and which of them code checks today:

* LAYOUT RULES (``drafting/firm_rules.py``): ``state.json`` ``firm_rules.rows``,
  one row per (rule, key), ``applied`` or not. An applied row is CHECKED: its
  one key and value are read back from the filed Word file by the revise
  checker the revise lane already trusts (``revise/checks.check_docx``). A row
  not applied (refused, or a key the firm's own file owns) is listed with its
  recorded reason, not scored: nothing was laid to measure.
* THE CITATION POLICY (``drafting-firm.yaml`` ``citations``, ``citations.py``):
  the firm's "never cite ..." made code. CHECKED when the drafting inputs are
  given and the matter's file texts are still in the job folder (a cite's kind
  is read from the file it names); otherwise listed as not checkable here.
  Findings are counted with their kinds, never their words.
* WORDING RULES (``wording-rules.json``, ``firm_wording.freeze``): sent to the
  model as words; no wording rule carries a check code reads yet (design piece
  1, second PR). Listed by id, never scored.

The verdict: ``fail`` when any check fails, ``pass`` when at least one check
ran and all held, else ``not_applicable`` (no rule in force that code can
check), with every listed rule in ``measured``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import FAIL, NOT_APPLICABLE, PASS, verdict

_KINDS = re.compile(r"forbids \(([^)]*)\)")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _size_pt(doc: Any, firm_data: dict[str, Any] | None) -> tuple[float, str]:
    fmt = (firm_data or {}).get("format") or {}
    if fmt.get("size_pt"):
        return float(fmt["size_pt"]), "drafting inputs"
    normal = doc.styles["Normal"].font.size if "Normal" in [s.name for s in doc.styles] else None
    return (
        (float(normal.pt), "the filed file's Normal style") if normal is not None else (12.0, "the renderer's default")
    )


def _tables_before_heading(doc: Any) -> int:
    """The tables above the first heading: the caption's, which the revise
    checker sets aside as the lane does."""
    from docx.text.paragraph import Paragraph

    from ..drafting.revise import layout as lay

    n = 0
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}tbl"):
            n += 1
        elif el.tag.endswith("}p"):
            p = Paragraph(el, doc)
            if lay.heading_level(p) and p.text.strip():
                return n
    return n


def _layout(data: Path, path: Path, cls: str, firm_data: dict[str, Any] | None) -> tuple[list[dict], list[dict]]:
    """(checked, listed) for the job's layout rows."""
    rows = ((_json(data / "state.json") or {}).get("firm_rules") or {}).get("rows") or []
    if not rows:
        return [], []
    import docx

    from ..drafting.revise import checks

    doc = docx.Document(str(path))
    size, size_from = _size_pt(doc, firm_data)
    skip = _tables_before_heading(doc)
    checked, listed = [], []
    for row in rows:
        rid, key = str(row.get("rule_id") or ""), str(row.get("key") or "")
        if not row.get("applied"):
            listed.append({"rule_id": rid, "key": key, "kind": "layout", "why": str(row.get("reason") or "not laid")})
            continue
        problems = checks.check_docx(path, {key: row.get("value")}, size, skip, cls)
        checked.append(
            {"rule_id": rid, "key": key, "kind": "layout", "holds": not problems, "problems": len(problems),
             "size_pt": size, "size_from": size_from}
        )  # fmt: skip
    return checked, listed


def _citations(data: Path, path: Path, cls: str, firm_data: dict[str, Any] | None) -> tuple[list[dict], list[dict]]:
    from ..drafting import citations

    policy = citations.policy_for(firm_data or {}, cls) if firm_data is not None else None
    if firm_data is None:
        return [], [{"rule_id": "citations", "kind": "citations", "why": "the drafting inputs were not given"}]
    if policy is None:
        return [], []
    if not (data / "extracted.jsonl").is_file():
        why = "the matter's file texts are not in the job folder"
        return [], [{"rule_id": "citations", "kind": "citations", "why": why}]
    import docx

    from ..demand import summarize
    from ..drafting.format_check import doc_text

    texts = {
        str(r["name"]): Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
        for r in summarize.corpus_files(data)
    }
    found = citations.check(doc_text(docx.Document(str(path))), policy, texts)
    kinds = sorted({k.strip() for f in found for m in [_KINDS.search(f)] if m for k in m.group(1).split(",")})
    return [
        {"rule_id": "citations", "kind": "citations", "holds": not found, "problems": len(found), "kinds": kinds}
    ], []


def _wording(data: Path) -> list[dict]:
    rules = (_json(data / "wording-rules.json") or {}).get("rules") or []
    why = "a wording rule is sent to the model as words; no check reads it"
    return [
        {"rule_id": str(r.get("rule_id") or ""), "kind": "wording", "why": why} for r in rules if isinstance(r, dict)
    ]


def score(data: Path, path: Path, cls: str, firm_data: dict[str, Any] | None) -> dict[str, Any]:
    """Test (e) for one filed document."""
    lay_checked, lay_listed = _layout(data, path, cls, firm_data)
    cite_checked, cite_listed = _citations(data, path, cls, firm_data) if cls != "demand_letter" else ([], [])
    checked = lay_checked + cite_checked
    measured = {"checked": checked, "not_scored": lay_listed + cite_listed + _wording(data)}
    if any(not c["holds"] for c in checked):
        return verdict(FAIL, "a rule in force does not hold in the filed document", measured)
    if checked:
        return verdict(PASS, "", measured)
    return verdict(NOT_APPLICABLE, "no rule in force that code can check", measured)


__all__ = ["score"]
