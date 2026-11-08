"""``python -m jobs.attorneys.firm <dir>``: the attorney roster's validator.

Exit 0 and one line of counts when ``<dir>/attorneys.json`` is a roster; exit 1
and the first problem when it is not; exit 2 on a usage error. The words never
quote a value from the file, so the first line is safe to print in a publish
log. STANDARD LIBRARY ONLY: the publish runs it with only the runner on the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import roster


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m jobs.attorneys.firm <attorney roster dir>", file=sys.stderr)
        return 2
    path = Path(args[0]) / roster.FILE_NAME
    if not path.is_file():
        print(f"{path}: no {roster.FILE_NAME} in the roster directory", file=sys.stderr)
        return 1
    read = roster.load(args[0])
    if read.status != roster.OK:
        print(read.reason, file=sys.stderr)
        return 1
    staff = len(read.people) - len(read.attorneys)
    print(f"{path}: OK ({len(read.attorneys)} attorney(s), {staff} staff signer(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
