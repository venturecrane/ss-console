#!/usr/bin/env python3
"""Seat check: the mounted jobs bundle is not older than the image it runs on.

Run with the hermes venv python (stdlib only). The boot smoke sends this file's
text to the Machine over ssh; the in-image self-test runs the image's copy at
/app/jobs-bundle-commit-probe.py. One program, two transports
(operator/templates/seat-checks.sh, check id ``jobs-bundle-built-for-image``).

WHY. A seat reads its jobs bundle pointer only at boot, and until 2026-11-07 the
release tick rebooted a seat onto a new image before it pointed the seat at the
bundle built for that image: a client seat ran rel-b4d97f03 with the previous
bundle for ten minutes, lane code from before the merge on the image from after
it. The tick now points first (operator/bin/lib/fleet/bundle_align.py); this
check is the seat's own refusal of the mismatch, whatever wrote the pointer.

THE RULE. The image bakes its bundle floor, ``/app/bundle-floor`` =
"<floor sha> <floor seq>": the last commit at or before the image's commit that
changed a jobs bundle path, and that commit's number (``git rev-list --count``,
which grows along main's one line). A bundle manifest carries the commit it was
built from and its number (``ss_console_sha``, ``ss_console_seq``). The bundle
passes when it was built from the floor or a later commit, and fails when it was
built from an earlier one: it lacks bundle code the image's commit has.

A BUNDLE THAT LAGS IS NOT A MISMATCH. A commit that changed no bundle path
builds no bundle, so an image built from it runs the bundle of an earlier
commit: that earlier commit is the image's floor (or comes after it), and the
check passes. A bundle NEWER than the image passes too (a runner-only merge
builds a bundle and no image). A manifest with no number predates the number,
so it predates every image that bakes a floor: it fails unless it is the floor.

NOTHING TO COMPARE, A PASS THAT SAYS SO: the image's own copy runs (it was
built from the image's own commit; jobs-bundle-state-matches-mounts and the
lane checks judge whether that is right), or the image bakes no floor (an image
from before this check, which a rollback re-smoke can boot).

FALSIFIER: mount a bundle whose manifest names an older commit number than
/app/bundle-floor, or delete the manifest, and this exits 1 naming both commits.

SMD_SEAT_ROOT prefixes every seat path; it is unset on a seat and set by the
tests to a fixture tree.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = os.environ.get("SMD_SEAT_ROOT", "")
STATE = Path(f"{ROOT}/run/smd-bundles/jobs")
FLOOR = Path(f"{ROOT}/app/bundle-floor")
IMAGE_SHA = Path(f"{ROOT}/app/OVERLAY_REF")
STORE = Path(f"{ROOT}/opt/data/.smd-root/bundles/jobs")
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
VERSION = re.compile(r"\A[0-9]{12}-[0-9a-f]{8}\Z")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _floor() -> tuple[str, int] | None:
    raw = _read(FLOOR)
    if raw is None:
        return None
    parts = raw.split()
    if len(parts) != 2 or not SHA.match(parts[0]) or not parts[1].isdigit():
        raise ValueError(f"{FLOOR} is not '<sha> <seq>': {raw[:120]!r}")
    return parts[0], int(parts[1])


def _manifest(version: str) -> tuple[str, int | None]:
    path = STORE / version / "manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    sha = doc.get("ss_console_sha") if isinstance(doc, dict) else None
    if not isinstance(sha, str) or not SHA.match(sha):
        raise ValueError(f"{path} names no 40-hex ss_console_sha")
    seq = doc.get("ss_console_seq")
    return sha, seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else None


def verdict(version: str, floor: tuple[str, int], image: str) -> tuple[bool, str]:
    """(ok, the one line that says why) for a mounted ``version`` against the image's ``floor``."""
    sha, seq = _manifest(version)
    floor_sha, floor_seq = floor
    if sha == floor_sha:
        return True, f"jobs bundle {version} is built from {sha[:8]}, the image's bundle floor"
    if seq is not None and seq > floor_seq:
        return True, f"jobs bundle {version} is built from {sha[:8]}, after the image's bundle floor {floor_sha[:8]}"
    at = f"commit #{seq}" if seq is not None else "a commit from before bundles carried their commit number"
    return False, (
        f"jobs bundle {version} is built from {sha[:8]} ({at}); the image built from {image[:8]} needs one built "
        f"from {floor_sha[:8]} (commit #{floor_seq}) or later"
    )


def main() -> int:
    state = _read(STATE)
    if state is None:
        print(f"{STATE}: unreadable, so what runs cannot be compared with the image")
        return 1
    if state == "image":
        print("the image's own runner copy runs (built from the image's own commit)")
        return 0
    if not VERSION.match(state):
        print(f"{STATE} names {state[:80]!r}, not a bundle version")
        return 1
    try:
        floor = _floor()
        if floor is None:
            print("this image bakes no bundle floor (an image from before the check): nothing to compare")
            return 0
        ok, why = verdict(state, floor, _read(IMAGE_SHA) or "unknown")
    except (OSError, ValueError) as exc:
        print(f"cannot compare jobs bundle {state} with the image: {exc}")
        return 1
    print(why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
