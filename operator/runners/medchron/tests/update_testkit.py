"""Synthetic fixtures for the update-into-the-delivered-chronology tests.

Every value is invented. The delivered chronology is authored here as
build_doc-shaped markdown and rendered with the seat's own renderer, so the
tests hold the same contract a real delivery does: the delivered .docx is
what the firm holds, and nothing else of the delivery survives.
"""

from __future__ import annotations

from pathlib import Path

from medchron import config as config_mod
from medchron_testkit import FIRM_CONFIG

INCIDENT = "2026-01-15"

ENTRY_PRE = (
    "12/01/2025\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Routine visit; blood pressure 120/80 recorded at the visit. (Exhibit 1 - p. 1)"
)
ENTRY_POST = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 1 - p. 2)\n\n"
    "All Other Information\nReturn to clinic in four weeks for a recheck of the neck. (Exhibit 1 - p. 2)"
)
PMH = "Prior Medical History\nA routine visit recorded blood pressure 120/80 before the incident. (Exhibit 1 - p. 1)"
#: The delivered summarize-only note, in scope.NOTE's words (invented span).
NOTE = (
    "[NTD: 3 additional pre-incident encounters dated 2025-06-01 to 2025-09-30 were reviewed and are not itemized "
    "in this chronology. They record routine or unrelated care with no bearing on the claimed injuries. They remain "
    "in the exhibits and can be itemized on request.]"
)


def base_md(
    entries: tuple[str, ...] = (ENTRY_PRE, ENTRY_POST), pmh: str = PMH, limits: bool = True, note: str | None = None
) -> str:
    o = [
        "Alpha Example - Medical Chronology\n",
        "## Treatment Timeline\n",
        "| Medical Provider | Treatment Period | Visits | Reference |",
        "| Example Clinic | 12/01/2025 - 01/20/2026 | 2 | Exhibit 1 - p. 1 |",
        "",
        "## Diagnostic Highlights\n",
        "| ICD Code | Description | First Diagnosed | Reference |",
        "| S13.4XXA | Sprain of ligaments of cervical spine, initial encounter | 01/20/2026 | Exhibit 1 - p. 2 |",
        "",
        "## Medical Chronology\n",
        pmh + "\n",
        *([note + "\n"] if note else []),
        *[e + "\n" for e in entries],
        "## Exhibit List\n",
        "| Exhibit No. | Description |",
        "| 1 | Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records) |",
    ]
    if limits:
        o += [
            "",
            "## Records Reviewed and Limitations\n",
            "This chronology was prepared from 1 document on the matter.",
            "* One record set was reviewed in full.",
        ]
    return "\n".join(o)


def cfg() -> config_mod.FirmConfig:
    return config_mod.FirmConfig(path=Path("<test>"), data=dict(FIRM_CONFIG))


def render(md: str, dst: Path, incident: str = INCIDENT) -> Path:
    from medchron.stages.render import render_text

    assert render_text(md, dst, cfg=cfg(), incident_date=incident, log=lambda _m: None) == 0
    return dst


HEADINGS = {"Medical Diagnoses", "All Other Information", "Discharge / Discontinuation in Care"}


#: Bold labels a laptop-rendered document carries ahead of the text (label, rest).
LIMITS_LABEL = ("Scope: ", "This chronology was prepared from 1 document on the matter.")
PMH_LABEL = ("Cardiovascular: ", PMH.split("\n", 1)[1])


def runs_of(path: Path) -> list[list[tuple[str, bool]]]:
    """Every non-empty paragraph's runs, body and table cells, in order, as
    (text, bold) with adjacent same-weight runs joined: how a reader sees
    the formatting. Independent of the runner's own helpers."""
    import docx

    d = docx.Document(str(path))
    paras = list(d.paragraphs) + [p for t in d.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    out = []
    for p in paras:
        segs: list[tuple[str, bool]] = []
        for r in p.runs:
            if r.text:
                if segs and segs[-1][1] == bool(r.bold):
                    segs[-1] = (segs[-1][0] + r.text, segs[-1][1])
                else:
                    segs.append((r.text, bool(r.bold)))
        if segs:
            out.append(segs)
    return out


def foreign_docx(dst: Path, *, note: str | None = None, typed: str | None = None, labels: bool = False) -> Path:
    """The chronology `base_md(note=note)` describes, as ANOTHER renderer made
    it (the laptop pipeline's shape, built here with python-docx directly):
    title, the review note at the front, numbered sections, the Prior Medical
    History and the scope note before the entry table, a two-column entry
    table, the Exhibit List. Its words are the delivery's; its formatting is
    not the seat's: the title and Prior Medical History heading carry their
    own bold runs, runs are split mid-sentence, entry bodies are plain
    paragraphs rather than bullets with bold headings, and the timeline is a
    lone gap line with no pictures. `typed`: a paragraph the firm typed under
    the entry table after delivery (words no markdown can place there)."""
    import docx

    d = docx.Document()

    def para(text: str, style: str | None = None, bold: bool | None = None, split: bool = False) -> None:
        p = d.add_paragraph(style=style)
        parts = [text[: len(text) // 2], text[len(text) // 2 :]] if split else [text]
        for t in parts:
            p.add_run(t).bold = bold

    def table(rows: list[list[str]]) -> None:
        t = d.add_table(rows=len(rows), cols=len(rows[0]))
        for i, r in enumerate(rows):
            for j, txt in enumerate(r):
                t.cell(i, j).text = txt

    para("Alpha Example - Medical Chronology", "Title", bold=True)

    def labelled(label: str, rest: str) -> None:
        p = d.add_paragraph()
        p.add_run(label).bold = True
        p.add_run(rest).bold = False

    para("Records Reviewed and Limitations", "Heading 1")
    if labels:
        labelled(*LIMITS_LABEL)
    else:
        para("This chronology was prepared from 1 document on the matter.", split=True)
    para("One record set was reviewed in full.", "List Bullet")
    para("1. Treatment Timeline", "Heading 1")
    para("Treatment Gap  Dec 1, 2025 - Jan 20, 2026  (50 days)", bold=True)
    para("2. Past Treatment History", "Heading 1")
    table(
        [
            ["Medical Provider", "Treatment Period", "Visits", "Reference"],
            ["Example Clinic", "12/01/2025 - 01/20/2026", "2", "Exhibit 1 - p. 1"],
        ]
    )
    para("3. Diagnostic Highlights", "Heading 1")
    table(
        [
            ["ICD Code", "Description", "First Diagnosed", "Reference"],
            ["S13.4XXA", "Sprain of ligaments of cervical spine, initial encounter", "01/20/2026", "Exhibit 1 - p. 2"],
        ]
    )
    para("4.  Medical Summary", "Heading 1")
    head, body = PMH.split("\n", 1)
    para(head, bold=True)
    if labels:
        labelled(*PMH_LABEL)
    else:
        para(body, split=True)
    if note:
        para(note)
    t = d.add_table(rows=2, cols=2)
    for i, e in enumerate((ENTRY_PRE, ENTRY_POST)):
        lines = e.splitlines()
        left, right = t.cell(i, 0), t.cell(i, 1)
        left.text = lines[0]
        left.add_paragraph(lines[1].split("|")[0].strip())
        right.text = lines[3]
        for ln in lines[4:]:
            right.add_paragraph(ln)
    if typed:
        para(typed)
    para("5. Exhibit List", "Heading 1")
    table([["Exhibit No.", "Description"], ["1", "Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records)"]])
    dst.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(dst))
    return dst


CLEAN_REVIEW = '{"findings": [], "complete": true}'


def part_anchors(content: list[dict]) -> list[str]:
    """The added entries a review part shows (its ``ADDED ENTRIES`` line)."""
    for b in content:
        t = b.get("text", "") if isinstance(b, dict) else ""
        if "ADDED ENTRIES IN THIS PART: " in t:
            return t.split("ADDED ENTRIES IN THIS PART: ", 1)[1].split("\n", 1)[0].split(", ")
    return []


def review_for_part(reply: str, content: list[dict]) -> str:
    """A scripted review reply cut to the findings on this part's entries (a
    delivered entry's finding is in every part; the stage deduplicates)."""
    import json

    names = set(part_anchors(content))
    found = [
        f
        for f in json.loads(reply)["findings"]
        if str(f.get("anchor", "")).split(".")[0] in names or str(f.get("anchor", "")).startswith("D")
    ]
    return json.dumps({"findings": found, "complete": True})


class ScriptedDoor:
    """A doorway for the update review stage: the review answers from
    `reviews` by round (the last repeated), cut to each part's entries; the
    finisher answers from `finishes` in order (the last repeated). The
    stage's prompts must load as their names (``prompts.load`` patched)."""

    def __init__(self, reviews: list[str], finishes: list[str] | None = None) -> None:
        self.reviews, self.finishes = reviews, list(finishes or ['{"edits": [], "complete": true}'])
        self.seen: list[dict] = []
        self.review_calls: list[dict] = []

    def call(self, _stage: str, **kw):
        self.seen.append(kw)
        content = kw["messages"][0]["content"]
        if kw.get("system") == "update-finish-system":
            text = self.finishes.pop(0) if len(self.finishes) > 1 else self.finishes[0]
        else:
            self.review_calls.append(kw)
            rnd = int(str(kw.get("custom_id", "x-r1-")).split("-r", 1)[1].split("-", 1)[0])
            text = review_for_part(self.reviews[min(rnd, len(self.reviews)) - 1], content)

        class _R:
            stop_reason, usage = "end_turn", None

        r = _R()
        r.text = text  # type: ignore[attr-defined]
        return r


def finish_reply(review_reply: str) -> str:
    """The finisher's reply making, for each finding of a review reply written
    in the review-repair form (action + corrected), that same edit. Findings
    are numbered F1... as the stage numbers them (a delivered entry's finding
    is never sent to the finisher)."""
    import json

    found = json.loads(review_reply)["findings"]
    todo = [f for f in found if not str(f.get("anchor", "")).startswith("D")]
    edits = [{**f, "finding": f"F{i}"} for i, f in enumerate(todo, 1)]
    return json.dumps({"edits": edits, "unresolved": [], "complete": True})
