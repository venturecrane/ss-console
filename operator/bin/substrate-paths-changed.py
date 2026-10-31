#!/usr/bin/env python3
"""Decide whether a PR touches the operator substrate, for the CI detect step.

operator-substrate is a required check on main. A required check must report
on every PR, so the workflow can no longer use `on.pull_request.paths` (a
skipped workflow reports nothing and the PR sits at "Expected" forever).
Instead the workflow runs on every PR, and this script answers one question
inside the job: did anything under the substrate path list change?

    substrate-paths-changed.py --base <sha> --head <sha> [--paths FILE] [--exclude FILE]
    substrate-paths-changed.py --base <sha> --head <sha> --image

Prints `relevant=true` or `relevant=false` on stdout (and appends the same
line to $GITHUB_OUTPUT when set). Fails CLOSED: if the diff cannot be
computed, or no base is given (a push to main), the answer is `true` and the
full suite runs. The only way to skip the suites is a successful diff whose
every path misses every pattern.

THE IMAGE PATH LIST (`--image`, ADR 0100 W3). deploy.yml's build-seat-image job
asks the same question of a narrower list: the substrate list minus its
`.github/` lines (CI, not the image) minus every artifact path
(`.github/operator-artifact-paths.txt`: what ships to seats as a versioned
artifact, today operator/skills/**). :func:`image_patterns` is the one
definition; the release controller's ordering rule (operator/bin/lib/fleet/
ordering.py, base(S)) imports it and asks git the same question through
:func:`git_pathspecs`, so "which commit last changed the image" cannot mean two
things.

The pattern list lives in `.github/operator-substrate-paths.txt`, and the
matcher here is the single implementation: the conformance test imports it,
so the test's idea of "which files trigger the suites" cannot drift from the
workflow's. Stdlib only (this runs before any pip install).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS_FILE = REPO_ROOT / ".github" / "operator-substrate-paths.txt"
ARTIFACT_PATHS_FILE = REPO_ROOT / ".github" / "operator-artifact-paths.txt"


def load_patterns(paths_file: Path = DEFAULT_PATHS_FILE) -> list[str]:
    """One pattern per non-blank, non-comment line."""
    out: list[str] = []
    for raw in paths_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    if not out:
        raise ValueError(f"{paths_file} holds no patterns")
    return out


def matches(pattern: str, path: str) -> bool:
    """Minimal GitHub-Actions-paths glob matcher for the shapes the list uses:
    `dir/**` (prefix), an exact file, and single-`*` segments."""
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    if "*" not in pattern:
        return path == pattern
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(regex, path) is not None


def relevant_paths(changed: list[str], patterns: list[str], excludes: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The changed paths some pattern matches and no exclude does."""
    return [p for p in changed if any(matches(pat, p) for pat in patterns) and not any(matches(x, p) for x in excludes)]


def image_patterns(
    paths_file: Path = DEFAULT_PATHS_FILE, artifact_file: Path = ARTIFACT_PATHS_FILE
) -> tuple[list[str], list[str]]:
    """(include, exclude): the paths a seat image carries.

    The substrate list without its `.github/` lines, less every artifact path.
    The one definition of "an image path" (deploy.yml build-seat-image, base(S)).
    """
    include = [p for p in load_patterns(paths_file) if not p.startswith(".github/")]
    return include, load_patterns(artifact_file)


def git_pathspecs(include: list[str], exclude: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The same match as :func:`relevant_paths`, as git pathspecs (for ``git log -- ...``).

    Glob magic matches the list's shapes as :func:`matches` does: ``dir/**``
    everything under dir, a single ``*`` within one segment, a plain path itself.
    """
    return [f":(glob){p}" for p in include] + [f":(glob,exclude){p}" for p in exclude]


def changed_files(base: str, head: str) -> list[str] | None:
    """Files changed on `head` since its merge-base with `base`. None if the
    diff cannot be computed (unknown sha, shallow clone), so the caller can
    fail closed rather than guess."""
    try:
        proc = subprocess.run(  # noqa: S603 - literal git argv, no shell; base and head are the shas CI passed in
            ["git", "diff", "--name-only", f"{base}...{head}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(f"::warning::git unavailable ({exc}); running the full suite", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(
            f"::warning::git diff {base}...{head} failed ({proc.stderr.strip()}); running the full suite",
            file=sys.stderr,
        )
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def emit(relevant: bool) -> None:
    line = f"relevant={'true' if relevant else 'false'}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="", help="base sha (empty on push: run everything)")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--paths", type=Path, default=DEFAULT_PATHS_FILE)
    ap.add_argument("--exclude", type=Path, action="append", default=[], help="a list whose paths never count")
    ap.add_argument(
        "--image",
        action="store_true",
        help="the seat image's path list (image_patterns): the substrate list minus .github/ and artifact paths",
    )
    args = ap.parse_args(argv)

    if args.image:
        patterns, excludes = image_patterns(args.paths)
    else:
        patterns, excludes = load_patterns(args.paths), []
    for extra in args.exclude:
        excludes = excludes + load_patterns(extra)

    if not args.base:
        print("no base sha (push event): running the full suite", file=sys.stderr)
        emit(True)
        return 0

    changed = changed_files(args.base, args.head)
    if changed is None:
        emit(True)
        return 0

    hits = relevant_paths(changed, patterns, excludes)
    print(f"{len(changed)} changed file(s), {len(hits)} under the path list", file=sys.stderr)
    for h in hits[:20]:
        print(f"  {h}", file=sys.stderr)
    emit(bool(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
