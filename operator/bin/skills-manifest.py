#!/usr/bin/env python3
"""Build a skills release manifest (run by scripts/ci-publish-skills.sh).

    skills-manifest.py --skills operator/skills --version <yyyymmddHHMM-sha8>
        --source-sha <40hex> --retired operator/templates/retired-skills.txt
        --verbs operator/workspace_broker/verbs.py
        --action-classes <overlay shared/action_classes.py at the pinned ref>
        --out manifest.json [--objects-dir DIR]

Writes the manifest the seat's skills_applier verifies (overlay WS2 contract):

    {"version", "source_sha", "created_at",
     "files": {"<skill>/<relpath>": {"sha256", "size", "mode"?}},
     "retired": [...],
     "needs": {"<skill>": {"paths", "tools", "verbs", "action_classes"}},
     "pins": {"<skill>": "<6hex>"}}     # what scripts/check-config-against-image.mjs reads

``mode`` is present only for an executable file ("0755"). With --objects-dir,
each file is also copied to DIR/<sha256> (the content-addressed object the
publisher uploads to fleet/skills/objects/<sha256>). __pycache__ and dotfiles
never ship. The file set is everything the image's `COPY operator/skills/
/app/skills/` would carry, so a skills release and an image release of the same
commit deliver the same bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from bin.lib import skill_needs

VERSION_RE = re.compile(r"^[0-9]{12}-[0-9a-f]{8}$")

# The seat's skills_applier refuses a WHOLE release that breaks any of these
# (overlay skills_applier, WS2 contract), so the publisher refuses first, here,
# where a person is reading the CI log.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def contract_problems(manifest: dict) -> list[str]:
    """Every way ``manifest`` would be refused by the seat's applier."""
    out = []
    files = manifest["files"]
    shipped = set()
    for key, entry in files.items():
        skill, _, rest = key.partition("/")
        shipped.add(skill)
        if not SKILL_NAME_RE.match(skill) or not rest or not all(PATH_SEGMENT_RE.match(seg) for seg in rest.split("/")):
            out.append(f"file key {key!r} breaks the <skill>/<path> shape")
        if entry["size"] > MAX_FILE_BYTES:
            out.append(f"{key} is {entry['size']} bytes (cap {MAX_FILE_BYTES})")
    if len(files) > MAX_FILES:
        out.append(f"{len(files)} files (cap {MAX_FILES})")
    if sum(e["size"] for e in files.values()) > MAX_TOTAL_BYTES:
        out.append(f"total size over {MAX_TOTAL_BYTES} bytes")
    for skill, needs in manifest["needs"].items():
        if skill not in shipped:
            out.append(f"needs names {skill}, which ships no files")
        out += [f"{skill}: path need {p!r} is not absolute" for p in needs["paths"] if not p.startswith("/")]
    out += [f"retired {r} is also shipped" for r in manifest["retired"] if r in shipped]
    return out


def shipped_files(skills: Path) -> list[Path]:
    out = []
    for p in sorted(skills.rglob("*")):
        rel = p.relative_to(skills)
        if (
            not p.is_file()
            or p.is_symlink()
            or "__pycache__" in rel.parts
            or any(part.startswith(".") for part in rel.parts)
        ):
            continue
        out.append(p)
    return out


def skill_pin(skill_dir: Path) -> str:
    """The 6-hex pin customer.yaml uses: the overlay's boot hash, restated.

    bootstrap.translate._skill_content_hash (overlay) sha256s every file, sorted
    by path, as ``relpath NUL bytes NUL``. Restated rather than imported because
    CI does not install the overlay; tests/fleet-skill-needs.test.ts holds the
    two in step against the gen-image-manifest pins of the same tree.
    """
    digest = hashlib.sha256()
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(skill_dir).as_posix().encode())
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
    return digest.hexdigest()[:6]


def retired(path: Path) -> list[str]:
    return sorted(
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )


def build(args: argparse.Namespace) -> dict:
    skills = Path(args.skills)
    tools, classes = skill_needs.tool_universe_from_source(Path(args.action_classes).read_text(encoding="utf-8"))
    verbs = skill_needs.verbs_from_table(Path(args.verbs).read_text(encoding="utf-8"))
    files: dict[str, dict] = {}
    for p in shipped_files(skills):
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entry: dict = {"sha256": digest, "size": len(data)}
        if os.stat(p).st_mode & stat.S_IXUSR:
            entry["mode"] = "0755"
        files[p.relative_to(skills).as_posix()] = entry
        if args.objects_dir:
            dest = Path(args.objects_dir) / digest
            if not dest.exists():
                shutil.copyfile(p, dest)
    shipped = {key.split("/", 1)[0] for key in files}
    dirs = [d for d in sorted(skills.iterdir()) if d.is_dir() and d.name in shipped]
    needs = {d.name: skill_needs.needs_for(d, tools, verbs, classes) for d in dirs}
    pins = {d.name: skill_pin(d) for d in dirs}
    return {
        "version": args.version,
        "source_sha": args.source_sha,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "retired": retired(Path(args.retired)),
        "needs": needs,
        "pins": pins,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    for flag in ("--skills", "--version", "--source-sha", "--retired", "--verbs", "--action-classes", "--out"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--objects-dir")
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version) or not re.match(r"^[0-9a-f]{40}$", args.source_sha):
        print("skills-manifest: --version must be yyyymmddHHMM-<sha8> and --source-sha 40 hex", file=sys.stderr)
        return 2
    if args.objects_dir:
        Path(args.objects_dir).mkdir(parents=True, exist_ok=True)
    manifest = build(args)
    problems = contract_problems(manifest)
    body = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if len(body.encode()) > MAX_MANIFEST_BYTES:
        problems.append(f"the manifest is {len(body.encode())} bytes (cap {MAX_MANIFEST_BYTES})")
    if problems:
        for line in problems:
            print(f"skills-manifest: refused: {line}", file=sys.stderr)
        return 1
    Path(args.out).write_text(body, encoding="utf-8")
    total = sum(
        len(v["paths"]) + len(v["tools"]) + len(v["verbs"]) + len(v["action_classes"])
        for v in manifest["needs"].values()
    )
    print(
        f"skills manifest {args.version}: {len(manifest['files'])} file(s), {len(manifest['needs'])} skill(s), {total} need(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
