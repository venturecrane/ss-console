"""A served FORM set's values its text layer cannot hold (the ``find`` stage's form half).

WHY (2026-11-21, two matters stopped ``served_ambiguous`` on the seat). A served
Judicial Council form (DISC-001) is a PDF whose text layer is the printed blank
form; the set number, the parties and the checkmarks are drawn on it, not
typed into it. The finder's one read is a text read, so a form's set number,
count and parties are not in what it reads, and requiring them stopped every
job with a served form set. For a form candidate:

* the count is the $0 pixel read's checked numbers (``served_papers.form_checked``),
  else the read's count when it gave one; neither: ``served_ambiguous``;
* the set number is the read's (``served_find`` checks it against the SET
  lines when the text has any), else one SET line's, else the file name's
  ("Set Two"), else, for the matter's only form candidate with no served form
  response on file, the one number a form draft on file names, or 1 when no
  form draft or response is on file (nothing shows an earlier form set);
  anything else is ``served_ambiguous`` for SMD, never a guess between numbers;
* a party the read leaves empty comes from the job's record
  (``served_find._form_parties``).

Every value says where it came from in ``found-report.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import parties
from . import served_papers as papers

FORM = "form_interrogatories"
_NAME_SET = re.compile(r"(?i)\bSet[\s_-]+(?:No\.?\s*)?([A-Za-z]+|\d+)\b")


def whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def lenient(got: dict[str, Any]) -> dict[str, Any]:
    """A form read's values with the ones a form's text cannot hold made optional:
    a set number or count that is not a whole number is None, a party that is
    None is ""."""
    out = dict(got)
    for k in ("set_number", "request_count"):
        out[k] = out[k] if whole(out[k]) else None
    for k in ("propounding_party", "responding_party"):
        out[k] = "" if out[k] is None else out[k]
    return out


def count(path: str, read_count: int | None) -> tuple[int | None, str]:
    """(the form set's request count, where it came from); None: no source."""
    checked = papers.form_checked(path)
    if checked:
        return len(checked), "the $0 pixel read's checked numbers"
    if read_count is not None:
        return read_count, "the read (the pixel read could not read the form)"
    return None, "the form's checks are drawn, not text, and the $0 pixel read could not read them"


def set_number(
    name: str,
    read_n: int | None,
    on_text: list[int],
    *,
    only_form: bool,
    form_responses: bool,
    form_drafts: set[int],
) -> tuple[int | None, str]:
    """(the form set's number, where it came from); None: the reason it is unknown."""
    if read_n is not None:
        return read_n, "the read"
    if len(set(on_text)) == 1:
        return on_text[0], "the form's SET line"
    m = _NAME_SET.search(re.sub(r"_", " ", name or ""))
    n = papers.set_number(m.group(1)) if m else None
    if n:
        return n, "the file name"
    if not only_form or form_responses or len(form_drafts) > 1:
        return None, (
            "its set number is drawn, not text, and the matter file holds "
            + ("another form set" if not only_form else "a served form response or drafts of several form sets")
        )
    if form_drafts:
        return next(iter(form_drafts)), "the one form draft on file (the matter's only form set)"
    return 1, "the matter's only form set; no earlier form set, response or draft is on file"


class Unresolved(ValueError):
    """A form value no source holds: the finder stops ``served_ambiguous`` with this reason."""


@dataclass
class Context:
    """What the matter file shows about form sets, for a form's set number."""

    only_form: bool = True
    form_responses: bool = False
    form_drafts: set[int] = field(default_factory=set)

    @classmethod
    def of(cls, served_kinds: list[str], response_kinds: list[str], form_drafts: set[int]) -> Context:
        return cls(served_kinds.count(FORM) == 1, FORM in response_kinds, set(form_drafts))


def resolve(name: str, path: str, v: dict[str, Any], on_text: list[int], ctx: Context) -> tuple[int, int, dict]:
    """(set number, count, where each came from) for one form candidate; ``Unresolved`` when either has no source."""
    n, n_src = set_number(name, v["set_number"], on_text, only_form=ctx.only_form,
                          form_responses=ctx.form_responses, form_drafts=ctx.form_drafts)  # fmt: skip
    c, c_src = count(path, v["request_count"])
    if n is None or c is None:
        raise Unresolved(n_src if n is None else c_src)
    return n, c, {"set_number_source": n_src, "request_count": c_src}


def answering(clients: list[str]) -> str:
    """Our client, when the record names exactly one (spellings of one party
    count once); "" otherwise."""
    distinct: list[str] = []
    for c in clients:
        if not any(parties.same(c, x) for x in distinct):
            distinct.append(c)
    return distinct[0] if len(distinct) == 1 else ""
