"""Every place the jobs bundle reads a PDF's text layer or downloads a firm's file.

Wave 5 moves every jobs lane onto one reader and one downloader under
``jobs/matter/``. This census fails when a module OUTSIDE that package reads a
PDF text layer, or calls the seat's ``fetch``, at a site that is not listed
here: each listed site is either recorded by a ``reader`` golden cell
(``cell: <cell prefix>``) or exempt for a stated reason. A new reader written
beside the one reader, or a seventh download site, fails here, so the count
can only fall.

What counts as a site, by the source's own syntax (``ast``):

* a PDF text read: a ``.get_text(...)`` or ``.extract_text(...)`` call in a
  module that imports ``pymupdf``, ``fitz`` or ``pypdf`` (opening a PDF and
  reading its text is how every reader here does it);
* a download: a ``.fetch(...)`` call on an expression that names a seat
  (``seat.fetch``, ``sr.seat.fetch``, ``self.seat.fetch``).

A site is ``<module path under jobs/>::<enclosing function>``. A listed site
that no longer exists (moved into ``jobs/matter/``) fails nothing: the census
holds new sites out; the concept ratchet (``scripts/sprawl/concepts.ts``)
holds the counts down.
"""

from __future__ import annotations

import ast
from pathlib import Path

JOBS = Path(__file__).resolve().parents[1] / "jobs"
#: The one reader and downloader wave 5 builds; nothing inside it is a site.
HOME = "matter"
PDF_LIBS = frozenset({"pymupdf", "fitz", "pypdf"})

_MOVES = "a whole-PDF reader of a firm's file; wave 5 PR R moves it onto jobs/matter"
#: site -> ``cell: <reader golden cell prefix>`` or the reason it is exempt.
SITES: dict[str, str] = {
    # ---- recorded by a reader cell -----------------------------------------------
    "text jobs/litigation/extract.py::pdf_text": "cell: lanes/litigation",
    "text jobs/negotiation/text.py::pdf_text": "cell: lanes/negotiation",
    "text jobs/stages/extract.py::pdf_text": "cell: lanes/medchron",
    "text jobs/demand/preflight.py::_pdf_pages": "cell: lanes/demand",
    "fetch jobs/stages/download.py::run": "cell: pulls/medchron_download",
    "fetch jobs/stages/msg.py::pull_containers": "cell: pulls/msg_containers",
    "fetch jobs/stages/update_base.py::_pull": "cell: pulls/update_base",
    "fetch jobs/demand/pull.py::_fetch_one": "cell: pulls/demand_pull",
    "fetch jobs/drafting/revise/run.py::_download": "cell: pulls/revise_download",
    "fetch jobs/proof/readback.py::remote_sha": "cell: pulls/readback",
    # ---- exempt, each for its reason ---------------------------------------------
    "text jobs/litigation/extract.py::extract_cached": (
        "TextCache.get_text, the litigation store's own method by that name; it reads no PDF"
    ),
    "text jobs/drafting/compose.py::exemplar_text": _MOVES,
    "text jobs/medchron/gates/foreign_identity.py::pdf_text": _MOVES,
    "text jobs/stages/billing_chart.py::text": _MOVES,
    "text jobs/stages/review_evidence.py::text": _MOVES,
    "text jobs/medchron/update/select.py::pdf_pages": _MOVES,
    "text jobs/stages/exhibit_merge.py::__init__": (
        "a page signature for matching an exhibit's pages, not text read for its words; stays where it is"
    ),
    "text jobs/stages/strip.py::page_match": "a page-equality signature for stripping a repeated record, not a read",
    "text jobs/stages/strip.py::identical": "a page-equality signature for stripping a repeated record, not a read",
    "text jobs/stages/strip.py::_same_doc": "a page-equality signature for stripping a repeated record, not a read",
    "text jobs/drafting/caption_read.py::page_one": (
        "the caption read's first page of a court paper, read once for its caption block; not a corpus reader"
    ),
    "text jobs/drafting/page_limits.py::_page_lines": "measures the draft SMD rendered against a page limit; not a firm's file",
    "text jobs/litigation/printout.py::_lay": "lays out the printout SMD builds; not a firm's file",
    "text jobs/litigation/printout.py::text_of": "reads back the printout SMD built; not a firm's file",
    "text jobs/medchron/audit/page_text.py::page_traits": (
        "the audit's traits of an exhibit page SMD assembled (its marks and layout), read after the chronology"
    ),
    "text jobs/medchron/audit/retired.py::_first_page_text": (
        "the retired audit's first-page probe of an exhibit SMD built; kept for its tests"
    ),
    "text jobs/stages/classify.py::run_nonrecord": (
        "the non-record classifier's first-page look at a file already read; it decides a kind, not the text"
    ),
}


def _imports_pdf(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] in PDF_LIBS for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in PDF_LIBS:
            return True
    return False


def census(root: Path = JOBS) -> set[str]:
    """Every site in the bundle outside ``jobs/matter/``."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()
        if rel.startswith(f"jobs/{HOME}/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pdf = _imports_pdf(tree)
        stack: list[str] = []

        class Visit(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_Call(self, node: ast.Call) -> None:
                f = node.func
                where = f"{rel}::{stack[-1] if stack else '<module>'}"
                if isinstance(f, ast.Attribute):
                    if pdf and f.attr in ("get_text", "extract_text"):
                        found.add(f"text {where}")
                    if f.attr == "fetch" and "seat" in ast.unparse(f.value).lower():
                        found.add(f"fetch {where}")
                self.generic_visit(node)

        Visit().visit(tree)
    return found


def test_every_pdf_text_read_and_download_outside_the_one_reader_is_a_cell_or_named():
    new = sorted(census() - set(SITES))
    assert not new, (
        f"a PDF text read or a seat.fetch outside jobs/{HOME}/ that no reader cell records and no exemption names: "
        f"{new}. Read through jobs/{HOME}/ (wave 5), or record it in a reader cell and list it here."
    )


def test_each_listed_cell_is_one_the_reader_family_records():
    import test_reader_goldens

    cells = set(test_reader_goldens.CELLS)
    for site, why in SITES.items():
        if why.startswith("cell: "):
            prefix = why.removeprefix("cell: ")
            assert any(c == prefix or c.startswith(prefix + "/") for c in cells), f"{site}: no cell {prefix}"
        else:
            assert len(why) > 30, f"{site}: an exemption says why"


def test_the_census_finds_a_site_it_was_never_told_about(tmp_path: Path):
    """FALSIFIER: a module that opens a PDF and reads its text, and one that
    downloads through the seat, are both found; jobs/matter/ is not looked in."""
    pkg = tmp_path / "jobs"
    (pkg / "lane").mkdir(parents=True)
    (pkg / HOME).mkdir()
    reader = "import pymupdf\n\ndef read(p):\n    return [pg.get_text() for pg in pymupdf.open(p)]\n"
    (pkg / "lane" / "reader.py").write_text(reader, encoding="utf-8")
    (pkg / "lane" / "pull.py").write_text(
        "def pull(sr, u, d):\n    return sr.seat.fetch(u, d, None)\n", encoding="utf-8"
    )
    (pkg / HOME / "pdf.py").write_text(reader, encoding="utf-8")
    assert census(pkg) == {"text jobs/lane/reader.py::read", "fetch jobs/lane/pull.py::pull"}
