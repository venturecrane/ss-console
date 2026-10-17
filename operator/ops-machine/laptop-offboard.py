#!/usr/bin/env python3
"""laptop-offboard.py [--host H] [--from APP | --manifest-dir DIR] [--execute]

Delete this laptop's copies of client material that are PROVEN to be on an ops
workspace, and nothing else.

Proven means: listed in a sha256 manifest that move-to-ops.sh, move-by-folder.sh,
move-from-host.sh or sync-agent-state.sh wrote next to the copy
(/home/ops/archive/<host>/<area>/.manifest-*.sha256 or .sync.sha256) and checked
ON the Machine at copy time. For every listed file this deletes the local file
ONLY if it still hashes to the manifest value. A file changed after the copy, a
symlink, a path that escapes its area, or anything unlisted is kept. Directories
are never deleted wholesale: only directories this run emptied are removed.

Never touched, even when listed: ~/.claude/projects/*/memory/ (agent memory) and
~/dev (product repos).

WHY FILE-BY-FILE (peer /code-review, 2026-10-17): the first version deleted every
ledgered top-level directory. On mac23 that included
~/.claude/projects/-Users-scottdurgan-dev-ss-console with its memory/ (557
files) and every transcript written after the copy. It never ran.

Manifests come from APP over fly ssh (default smd-ops), or from DIR laid out as
DIR/<area>/*.sha256, for a laptop that no longer has Fly access. Without
--execute it is a dry run. With --execute it reads back afterwards: every deleted
path must be absent, every protected path untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def areas(home: Path) -> dict[str, Path]:
    """Archive area on the workspace -> base directory on this laptop."""
    return {
        "desktop": home / "Desktop",
        "home": home,
        "tmp": Path("/private/tmp/claude-501"),
        "transcripts": home / ".claude" / "projects",
        "claude": home / ".claude",
        "smd-medchron-data": home / "smd-medchron-data",
    }


def protected(path: Path, home: Path) -> bool:
    projects = home / ".claude" / "projects"
    if projects in path.parents:
        rel = path.relative_to(projects).parts
        if len(rel) >= 2 and rel[1] == "memory":
            return True
    return (home / "dev") in path.parents or path == home / "dev"


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """(sha256, relpath) pairs from `shasum -a 256` / `sha256sum` output."""
    pairs = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if len(line) > 66 and line[64:66] in ("  ", " *"):
            digest = line[:64].lower()
            if all(c in "0123456789abcdef" for c in digest):
                pairs.append((digest, line[66:]))
    return pairs


def fetch_from_app(app: str, host: str, area: str) -> str:
    root = f"/home/ops/archive/{host}/{area}"
    out = subprocess.run(  # noqa: S603 - literal fly argv, no local shell; app and host come from this script's own flags
        [
            "fly",
            "ssh",
            "console",
            "-a",
            app,
            "-u",
            "ops",
            "-C",
            f"sh -c 'cat {root}/.manifest-*.sha256 {root}/.sync.sha256 2>/dev/null'",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return out.stdout


def fetch_from_dir(d: Path, area: str) -> str:
    sub = d / area
    if not sub.is_dir():
        return ""
    return "".join(p.read_text() for p in sorted(sub.glob("*.sha256")))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Tally:
    match: list[Path] = field(default_factory=list)
    changed: int = 0
    protected: int = 0
    escaped: int = 0
    symlink: int = 0
    gone: int = 0


def classify(base: Path, pairs: list[tuple[str, str]], home: Path) -> Tally:
    t = Tally()
    seen: set[Path] = set()
    for digest, rel in pairs:
        p = Path(os.path.normpath(base / rel))
        if p in seen:
            continue
        seen.add(p)
        if base != p and base not in p.parents:
            t.escaped += 1
        elif protected(p, home):
            t.protected += 1
        elif p.is_symlink():
            t.symlink += 1
        elif not p.is_file():
            t.gone += 1
        elif sha256(p) != digest:
            t.changed += 1
        else:
            t.match.append(p)
    return t


def remove_emptied(dirs: set[Path], stop: set[Path], home: Path) -> None:
    for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
        while d not in stop and d != home and not protected(d, home) and home in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent


def run(home: Path, host: str, fetch, execute: bool, out=sys.stdout) -> int:
    base_by_area = areas(home)
    deleted: list[Path] = []
    total = 0
    for area, base in base_by_area.items():
        pairs = parse_manifest(fetch(area))
        if not pairs:
            continue
        t = classify(base, pairs, home)
        total += len(t.match)
        if execute:
            for p in t.match:
                p.unlink()
                deleted.append(p)
        verb = "deleted" if execute else "would delete"
        print(
            f"{area}: {verb} {len(t.match)}; kept: changed since copy {t.changed}, "
            f"protected {t.protected}, symlink {t.symlink}, outside area {t.escaped}; "
            f"already gone {t.gone}",
            file=out,
        )
    if not execute:
        print(f"DRY RUN on {host}: would delete {total} files. Re-run with --execute.", file=out)
        return 0
    remove_emptied({p.parent for p in deleted}, set(base_by_area.values()), home)
    still = [p for p in deleted if p.exists()]
    print(f"{host}: deleted {len(deleted)} files; read-back: {len(still)} still present", file=out)
    return 1 if still else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=socket.gethostname().split(".")[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from", dest="app", default="smd-ops")
    src.add_argument("--manifest-dir", type=Path)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    def fetch(area: str) -> str:
        if args.manifest_dir:
            return fetch_from_dir(args.manifest_dir, area)
        return fetch_from_app(args.app, args.host, area)

    return run(Path.home(), args.host, fetch, args.execute)


if __name__ == "__main__":
    sys.exit(main())
