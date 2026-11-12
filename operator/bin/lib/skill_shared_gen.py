"""Stamp shared Python modules from operator/contracts/skill-shared-modules.yaml.

The yaml names one source per shared module and every place that carries a copy
of it: skill folders (``skills``) and, for a module shared across processes,
other repo paths (``copies``). A skill folder ships alone (the scheduler stages
one directory, and a stdlib-only ``pre_run.py`` cannot install a package); the
broker, the gateway, the job runner and each connector run in separate
virtualenvs that import nothing from one another. So each copy is a whole file.
The copies are the source's bytes exactly: they are what the image, the skills
release and each venv ship, so this module never adds a header.

operator/bin/gen-skill-shared.py writes the copies; ``--check`` fails on a copy
that differs from its source, a listed copy that is missing, and a skill-folder
copy the yaml does not list. operator/bin/tests/test_skill_shared_generation.py
runs it in CI. It replaced nine hand-kept skill sync tests (operator/tests/
test_*_sync.py, 2026-11-10) and, for the cross-process copies, the per-pair
parity tests and overlay-pairs.json pins (2026-11-12).
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
_REQUIRED = {"source"}
_OPTIONAL = {"copy_as", "skills", "copies"}


@dataclass(frozen=True)
class Module:
    id: str
    source: str
    copy_as: str
    skills: tuple[str, ...]
    paths: tuple[str, ...] = ()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _repo_py(value: object) -> bool:
    return isinstance(value, str) and value.endswith(".py") and not value.startswith("/") and ".." not in value


def _module(mid: object, body: object, targets: dict[str, str]) -> Module:
    """One yaml entry, shape-checked. ``targets`` maps every claimed copy path to its module."""
    if not isinstance(mid, str) or not _ID.match(mid):
        raise ValueError(f"module id {mid!r} is not lower_snake_case")
    if not isinstance(body, dict) or not _REQUIRED <= set(body) or not set(body) <= _REQUIRED | _OPTIONAL:
        raise ValueError(
            f"{mid}: needs exactly the fields {sorted(_REQUIRED)} plus any of {sorted(_OPTIONAL)}, "
            "has unknown or missing fields"
        )
    source = body["source"]
    skills = body.get("skills", [])
    paths = body.get("copies", [])
    copy_as = body.get("copy_as", "")
    if not _repo_py(source):
        raise ValueError(f"{mid}: source must be a repo-relative .py path")
    if not isinstance(skills, list) or not isinstance(paths, list) or not (skills or paths):
        raise ValueError(f"{mid}: skills and copies must be lists, and at least one must be non-empty")
    if skills and (not isinstance(copy_as, str) or not _FILE.match(copy_as)):
        raise ValueError(f"{mid}: copy_as must be a plain .py file name")
    if "copy_as" in body and not skills:
        raise ValueError(f"{mid}: copy_as names the file in a skill folder; it needs skills")
    claimed: list[str] = []
    for skill in skills:
        if not isinstance(skill, str) or not _SKILL.match(skill):
            raise ValueError(f"{mid}: skill {skill!r} is not a valid skill folder name")
        claimed.append(f"{SKILLS_REL}/{skill}/{copy_as}")
    for path in paths:
        if not _repo_py(path):
            raise ValueError(f"{mid}: copy {path!r} must be a repo-relative .py path")
        if str(path).startswith(f"{SKILLS_REL}/"):
            raise ValueError(f"{mid}: copy {path} is in a skill folder; list the skill under skills")
        claimed.append(path)
    for target in claimed:
        if target in targets:
            raise ValueError(f"{target} is claimed by more than one module ({targets[target]}, {mid})")
        if target == source:
            raise ValueError(f"{mid}: the source {source} is also listed as a copy of itself")
        targets[target] = mid
    return Module(mid, source, copy_as, tuple(skills), tuple(paths))


def load(path: Path) -> list[Module]:
    """Parse and shape-check the yaml. Raises ValueError naming the first fault."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"modules"} or not isinstance(data["modules"], dict):
        raise ValueError(f"{path}: expected exactly one top-level key, `modules`, holding a mapping")
    if not data["modules"]:
        raise ValueError(f"{path}: declares no modules; it needs at least one module, or --check passes vacuously")
    targets: dict[str, str] = {}
    out = [_module(mid, body, targets) for mid, body in data["modules"].items()]
    sources = {m.source for m in out}
    for target, mid in targets.items():
        if target in sources:
            raise ValueError(f"{mid}: {target} is another module's source; a source is never a copy")
    return out


def copies(root: Path, modules: list[Module]) -> dict[Path, Path]:
    """Every copy the yaml declares, mapped to its source."""
    out = {root / SKILLS_REL / skill / m.copy_as: root / m.source for m in modules for skill in m.skills}
    out.update({root / path: root / m.source for m in modules for path in m.paths})
    return out


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
    shares none with the empty-seat gate, so it is not a copy. (A copy outside the
    skills tree that nobody declared is found by the sprawl census instead:
    hand-kept-python-copies, gated by tests/sprawl-ratchet.test.ts.)
    """
    declared = set(copies(root, modules))
    found: list[Path] = []
    for m in modules:
        source = root / m.source
        if not m.skills or not source.is_file():
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
        for path in m.paths:
            if not (root / path).parent.is_dir():
                out.append(f"{m.id}: the folder of copy {path} does not exist")
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
