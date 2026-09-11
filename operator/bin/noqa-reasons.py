#!/usr/bin/env python3
"""Every noqa comment in operator/ names its rule and carries a reason.

WHY. The 2026-08-24 review found 261 suppressions for a linter that never
ran; by 2026-09-10 it was 398, two thirds naming rules ruff did not enable, and
61 of the 210 BLE001 suppressions carried nothing after the code. A noqa
with no reason is a claim that the rule is wrong here, made by nobody, to
nobody. RUF100 (enabled in ruff.toml) deletes the inert ones; this gate is the
other half: a suppression that survives must say what it protects.

THE RULE. A noqa comment matches

    <hash> noqa: CODE[, CODE...] <sep> <reason of at least 20 characters>

where <sep> is ` - `, ` -- ` or ` — `. A bare noqa (no code) fails: it
silences every rule on the line, which is never what anyone means. Same shape
as the nosemgrep-justification gate in .github/workflows/security.yml, which
has held the TypeScript side to this since 2026-07.

RUN.  cd operator && python3 bin/noqa-reasons.py          (exit 1 on any offender)
      python3 bin/noqa-reasons.py --self-test            (proves it can fail)
Pinned by bin/tests/test_noqa_reasons.py and run in operator-substrate.yml.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MIN_REASON_CHARS = 20
NOQA_RE = re.compile(r"#\s*noqa\b(?P<codes>:\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?(?P<rest>.*)$")
REASON_RE = re.compile(r"^\s*(?:-{1,2}|—)\s*(?P<reason>\S.*)$")
SKIP_PARTS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".rendered", "node_modules"}


def offenders_in_text(text: str, path: str = "<text>") -> list[str]:
    """Human-readable offender lines for one file's contents."""
    out: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = NOQA_RE.search(line)
        if not m:
            continue
        if not m.group("codes"):
            out.append(f"{path}:{lineno}: bare noqa silences every rule; name the code")
            continue
        r = REASON_RE.match(m.group("rest"))
        if not r or len(r.group("reason").strip()) < MIN_REASON_CHARS:
            out.append(
                f"{path}:{lineno}: `noqa{m.group('codes')}` needs ` - ` and at least "
                f"{MIN_REASON_CHARS} characters saying what the suppression protects"
            )
    return out


def walk(root: Path) -> list[Path]:
    files = []
    for p in sorted(root.rglob("*.py")):
        if SKIP_PARTS.intersection(p.relative_to(root).parts):
            continue
        files.append(p)
    return files


def offenders(root: Path) -> list[str]:
    out: list[str] = []
    for p in walk(root):
        out.extend(offenders_in_text(p.read_text(encoding="utf-8"), p.relative_to(root).as_posix()))
    return out


def self_test() -> int:
    """A gate that cannot fail has measured nothing (Law 12)."""
    h = "# "  # built at runtime so this file does not match its own gate
    bad = f"x = 1  {h}noqa: BLE001\ny = 2  {h}noqa\nz = 3  {h}noqa: E402 - short\n"
    good = f"x = 1  {h}noqa: BLE001 - the write must not gate the wake; the row is best effort\n"
    found = offenders_in_text(bad, "fixture.py")
    if len(found) != 3 or offenders_in_text(good, "fixture.py"):
        print("SELF-TEST FAILED: the gate did not flag the fixture offenders", file=sys.stderr)
        return 3
    print("self-test ok: 3 fixture offenders flagged, 1 reasoned line accepted")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    found = offenders(args.root)
    if found:
        print(f"{len(found)} noqa annotation(s) without a reason:")
        for line in found:
            print(f"  {line}")
        return 1
    print("every noqa in the tree names its rule and carries a reason")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
