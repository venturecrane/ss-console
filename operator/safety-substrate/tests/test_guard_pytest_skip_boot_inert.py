"""Guard 1 (audit Wave 0) — PYTEST-SKIP boot-inert guard.

THE INERT-CONTROL CLASS THIS CLOSES
-----------------------------------
The boot-time safety gate is ``safety-substrate/run_invariants.py``. It
discovers ``tests/test_invariant_*.py`` files, imports each, and invokes a
module-level ``run() -> (bool, str)`` callable. Any failure in ``--strict``
mode blocks agent startup.

There is a silent hole in that mechanism. ``run_invariants.load_test``
classifies a ``ModuleNotFoundError`` for ``pytest`` as ``_PYTEST_ONLY_MARKER``
and the runner SKIPS the file:

    except ModuleNotFoundError as e:
        if e.name == "pytest":
            return None, _PYTEST_ONLY_MARKER   # -> SKIP at boot

The runtime venv on a customer Machine does NOT have pytest installed. So an
invariant whose ONLY boot-discoverable entry point is a ``test_invariant_*.py``
that does ``import pytest`` at module top is **never actually run at boot** —
the import aborts before the runner can reach the ``run()`` callable, the file
is logged ``SKIP``, and the invariant silently does not fire. The boot gate
goes green having enforced nothing.

This is precisely the class the security audit named: a control that exists,
has a ``run()``, passes in the pytest suite, and is structurally inert at
runtime. ``test_invariant_7.py`` is the live instance — it ``import pytest``s
on line 30 and is the only ``test_invariant_7*.py`` file, so invariant #7
(cross-Machine isolation, the load-bearing tenancy promise) does not fire at
Machine boot.

WHAT THIS GUARD ASSERTS
-----------------------
For every invariant number N that the boot runner can discover
(``tests/test_invariant_N*.py``), at least ONE of its boot-discoverable files
must be a *pytest-free runnable boot entry*: it defines a top-level ``run()``
AND does not import pytest at module top. That is the exact predicate the
boot runner needs to actually execute the check.

A pytest-importing ``test_invariant_N.py`` is fine to ALSO exist (it is the
rich unit suite) — but it cannot be the only thing standing between the
runtime and the invariant. The guard mirrors the predicate already encoded in
``.github/workflows/operator-substrate.yml`` line 58
(``grep '^def run()' && ! grep 'import pytest'``); this test makes that
predicate fail CI per-invariant instead of being an implicit shell condition
no one is forced to satisfy.

The guard also proves it bites: ``test_guard_catches_pytest_only_boot_entry``
constructs a temp invariant dir whose only ``test_invariant_*.py`` imports
pytest, and asserts the classifier flags it inert.

Run::

    cd operator && python3 -m pytest \
        safety-substrate/tests/test_guard_pytest_skip_boot_inert.py -v
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve()
_TESTS_DIR = _HERE.parent  # safety-substrate/tests/

# Matches the boot runner's discovery glob: test_invariant_<N>...py, where N is
# the leading integer of the invariant number. run_invariants.py globs
# "test_invariant_*.py"; we additionally parse the invariant number so the
# guard reports per-invariant rather than per-file.
_INVARIANT_FILE_RE = re.compile(r"^test_invariant_(\d+).*\.py$")

# A module-top pytest import. We deliberately anchor to the start of a line
# (after optional leading whitespace is NOT allowed — a top-level import is
# column 0) because that is exactly what aborts module import in the
# pytest-free runtime venv. A pytest import nested inside a function body does
# not abort import and is not the failure mode.
_TOP_PYTEST_IMPORT_RE = re.compile(r"^(import pytest|from pytest\b)", re.MULTILINE)

# A top-level `def run()` — the callable the boot runner invokes.
_TOP_RUN_DEF_RE = re.compile(r"^def run\(\)", re.MULTILINE)


def _boot_discoverable_files() -> dict[str, list[Path]]:
    """Group boot-discoverable invariant test files by invariant number.

    Mirrors ``run_invariants.py``'s ``tests_dir.glob('test_invariant_*.py')``.
    """
    by_invariant: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(_TESTS_DIR.glob("test_invariant_*.py")):
        m = _INVARIANT_FILE_RE.match(path.name)
        if not m:
            continue
        by_invariant[m.group(1)].append(path)
    return by_invariant


def _imports_pytest_at_module_top(source: str) -> bool:
    return _TOP_PYTEST_IMPORT_RE.search(source) is not None


def _has_top_level_run(source: str) -> bool:
    return _TOP_RUN_DEF_RE.search(source) is not None


def _is_pytestfree_boot_entry(path: Path) -> bool:
    """True iff this file is a boot entry the runner can actually execute:
    a top-level ``run()`` and no module-top pytest import.

    This is the exact predicate ``run_invariants.load_test`` needs to reach
    the ``run()`` callable in the pytest-free runtime venv.
    """
    source = path.read_text(encoding="utf-8")
    return _has_top_level_run(source) and not _imports_pytest_at_module_top(source)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_every_discoverable_invariant_has_a_pytestfree_boot_entry():
    """Each invariant the boot runner discovers must have at least one
    pytest-free runnable boot entry, or it is SKIPPED (inert) at Machine
    boot in the pytest-free runtime venv.
    """
    by_invariant = _boot_discoverable_files()
    assert by_invariant, (
        f"no test_invariant_*.py files discovered under {_TESTS_DIR} — the boot runner would find nothing to run"
    )

    inert: list[str] = []
    for invariant, paths in sorted(by_invariant.items()):
        if not any(_is_pytestfree_boot_entry(p) for p in paths):
            file_states = ", ".join(
                f"{p.name}"
                f"[run()={'Y' if _has_top_level_run(p.read_text(encoding='utf-8')) else 'N'},"
                f"pytest={'Y' if _imports_pytest_at_module_top(p.read_text(encoding='utf-8')) else 'N'}]"
                for p in paths
            )
            inert.append(f"invariant #{invariant}: {file_states}")

    assert not inert, (
        "These invariants have NO pytest-free runnable boot entry. In the "
        "runtime venv (no pytest) the boot runner cannot import a file that "
        "does `import pytest` at module top, so it SKIPS it and the invariant "
        "NEVER FIRES at Machine boot — an inert control.\n\n"
        + "\n".join(f"  - {row}" for row in inert)
        + "\n\nFIX: add a sibling test_invariant_<N>_<name>.py with a top-level "
        "run() and no module-top pytest import (delegate to the invariant "
        "module's own pytest-free self-check, as test_invariant_6_no_citations.py "
        "does), or move the pytest import inside the test functions.\n"
        "This mirrors .github/workflows/operator-substrate.yml line 58."
    )


def test_classifier_distinguishes_clean_from_pytest_entries():
    """Sanity-check the predicate against the known files: the clean
    run()-style invariants are recognised as boot entries, and the
    pytest-importing files are recognised as NOT boot entries. Locks the
    classifier so a future refactor of the regexes cannot silently invert it.
    """
    clean = _TESTS_DIR / "test_invariant_1_no_destructive_without_confirmation.py"
    pytest_only = _TESTS_DIR / "test_invariant_7.py"

    assert clean.exists(), f"expected fixture file missing: {clean}"
    assert pytest_only.exists(), f"expected fixture file missing: {pytest_only}"

    assert _is_pytestfree_boot_entry(clean), "invariant #1's run()-style file should be a valid pytest-free boot entry"
    assert not _is_pytestfree_boot_entry(pytest_only), (
        "test_invariant_7.py imports pytest at module top — it must NOT count "
        "as a runnable boot entry (it is SKIPPED by the boot runner)"
    )


# ---------------------------------------------------------------------------
# Proof the guard bites — synthetic failing fixture
# ---------------------------------------------------------------------------


def test_guard_catches_pytest_only_boot_entry(tmp_path: Path):
    """Construct an invariant whose ONLY boot-discoverable file imports
    pytest at module top, and assert the guard's per-invariant predicate
    classifies it inert. Proves the guard catches the real failure shape
    rather than tautologically passing.
    """
    bad = tmp_path / "test_invariant_99_synthetic.py"
    bad.write_text(
        "import pytest\n\ndef run():\n    return (True, 'this run() is unreachable at boot — import aborts first')\n",
        encoding="utf-8",
    )
    assert not _is_pytestfree_boot_entry(bad), (
        "a file that imports pytest at module top must be classified as NOT a boot entry, even though it defines run()"
    )

    # And the positive control: the same body without the pytest import IS a
    # valid boot entry.
    good = tmp_path / "test_invariant_99_clean.py"
    good.write_text(
        "import sys\n\ndef run():\n    return (True, 'reachable at boot')\n",
        encoding="utf-8",
    )
    assert _is_pytestfree_boot_entry(good)
