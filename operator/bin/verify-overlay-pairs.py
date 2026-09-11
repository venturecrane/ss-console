#!/usr/bin/env python3
"""Overlay-runtime side of the adapter <-> overlay drift gate (SEC-32).

The vitest gate (`tests/operator-overlay-pairs.test.ts`) pins and verifies the
ss-console-side (`operator/adapter/*`) hash offline, and verifies the manifest
is well-formed and pins the overlay at the same commit the Dockerfile ships.
It CANNOT reach the overlay repo (it runs in the hermetic `verify` workflow).

This script is the other half: it fetches `venturecrane/hermes-smd-overlay` at
the manifest's `overlayRef` and asserts every runtime twin file's sha256 equals
the manifest's recorded `overlaySha256`. Run by the `operator-substrate` CI
workflow, which has network access.

Why this closes the hole: before SEC-32 only the dormant adapter copies were
hashed, so a neutered RUNTIME file (e.g. a no-op audit emitter) shipped green
because the adapter file was untouched and its hash still matched. Now a change
to a runtime file moves its sha256, this check FAILS, and shipping it requires a
conscious manifest bump (with a `syncNote`) — the same conscious-act ledger the
adapter side already uses.

Fail-closed: any missing file, hash mismatch, fetch failure, or malformed
manifest exits non-zero. Silence is never success.

Usage:
    operator/bin/verify-overlay-pairs.py
    operator/bin/verify-overlay-pairs.py --manifest operator/contracts/overlay-pairs.json

Exit codes:
    0  every runtime twin matches its pinned overlaySha256
    1  one or more mismatches / missing files / malformed manifest
    2  could not fetch the overlay at the pinned ref (environment/network)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MANIFEST = _REPO_ROOT / "operator" / "contracts" / "overlay-pairs.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(manifest_path: Path) -> dict:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("overlay-pairs.json must be an object { overlayRepo, overlayRef, pairs[] }")
    for key in ("overlayRepo", "overlayRef", "pairs"):
        if key not in raw:
            raise ValueError(f"overlay-pairs.json missing required key {key!r}")
    if not isinstance(raw["pairs"], list) or not raw["pairs"]:
        raise ValueError("overlay-pairs.json: `pairs` must be a non-empty array")
    return raw


def _fetch_overlay(repo: str, ref: str, dest: Path) -> None:
    """Shallow-fetch the overlay at exactly `ref` and check it out into `dest`.

    Raises subprocess.CalledProcessError on any git failure; the caller maps
    that to exit code 2 (environment), distinct from a hash mismatch (1).
    """

    def run(*args: str) -> None:
        subprocess.run(args, cwd=dest, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "remote", "add", "origin", repo)
    run("git", "fetch", "--depth", "1", "origin", ref)
    run("git", "checkout", "-q", "FETCH_HEAD")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    args = ap.parse_args()

    try:
        manifest = _load_manifest(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot load manifest {args.manifest}: {exc}", file=sys.stderr)
        return 1

    repo = manifest["overlayRepo"]
    ref = manifest["overlayRef"]
    pairs = manifest["pairs"]

    # Validate the manifest carries an overlaySha256 for every pair BEFORE any
    # network work — a missing pin is a manifest defect, not an env problem.
    malformed = [
        p.get("overlayPath", "<unknown>")
        for p in pairs
        if not isinstance(p.get("overlaySha256"), str) or len(p.get("overlaySha256", "")) != 64
    ]
    if malformed:
        print(
            "FAIL: pairs missing a well-formed overlaySha256: " + ", ".join(malformed),
            file=sys.stderr,
        )
        return 1

    print(f"Verifying {len(pairs)} overlay runtime file(s) against {repo} @ {ref}")

    with tempfile.TemporaryDirectory(prefix="overlay-pairs-") as tmp:
        dest = Path(tmp)
        try:
            _fetch_overlay(repo, ref, dest)
        except subprocess.CalledProcessError as exc:
            print(
                f"FAIL (environment): could not fetch overlay {repo} @ {ref}\n"
                f"  git stderr: {exc.stderr.strip() if exc.stderr else exc}",
                file=sys.stderr,
            )
            return 2

        failures: list[str] = []
        for pair in pairs:
            overlay_path = pair["overlayPath"]
            expected = pair["overlaySha256"]
            runtime_file = dest / overlay_path
            if not runtime_file.is_file():
                failures.append(
                    f"{overlay_path}: MISSING at {ref} (renamed/removed in overlay without a manifest update?)"
                )
                continue
            actual = _sha256_file(runtime_file)
            if actual != expected:
                failures.append(
                    f"{overlay_path}: runtime file changed.\n"
                    f"    expected overlaySha256 {expected}\n"
                    f"    actual                {actual}\n"
                    f"    Its control-plane twin is {pair.get('adapterPath')}.\n"
                    f"    Decide whether this needs a paired adapter change, then update\n"
                    f"    overlaySha256 to {actual} and record the decision in syncNote\n"
                    f"    (operator/contracts/overlay-pairs.json)."
                )
            else:
                print(f"  OK  {overlay_path}")

        if failures:
            print("\nFAIL: overlay runtime drift detected:\n", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1

    print(f"\nPASS: all {len(pairs)} overlay runtime file(s) match their pinned overlaySha256.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
