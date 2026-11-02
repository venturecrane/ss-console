"""Cut a page range out of a large PDF in a child process with a hard memory cap.

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

Usage (internal)::

    python -I -m smokeball_connector.page_cut <source> <first> <last> <dest>

Exit 0 and the cut at ``dest``; otherwise exit non-zero with one line on stderr
that the connector turns into the refusal.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: The child's address-space cap. Interpreter + pypdf import is ~60 MB resident
#: and well under 200 MB of address space; a lazily read cut of a few pages adds
#: the objects those pages reference. 384 MiB leaves a 1 GB seat its gateway.
MEMORY_LIMIT_BYTES = 384 * 1024 * 1024
TIMEOUT_SECONDS = 120

_EXIT_REFUSED = 3
_EXIT_MEMORY = 4


class CutRefused(ValueError):
    """The pages could not be cut; the message is the plain reason."""


def _limit_memory(limit: int) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # a platform without RLIMIT_AS (macOS) runs unbounded
        pass


def _child(source: str, first: int, last: int, dest: str) -> int:
    from pypdf import PdfReader, PdfWriter

    try:
        with open(source, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                print("the file is encrypted, so its pages cannot be cut out", file=sys.stderr)
                return _EXIT_REFUSED
            total = len(reader.pages)
            if last > total:
                print(f"the file has {total} pages, so pages {first}-{last} do not exist", file=sys.stderr)
                return _EXIT_REFUSED
            writer = PdfWriter()
            for index in range(first - 1, last):
                writer.add_page(reader.pages[index])
            with open(dest, "wb") as out:
                writer.write(out)
    except MemoryError:
        return _EXIT_MEMORY
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal, named by class only
        print(f"the file could not be read as a PDF ({exc.__class__.__name__})", file=sys.stderr)
        return _EXIT_REFUSED
    return 0


def cut(source: Path, first: int, last: int, dest: Path, *, memory_limit: int = MEMORY_LIMIT_BYTES) -> bytes:
    """Pages ``first``..``last`` (1-based, inclusive) of ``source``, or :class:`CutRefused`."""
    argv = [sys.executable, "-I", "-m", "smokeball_connector.page_cut", str(source), str(first), str(last), str(dest)]
    try:
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is this interpreter, this module, and paths the connector itself built; no shell
        done = subprocess.run(  # noqa: S603 - this interpreter, this module, paths the connector built
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={"SMD_PAGE_CUT_MEMORY_LIMIT": str(memory_limit), "PATH": "/usr/bin:/bin"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CutRefused(f"cutting the pages took longer than {TIMEOUT_SECONDS} seconds") from exc
    if done.returncode == 0 and dest.is_file():
        return dest.read_bytes()
    if done.returncode != _EXIT_REFUSED:
        # MemoryError (exit 4), a kill by the cap (negative), or an interpreter
        # that could not even start inside the cap (exit 1, MemoryError on stderr).
        raise CutRefused(
            "the file is too large to cut pages from safely on this seat; ask the firm for those pages as their own file"
        )
    reason = (done.stderr or "").strip().splitlines()[-1:] or ["the pages could not be cut out"]
    raise CutRefused(reason[0][:300])


def main(argv: list[str]) -> int:
    import os

    _limit_memory(int(os.environ.get("SMD_PAGE_CUT_MEMORY_LIMIT") or MEMORY_LIMIT_BYTES))
    source, first, last, dest = argv
    return _child(source, int(first), int(last), dest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
