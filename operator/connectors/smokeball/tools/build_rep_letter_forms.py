"""Build the firm's rep-letter FORMS from two of its own filled letters.

    python tools/build_rep_letter_forms.py SPEC.json OUT_DIR

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
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from smokeball_connector.form_docx import build_form, placeholders_in


def _build(entry: dict, out_dir: Path) -> str:
    source = Path(entry["source"]).read_bytes()
    replacements = [(str(old), str(new)) for old, new in entry.get("replace", [])]
    drops = [str(text) for text in entry.get("drop_paragraphs", [])]
    form = build_form(source, replacements, drops)
    target = out_dir / str(entry["out"])
    target.write_bytes(form)
    return f"{target.name}: {', '.join(placeholders_in(form))}"


def main(argv: list[str]) -> int:
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
