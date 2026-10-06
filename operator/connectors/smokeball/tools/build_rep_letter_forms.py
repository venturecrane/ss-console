"""Build the firm's rep-letter FORMS from its own filled letters.

    python tools/build_rep_letter_forms.py SPEC.json OUT_DIR
    python tools/build_rep_letter_forms.py --third-party-task-form LETTER.docx OUT_DIR

The firm's 1st and 3rd party representation letters are Smokeball task forms,
and the API serves the filled letters but not the forms (``/formtemplates`` is
not a route; probed 2026-10-05). So the form is rebuilt from a filled letter:
every merged value is replaced by its ``{{field}}`` placeholder in a single run,
field codes are unwrapped (a live ``DATE`` field would re-date the letter when
it is opened), and the source matter's binding is emptied from the document
properties. Every other part of the package is copied byte for byte, which is
what keeps the firm's exact formatting. ``smokeball_connector.form_letters``
fills these forms; the result is filed into the firm's Document Library under
the names ``form_letters.FORMS`` expects.

THE SPEC IS CLIENT DATA AND NEVER LIVES IN THIS REPOSITORY. It names the source
letters and the exact text each merged value printed (a client's name, a claim
number), so it sits beside the source letters, outside this public repo. Shape::

    {"forms": [
      {"source": "/path/to/filled-1st-party-letter.docx",
       "out": "Form - 1st Party Rep Letter.docx",
       "drop_paragraphs": ["<a delivery line after the first>"],
       "replace": [["<the date as printed>", "{{date}}"],
                   ["<first delivery line>", "{{delivery_lines}}"],
                   ["<carrier>\\n<address line>\\n<city line>", "{{carrier_name}}\\n{{carrier_address}}"],
                   ["<client name>", "{{client_name}}"], ...]}]}

Each ``replace`` text must occur exactly once in the letter (``\\n`` is a line
break inside a paragraph, ``\\t`` a tab), or the build stops and names it.

THE 3RD PARTY FORM IS BUILT FROM THE TASK FORM'S OWN OUTPUT, with no spec.
The first 3rd party form (2026-10-05) was rebuilt from a letter made with the
document toolbar on another matter, which is a different letter from the one
the firm's 3rd party TASK produces, so the Operator's letter did not match the
staff's. ``--third-party-task-form`` takes a letter that task made and finds
each merged value by the Smokeball field that printed it (``_TASK_FIELDS``:
the insurer's name and address, the client, the date of loss, the responsible
attorney), never by its text, so the build reads no client value and can be
rerun on any letter the task made. The values the task leaves to the person
filling it are found by position: the "Via Fax:" / "Via Email:" line, the
value under "Claim#:", and the title line under the signer. Every placeholder
must land exactly once or the build stops. The source letter is client data:
it is passed as a path and never committed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from lxml import etree
from smokeball_connector.form_docx import (
    W,
    W_FLDCHAR,
    W_INSTR,
    W_P,
    W_R,
    W_RPR,
    W_T,
    FormError,
    build_form,
    paragraph_text,
    placeholders_in,
    replace_in_paragraph,
    rewrite_document,
    unwrap_fields,
)

THIRD_PARTY_OUT = "Form - 3rd Party Rep Letter.docx"

#: The task form's merge fields, by the tail of their AUTOMATIONFIELD path.
#: ``None`` drops the field's paragraph: the insurer's last address line, which
#: ``{{carrier_address}}`` prints as its own paragraph (one per address line,
#: the way the task form lays the address out).
_TASK_FIELDS: dict[str, str | None] = {
    "Insurer/Full Name": "carrier_name",
    "Insurer/Street Address - Lines 1 & 2 (formatted across)": "carrier_address",
    "Insurer/Street Address - Last Line": None,
    "Plaintiff 1/Full Name(s) (All Parties)": "client_name",
    "Case Details/Incident Details/Date": "date_of_loss",
    "Info/Attorney Responsible/Full Name": "signer_name",
}
_LAST_LINE = "(insurer last address line)"
_DELIVERY = re.compile(r"Via (Fax|Email):", re.I)
_FLD_TYPE = f"{{{W}}}fldCharType"
W_TC = f"{{{W}}}tc"
THIRD_PARTY_FIELDS = (
    "delivery_lines",
    "carrier_name",
    "carrier_address",
    "client_name",
    "claim_number",
    "date_of_loss",
    "signer_name",
    "signer_title",
)


def _build(entry: dict, out_dir: Path) -> str:
    source = Path(entry["source"]).read_bytes()
    replacements = [(str(old), str(new)) for old, new in entry.get("replace", [])]
    drops = [str(text) for text in entry.get("drop_paragraphs", [])]
    form = build_form(source, replacements, drops)
    target = out_dir / str(entry["out"])
    target.write_bytes(form)
    return f"{target.name}: {', '.join(placeholders_in(form))}"


# ---- The 3rd party form, from the task form's own letter --------------------


def _fields(paragraph: etree._Element) -> list[tuple[str, list[etree._Element]]]:
    """Each complete field in the paragraph: its instruction text, and the
    runs between ``separate`` and ``end`` that hold what it printed."""
    out: list[tuple[str, list[etree._Element]]] = []
    instr, result, state = "", [], ""
    for run in paragraph.iter(W_R):
        char = run.find(W_FLDCHAR)
        kind = char.get(_FLD_TYPE) if char is not None else None
        if kind == "begin":
            instr, result, state = "", [], "instr"
        elif kind == "separate":
            state = "result"
        elif kind == "end":
            out.append((instr, result))
            state = ""
        elif state == "instr":
            instr += "".join(t.text or "" for t in run.iter(W_INSTR))
        elif state == "result":
            result.append(run)
    return out


def _placeholder_run(run: etree._Element, name: str) -> None:
    """``run`` keeps its formatting and prints ``{{name}}`` only."""
    for child in [c for c in run if c.tag != W_RPR]:
        run.remove(child)
    etree.SubElement(run, W_T).text = f"{{{{{name}}}}}"


def _mark_task_fields(root: etree._Element) -> dict[str, int]:
    """Each mapped field's printed value becomes its placeholder; returns how
    many times each landed."""
    placed: dict[str, int] = {}
    for paragraph in list(root.iter(W_P)):
        for instr, result in _fields(paragraph):
            key = next((k for k in _TASK_FIELDS if instr.rstrip().endswith(k)), None)
            if key is None:
                continue
            name = _TASK_FIELDS[key]
            if name is None:
                paragraph.getparent().remove(paragraph)
                placed[_LAST_LINE] = placed.get(_LAST_LINE, 0) + 1
                break
            if not result:
                raise FormError(f"the {name} field printed nothing in the source letter")
            for run in result[1:]:
                run.getparent().remove(run)
            _placeholder_run(result[0], name)
            placed[name] = placed.get(name, 0) + 1
    return placed


def _next_paragraph(paragraph: etree._Element) -> etree._Element | None:
    """The next sibling paragraph that has text (same cell, same body)."""
    sibling = paragraph.getnext()
    while sibling is not None and (sibling.tag != W_P or not paragraph_text(sibling).strip()):
        sibling = sibling.getnext()
    return sibling


def _value_beside(label: etree._Element) -> etree._Element | None:
    """The value printed for a label: in the task form's RE: table it is the
    next cell of the label's row; outside a table, the next paragraph."""
    cell = label.getparent()
    if cell is None or cell.tag != W_TC:
        return _next_paragraph(label)
    neighbour = cell.getnext()
    while neighbour is not None and neighbour.tag != W_TC:
        neighbour = neighbour.getnext()
    if neighbour is None:
        return None
    return next((p for p in neighbour.iter(W_P) if paragraph_text(p).strip()), None)


def _set_text(paragraph: etree._Element, name: str) -> None:
    text = paragraph_text(paragraph).strip("\t\n ")
    if not text:
        raise FormError(f"the {name} line is empty in the source letter")
    replace_in_paragraph(paragraph, text, f"{{{{{name}}}}}")


def _mark_typed_values(root: etree._Element) -> None:
    """The values the task form leaves to the person filling it, by position."""
    paragraphs = list(root.iter(W_P))
    delivery = [p for p in paragraphs if _DELIVERY.match(paragraph_text(p).strip())]
    if not delivery:
        raise FormError("no 'Via Fax:' or 'Via Email:' line in the source letter")
    text = paragraph_text(delivery[0])
    found = _DELIVERY.search(text)
    replace_in_paragraph(delivery[0], text[found.start() if found else 0 :], "{{delivery_lines}}")
    for extra in delivery[1:]:
        extra.getparent().remove(extra)
    labels = [p for p in paragraphs if paragraph_text(p).strip() == "Claim#:"]
    value = _value_beside(labels[0]) if len(labels) == 1 else None
    if value is None:
        raise FormError("expected one 'Claim#:' label with its value under it")
    _set_text(value, "claim_number")
    signer = [p for p in root.iter(W_P) if "{{signer_name}}" in paragraph_text(p)]
    title = _next_paragraph(signer[0]) if len(signer) == 1 else None
    if title is None:
        raise FormError("expected one signer line with a title line under it")
    _set_text(title, "signer_title")


def build_third_party_task_form(source: bytes) -> bytes:
    """The 3rd party form from a letter the firm's 3rd party TASK made."""

    def edit(root: etree._Element) -> None:
        placed = _mark_task_fields(root)
        repeated = {k: n for k, n in placed.items() if n != 1}
        missing = [v or _LAST_LINE for v in _TASK_FIELDS.values() if (v or _LAST_LINE) not in placed]
        if repeated or missing:
            raise FormError(f"task-form fields not found exactly once: missing {missing}, repeated {repeated}")
        unwrap_fields(root)
        _mark_typed_values(root)

    form = build_form(rewrite_document(source, edit), [], [])
    got = placeholders_in(form)
    if tuple(got) != THIRD_PARTY_FIELDS:
        raise FormError(f"the built form carries {got}, expected {list(THIRD_PARTY_FIELDS)}")
    return form


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--third-party-task-form":
        out_dir = Path(argv[3])
        out_dir.mkdir(parents=True, exist_ok=True)
        form = build_third_party_task_form(Path(argv[2]).read_bytes())
        (out_dir / THIRD_PARTY_OUT).write_bytes(form)
        print(f"{THIRD_PARTY_OUT}: {', '.join(placeholders_in(form))}")
        return 0
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in spec.get("forms", []):
        print(_build(entry, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
