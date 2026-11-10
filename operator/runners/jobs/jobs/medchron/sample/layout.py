"""What is printed on a page, in reading order.

One function decides it, and both the PDF renderer and the answer key's
transcription read it, so the words a demo PDF shows and the words the answer
key says a reader would transcribe can never drift apart.
"""

from __future__ import annotations

from .model import SEED_MARK, Document, Page, Plan, us_date


def header(plan: Plan, doc: Document) -> list[str]:
    pv = plan.providers[doc.provider]
    contact = "   ".join(
        x for x in (pv.address, f"Tel {pv.phone}" if pv.phone else "", f"Fax {pv.fax}" if pv.fax else "") if x
    )
    return [pv.facility.upper(), contact]


def footer(plan: Plan, doc: Document, page: Page) -> list[str]:
    pt = plan.patient
    name, dob = pt["name"], pt["dob"]
    mrn = str((pt.get("mrn") or {}).get(doc.provider, ""))
    if page.other_patient:
        name, dob, mrn = page.other_patient.split("|")
    ident = f"Patient: {name}   DOB: {dob}" + (f"   MRN: {mrn}" if mrn else "")
    return [ident, f"Page {page.number} of {len(doc.pages)}", SEED_MARK]


def fax_line(plan: Plan, doc: Document, page: Page) -> str:
    """The sending machine's header, printed across the top of a faxed page."""
    if not (page.scan and doc.fax):
        return ""
    pv = plan.providers[doc.provider]
    sent = str(doc.fax.get("sent", ""))
    stamp = f"{us_date(sent[:10])} {sent[11:16]}" if len(sent) >= 16 else us_date(sent[:10])
    who = str(doc.fax.get("from_name") or pv.facility)[:28].upper()
    return f"{stamp}  FROM: {pv.fax}  {who}  P. {page.number:03d}/{len(doc.pages):03d}"


def page_text(plan: Plan, doc: Document, page: Page) -> list[str]:
    out = []
    fax = fax_line(plan, doc, page)
    if fax:
        out.append(fax)
    out += header(plan, doc)
    if page.title:
        out.append(page.title.upper())
    out += page.lines
    out += footer(plan, doc, page)
    return out
