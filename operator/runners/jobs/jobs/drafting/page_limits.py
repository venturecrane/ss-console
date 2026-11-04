"""A section's page limit, measured on the rendered pages (``format.limits``).

WHY. The attorney sets limits a model cannot keep by counting words: "the
liability section no more than three pages" (Chris Price, 2026-11-03). A page
is a fact of layout (the font, the double spacing, the chart sizes), so it is
measured, never estimated: the rendered Word file is converted to PDF in the
job (LibreOffice, headless, the same file the attorney receives), and the
section's span is read off the PDF pages, from the page its heading is on to
the last page holding its text.

``format.limits`` in ``drafting-firm.yaml``::

    limits:
      mediation_brief:
        "V. LIABILITY": 3      # a title from mediation_brief_sections; max pages

An over-limit section is a CONTENT finding (``format_check.Result.model``): the
job's one scoped format repair shortens it, the document is rendered and
measured again, and a section still over its limit stops the job for SMD with
the measured count (``length_limit``), nothing filed. A document that could not
be measured (no converter on the machine, a conversion that failed, a heading
not found on any page) is never read as within the limit: it stops the job the
same way, saying it could not be measured.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .outcome import DraftingFailed

_ROMAN = re.compile(r"^\s*[IVXLC]+\.\s*", re.I)
#: A converter: (docx path, output dir) -> the PDF path.
Converter = Callable[[Path, Path], Path]
CONVERT_TIMEOUT_S = 180
#: The unit's record of what was measured.
RECORD = "page-limits.json"


class Unmeasured(RuntimeError):
    """The pages could not be measured; the message says why."""


@dataclass(frozen=True)
class Span:
    title: str
    pages: int
    limit: int

    @property
    def over(self) -> bool:
        return self.pages > self.limit

    def finding(self) -> str:
        return (
            f"length: the {self.title!r} section runs {self.pages} pages; the firm's limit is {self.limit} "
            f"page(s). Shorten that section to fit, keeping every citation and marker; change nothing else"
        )


def _norm(s: str) -> str:
    return " ".join(_ROMAN.sub("", s).split()).casefold()


def limits_for(fmt: dict[str, Any], cls: str) -> dict[str, int]:
    return dict(((fmt.get("limits") or {}).get(cls) or {}))


def validate(limits: Any, classes: tuple[str, ...], sections: list[str]) -> list[str]:
    """``format.limits``: {<class>: {<section title>: <whole pages 1..100>}}; a
    mediation brief's titles are among its authored sections."""
    where = "format.limits"
    if not isinstance(limits, dict):
        return [f"{where}: expected a map"]
    out: list[str] = []
    for cls, body in limits.items():
        if cls not in classes:
            out.append(f"{where}.{cls}: unknown class (expected {list(classes)})")
            continue
        if not isinstance(body, dict) or not body:
            out.append(f"{where}.{cls}: expected a map of section title to pages")
            continue
        for title, n in body.items():
            if not isinstance(title, str) or not title.strip():
                out.append(f"{where}.{cls}: a section title must be text")
            elif not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 100):
                out.append(f"{where}.{cls}.{title}: expected whole pages 1..100")
            elif cls == "mediation_brief" and _norm(title) not in {_norm(s) for s in sections}:
                out.append(f"{where}.{cls}.{title}: not one of format.mediation_brief_sections")
    return out


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert(docx: Path, out_dir: Path) -> Path:
    """The rendered file as PDF, by headless LibreOffice in a private profile."""
    exe = soffice()
    if exe is None:
        raise Unmeasured("no PDF converter (LibreOffice) on this machine")
    profile = out_dir / "lo-profile"
    cmd = [
        exe,
        f"-env:UserInstallation=file://{profile}",
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx),
    ]
    try:
        # The jobs uid has no home: LibreOffice gets the temp dir as one.
        env = {**os.environ, "HOME": str(out_dir)}
        subprocess.run(  # noqa: S603 - argv is soffice from PATH, fixed flags and the job's own rendered file; no shell
            cmd, check=True, capture_output=True, timeout=CONVERT_TIMEOUT_S, env=env
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unmeasured(f"the PDF conversion failed ({type(exc).__name__})") from exc
    pdf = out_dir / (docx.stem + ".pdf")
    if not pdf.is_file():
        raise Unmeasured("the PDF conversion wrote no file")
    return pdf


def _page_lines(pdf: Path, drop: set[str]) -> list[list[str]]:
    """Each page's text lines, top to bottom, without the footer's lines (a
    bare page number, the footer title)."""
    import pymupdf

    out: list[list[str]] = []
    with pymupdf.open(str(pdf)) as doc:
        for page in doc:
            lines = []
            for raw in page.get_text("text").splitlines():
                t = " ".join(raw.split())
                if not t or re.fullmatch(r"(?:page\s*)?\d{1,3}(?:\s*of\s*\d{1,3})?", t, re.I) or t.casefold() in drop:
                    continue
                lines.append(t)
            out.append(lines)
    return out


def _find(pages: list[list[str]], heading: str, start: tuple[int, int]) -> tuple[int, int] | None:
    want = _norm(heading)
    for p in range(start[0], len(pages)):
        for i, line in enumerate(pages[p]):
            if (p, i) > start and _norm(line) == want:
                return p, i
    return None


def spans(pdf: Path, headings: list[str], limits: dict[str, int], footer_title: str | None = None) -> list[Span]:
    """The page span of each limited section. ``headings`` are the document's
    level-1 headings in order (as rendered); a limited section is the heading
    whose title matches the limit's, and it runs to the next one."""
    pages = _page_lines(pdf, {footer_title.casefold()} if footer_title else set())
    at: list[tuple[int, int]] = []
    cur = (0, -1)
    for h in headings:
        hit = _find(pages, h, cur)
        if hit is None:
            raise Unmeasured(f"the heading {h[:60]!r} was not found on the rendered pages")
        at.append(hit)
        cur = hit
    out = []
    for title, limit in limits.items():
        idx = next((i for i, h in enumerate(headings) if _norm(h) == _norm(title)), None)
        if idx is None:
            continue  # the section is absent: the format check's section rule reports that
        first, _ = at[idx]
        if idx + 1 < len(at):
            nxt_page, nxt_line = at[idx + 1]
            last = nxt_page if nxt_line > 0 else nxt_page - 1
        else:
            last = len(pages) - 1
        out.append(Span(title, max(1, last - first + 1), int(limit)))
    return out


def measure(
    docx: Path, cls: str, fmt: dict[str, Any], headings: list[str], converter: Converter | None = None
) -> list[Span]:
    """Convert and measure; ``[]`` when the class has no limits (nothing converted)."""
    limits = limits_for(fmt, cls)
    if not limits:
        return []
    footer = ((fmt.get("layout") or {}).get(cls) or {}).get("footer_title")
    with tempfile.TemporaryDirectory(prefix="pagelimits-") as tmp:
        pdf = (converter or convert)(Path(docx), Path(tmp))
        return spans(pdf, headings, limits, footer)


def enforce(
    path: Path,
    cls: str,
    fmt: dict[str, Any],
    res: Any,
    repaired: bool,
    record: Callable[[Any], None],
    log: Callable[[str], None],
    converter: Converter | None = None,
) -> None:
    """The job's step after the format check (``finish._render_unit``). Over a
    limit: a content finding on ``res``, so the one format repair shortens it;
    still over once that repair is done (``repaired``), or not measurable at
    all: the job stops for SMD with the count (``length_limit``), nothing filed."""
    from . import format_check

    if res.ours or not limits_for(fmt, cls):
        return
    try:
        found = measure(path, cls, fmt, format_check.headings(path), converter)
    except Unmeasured as exc:
        raise DraftingFailed(
            f"length_limit: the firm's section page limits could not be measured ({exc}); nothing was filed"
        ) from None
    record([{"section": s.title, "pages": s.pages, "limit": s.limit} for s in found])
    for s in found:
        log(f"  page limit: {s.title} {s.pages} of {s.limit} page(s)")
    over = [s for s in found if s.over]
    if over and repaired:
        raise DraftingFailed(
            "length_limit: after one shortening pass, "
            + "; ".join(f"the {s.title!r} section runs {s.pages} pages (the firm's limit is {s.limit})" for s in over)
            + "; nothing was filed"
        )
    for s in over:
        res.model(s.finding())


def step(run: Any, u: Any, path: Path, fmt: dict[str, Any], res: Any) -> None:
    """``enforce`` as the drafting job calls it, on one unit of ``run`` (a ``finish.Finish``)."""
    enforce(
        path,
        run.cls,
        fmt,
        res,
        run._is_done(u.stage("format_repair")),
        lambda body: run._uwrite(u, RECORD, body),
        run.log,
    )
