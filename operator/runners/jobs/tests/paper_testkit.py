"""Invented discovery papers as real .docx files, for the paper reader tests.

Every name, party and fact here is made up. The layouts are the ones seen on
the first firm's filed sets (a bold "DEFINITIONS" title over unnumbered
definitions, curly-quoted terms with the comma inside, "X" or "XS" plurals, a
declaration stating the count in prose and beside the statute with "(A)" to
"(E)" lists, a signature table after the last request) plus the risk inputs
(Word auto-numbering with the number not in the text, a signature in a text
box).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass
class SetSpec:
    n: int = 40
    #: Item numbers to leave out (a gap).
    skip: tuple[int, ...] = ()
    label: str = "SPECIAL INTERROGATORY"
    #: Number the items with Word's own numbering instead of the text: "label" or "decimal".
    auto_number: str | None = None
    definitions: bool = True
    declaration: bool = True
    #: The count the declaration states (defaults to the item count).
    decl_count: int | None = None
    #: A per-reason list naming a number the set lacks.
    decl_extra: int | None = None
    signature: str = "table"  # "table" | "textbox" | "lines"
    #: Prose after the signature that belongs to no region.
    stray_after_signature: bool = False
    #: The statutory prior-sets statement in the declaration: "" for none,
    #: "one" for one sentence, "two" for "... Of these, M were not form."
    prior: str = ""
    #: A plain (not bold) "Notes for the Attorney" page at the very end.
    notes: bool = False
    set_no: str = "ONE"


def _bold(doc: Any, text: str, align_center: bool = False) -> Any:
    p = doc.add_paragraph()
    p.add_run(text).bold = True
    if align_center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def add_numbering(doc: Any, lvl_text: str = "%1.", fmt: str = "decimal") -> str:
    """A fresh list in numbering.xml; returns its numId."""
    numbering = doc.part.numbering_part.element
    aid = str(900 + len(numbering.findall(f"{{{W_NS}}}abstractNum")))
    nid = str(900 + len(numbering.findall(f"{{{W_NS}}}num")))
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{aid}"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
        f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{lvl_text}"/></w:lvl></w:abstractNum>'
    )
    num = parse_xml(f'<w:num {nsdecls("w")} w:numId="{nid}"><w:abstractNumId w:val="{aid}"/></w:num>')
    first_num = numbering.find(f"{{{W_NS}}}num")
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)
    numbering.append(num)
    return nid


def number(p: Any, num_id: str, ilvl: int = 0) -> None:
    ppr = p._p.get_or_add_pPr()
    ppr.append(parse_xml(f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr>'))


def textbox(p: Any, lines: list[str]) -> None:
    paras = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in lines)
    run = parse_xml(
        f'<w:r {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape><v:textbox>'
        f"<w:txbxContent>{paras}</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
    )
    p._p.append(run)


def caption(doc: Any, title: str, set_no: str, responding: bool = False) -> None:
    doc.add_paragraph("Alex Example, Esq. (SBN 000001)")
    doc.add_paragraph("EXAMPLE LAW GROUP")
    _bold(doc, "SUPERIOR COURT OF THE STATE OF CALIFORNIA", align_center=True)
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "PAT SAMPLE, Plaintiff, v. RIVER CO., Defendant."
    t.cell(0, 1).text = "Case No. 00CV000000"
    _bold(doc, title, align_center=True)
    first, second = ("Plaintiff PAT SAMPLE", "Defendant RIVER CO.")
    doc.add_paragraph(f"PROPOUNDING PARTY: {second if responding else first}")
    doc.add_paragraph(f"RESPONDING PARTY: {first if responding else second}")
    doc.add_paragraph(f"SET NO.: {set_no}")


def definitions(doc: Any) -> list[str]:
    _bold(doc, "DEFINITIONS")
    doc.add_paragraph("The following definitions apply to each request below.")
    doc.add_paragraph("“YOU,” and “YOUR” mean Defendant RIVER CO. and its agents.")
    doc.add_paragraph('"DOCUMENT" or "DOCUMENTS" means any WRITING as defined by Evidence Code section 250.')
    doc.add_paragraph("This includes drafts and copies.")
    doc.add_paragraph("“INCIDENT.” means the event of an invented date.")
    doc.add_paragraph('"PERSON(S)" means any natural person or entity.')
    return ["YOU", "DOCUMENT", "INCIDENT", "PERSON"]


def items(doc: Any, spec: SetSpec) -> None:
    """Items labelled in the text, or numbered by Word: ``auto_number`` "label"
    puts the label and number in Word's numbering on an empty bold line,
    "decimal" numbers each request paragraph "1." with no label at all."""
    lvl = {"label": f"{spec.label} NO. %1:", "decimal": "%1."}.get(spec.auto_number or "", "")
    nid = add_numbering(doc, lvl) if lvl else None
    for k in range(1, spec.n + 1 + len(spec.skip)):
        if k in spec.skip:  # a gap in the text; Word's own numbering cannot have one
            continue
        if nid and spec.auto_number == "decimal":
            number(doc.add_paragraph(f"State each fact about invented topic {k}."), nid)
            continue
        if nid:
            number(_bold(doc, ""), nid)
        else:
            _bold(doc, f"{spec.label} NO. {k}:")
        doc.add_paragraph(f"State each fact about invented topic {k}.")


def closing(doc: Any, spec: SetSpec) -> None:
    if spec.signature == "table":
        t = doc.add_table(rows=1, cols=2)
        t.cell(0, 0).text = "Dated: an invented date"
        t.cell(0, 1).text = "EXAMPLE LAW GROUP By: Alex Example Attorneys for Plaintiff"
    elif spec.signature == "textbox":
        doc.add_paragraph("Dated: an invented date")
        p = doc.add_paragraph()
        textbox(p, ["EXAMPLE LAW GROUP", "By: Alex Example", "Attorneys for Plaintiff"])
    else:
        doc.add_paragraph("Dated: an invented date")
        doc.add_paragraph("EXAMPLE LAW GROUP")
        doc.add_paragraph("By: Alex Example")
    if spec.stray_after_signature:
        doc.add_paragraph(
            "This paragraph of invented prose sits after the signature and is long enough that it is "
            "plainly not a signature line, so no region of the paper can hold it."
        )


def declaration(doc: Any, spec: SetSpec) -> None:
    n = spec.n if spec.decl_count is None else spec.decl_count
    _bold(doc, "DECLARATION FOR ADDITIONAL DISCOVERY")
    doc.add_paragraph("I, Alex Example, declare: I am an attorney for Plaintiff.")
    doc.add_paragraph(f"I am propounding to Defendant RIVER CO. the attached set of {n} special interrogatories.")
    if spec.prior == "one":
        doc.add_paragraph(
            "I have previously propounded a total of 12 interrogatories to this party, of which 12 were not "
            "form interrogatories."
        )
    elif spec.prior == "two":
        doc.add_paragraph(
            "I have previously propounded a total of 12 interrogatories to this party. Of these, 10 interrogatories "
            "were not form interrogatories."
        )
    doc.add_paragraph(
        "This set will cause the total number of specially prepared interrogatories propounded to exceed the "
        "number permitted by Code of Civil Procedure section 2030.030, subdivision (a)(1)."
    )
    doc.add_paragraph(f"Code of Civil Procedure section 2030.050: {n}")
    doc.add_paragraph(f"I have a total of {n} interrogatories in this set for these reasons:")
    half = max(1, spec.n // 2)
    first = ", ".join(str(i) for i in range(1, half + 1))
    second = ", ".join(str(i) for i in range(half + 1, spec.n + 1))
    doc.add_paragraph(f"(A) The complexity of the issues in the case: Nos. {first}.")
    doc.add_paragraph("(B) The financial burden on a party.")
    doc.add_paragraph(f"(C) The expedience of using this method: No. {spec.decl_extra or 1}.")
    doc.add_paragraph(f"(E) The need for discovery of facts: Nos. {second}.")
    doc.add_paragraph("I declare under penalty of perjury that the foregoing is true and correct.")


def set_docx(path: Path, spec: SetSpec | None = None) -> Path:
    spec = spec or SetSpec()
    doc = Document()
    caption(
        doc, f"PLAINTIFF'S {spec.label.replace('INTERROGATORY', 'INTERROGATORIES')}, SET {spec.set_no}", spec.set_no
    )
    if spec.definitions:
        definitions(doc)
    items(doc, spec)
    closing(doc, spec)
    if spec.declaration:
        declaration(doc, spec)
    if spec.notes:
        doc.add_paragraph("Notes for the Attorney")
        doc.add_paragraph(
            "Consider adding 3 more requests about the invented repair records before this set is served, "
            "and confirm the invented date of the incident with the client."
        )
    doc.save(str(path))
    return path


def response_docx(
    path: Path,
    n: int = 3,
    wrong_head: int | None = None,
    numbers: list[int] | None = None,
    title: str = "DEFENDANT'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE",
    label: str = "SPECIAL INTERROGATORY",
) -> Path:
    doc = Document()
    caption(doc, title, "ONE", responding=True)
    _bold(doc, "PRELIMINARY STATEMENT")
    doc.add_paragraph("Responding Party has not completed its investigation of the invented facts.")
    nums = numbers or list(range(1, n + 1))
    for k in nums:
        _bold(doc, f"{label} NO. {k}:")
        doc.add_paragraph(f"State each fact about invented topic {k}.")
        head = wrong_head if (wrong_head and k == nums[-1]) else k
        _bold(doc, f"RESPONSE TO {label} NO. {head}:")
        doc.add_paragraph("Responding Party objects to this interrogatory as vague and overbroad.")
        doc.add_paragraph(f"Without waiving these objections, Responding Party answers: invented answer {k}.")
        if k == nums[0]:
            doc.add_paragraph("See Exhibit 1, Bates RC-0001.")
    doc.add_paragraph("Dated: an invented date")
    doc.add_paragraph("By: Alex Example")
    _bold(doc, "VERIFICATION")
    doc.add_paragraph("I have read the foregoing responses and know their contents; they are true.")
    doc.save(str(path))
    return path


bold = _bold
