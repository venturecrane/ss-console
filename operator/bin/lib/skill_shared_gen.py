"""Stamp shared skill modules from operator/contracts/skill-shared-modules.yaml.

The yaml names one source per shared module and the skills that carry a copy of
it. A skill folder ships alone (the scheduler stages one directory, and a
stdlib-only ``pre_run.py`` cannot install a package), so each copy is a whole
file beside the script. The copies are the source's bytes exactly: they are what
the image and the skills release ship, so this module never adds a header.

operator/bin/gen-skill-shared.py writes the copies; ``--check`` fails on a copy
that differs from its source, a listed copy that is missing, and a copy the
yaml does not list. operator/bin/tests/test_skill_shared_generation.py runs it in
CI. It replaced nine hand-kept sync tests (operator/tests/test_*_sync.py).
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

GENERATOR = "operator/bin/gen-skill-shared.py"
CONTRACT_REL = "operator/contracts/skill-shared-modules.yaml"
SKILLS_REL = "operator/skills"

_ID = re.compile(r"\A[a-z][a-z0-9_]*\Z")
# The skills release refuses any other skill name (skills-manifest.py SKILL_NAME_RE).
_SKILL = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
_FILE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]*\.py\Z")
_FIELDS = {"source", "copy_as", "skills"}


@dataclass(frozen=True)
class Module:
    id: str
    source: str
    copy_as: str
    skills: tuple[str, ...]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load(path: Path) -> list[Module]:
    """Parse and shape-check the yaml. Raises ValueError naming the first fault."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"modules"} or not isinstance(data["modules"], dict):
        raise ValueError(f"{path}: expected exactly one top-level key, `modules`, holding a mapping")
    if not data["modules"]:
        raise ValueError(f"{path}: declares no modules; it needs at least one module, or --check passes vacuously")
    out: list[Module] = []
    targets: dict[tuple[str, str], str] = {}
    for mid, body in data["modules"].items():
        if not isinstance(mid, str) or not _ID.match(mid):
            raise ValueError(f"module id {mid!r} is not lower_snake_case")
        if not isinstance(body, dict) or set(body) != _FIELDS:
            raise ValueError(f"{mid}: needs exactly the fields {sorted(_FIELDS)}, has unknown or missing fields")
        source, copy_as, skills = body["source"], body["copy_as"], body["skills"]
        if not isinstance(source, str) or not source.endswith(".py") or source.startswith("/") or ".." in source:
            raise ValueError(f"{mid}: source must be a repo-relative .py path")
        if not isinstance(copy_as, str) or not _FILE.match(copy_as):
            raise ValueError(f"{mid}: copy_as must be a plain .py file name")
        if not isinstance(skills, list) or not skills:
            raise ValueError(f"{mid}: skills must be a non-empty list")
        for skill in skills:
            if not isinstance(skill, str) or not _SKILL.match(skill):
                raise ValueError(f"{mid}: skill {skill!r} is not a valid skill folder name")
            key = (skill, copy_as)
            if key in targets:
                raise ValueError(f"{skill}/{copy_as} is claimed by more than one module ({targets[key]}, {mid})")
            targets[key] = mid
            if f"{SKILLS_REL}/{skill}/{copy_as}" == source:
                raise ValueError(f"{mid}: the source {source} is also listed as a copy of itself")
        out.append(Module(mid, source, copy_as, tuple(skills)))
    return out


def copies(root: Path, modules: list[Module]) -> dict[Path, Path]:
    """Every copy the yaml declares, mapped to its source."""
    return {root / SKILLS_REL / skill / m.copy_as: root / m.source for m in modules for skill in m.skills}


def function_bodies(text: str) -> set[str]:
    """Hashes of each top-level function's source with its name stripped.

    A copy that was renamed or edited in one place still shares most of these
    with its source, which is how an undeclared hand-made copy is recognised
    under a file name (pre_run.py) that bespoke files also use.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = (ast.get_source_segment(text, node) or "").replace(node.name, "<name>", 1)
            out.add(hashlib.sha256(segment.encode("utf-8")).hexdigest())
    return out


def undeclared(root: Path, modules: list[Module]) -> list[Path]:
    """Files in a skill folder that are copies of a source the yaml does not list there.

    A file counts when it has the module's ``copy_as`` name and either equals the
    source or shares a top-level function body with it. A bespoke ``pre_run.py``
    shares none with the empty-seat gate, so it is not a copy.
    """
    declared = set(copies(root, modules))
    found: list[Path] = []
    for m in modules:
        source = root / m.source
        if not source.is_file():
            continue
        src_bytes = source.read_bytes()
        src_bodies = function_bodies(src_bytes.decode("utf-8"))
        for candidate in sorted((root / SKILLS_REL).glob(f"*/{m.copy_as}")):
            if candidate in declared or candidate.resolve() == source.resolve():
                continue
            data = candidate.read_bytes()
            if data == src_bytes or src_bodies & function_bodies(data.decode("utf-8", errors="replace")):
                found.append(candidate)
    return found


def problems(root: Path, modules: list[Module]) -> list[str]:
    """Every way the committed tree disagrees with the yaml, as repo-relative sentences."""
    out: list[str] = []

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    for m in modules:
        if not (root / m.source).is_file():
            out.append(f"{m.id}: source {m.source} does not exist")
        for skill in m.skills:
            if not (root / SKILLS_REL / skill).is_dir():
                out.append(f"{m.id}: skill folder {SKILLS_REL}/{skill} does not exist")
    for target, source in copies(root, modules).items():
        if not source.is_file() or not target.parent.is_dir():
            continue
        if not target.is_file():
            out.append(f"missing: {rel(target)} (copy of {rel(source)})")
        elif target.read_bytes() != source.read_bytes():
            out.append(f"differs: {rel(target)} is not byte-identical to {rel(source)}")
    out += [
        f"undeclared: {rel(p)} is a copy the yaml does not list; add the skill to {CONTRACT_REL}"
        for p in undeclared(root, modules)
    ]
    return out


def stamp(root: Path, modules: list[Module]) -> list[Path]:
    """Write every declared copy that is missing or differs. Returns what was written."""
    written: list[Path] = []
    for target, source in copies(root, modules).items():
        if not source.is_file() or not target.parent.is_dir():
            continue
        data = source.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            continue
        target.write_bytes(data)
        written.append(target)
    return written
