"""Restage a matter PDF (whole, or a page range) in a child process with a hard memory cap.

WHY A CHILD. The attorney's request (2026-11-01) included pages 3-11 of a
2,310-page, 413 MB set. ``pypdf.PdfReader(path)`` reads the WHOLE file into
memory (``_initialize_stream``: ``BytesIO(fh.read())``), and even handed a file
object it reads the whole stream again when it has to rebuild a damaged
cross-reference table. In the connector process that is a seat-wide risk: the
connector is a child of the gateway, and an out-of-memory kill on a small seat
can take the gateway's turn with it.

So the cut runs here, in its own interpreter:

* the reader is given a FILE HANDLE, so a well-formed PDF is read lazily (the
  xref, the page tree, and only the objects the named pages use);
* the child's address space is capped (:data:`MEMORY_LIMIT_BYTES`, via
  ``RLIMIT_AS`` where the platform has it), so a file that would need more
  fails with MemoryError inside the child and the connector reports a plain
  refusal; nothing else on the seat feels it;
* a wall-clock bound (:data:`TIMEOUT_SECONDS`) does the same for a slow file;
* the cut is written straight to a file, never returned through a pipe.

EVERY STAGED FILE IS REWRITTEN HERE, whole files too (2026-11-02, two refusals
on the first live run). Two ordinary firm records were refused by the outbound
PDF check on A&P: a 35-page discharge summary for one embedded file named
"Content Credentials" (a 24 KB C2PA provenance item a scanner adds), and a
223-page set for its 97,058 objects. So the child, after copying the pages:

* STRIPS embedded files: every ``/AF`` (associated files, where C2PA keeps its
  manifest), the catalog's ``/EmbeddedFiles`` name tree and ``/Collection``
  (a portfolio), and every ``/FileAttachment`` annotation, then drops the
  objects nothing references any more, so no embedded-file dictionary is left
  in the bytes for the broker's byte scan to find. A firm's own record going
  back to its own staff loses an item no reader of the page sees; the colleague
  still has the original in Smokeball. Nothing else is removed: a script, an
  action, a launch, an XFA form or a remaining file specification stays in the
  output, where the overlay's structural check and the broker refuse it
  exactly as before. An encrypted source refuses here.
* COUNTS the output's objects the way the overlay's check does
  (``shared/outbound_attachment._pdf_refuse_active_content``: every node
  reachable from the trailer), and past :data:`MAX_PDF_OBJECTS` refuses with a
  reason that tells the model to stage a page range instead, because a range
  carries only its own pages' objects. The limit is a size bound, not a threat
  sign; this turns it into an instruction instead of a held reply.

The whole-file copy clones the document (outline and forms kept); a range copies
only its pages. Either way the staged bytes are the rewrite, and the sha256 the
connector records is of what goes.

Usage (internal)::

    python -I -m smokeball_connector.page_cut <source> <first> <last> <dest>

``first`` and ``last`` both 0 mean the whole file. Exit 0 and the result at
``dest`` (stdout's last line: how many embedded items were removed); otherwise
exit non-zero with one line on stderr that the connector turns into the refusal.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The child's address-space cap is derived from the seat (:func:`memory_limit`):
#: 40% of the machine's memory, never below the floor (interpreter + pypdf + a
#: lazily read cut of a few pages fits well inside it, and a 1 GB seat keeps its
#: gateway) and never above the ceiling. An 8 GB seat gets the ceiling, enough
#: for pypdf to rebuild a damaged cross-reference table of a 413 MB set.
MEMORY_FLOOR_BYTES = 384 * 1024 * 1024
MEMORY_CEILING_BYTES = 3 * 1024 * 1024 * 1024
MEMORY_FRACTION = 0.40
#: Kept for callers that name the old constant: the floor.
MEMORY_LIMIT_BYTES = MEMORY_FLOOR_BYTES
TIMEOUT_SECONDS = 120

_EXIT_REFUSED = 3
_EXIT_MEMORY = 4

#: Mirrors the overlay's ``_MAX_PDF_OBJECTS`` (``shared/outbound_attachment.py``),
#: the bound the reply's structural check holds every file to; a test pins the two.
MAX_PDF_OBJECTS = 50_000
#: Keys that carry or list embedded files. Deleted wherever they appear.
_EMBED_KEYS = ("/AF", "/EmbeddedFiles", "/Collection")


def memory_limit(meminfo: str = "/proc/meminfo") -> int:
    """40% of ``MemTotal``, clamped to [floor, ceiling]; the floor when unreadable."""
    try:
        with open(meminfo, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024  # the kernel reports kB
                    wanted = int(total * MEMORY_FRACTION)
                    return max(MEMORY_FLOOR_BYTES, min(MEMORY_CEILING_BYTES, wanted))
    except (OSError, ValueError, IndexError):
        pass
    return MEMORY_FLOOR_BYTES


class CutRefused(ValueError):
    """The pages could not be cut; the message is the plain reason."""


def _limit_memory(limit: int) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # a platform without RLIMIT_AS (macOS) runs unbounded
        pass


def _is_file_attachment(annot: Any) -> bool:
    try:
        annot = annot.get_object()
    except Exception:  # noqa: BLE001 - an unresolvable annotation is kept, and checked downstream
        return False
    return hasattr(annot, "get") and annot.get("/Subtype") == "/FileAttachment"


def strip_embedded(writer: Any) -> int:
    """Remove embedded files from ``writer`` in place; how many items went.

    Walks every object reachable from the catalog. Only embedded-file carriers
    are touched (:data:`_EMBED_KEYS`, ``/FileAttachment`` annotations); every
    other key, active or not, is left for the downstream checks to judge."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    removed = 0
    stack: list[Any] = [writer.root_object]
    seen: set[tuple[int, int]] = set()
    while stack:
        node = stack.pop()
        if isinstance(node, IndirectObject):
            ref = (node.idnum, node.generation)
            if ref in seen:
                continue
            seen.add(ref)
            node = node.get_object()
        if isinstance(node, DictionaryObject):
            for key in _EMBED_KEYS:
                if key in node:
                    del node[key]
                    removed += 1
            annots = node.get("/Annots")
            annots = annots.get_object() if annots is not None else None
            if isinstance(annots, ArrayObject):
                keep = [a for a in annots if not _is_file_attachment(a)]
                removed += len(annots) - len(keep)
                annots[:] = keep
            stack.extend(v for k, v in node.items() if k != "/Parent")
        elif isinstance(node, ArrayObject):
            stack.extend(node)
    return removed


def count_objects(reader: Any, bound: int = MAX_PDF_OBJECTS) -> int:
    """Nodes reachable from the trailer, counted exactly as the overlay's
    structural check counts them; stops at ``bound + 1``."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    stack: list[Any] = [reader.trailer]
    seen: set[tuple[int, int]] = set()
    visited = 0
    while stack and visited <= bound:
        node = stack.pop()
        if isinstance(node, IndirectObject):
            ref = (node.idnum, node.generation)
            if ref in seen:
                continue
            seen.add(ref)
            node = node.get_object()
        visited += 1
        if isinstance(node, DictionaryObject):
            stack.extend(v for k, v in node.items() if k != "/Parent")
        elif isinstance(node, ArrayObject):
            stack.extend(node)
    return visited


def _child(source: str, first: int, last: int, dest: str) -> int:
    from pypdf import PdfReader, PdfWriter

    whole = first == 0 and last == 0
    try:
        with open(source, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                what = "it cannot be sent" if whole else "its pages cannot be cut out"
                print(f"the file is encrypted, so {what}", file=sys.stderr)
                return _EXIT_REFUSED
            if whole:
                writer = PdfWriter(clone_from=reader)
            else:
                total = len(reader.pages)
                if last > total:
                    print(f"the file has {total} pages, so pages {first}-{last} do not exist", file=sys.stderr)
                    return _EXIT_REFUSED
                writer = PdfWriter()
                for index in range(first - 1, last):
                    writer.add_page(reader.pages[index])
            removed = strip_embedded(writer)
            draft = f"{dest}.draft"
            with open(draft, "wb") as out:
                writer.write(out)
        # pypdf writes every object it holds, referenced or not, so the stripped
        # files would still be in the bytes the broker scans. A clone copies
        # only what the catalog reaches: the second pass leaves them behind.
        try:
            with open(draft, "rb") as fh:
                writer = PdfWriter(clone_from=PdfReader(fh, strict=False))
                with open(dest, "wb") as out:
                    writer.write(out)
        finally:
            os.unlink(draft)
        with open(dest, "rb") as fh:
            objects = count_objects(PdfReader(fh, strict=False))
        if objects > MAX_PDF_OBJECTS:
            fix = (
                "stage it again with a page range (pages), which carries only those pages"
                if whole
                else "ask for fewer pages"
            )
            print(
                f"the file has more than {MAX_PDF_OBJECTS:,} PDF objects, more than one attachment may carry; {fix}",
                file=sys.stderr,
            )
            return _EXIT_REFUSED
    except MemoryError:
        return _EXIT_MEMORY
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal, named by class only
        print(f"the file could not be read as a PDF ({exc.__class__.__name__})", file=sys.stderr)
        return _EXIT_REFUSED
    print(removed)
    return 0


def cut(source: Path, first: int, last: int, dest: Path, *, limit: int | None = None) -> bytes:
    """Pages ``first``..``last`` (1-based, inclusive) of ``source``, or :class:`CutRefused`.
    ``limit`` overrides the seat-derived memory cap (tests)."""
    return restage(source, (first, last), dest, limit=limit)[0]


def restage(source: Path, pages: tuple[int, int] | None, dest: Path, *, limit: int | None = None) -> tuple[bytes, int]:
    """(the rewritten PDF, embedded items removed) for ``source`` whole
    (``pages`` None) or a 1-based inclusive range, or :class:`CutRefused`."""
    first, last = pages if pages else (0, 0)
    cap = limit if limit is not None else memory_limit()
    argv = [sys.executable, "-I", "-m", "smokeball_connector.page_cut", str(source), str(first), str(last), str(dest)]
    try:
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is this interpreter, this module, and paths the connector itself built; no shell
        done = subprocess.run(  # noqa: S603 - this interpreter, this module, paths the connector built
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={"SMD_PAGE_CUT_MEMORY_LIMIT": str(cap), "PATH": "/usr/bin:/bin"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        what = "cutting the pages" if pages else "preparing the file"
        raise CutRefused(f"{what} took longer than {TIMEOUT_SECONDS} seconds") from exc
    if done.returncode == 0 and dest.is_file():
        tail = (done.stdout or "").strip().splitlines()[-1:] or ["0"]
        return dest.read_bytes(), int(tail[0]) if tail[0].isdigit() else 0
    if done.returncode != _EXIT_REFUSED:
        # MemoryError (exit 4), a kill by the cap (negative), or an interpreter
        # that could not even start inside the cap (exit 1, MemoryError on stderr).
        if not pages:
            raise CutRefused("the file is too large to prepare whole on this seat; stage it again with a page range")
        raise CutRefused(
            "the file is too large to cut pages from safely on this seat; ask the firm for those pages as their own file"
        )
    reason = (done.stderr or "").strip().splitlines()[-1:] or ["the pages could not be cut out"]
    raise CutRefused(reason[0][:300])


def main(argv: list[str]) -> int:
    _limit_memory(int(os.environ.get("SMD_PAGE_CUT_MEMORY_LIMIT") or MEMORY_LIMIT_BYTES))
    source, first, last, dest = argv
    return _child(source, int(first), int(last), dest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
