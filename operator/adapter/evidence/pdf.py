"""Minimal pure-Python PDF renderer for the evidence-packet summary page.

The spec calls for ``01-summary.pdf`` — a Susan-readable narrative,
plain language first, technical exhibits after. The spec's
implementation notes name Pandoc + wkhtmltopdf as the production path;
the spec also accepts fpdf2 or reportlab via the task brief. Neither is
in the adapter's dependency set today.

This module ships a self-contained minimal PDF writer that produces a
single-document multi-page PDF with the page count, fonts, and content
streams needed for a compliance evidence summary. It targets PDF 1.4
syntax, uses the 14 standard Type 1 fonts (Helvetica and
Helvetica-Bold), and does no compression — readability and
portability beat byte-size for an evidence packet. The file is a
deterministic byte sequence: same inputs produce identical output, so
the manifest sha256 is stable for round-trip tests.

When ``fpdf2`` or ``reportlab`` is installed, callers may prefer that
path; the renderer here is the default so the evidence packet works
end-to-end without extra installs. Bumping the adapter to a real PDF
library is a small follow-on and the manifest contract does not
change.
"""

from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Layout constants — Letter, 1-inch margins, 12pt body, 16pt headings.
# ---------------------------------------------------------------------------

PAGE_WIDTH = 612.0   # 8.5in * 72pt
PAGE_HEIGHT = 792.0  # 11in  * 72pt
MARGIN_LEFT = 72.0
MARGIN_RIGHT = 72.0
MARGIN_TOP = 72.0
MARGIN_BOTTOM = 72.0

BODY_FONT_NAME = "Helvetica"
BOLD_FONT_NAME = "Helvetica-Bold"
BODY_FONT_SIZE = 11.0
HEADING_FONT_SIZE = 16.0
LINE_HEIGHT = 14.0
HEADING_LINE_HEIGHT = 22.0
PARAGRAPH_GAP = 6.0
SECTION_GAP = 16.0
WRAP_WIDTH = 88   # chars before wrap at body size + margins


def _pdf_escape(text: str) -> str:
    """Escape `(`, `)`, `\\` for PDF string literals.

    Strip ASCII em-dash equivalents and non-ASCII bytes to keep the
    standard-font glyph table happy and to comply with the repo's
    no-em-dash style rule for shipped content.
    """
    cleaned = text.replace("—", "--").replace("–", "-")
    cleaned = cleaned.encode("ascii", "replace").decode("ascii")
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class _Block:
    kind: str          # 'heading' | 'paragraph' | 'bullet' | 'spacer' | 'monospace'
    text: str = ""


def _build_blocks(sections: Sequence[tuple[str, Sequence[str]]]) -> List[_Block]:
    """Convert (heading, paragraphs) tuples into a flat block list."""
    out: List[_Block] = []
    for heading, paragraphs in sections:
        if heading:
            out.append(_Block(kind="heading", text=heading))
        for para in paragraphs:
            if not para:
                out.append(_Block(kind="spacer"))
                continue
            if para.startswith("- "):
                out.append(_Block(kind="bullet", text=para[2:]))
            elif para.startswith("```") or para.startswith("    "):
                out.append(_Block(kind="monospace", text=para.lstrip()))
            else:
                out.append(_Block(kind="paragraph", text=para))
        out.append(_Block(kind="spacer"))
    return out


def _wrap_lines(text: str, width: int) -> List[str]:
    if not text:
        return [""]
    return textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def _paginate(blocks: Iterable[_Block]) -> List[List[tuple[str, str, float]]]:
    """Lay out blocks into pages. Returns list of pages, each a list of
    (font_name, escaped_text, leading) tuples to emit in PDF text mode."""
    usable_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    pages: List[List[tuple[str, str, float]]] = []
    current: List[tuple[str, str, float]] = []
    y = 0.0

    def _flush_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = []
        y = 0.0

    for block in blocks:
        if block.kind == "spacer":
            if y + PARAGRAPH_GAP > usable_height:
                _flush_page()
            else:
                y += PARAGRAPH_GAP
                current.append(("", "", PARAGRAPH_GAP))
            continue

        if block.kind == "heading":
            font = BOLD_FONT_NAME
            leading = HEADING_LINE_HEIGHT
            lines = _wrap_lines(block.text, WRAP_WIDTH)
        elif block.kind == "bullet":
            font = BODY_FONT_NAME
            leading = LINE_HEIGHT
            wrapped = _wrap_lines(block.text, WRAP_WIDTH - 4)
            lines = ["* " + wrapped[0]] + ["  " + ln for ln in wrapped[1:]]
        elif block.kind == "monospace":
            font = BODY_FONT_NAME
            leading = LINE_HEIGHT
            lines = _wrap_lines(block.text, WRAP_WIDTH)
        else:
            font = BODY_FONT_NAME
            leading = LINE_HEIGHT
            lines = _wrap_lines(block.text, WRAP_WIDTH)

        for line in lines:
            if y + leading > usable_height:
                _flush_page()
            escaped = _pdf_escape(line)
            current.append((font, escaped, leading))
            y += leading

    _flush_page()
    if not pages:
        pages.append([(BODY_FONT_NAME, "", LINE_HEIGHT)])
    return pages


def _build_content_stream(items: Sequence[tuple[str, str, float]]) -> bytes:
    """Emit a PDF text content stream for one page."""
    stream = io.StringIO()
    stream.write("BT\n")
    x = MARGIN_LEFT
    y_top = PAGE_HEIGHT - MARGIN_TOP
    stream.write(f"1 0 0 1 {x:.2f} {y_top:.2f} Tm\n")

    current_font: Optional[str] = None
    for font, text, leading in items:
        if not font and not text:
            stream.write(f"0 -{leading:.2f} Td\n")
            continue
        font_key = "F2" if font == BOLD_FONT_NAME else "F1"
        size = HEADING_FONT_SIZE if font == BOLD_FONT_NAME else BODY_FONT_SIZE
        if font_key != current_font:
            stream.write(f"/{font_key} {size:.2f} Tf\n")
            current_font = font_key
        stream.write(f"({text}) Tj\n")
        stream.write(f"0 -{leading:.2f} Td\n")
    stream.write("ET\n")
    return stream.getvalue().encode("latin-1", "replace")


def _serialize_pdf(pages: Sequence[Sequence[tuple[str, str, float]]]) -> bytes:
    """Assemble the multi-page PDF byte sequence."""
    objects: List[bytes] = []

    def _add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    # Object 1: catalog (placeholder; written after we know pages obj id)
    _add(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Object 2: pages (placeholder; written after we know all page kids)
    _add(b"")
    # Object 3: font Helvetica
    _add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    # Object 4: font Helvetica-Bold
    _add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>"
    )

    page_ids: List[int] = []
    for page_items in pages:
        content_stream = _build_content_stream(page_items)
        content_obj_id = _add(
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
            + content_stream
            + b"\nendstream"
        )
        page_obj_id = _add(
            (
                "<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PAGE_WIDTH:.2f} {PAGE_HEIGHT:.2f}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                f"/Contents {content_obj_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_obj_id)

    # Now rewrite object 2 with the real /Kids list.
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    )

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{idx} 0 obj\n".encode("ascii"))
        buf.write(obj)
        buf.write(b"\nendobj\n")

    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buf.write(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_pos}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    return buf.getvalue()


def render_summary_pdf(
    *,
    customer_slug: str,
    customer_name: str,
    period_start: str,
    period_end: str,
    matter: str,
    signer_key_id: str,
    manifest_sha256: str,
    counts: dict,
    coverage_lines: Optional[Sequence[str]] = None,
    counts_are_partial: bool = False,
    extra_sections: Optional[Sequence[tuple[str, Sequence[str]]]] = None,
) -> bytes:
    """Render the Susan-readable summary PDF and return its bytes.

    The PDF includes the plain-language first page (per spec §00-README)
    and a per-section narrative summarizing what is in the packet. The
    first page is intentionally readable by a non-technical attorney.

    ``coverage_lines`` is the packet's statement of what its audit
    section can and cannot answer, rendered ahead of the counts so a
    reader meets the boundary before the numbers. ``counts_are_partial``
    qualifies the counts preamble when rows in the period could not be
    attributed to a matter either way; without it, "zero values are
    truthful zeros" would be an overclaim.

    The last page always carries:

    * ``Signature (SMDurgan, LLC): <signer_key_id>``
    * ``Manifest SHA-256: <hex>``

    so a reader can verify the document is the one the manifest
    references.
    """
    sections: List[tuple[str, Sequence[str]]] = []

    sections.append(
        (
            f"Compliance Evidence -- {customer_name}",
            [
                f"Customer slug: {customer_slug}",
                f"Matter scope: {matter}",
                f"Period covered: {period_start} to {period_end}",
                "",
                "This package documents how the Operator operated inside this "
                "customer instance during the period above. It is meant to be "
                "read by an attorney advising on AI governance, by an outside "
                "auditor, or by the customer itself.",
                "",
                "You do not need a technical background to read this package. "
                "The first two documents (the README and this summary PDF) are "
                "written for general counsel. The remaining documents are the "
                "underlying evidence, in case you want to verify specific "
                "claims.",
            ],
        )
    )

    if coverage_lines:
        body: List[str] = []
        for line in coverage_lines:
            body.append(line)
            body.append("")
        sections.append(("What this package covers, and what it cannot", body))

    sections.append(
        (
            "What this package proves",
            [
                "- Every external action ran under a named human reviewer; no "
                "autonomous external sends without an approver of record.",
                "- The agent operated only within the scope the customer "
                "authorized; see the audit log and the redacted customer.yaml.",
                "- Every memory rule, person mapping, and voice sample the "
                "agent learned is enumerated in the memory snapshot.",
                "- The safety substrate's eight architectural invariants ran "
                "on every boot; failures appear in the boot-check log.",
                "- The packet's manifest is sha256-hashed. It is NOT yet "
                "cryptographically signed (the signature is a stub; a real RSA "
                "detached signature lands in a follow-on per the spec). Verify "
                "integrity against the out-of-band manifest hash, not the copy "
                "inside this packet (see Verification).",
            ],
        )
    )

    if counts_are_partial:
        counts_preamble = (
            "The exact numbers come from the audit log dump in this packet. "
            "Counts below are inclusive of the period. Read them as a FLOOR, "
            "not a complete tally: as stated above, some rows in this period "
            "carry no matter attribution and are outside this packet's scope. "
            "A zero below means nothing was recorded under this label, not "
            "that nothing happened."
        )
    else:
        counts_preamble = (
            "The exact numbers come from the audit log dump in this packet. "
            "Counts below are inclusive of the period; zero values are "
            "truthful zeros (no data ingested), not placeholders."
        )

    sections.append(
        (
            "What the agent did (counts)",
            [
                counts_preamble,
                "",
                f"- Audit events recorded: {counts.get('audit_events', 0)}",
                f"- Drafts created: {counts.get('drafts_created', 0)}",
                f"- Drafts approved: {counts.get('drafts_approved', 0)}",
                f"- Drafts rejected: {counts.get('drafts_rejected', 0)}",
                f"- Memory rules touched: {counts.get('memory_rule_events', 0)}",
                f"- Skills enabled at any point: {counts.get('skills_enabled', 0)}",
                f"- Invariant boot checks recorded: {counts.get('boot_checks', 0)}",
                f"- Invariant violations: {counts.get('invariant_violations', 0)}",
                f"- Escalations fired: {counts.get('escalations', 0)}",
            ],
        )
    )

    sections.append(
        (
            "Packet contents",
            [
                "The full packet, this PDF excluded, contains:",
                "- 00-README.md (plain-language first page)",
                "- 01-summary.pdf (this document)",
                "- 03-audit-log.csv (structured audit_log dump)",
                "- 05-customer-yaml.redacted.yml (customer config, secrets redacted)",
                "- 06-memory-snapshot.json (rules, person mappings, voice metadata)",
                "- 07-skill-catalog.json (active skills + content hashes)",
                "- 09-boot-checks.csv (invariant boot-check dump)",
                "- manifest.json (file hashes + the SMDurgan, LLC signature block)",
                "",
                "Per spec, the substantive payload of drafts and sent messages "
                "is NOT in this packet -- those live in R2 keyed by digest. The "
                "audit log records the digest so an auditor can request the "
                "underlying object on demand.",
            ],
        )
    )

    if extra_sections:
        sections.extend(extra_sections)

    sections.append(
        (
            "Verification",
            [
                f"Signature (SMDurgan, LLC): {signer_key_id} (UNSIGNED stub -- not "
                "cryptographically verifiable in this release).",
                f"Manifest SHA-256: {manifest_sha256}",
                "",
                "This packet is UNSIGNED. The manifest SHA-256 quoted here and "
                "in the README lives inside the same archive it describes, so "
                "on its own it proves only internal self-consistency, not "
                "authenticity. To check integrity, compare the manifest "
                "SHA-256 against the value recorded out of band when the "
                "packet was generated: the COMPLIANCE_PACKET_EXPORTED "
                "audit-log row's manifest_sha256, obtained from the firm or "
                "SMD rather than from this archive. Once that matches, hash a "
                "specific file with sha256 and compare to manifest.json -> "
                "file_hashes -> <filename>.",
            ],
        )
    )

    blocks = _build_blocks(sections)
    pages = _paginate(blocks)
    return _serialize_pdf(pages)


__all__ = [
    "render_summary_pdf",
    "PAGE_WIDTH",
    "PAGE_HEIGHT",
]
