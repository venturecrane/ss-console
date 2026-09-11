"""stdin adapter for the staging-time shape gate (ss#2423).

Reads NUL-delimited name/value pairs: the candidate secret first, then every
secret already staged in this provisioning run. Prints one actionable error to
stdout and exits 0 if the candidate must be refused, prints nothing otherwise.

WHY STDIN. The value must never appear in an argv element: on a seat, argv is
readable through the process table, which is the ss#2218 lesson. It is also why
this file prints only what `secret_shape` returns -- variable name, length,
character class -- and never the value itself.

Exit code is 0 either way ON PURPOSE. The caller decides what to do with a
non-empty message; a non-zero exit here under `set -e` would abort provisioning
before the caller could log the reason.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("secret_shape", Path(__file__).resolve().parent / "secret_shape.py")
assert _SPEC and _SPEC.loader
secret_shape = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(secret_shape)


def main() -> int:
    raw = sys.stdin.buffer.read().split(b"\0")
    # A trailing NUL leaves an empty final element; drop it rather than pairing
    # it with nothing.
    if raw and raw[-1] == b"":
        raw.pop()
    if len(raw) < 2:
        return 0
    fields = [f.decode("utf-8", "replace") for f in raw]
    name, value = fields[0], fields[1]
    others = dict(zip(fields[2::2], fields[3::2], strict=False))

    err = secret_shape.check_staged_secret(name, value, others)
    if err:
        sys.stdout.write(err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
