"""Which seats may take an artifact release yet: the ordering rule, once, for skills and bundles.

THE RULE (ADR 0100 W3). An artifact published at commit S (a skills manifest, a
jobs bundle) is written against the code a seat runs, and the only code an
artifact does not carry itself is the seat IMAGE's. So it goes to a seat only
once that seat's image commit is base(S) or descends from it, where

    base(S) = git log -1 --format=%H S -- <image paths>

the last commit at or before S that changed anything the image carries. Image
paths are the substrate list minus its .github/ lines minus the artifact paths,
from :func:`image_patterns` in operator/bin/substrate-paths-changed.py, the same
function deploy.yml's build-seat-image job uses to decide whether a merge builds
an image. So "the image a skills-only merge needs" is the image that merge did
not build: the newest one before it.

Before 2026-10-31 the rule was image commit at or after S itself, which was
sound only while every artifact merge also built an image (operator/skills/** was
an image path). Now a skills-only merge builds none, and waiting for an image at
S would wait for the next unrelated substrate merge.

A seat that already reports a NEWER version of the artifact waits too: promotion
never moves a seat backwards. Waiting is a deferral, never a halt.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from . import ids
from .runner import Runner, ToolingError

DETECT_SCRIPT = "operator/bin/substrate-paths-changed.py"
SUBSTRATE_PATHS = ".github/operator-substrate-paths.txt"
ARTIFACT_PATHS = ".github/operator-artifact-paths.txt"


@lru_cache(maxsize=4)
def _detect(repo: str) -> ModuleType:
    path = Path(repo) / DETECT_SCRIPT
    spec = importlib.util.spec_from_file_location("substrate_paths_changed", path)
    if spec is None or spec.loader is None:
        raise ToolingError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def image_pathspecs(repo: Path) -> list[str]:
    """The image path list as git pathspecs, read from ``repo``'s own lists."""
    detect = _detect(str(repo))
    include, exclude = detect.image_patterns(repo / SUBSTRATE_PATHS, repo / ARTIFACT_PATHS)
    return detect.git_pathspecs(include, exclude)


def base_commit(runner: Runner, repo: Path, sha: str) -> str | None:
    """base(S): the last commit at or before ``sha`` touching an image path, or None.

    None when this clone does not hold ``sha`` (the caller defers: never a
    guess), or no commit at or before it touched an image path. Any other git
    failure is a tooling error.
    """
    if not ids.SHA_RE.match(sha or ""):
        return None
    present = runner.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], timeout=60)
    if present.rc != 0:
        return None
    res = runner.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H", sha, "--", *image_pathspecs(repo)], timeout=60
    )
    if not res.ok:
        raise ToolingError(f"git log for base({sha[:8]}) failed (rc={res.rc}): {res.text.strip()[-300:]}")
    found = res.stdout.strip()
    return found if ids.SHA_RE.match(found) else None


def stamp(version: str | None) -> str | None:
    """An artifact version's publish time (yyyymmddHHMM), or None when it is not one."""
    return version.split("-", 1)[0] if version and ids.SKILLS_VERSION_RE.match(version) else None


def deferrals(
    what: str,
    sha: str | None,
    version: str | None,
    seats: list[str],
    status: dict[str, dict[str, Any]],
    current_of: Callable[[dict[str, Any]], str | None],
    image_sha_of: Callable[[str | None], str | None],
    at_or_after: Callable[[str, str], bool],
    base_of: Callable[[str], str | None],
) -> dict[str, str]:
    """The seats that may not take an artifact published at ``sha`` this tick, with why.

    ``what`` names the artifact in the reasons ("skills", "bundle jobs").
    ``current_of(row)`` is the version a seat's fleet_status row reports now.
    """
    mine = stamp(version)
    out: dict[str, str] = {}
    base: str | None = None
    if sha and ids.SHA_RE.match(sha):
        base = base_of(sha)
    for seat in seats:
        row = status.get(seat) or {}
        current = current_of(row)
        theirs = stamp(current)
        if mine and theirs and theirs > mine:
            out[seat] = f"already runs newer {what} {current}"
            continue
        if not sha or not ids.SHA_RE.match(sha):
            out[seat] = f"the {what} release names no source commit"
            continue
        if not base:
            out[seat] = f"cannot find the image commit the {what} at {sha[:8]} need (base); waiting"
            continue
        image_sha = image_sha_of(row.get("image_ref"))
        if not image_sha:
            out[seat] = (
                f"its image is not a recorded release whose commit this controller holds; the {what} wait for one"
            )
        elif not at_or_after(base, image_sha):
            out[seat] = (
                f"waiting for the image: it runs {image_sha[:8]}, the {what} (written at {sha[:8]}) "
                f"need an image at or after {base[:8]}"
            )
    return out
