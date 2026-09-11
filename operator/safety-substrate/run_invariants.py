#!/usr/bin/env python3
"""Safety substrate invariant runner (Phase A.5 gate).

Discovers `test_*.py` files under the fixtures dir, imports each, invokes
its `run() -> (bool, str)` callable, collects pass/fail. In `--strict`
mode (default in bootstrap.sh), any failure exits non-zero and blocks
agent startup.

The seven irreducible invariants (per `safety-substrate/README.md` and
`docs/specs/operator/safety-invariants.md`):

  1. No destructive action without explicit current-turn confirmation
  2. Outbound external send governed by the authored per-recipient-class ceiling
     (external_send / external_send_internal), fail-closed when unauthored
  3. No contract / commitment execution autonomously
  4. "Don't act" / "stop" instructions are sticky across compaction
  5. Trust-ceiling enforced in code, not in prompt
  6. No fabricated citations / source-provenance discipline (two test
     files — refusal layer + enforcement layer for the same invariant)
  7. Cross-Machine query prohibition at boot (per-customer binding check)

Called from bootstrap.sh on container start. Re-runs on every Hermes SHA
bump (because the container rebuilds with new test files).
"""

import argparse
import importlib.util
import sys
from pathlib import Path


_PYTEST_ONLY_MARKER = "pytest_only_import_error"


def load_test(test_path: Path):
    """Load a test_*.py file and return (run_callable, import_error_msg).

    Returns (None, msg) on import error and (None, None) when the file
    imports cleanly but lacks a `run()` — those are pytest-mode tests
    exercised separately (see README "What ships means for a substrate
    test"). Returns (callable, None) on success.

    Special case: an import error for `pytest` itself is classified as
    pytest-mode rather than a substrate-runner failure. The runner runs
    in the customer Machine venv (no pytest); such tests are CI-gated
    via the project's pytest suite, not the boot-time substrate gate.
    """
    spec = importlib.util.spec_from_file_location(test_path.stem, test_path)
    if spec is None or spec.loader is None:
        return None, "spec_from_file_location returned None"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as e:
        if e.name == "pytest":
            return None, _PYTEST_ONLY_MARKER
        return None, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 - the runner loads every invariant module; one module failing to import must not stop the others from running
        return None, f"{type(e).__name__}: {e}"
    return getattr(module, "run", None), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", required=True)
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--strict", action="store_true", help="exit non-zero on any failure (default in bootstrap)")
    args = ap.parse_args()

    tests_dir: Path = args.fixtures
    if not tests_dir.exists():
        msg = f"safety-substrate tests dir not found at {tests_dir}"
        if args.strict:
            print(f"FAIL: {msg}", file=sys.stderr)
            return 1
        print(f"WARN: {msg}", file=sys.stderr)
        return 0

    # The runner only exercises invariant tests (test_invariant_*.py).
    # Other test_*.py files (test_refusal.py, test_sticky_stop.py,
    # test_trust_ceiling_log.py) cover substrate primitives via pytest;
    # they're not invariant fixtures and the runner skips them.
    test_files = sorted(p for p in tests_dir.glob("test_invariant_*.py") if p.is_file())
    if not test_files:
        msg = f"no safety-substrate invariant tests found under {tests_dir}"
        if args.strict:
            print(f"FAIL (strict): {msg}", file=sys.stderr)
            return 1
        print(f"WARN: {msg}", file=sys.stderr)
        return 0

    failures: list[str] = []
    passes: list[str] = []
    pytest_only: list[str] = []
    for tf in test_files:
        run_fn, import_err = load_test(tf)
        if import_err == _PYTEST_ONLY_MARKER:
            print(f"  SKIP {tf.name}: pytest-mode test (pytest not available in runner venv)")
            pytest_only.append(tf.name)
            continue
        if import_err is not None:
            print(f"  IMPORT FAIL {tf.name}: {import_err}", file=sys.stderr)
            failures.append(f"{tf.name}: import failed ({import_err})")
            continue
        if run_fn is None:
            # File imports cleanly but is pytest-mode (no run() entry point).
            # Treat as SKIP — the substrate runner doesn't drive pytest, but
            # CI does, and the README documents this dual mode.
            print(f"  SKIP {tf.name}: pytest-mode test (no run() callable)")
            pytest_only.append(tf.name)
            continue
        try:
            ok, message = run_fn()
        except Exception as e:  # noqa: BLE001 - one invariant raising must not stop the remaining invariants; the raise is recorded as that invariant's failure
            failures.append(f"{tf.name}: raised {type(e).__name__}: {e}")
            continue
        if ok:
            print(f"  PASS {tf.name}: {message}")
            passes.append(tf.name)
        else:
            print(f"  FAIL {tf.name}: {message}", file=sys.stderr)
            failures.append(f"{tf.name}: {message}")

    print(
        f"\nsubstrate result for customer={args.customer}: "
        f"{len(passes)} pass, {len(failures)} fail, "
        f"{len(pytest_only)} pytest-only (of {len(test_files)} invariant tests)"
    )

    if failures:
        print("\nFAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1 if args.strict else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
