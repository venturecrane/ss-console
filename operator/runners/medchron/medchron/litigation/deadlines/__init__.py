"""The deadlines report: a litigation job kind with no model call at all.

What the firm asks for when a litigation paralegal is out (letters 319 and
381 at the first client): every calendar date on the named attorneys'
litigation files, split by kind, their open tasks, and the cases with a
statute date and no filing recorded. Two workbooks, filed to the firm's own
library matter, read back. Read-only in the practice system otherwise.

Execution class N (ADR 0050): a read and a render, $0. Ported from the
hand-run kit that delivered letter 382 (the engagements repo's
``tools/deadlines/``: ``seat_pull.py``, ``build.py``, ``check_coverage.py``),
kept to the same calls and the same rules so the job's counts equal the
kit's on the same data (``tests/test_litigation_deadlines.py``).

Stages (``data/state.json``, resumable): pull, book, check, file, report.
"""
