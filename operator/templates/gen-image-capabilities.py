"""Bake what this image can do for a skill (fleet release, 2026-10-19).

Run once at image build, LAST, after every COPY into /app and /opt. Writes
/app/image-capabilities.json:

    {"schema_version": 1,
     "paths": ["/app/...", "/opt/..."],          # files a skill may cite
     "tools": ["email_search", ...],             # tools the trust plugin gates
     "verbs": ["medchron_job_submit", ...],      # workspace-broker verbs
     "action_classes": ["read", ...]}            # the closed action vocabulary

WHY. Skills now reach a seat separately from its image: `fleet release --skills
<version>` repoints the seat at a new skill set without a restart. A skill that
cites a file, calls a tool or a broker verb, or declares an action class the
seat's image does not carry would land on that seat and fail at its first run.
The skills manifest records what each skill NEEDS (scripts/lib/skill-needs.py);
this file records what the image PROVIDES; `fleet release --skills` refuses a
seat whose image lacks a need. `fleet build` uploads this file to R2 under
fleet/images/<digest>/ so the check runs without touching the seat.

AUTHORITATIVE SOURCES, imported or parsed rather than restated:
  * tools and action classes: the overlay's ``shared.action_classes`` (the
    registry the trust plugin enforces; a tool absent from it is refused at
    call time anyway). Banned tools are excluded: no skill may need one.
  * verbs: the broker's own verb table, ``workspace_broker/verbs.py``, read by
    the same ``Verb("name"`` shape tests/broker-verb-registry.test.ts reads.
    Parsed, not imported, because the broker lives in its own venv.
  * paths: a walk of the image's real tree, minus the volume mount (/opt/data,
    which shadows anything baked there), the Hermes source tree (a skill cites
    its venv's bin, never its source), and package internals no skill names.

Usage: gen-image-capabilities.py <out.json> [--verbs-file PATH] [--root DIR ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_ROOTS = ("/app", "/opt")
DEFAULT_VERBS_FILE = "/opt/workspace-broker/workspace_broker/verbs.py"

# Directory NAMES never descended into: package internals no skill cites, and
# the bulk of the tree by file count.
SKIP_DIR_NAMES = frozenset({"__pycache__", "node_modules", "site-packages", ".git", "include", "share"})

# Whole subtrees never walked. /opt/data is the volume mount (the baked copy is
# shadowed at runtime); /app/skills is delivered by the skills artifact, not
# the image; /opt/hermes is the upstream source, of which a skill may only
# name the venv's bin (walked separately below).
SKIP_SUBTREES = ("/opt/data", "/app/skills", "/opt/hermes")
EXTRA_TREES = ("/opt/hermes/.venv/bin",)

VERB_ROW = re.compile(r'\bVerb\(\s*"([a-z0-9_]+)"')

# The in-image self-test is present when both its runner and its check list are.
SELFTEST_FILES = ("/app/selftest.sh", "/app/seat-checks.sh")


def _skipped(path: str, skip_subtrees: Iterable[str]) -> bool:
    return any(path == s or path.startswith(s + "/") for s in skip_subtrees)


def walk_paths(
    roots: Iterable[str], skip_subtrees: Iterable[str] = SKIP_SUBTREES, extra: Iterable[str] = EXTRA_TREES
) -> list[str]:
    """Every regular file under ``roots`` (and ``extra``), minus the skipped trees."""
    skip = tuple(skip_subtrees)
    out: set[str] = set()
    for root in [*roots, *extra]:
        if not os.path.isdir(root):
            continue
        is_extra = root in extra
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in SKIP_DIR_NAMES and (is_extra or not _skipped(os.path.join(dirpath, d), skip))
            )
            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.isfile(full):
                    out.add(full)
    return sorted(out)


def verbs_from_table(source: str) -> list[str]:
    verbs = sorted(set(VERB_ROW.findall(source)))
    if not verbs:
        raise SystemExit("gen-image-capabilities: no Verb(...) rows found in the broker verb table; refusing")
    return verbs


def tools_and_classes() -> tuple[list[str], list[str]]:
    """The overlay's enforced tool map, minus banned tools, and the action-class vocabulary."""
    from shared.action_classes import BANNED_TOOLS, TOOL_ACTION_CLASS_MAP, ActionClass

    tools = sorted(t for t in TOOL_ACTION_CLASS_MAP if t not in BANNED_TOOLS)
    classes = sorted(c.value for c in ActionClass)
    if not tools or not classes:
        raise SystemExit("gen-image-capabilities: empty tool map or action-class set; refusing")
    return tools, classes


def build(paths: list[str], tools: list[str], verbs: list[str], action_classes: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "paths": paths,
        "tools": tools,
        "verbs": verbs,
        "action_classes": action_classes,
        # Read from the walked tree, never asserted: `fleet release --verify
        # selftest` trusts an image's heartbeat self-test only when the image
        # carries the runner and its check list; any other image is verified by
        # the boot smoke over ssh.
        "selftest": all(p in paths for p in SELFTEST_FILES),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--verbs-file", default=DEFAULT_VERBS_FILE)
    ap.add_argument("--root", action="append", dest="roots")
    args = ap.parse_args(argv)
    tools, classes = tools_and_classes()
    verbs = verbs_from_table(Path(args.verbs_file).read_text(encoding="utf-8"))
    paths = walk_paths(args.roots or DEFAULT_ROOTS)
    doc = build(paths, tools, verbs, classes)
    Path(args.out).write_text(json.dumps(doc, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"image capabilities: {len(paths)} path(s), {len(tools)} tool(s), {len(verbs)} verb(s), {len(classes)} class(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
