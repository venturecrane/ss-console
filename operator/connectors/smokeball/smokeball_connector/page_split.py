"""Split pages out of a large PDF into single-page PDFs, in a memory-capped child.

The page finder (page_finder.py) needs each page of a scanned set as its own
small PDF to show the model. The same reason page_cut.py runs in a child
applies here: a 413 MB set must never be read whole into the connector, a
child of the gateway. Same cap (``page_cut.memory_limit``), same timeout, the
reader given a file handle.

Usage (internal)::

    python -I -m smokeball_connector.page_split <source> <first> <last> <outdir>

Writes ``p<N>.pdf`` for each page first..last that exists (``last`` past the
end is clamped; ``last`` below ``first`` writes nothing) and prints one JSON
line ``{"total": <page count>, "written": [N, ...]}``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .page_cut import MEMORY_LIMIT_BYTES, TIMEOUT_SECONDS, CutRefused, _limit_memory, memory_limit

_EXIT_REFUSED = 3


def _child(source: str, first: int, last: int, outdir: str) -> int:
    from pypdf import PdfReader, PdfWriter

    try:
        with open(source, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                print("the file is encrypted, so its pages cannot be read", file=sys.stderr)
                return _EXIT_REFUSED
            total = len(reader.pages)
            written = []
            for number in range(first, min(last, total) + 1):
                writer = PdfWriter()
                writer.add_page(reader.pages[number - 1])
                with open(Path(outdir) / f"p{number}.pdf", "wb") as out:
                    writer.write(out)
                written.append(number)
    except MemoryError:
        return 4
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal, named by class only
        print(f"the file could not be read as a PDF ({exc.__class__.__name__})", file=sys.stderr)
        return _EXIT_REFUSED
    print(json.dumps({"total": total, "written": written}))
    return 0


def split(source: Path, first: int, last: int, outdir: Path, *, limit: int | None = None) -> tuple[int, list[int]]:
    """(the file's page count, the pages written as ``outdir/p<N>.pdf``), or :class:`CutRefused`."""
    cap = limit if limit is not None else memory_limit()
    argv = [
        sys.executable,
        "-I",
        "-m",
        "smokeball_connector.page_split",
        str(source),
        str(first),
        str(last),
        str(outdir),
    ]
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
        raise CutRefused(f"splitting the pages took longer than {TIMEOUT_SECONDS} seconds") from exc
    if done.returncode == _EXIT_REFUSED:
        reason = (done.stderr or "").strip().splitlines()[-1:] or ["the pages could not be read"]
        raise CutRefused(reason[0][:300])
    if done.returncode != 0:
        raise CutRefused("the file is too large to read pages from safely on this seat")
    try:
        data = json.loads(((done.stdout or "").strip().splitlines() or ["{}"])[-1])
        return int(data["total"]), [int(n) for n in data["written"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise CutRefused("the pages could not be split out") from exc


def main(argv: list[str]) -> int:
    _limit_memory(int(os.environ.get("SMD_PAGE_CUT_MEMORY_LIMIT") or MEMORY_LIMIT_BYTES))
    source, first, last, outdir = argv
    return _child(source, int(first), int(last), outdir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
