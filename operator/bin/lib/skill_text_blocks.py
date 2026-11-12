"""Stamp shared SKILL.md sections from operator/contracts/skill-text-blocks.yaml.

A block is one ``## `` section of a SKILL.md that several skills carry word for
word (the law-seat writing rules). Its source file holds the whole section,
heading line first; operator/bin/gen-skill-shared.py writes it into each listed
skill's SKILL.md, replacing that skill's section (the heading to the next
``## `` heading, or the end of the file). The stamped text is the source's
bytes, so a skill whose section already matched ships exactly what it shipped.

A skill whose section has drifted from the source is listed under ``own_text``:
its section is its own and is never written, so no client seat's model reads
different words until that skill is reconciled on purpose, one skill per
release. ``--check`` fails on a listed section that differs from the source, a
skill with the heading that the yaml does not list, and an ``own_text`` section
that has come to equal the source (move it to ``skills``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONTRACT_REL = "operator/contracts/skill-text-blocks.yaml"
SKILLS_REL = "operator/skills"

_ID = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_SKILL = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
_NEXT_SECTION = re.compile(r"^## ", re.M)


@dataclass(frozen=True)
class Block:
    id: str
    source: str
    skills: tuple[str, ...]
    own_text: tuple[str, ...]


def load(path: Path) -> list[Block]:
    """Parse and shape-check the yaml. Raises ValueError naming the first fault."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"blocks"}
        or not isinstance(data["blocks"], dict)
        or not data["blocks"]
    ):
        raise ValueError(f"{path}: expected exactly one top-level key, `blocks`, holding a non-empty mapping")
    out: list[Block] = []
    for bid, body in data["blocks"].items():
        if not isinstance(bid, str) or not _ID.match(bid):
            raise ValueError(f"block id {bid!r} is not lower_snake_case")
        if not isinstance(body, dict) or set(body) - {"source", "skills", "own_text"} or "source" not in body:
            raise ValueError(f"{bid}: needs `source`, `skills` and optionally `own_text`")
        source, skills, own = body["source"], body.get("skills", []), body.get("own_text", [])
        if not isinstance(source, str) or not source.endswith(".md") or source.startswith("/") or ".." in source:
            raise ValueError(f"{bid}: source must be a repo-relative .md path")
        if source.startswith(f"{SKILLS_REL}/"):
            raise ValueError(f"{bid}: the source lives outside {SKILLS_REL}, so it is never shipped as a skill file")
        if not isinstance(skills, list) or not skills or not isinstance(own, list):
            raise ValueError(f"{bid}: skills must be a non-empty list and own_text a list")
        names = [*skills, *own]
        if not all(isinstance(s, str) and _SKILL.match(s) for s in names):
            raise ValueError(f"{bid}: a skill name is not a valid skill folder name")
        if len(names) != len(set(names)):
            raise ValueError(f"{bid}: a skill is listed twice")
        out.append(Block(bid, source, tuple(skills), tuple(own)))
    return out


def heading(text: str) -> str:
    """The block's heading: its source's first line, which must be a ``## `` heading."""
    first = text.split("\n", 1)[0]
    if not first.startswith("## ") or not text.endswith("\n"):
        raise ValueError("a block source starts with its `## ` heading line and ends with a newline")
    return first


def section(text: str, head: str) -> tuple[int, int] | None:
    """Start and end offsets of the section ``head`` opens in ``text``, or None.

    The heading must start a line; the section runs to the next ``## `` heading
    or the end of the file."""
    m = re.search(rf"^{re.escape(head)}$", text, re.M)
    if m is None:
        return None
    nxt = _NEXT_SECTION.search(text, m.end())
    return m.start(), (len(text) if nxt is None else nxt.start())


def _skill_md(root: Path, skill: str) -> Path:
    return root / SKILLS_REL / skill / "SKILL.md"


def problems(root: Path, blocks: list[Block]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        src_path = root / b.source
        if not src_path.is_file():
            out.append(f"{b.id}: source {b.source} does not exist")
            continue
        src = src_path.read_text(encoding="utf-8")
        head = heading(src)
        listed = set(b.skills) | set(b.own_text)
        for skill in [*b.skills, *b.own_text]:
            md = _skill_md(root, skill)
            if not md.is_file():
                out.append(f"{b.id}: {md.relative_to(root)} does not exist")
                continue
            text = md.read_text(encoding="utf-8")
            span = section(text, head)
            if span is None:
                out.append(f"missing: {md.relative_to(root)} has no section {head!r}")
            elif skill in b.skills and text[span[0] : span[1]] != src:
                out.append(f"differs: {md.relative_to(root)} section is not byte-identical to {b.source}")
            elif skill in b.own_text and text[span[0] : span[1]] == src:
                out.append(f"{b.id}: {skill} now matches {b.source}; move it from own_text to skills")
        for md in sorted((root / SKILLS_REL).glob("*/SKILL.md")):
            if md.parent.name not in listed and section(md.read_text(encoding="utf-8"), head) is not None:
                out.append(f"undeclared: {md.relative_to(root)} carries {head!r}; list it in {CONTRACT_REL}")
    return out


def stamp(root: Path, blocks: list[Block]) -> list[Path]:
    """Write the block into every listed skill whose section differs. Returns what was written."""
    written: list[Path] = []
    for b in blocks:
        src_path = root / b.source
        if not src_path.is_file():
            continue
        src = src_path.read_text(encoding="utf-8")
        head = heading(src)
        for skill in b.skills:
            md = _skill_md(root, skill)
            if not md.is_file():
                continue
            text = md.read_text(encoding="utf-8")
            span = section(text, head)
            if span is None or text[span[0] : span[1]] == src:
                continue
            md.write_text(text[: span[0]] + src + text[span[1] :], encoding="utf-8")
            written.append(md)
    return written
