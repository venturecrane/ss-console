"""The firm's files the ``reader`` goldens read, every one built here in code.

No binary is committed: each fixture is made by this module from its own
literal description, the same bytes on every run (a PDF's trailer ID is fixed
per fixture, so MuPDF writes no random one). Every name, number and word is
invented; nothing is a firm's, a client's or a matter's.

A scanned page is a page-sized raster image of one solid colour, and the colour
names the page: red is the fixture's index times 8, green the page number times
8 (``identity``). A fake model reading a rendered page (``reader_kit``) answers
with the page it was shown, so the goldens record WHICH pages were sent to paid
reading, in what order, at what size, without recording a pixel. A blank page
(no image and no text) has no colour and is recorded as unknown.

``FIXTURES`` is the one table: id, listing name and extension kept apart (the
way the seat lists a file), and the bytes. ``x_emailed`` and ``x_filed`` are the
pair whose joined names meet: ``x.pdf`` as an email attachment is pulled with
its extension in its name AND in ``ext``; ``x`` + ``.pdf`` is the same name filed.

The boundary pairs sit on each lane's own threshold: ``edge14``/``edge15`` the
connector's 15 characters a page (averaged), ``edge24``/``edge25`` the 25 a page
of litigation, negotiation and demand, ``thin199``/``thin200`` the chronology's
thin page, ``absent399``/``absent400`` its absent text layer (400 over 200 pages).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

#: A page of a fixture PDF: ("text", body) | ("scan",) | ("blank",) |
#: ("text+scan", body): a text layer over a page image (a stamped scan).
Page = tuple[str, ...]

PROSE = (
    "The patient was seen in the clinic today for a follow up visit after the collision and reports that the pain "
    "in the neck is improving with therapy. The exam is unchanged from the prior visit and the plan is to continue "
    "the current care with a return visit in four weeks. The patient was given a copy of the home exercise program "
    "and asked to call the office with any new complaint or question about the plan of care."
)


def chars(n: int, word: str = "stamp") -> str:
    """Exactly ``n`` characters of words separated by single spaces."""
    out = ""
    while len(out) < n:
        out += (" " if out else "") + word
    return out[:n].rstrip() + "x" * (n - len(out[:n].rstrip()))


def nonspace(n: int, word: str = "words") -> str:
    """Words separated by single spaces carrying exactly ``n`` non-space characters."""
    parts: list[str] = []
    total = 0
    while total + len(word) <= n:
        parts.append(word)
        total += len(word)
    if total < n:
        parts.append("x" * (n - total))
    return " ".join(parts)


def wrap(text: str, width: int = 95) -> list[str]:
    lines, cur = [], ""
    for w in text.split(" "):
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return [*lines, cur] if cur else lines


def identity(index: int, page: int) -> tuple[int, int, int]:
    return (8 * index, 8 * page, 200)


def pdf(index: int, pages: list[Page]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n, spec in enumerate(pages, 1):
        pg = doc.new_page(width=612, height=792)
        kind = spec[0]
        if kind in ("scan", "text+scan"):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
            pix.set_rect(pix.irect, identity(index, n))
            pg.insert_image(pg.rect, pixmap=pix)
        if kind in ("text", "text+scan"):
            for i, line in enumerate(spec[1].split("\n")):
                pg.insert_text((36, 48 + 12 * i), line, fontsize=9)
    doc.xref_set_key(-1, "ID", f"[<{index:032X}><{index:032X}>]")
    data = doc.tobytes(no_new_id=True)
    doc.close()
    return data


def _locked_once(index: int) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "NOTICE OF MOTION", fontsize=11)
    doc.xref_set_key(-1, "ID", f"[<{index:032X}><{index:032X}>]")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="not-on-file", owner_pw="owner-only")
    doc.close()
    return data


def locked(index: int) -> bytes:
    """A password-locked PDF of one fixed size.

    The encryption keys are random on every build, and how they are written
    makes about 1 build in 50 a byte or more shorter (10 of 500 measured,
    2026-11-18). The reader goldens record this file's size, so a short build
    failed the goldens check on a change that touched nothing here. Nine
    builds are made and one of the most common size is returned."""
    builds = [_locked_once(index) for _ in range(9)]
    sizes = [len(b) for b in builds]
    usual = max(set(sizes), key=sizes.count)
    return next(b for b in builds if len(b) == usual)


def jpg(index: int) -> bytes:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 80), 0)
    pix.set_rect(pix.irect, identity(index, 1))
    return pix.tobytes("jpg")


_FIXED_ZIP_TIME = (2026, 1, 2, 3, 4, 6)


def _rezip(data: bytes, rewrite: Callable[[str, bytes], bytes] = lambda _n, b: b) -> bytes:
    """A zip with every member's time fixed, so a Word file is the same bytes
    on every run."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, _FIXED_ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, rewrite(info.filename, src.read(info)))
    return out.getvalue()


def docx(merged: bool = True) -> bytes:
    import docx as docx_mod

    d = docx_mod.Document()
    d.core_properties.author = "Example"
    d.add_paragraph("LETTER OF REPRESENTATION")
    d.add_paragraph("")
    d.add_paragraph("   ")
    d.add_paragraph("We represent Alpha Example for injuries from the collision on 01/15/2026.")
    if merged:
        t = d.add_table(rows=3, cols=3)
        t.cell(0, 0).merge(t.cell(0, 2)).text = "Provider summary"
        t.cell(1, 0).text, t.cell(1, 1).text, t.cell(1, 2).text = "Example Clinic", "01/20/2026", "1,250.00"
        t.cell(2, 0).merge(t.cell(2, 1)).text = "Total"
        t.cell(2, 2).text = "1,250.00"
    d.add_paragraph("")
    d.add_paragraph("Sincerely, Example Law Office")
    buf = io.BytesIO()
    d.save(buf)
    return _rezip(buf.getvalue())


_DOCX_MAIN = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_DOTX_MAIN = b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"


def dotx() -> bytes:
    """A Word template: a docx with its main part's content type renamed."""
    return _rezip(
        docx(merged=False), lambda n, b: b.replace(_DOCX_MAIN, _DOTX_MAIN) if n == "[Content_Types].xml" else b
    )


def eml(attachments: list[tuple[str, str, bytes]]) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = (
        "adjuster@carrier.example",
        "intake@firm.example",
        "Records and offer",
        "Wed, 7 Oct 2026 10:00:00 -0700",
    )
    m["Message-ID"] = "<fixture-1@carrier.example>"
    m.set_content("We offer 12,500 to settle the claim. The records are attached.")
    for name, subtype, data in attachments:
        m.add_attachment(data, maintype="application", subtype=subtype, filename=name)
    m.set_boundary("fixture-boundary-1")
    return m.as_bytes()


HTM = (
    b"<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
    b"<body><h1>Notice of Hearing</h1><p>The hearing is set for 03/02/2026 in Department 4.</p></body></html>"
)
RTF = rb"{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}\f0 Proof of service\par Served on 04/10/2026 by mail.\par}"
TXT = b"Phone note: the adjuster called on 04/12/2026 and asked for the bills.\n"
#: What the fake Outlook reader (``reader_kit.FakeMsg``) opens a ``.msg`` as.
MSG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 fixture outlook container (opened by reader_lanes.FakeMsg)"

GLYPH = " ".join(f"/{i % 10}" for i in range(400))
CIPHER = "".join(chr(ord(c) + 3) if c.isalpha() else c for c in PROSE + " " + PROSE)
CONTROL = "Statement of charges " + "\x01\x02\x03\x04\x05" * 8 + " total due on receipt"
#: Pleading paper: 28 lines, each opening with its margin line number (the run
#: ``pleading_margin`` removes; it needs 26 or more in order).
PLEADING = "\n".join(f"{n}   {line}" for n, line in enumerate(wrap(" ".join([PROSE] * 6), 80)[:28], 1))


@dataclass(frozen=True)
class Fixture:
    key: str
    id: str
    name: str
    ext: str
    data: bytes

    @property
    def full(self) -> str:
        return f"{self.name}{self.ext}"


def _pdfs() -> list[tuple[str, list[Page]]]:
    para = "\n".join(wrap(PROSE))
    typed60: list[Page] = [
        ("blank",) if n in (20, 41) else ("text", f"Page {n} of the typed record.\n{para}") for n in range(1, 61)
    ]
    return [
        ("digital", [("text", para), ("text", para.replace("today", "again"))]),
        ("empty_layer", [("blank",)]),
        ("scan2", [("scan",), ("scan",)]),
        ("scan8", [("scan",)] * 8),
        ("cover_scans", [("text", "COVER SHEET\n" + para), *[("scan",)] * 5]),
        ("typed60", typed60),
        ("edge14", [("text+scan", chars(14))]),
        ("edge15", [("text+scan", chars(15))]),
        ("edge24", [("text+scan", chars(24))]),
        ("edge25", [("text+scan", chars(25))]),
        ("thin199", [("text", para), ("text+scan", "\n".join(wrap(nonspace(199))))]),
        ("thin200", [("text", para), ("text+scan", "\n".join(wrap(nonspace(200))))]),
        ("absent399", [("text", "\n".join(wrap(chars(399, "the note"))))] + [("blank",)] * 199),
        ("absent400", [("text", "\n".join(wrap(chars(400, "the note"))))] + [("blank",)] * 199),
        ("glyph_junk", [("text", "\n".join(wrap(GLYPH)))]),
        ("cipher", [("text", "\n".join(wrap(CIPHER)))]),
        ("control", [("text+scan", CONTROL)]),
        ("pleading", [("text", PLEADING)]),
    ]


def build() -> dict[str, Fixture]:
    out: dict[str, Fixture] = {}

    def add(key: str, name: str, ext: str, data: bytes) -> None:
        out[key] = Fixture(key, f"r-{key.replace('_', '-')}", name, ext, data)

    for i, (key, pages) in enumerate(_pdfs(), 1):
        add(key, key.replace("_", " ").title(), ".pdf", pdf(i, pages))
    base = len(out) + 1
    add("locked", "Locked Motion", ".pdf", locked(base))
    add("corrupt", "Corrupt Upload", ".pdf", b"%PDF-1.4\n1 0 obj << /Broken >>\n%%EOF\n")
    add("zero", "Zero Bytes", ".pdf", b"")
    add("merged_docx", "Representation Letter", ".docx", docx())
    add("dotx", "Firm Letterhead", ".dotx", dotx())
    attach_scan = pdf(base + 1, [("scan",), ("scan",)])
    add(
        "mail_eml",
        "Records And Offer",
        ".eml",
        eml(
            [
                ("Scan.pdf", "pdf", attach_scan),
                ("Letter.docx", "vnd.openxmlformats-officedocument.wordprocessingml.document", docx()),
            ]
        ),
    )
    add("notice_htm", "Notice Of Hearing", ".htm", HTM)
    add("proof_rtf", "Proof Of Service", ".rtf", RTF)
    add("note_txt", "Phone Note", ".txt", TXT)
    add("photo_jpg", "Photo Of Card", ".jpg", jpg(base + 2))
    add("outlook_msg", "RE Offer", ".msg", MSG)
    # The collision: one unit, two files whose joined names are "x.pdf.pdf"
    # and "x.pdf", with different bytes.
    add("x_emailed", "x.pdf", ".pdf", pdf(base + 3, [("text", "EMAILED COPY\n" + "\n".join(wrap(PROSE)))]))
    add("x_filed", "x", ".pdf", pdf(base + 4, [("text", "FILED COPY\n" + "\n".join(wrap(PROSE)))]))
    return out


#: Fixture index -> key, for decoding a page's colour back to its fixture.
def index_of(fixtures: dict[str, Fixture]) -> dict[int, str]:
    keys = list(fixtures)
    pdf_keys = [k for k, _ in _pdfs()]
    out = {i: k for i, k in enumerate(pdf_keys, 1)}
    base = len(pdf_keys) + 1
    out[base + 1] = "mail_eml"
    out[base + 2] = "photo_jpg"
    out[base + 3] = "x_emailed"
    out[base + 4] = "x_filed"
    out[base + 5] = "outlook_msg"
    assert set(out.values()) <= set(keys)
    return out


FIXTURES = build()
#: The attachment the fake Outlook reader hands back for the ``.msg`` fixture.
MSG_ATTACHMENT = pdf(len(_pdfs()) + 6, [("scan",), ("scan",)])
INDEX = index_of(FIXTURES)

__all__ = ["FIXTURES", "INDEX", "MSG_ATTACHMENT", "Fixture", "PROSE", "chars", "identity", "pdf"]
