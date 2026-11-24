"""Earlier unserved response papers of ours on the matter, for a set still owed (2026-11-24).

WHY. The finder's ``prior_drafts`` reads a draft by its NAME ("DRAFT ...
Responses to <kind>, Set <n>", or the Operator's own naming), and any draft it
reads that no Operator job delivered stops the job (``served_prior_draft``).
A response paper of ours the finder reads by its text instead (``_response`` in
``served_find``: unserved, so it answers nothing), named any other way
("Responses to <party> RFP Set One DRAFT <date> by A&P Operator Agent",
"<name>_Resp_RFP_CORRECTED"), was never matched to its set: it was a served set
of another kind, and the whole job was refused.

What happens to one now, per set the job keeps and has no prior draft for:

* the one earlier paper for that set (type, number, asking party when both are
  read) is a document an earlier Operator job delivered for the same served set
  (``served_find.delivered_drafts``) and the job still drafts another set: the
  A1 prior-draft path (``prior_draft`` + ``prior_job``): named in the reply,
  revised from the delivery's thread, not drafted again;
* otherwise the set is drafted, and the attorney notes name the earlier paper,
  so he compares the two before serving. Never a stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from . import parties
from .served import OPERATOR, ServedSet
from .served_docs import load


@dataclass(frozen=True)
class Earlier:
    """An unserved response paper of ours: its file name, the set kind and number code read (number None: unread)."""

    name: str
    kind: str
    number: int | None
    asking: str


def _stem(name: str) -> str:
    return re.sub(r"(?i)\.docx?$", "", " ".join(str(name).split())).casefold()


def _for(s: ServedSet, papers: Iterable[Earlier]) -> list[Earlier]:
    return [
        p for p in papers
        if p.kind == s.set_type and p.number in (None, s.set_number)
        and (not p.asking or parties.same(p.asking, s.propounding_party))
    ]  # fmt: skip


def apply(keep: list[ServedSet], papers: list[Earlier], delivered: list[Any]) -> tuple[list[ServedSet], list[str]]:
    """The kept sets (one routed to its delivered draft where the A1 path
    applies) and the attorney-note lines naming every other earlier paper."""
    out, notes = list(keep), []
    for i, s in enumerate(keep):
        hits = [] if s.prior_draft else _for(s, papers)
        if not hits:
            continue
        ours = [
            d for d in delivered
            if (d.set_type, d.set_number) == (s.set_type, s.set_number)
            and parties.same(d.propounding_party, s.propounding_party) and _stem(d.document) == _stem(hits[0].name)
        ]  # fmt: skip
        others_drafted = any(x.drafted_by == OPERATOR and not x.prior_draft for j, x in enumerate(out) if j != i)
        if len(hits) == 1 and ours and others_drafted:
            out[i] = replace(s, prior_draft=hits[0].name, prior_job=ours[-1].job_id)
            continue
        names = " and ".join(f'"{p.name}"' for p in hits)
        notes.append(
            f"An earlier response paper of ours for {s.label.removeprefix('Responses to ')} is in the matter file "
            f"and shows no proof of service: {names}. These responses were drafted from the served set itself; compare the "
            "two before serving."
        )
    return out, notes


def attorney_lines(report: Path) -> list[str]:
    """The finder's lines for the attorney notes, from its ``found-report.json``
    (``served_find.REPORT``) when that run did not stop; [] without one."""
    doc = load(Path(report), {})
    lines = doc.get("attorney_notes") if isinstance(doc, dict) and not doc.get("stop") else None
    return [str(x) for x in lines] if isinstance(lines, list) else []
