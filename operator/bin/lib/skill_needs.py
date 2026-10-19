"""What each skill NEEDS from the image it runs on (fleet skills releases).

Skills reach seats separately from images (`fleet release --skills`), so the
skills manifest carries, per skill, what the seat's IMAGE must provide; the
fleet CLI compares it with the image's /app/image-capabilities.json
(operator/templates/gen-image-capabilities.py) and refuses a seat that lacks
any of it.

Four kinds, each extracted only from contexts where a name means a reference,
because a false need refuses a good release and a missed one weakens the gate:

  paths           an absolute /app/... or /opt/... path cited in SKILL.md,
                  references/*.md or a shipped .py (tests/ excluded), plus a
                  repo-relative citation under a RUNTIME-READ prefix resolved
                  from the image WORKDIR (/app). The same extraction
                  tests/shipped-runtime-paths.test.ts uses; its runtime-read
                  prefixes are pinned equal to RUNTIME_READ_PREFIXES by
                  tests/fleet-skill-needs.test.ts. The volume (/opt/data) and
                  the skills tree itself (/app/skills, delivered by this very
                  manifest) are never needs.
  tools           a `backticked` name in SKILL.md / references that is in the
                  TOOL UNIVERSE: the overlay's enforced tool map at the pinned
                  OVERLAY_REF, minus banned tools.
  verbs           a `backticked` name in SKILL.md / references, or a quoted
                  string literal in a shipped .py, that is a broker verb
                  (workspace_broker/verbs.py). Prose words that happen to be
                  verbs ("execute", "health") are not references.
  action_classes  the frontmatter's metadata.smd.action_class, split on "+",
                  keeping the members of the overlay's ActionClass vocabulary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RUNTIME_READ_PREFIXES: tuple[str, ...] = ("operator/templates/drafting/",)
IMAGE_WORKDIR = "/app"
ABS_PATH = re.compile(r"(?<![\w./-])/(?:app|opt)/[A-Za-z0-9_./-]*[A-Za-z0-9_-]")
REPO_PATH = re.compile(r"(?<![\w./-])operator/[A-Za-z0-9_./-]*[A-Za-z0-9_-]")
TICKED = re.compile(r"`([A-Za-z][A-Za-z0-9_]{2,})`")
QUOTED = re.compile(r"""["']([a-z][a-z0-9_]{2,})["']""")
NOT_NEEDS = ("/opt/data", "/app/skills")
VERB_ROW = re.compile(r'\bVerb\(\s*"([a-z0-9_]+)"')


def _docs(skill_dir: Path) -> list[Path]:
    out = [skill_dir / "SKILL.md"] if (skill_dir / "SKILL.md").is_file() else []
    out += sorted((skill_dir / "references").glob("*.md")) if (skill_dir / "references").is_dir() else []
    return out


def _code(skill_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in skill_dir.rglob("*.py")
        if "tests" not in p.relative_to(skill_dir).parts and not p.name.startswith("test_")
    )


def _excluded(path: str) -> bool:
    return any(path == x or path.startswith(x + "/") for x in NOT_NEEDS)


def paths_needed(skill_dir: Path) -> list[str]:
    found: set[str] = set()
    for f in _docs(skill_dir) + _code(skill_dir):
        text = f.read_text(encoding="utf-8", errors="replace")
        found.update(p for p in ABS_PATH.findall(text) if not _excluded(p))
        for rel in REPO_PATH.findall(text):
            if rel.startswith(RUNTIME_READ_PREFIXES):
                found.add(f"{IMAGE_WORKDIR}/{rel}")
    return sorted(p.rstrip("/") for p in found)


ACTION_CLASS_LINE = re.compile(r"^\s+action_class:\s*(?P<value>[^#\n]*)", re.MULTILINE)


def frontmatter_text(skill_md: Path) -> str:
    """The text between the leading ``---`` fences (empty if there are none)."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else ""


def action_classes_needed(skill_dir: Path, universe: set[str]) -> list[str]:
    """metadata.smd.action_class, split on "+", members of the vocabulary only.

    Read as the one scalar line it is, not through a YAML parser, so the
    publisher runs on a bare CI python (stdlib only). Prose after a ``#`` is a
    comment and dropped; tokens outside the vocabulary ("draft", "route") are
    descriptions, not classes.
    """
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return []
    found = ACTION_CLASS_LINE.search(frontmatter_text(md))
    raw = found.group("value") if found else ""
    return sorted({tok.strip() for tok in raw.split("+") if tok.strip() in universe})


def ticked_names(skill_dir: Path) -> set[str]:
    out: set[str] = set()
    for f in _docs(skill_dir):
        out.update(TICKED.findall(f.read_text(encoding="utf-8", errors="replace")))
    return out


def quoted_names(skill_dir: Path) -> set[str]:
    out: set[str] = set()
    for f in _code(skill_dir):
        out.update(QUOTED.findall(f.read_text(encoding="utf-8", errors="replace")))
    return out


def needs_for(skill_dir: Path, tools: set[str], verbs: set[str], classes: set[str]) -> dict[str, list[str]]:
    ticked = ticked_names(skill_dir)
    return {
        "paths": paths_needed(skill_dir),
        "tools": sorted(ticked & tools),
        # A one-word verb ("execute", "health", "authorize") is indistinguishable
        # from an English word or an ordinary string, and every image has carried
        # those three since the broker existed: only compound names are needs.
        "verbs": sorted(v for v in (ticked | quoted_names(skill_dir)) & verbs if "_" in v),
        "action_classes": action_classes_needed(skill_dir, classes),
    }


def verbs_from_table(source: str) -> set[str]:
    return set(VERB_ROW.findall(source))


def tool_universe_from_source(source: str) -> tuple[set[str], set[str]]:
    """(tools, action classes) from the overlay's shared/action_classes.py SOURCE.

    Read with ``ast``, never imported: CI holds the overlay file at the pinned
    ref as data. The map is a dict literal keyed by tool-name strings; banned
    tools are a frozenset literal; ActionClass members are string assignments.
    """
    tree = ast.parse(source)
    tools: set[str] = set()
    banned: set[str] = set()
    classes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            name = getattr(target, "id", "")
            if name == "_RAW_TOOL_ACTION_CLASS_MAP" and isinstance(node.value, ast.Dict):
                tools |= {k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            elif name == "BANNED_TOOLS" and node.value is not None:
                banned |= {
                    c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }
        if isinstance(node, ast.ClassDef) and node.name == "ActionClass":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    classes |= {
                        c.value
                        for c in ast.walk(stmt.value)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    }
    if not tools or not classes:
        raise ValueError("could not read the tool map / ActionClass from the overlay's action_classes.py")
    return tools - banned, classes
