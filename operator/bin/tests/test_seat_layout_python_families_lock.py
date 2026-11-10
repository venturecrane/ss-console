"""The config, interpreter and audit seat paths are spelled once (W2-W4).

operator/contracts/seat-layout.yaml declares every seat path, and each Python
install tree carries a generated ``seat_layout`` module. After the W2-W4
migration, a hand-written module in the jobs runner, the workspace broker or the
overlay reads these families from that module, never from a string it spells:

  CONFIG       /var/lib/smd-config and everything under it, /opt/data/customer.yaml
  INTERPRETER  /opt/hermes and everything under it, /opt/workspace-broker and under
  AUDIT/SOCKET /run/smd-audit, /opt/data/audit and /opt/data/audit.db,
               /run/smd-workspace-broker, /run/smd-selftest

The scan reads string constants from the parsed module (``ast``), so comments
and docstrings never count and a path assembled from pieces still does. A
constant counts when it IS a family root or sits under one. Strict both ways: a
file outside the allowlist that spells one fails, and an allowlist row whose
file no longer spells its path fails too, so the list cannot outlive its reason.

Run::

    cd operator && python3 -m pytest bin/tests/test_seat_layout_python_families_lock.py -v
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

from bin.lib import seat_layout_gen as gen

ROOT = gen.repo_root()
LAYOUT = gen.load(ROOT / gen.CONTRACT_REL)

TREES = ("operator/runners/jobs/", "operator/workspace_broker/", "operator/hermes-smd-overlay/")

#: The family roots, by declared id (so a root that moves in the yaml moves here).
FAMILY_ROOT_IDS = (
    "smd_config",
    "volume_customer_yaml",
    "hermes_root",
    "broker_root",
    "audit_run",
    "legacy_audit_db",
)
#: Roots the yaml has no id for: each is the directory of a declared file.
FAMILY_ROOT_PARENTS = ("broker_socket", "selftest_results", "escalation_ledger")

#: Mirrors scripts/sprawl/analyze.ts TEST_PATH and sources.ts SKIP, so this lock
#: and the sprawl census read the same files.
_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$|\.test\.[a-z]+$|_test\.py$|(^|/)conftest\.py$")
_SKIP = re.compile(r"(^|/)(node_modules|dist|\.venv|__pycache__|fixtures)/")

_THE_LEDGER_COPIES = (
    "byte-identical with the copies vendored into operator/skills/*/ (staged alone and "
    "loaded by path, so they cannot import a tree module); the sync tests pin the bytes"
)
_THE_SWITCH_COPIES = (
    "byte-identical with operator/connectors/smokeball/smokeball_connector/logic_in_code.py, "
    "a separate venv with no generated module (operator/tests/test_logic_in_code_sync.py)"
)

#: file -> (paths it may spell, why). Every path listed must still be spelled.
ALLOWED: dict[str, tuple[frozenset[str], str]] = {
    "operator/hermes-smd-overlay/shared/casework_ledger.py": (
        frozenset({"/opt/data/audit/casework-ledger.jsonl"}),
        _THE_LEDGER_COPIES,
    ),
    "operator/workspace_broker/casework_ledger.py": (
        frozenset({"/opt/data/audit/casework-ledger.jsonl"}),
        _THE_LEDGER_COPIES,
    ),
    "operator/hermes-smd-overlay/shared/escalation_ledger.py": (
        frozenset({"/opt/data/audit/escalation-ledger.jsonl"}),
        _THE_LEDGER_COPIES,
    ),
    "operator/workspace_broker/escalation_ledger.py": (
        frozenset({"/opt/data/audit/escalation-ledger.jsonl"}),
        _THE_LEDGER_COPIES,
    ),
    "operator/hermes-smd-overlay/shared/logic_in_code.py": (
        frozenset({"/var/lib/smd-config/customer.yaml"}),
        _THE_SWITCH_COPIES,
    ),
    "operator/runners/jobs/jobs/attorneys/roster.py": (
        frozenset({"/var/lib/smd-config/attorneys"}),
        "byte-identical with operator/connectors/_sdk/operator_connector_sdk/library/roster.py, "
        "a separate package with no generated module (runners/jobs/tests/test_attorney_roster.py)",
    ),
    "operator/workspace_broker/logic_in_code.py": (
        frozenset({"/var/lib/smd-config/customer.yaml"}),
        _THE_SWITCH_COPIES,
    ),
}


def family_roots() -> tuple[str, ...]:
    roots = {LAYOUT.entries[i].path for i in FAMILY_ROOT_IDS}
    roots |= {str(Path(LAYOUT.entries[i].path).parent) for i in FAMILY_ROOT_PARENTS}
    return tuple(sorted(roots))


def in_family(value: str, roots: tuple[str, ...]) -> bool:
    """Exact token: the constant IS a root, or names a path under one."""
    return any(value == root or value.startswith(root + "/") for root in roots)


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The string constants that are bare expression statements (docstrings, notes)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }


def family_literals(source: str, roots: tuple[str, ...]) -> set[str]:
    tree = ast.parse(source)
    skip = _docstring_ids(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if in_family(node.value, roots):
                found.add(node.value)
    return found


def scanned_files() -> list[str]:
    # A git hook exports GIT_DIR and friends, which beat cwd; read THIS checkout.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(  # noqa: S603 - the repo's own file list
        ["git", "ls-files", "-z", "--", *TREES],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = []
    for rel in done.stdout.split("\0"):
        if not rel.endswith(".py") or _TEST_PATH.search(rel) or _SKIP.search(rel):
            continue
        if not (ROOT / rel).is_file():
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if text.startswith("# GENERATED by "):
            continue
        out.append(rel)
    return out


def test_the_families_are_spelled_only_where_the_allowlist_says() -> None:
    roots = family_roots()
    files = scanned_files()
    assert len(files) > 100, f"the scan found only {len(files)} files: the tree layout moved?"
    spelled = {}
    for rel in files:
        hits = family_literals((ROOT / rel).read_text(encoding="utf-8"), roots)
        if hits:
            spelled[rel] = hits
    unexpected = {
        rel: sorted(hits - ALLOWED.get(rel, (frozenset(), ""))[0])
        for rel, hits in spelled.items()
        if hits - ALLOWED.get(rel, (frozenset(), ""))[0]
    }
    assert unexpected == {}, (
        "read these from the tree's generated seat_layout module (declare a missing id in "
        f"{gen.CONTRACT_REL} and run python3 {gen.GENERATOR}): {unexpected}"
    )
    stale = {
        rel: sorted(paths - spelled.get(rel, set()))
        for rel, (paths, _why) in ALLOWED.items()
        if paths - spelled.get(rel, set())
    }
    assert stale == {}, f"allowlist rows whose file no longer spells the path; remove them: {stale}"


def test_every_family_root_is_a_declared_layout_fact() -> None:
    roots = family_roots()
    assert roots == (
        "/opt/data/audit",
        "/opt/data/audit.db",
        "/opt/data/customer.yaml",
        "/opt/hermes",
        "/opt/workspace-broker",
        "/run/smd-audit",
        "/run/smd-selftest",
        "/run/smd-workspace-broker",
        "/var/lib/smd-config",
    )


def test_the_scan_catches_each_way_a_path_can_be_spelled() -> None:
    """FALSIFIER: the forms the migration removed, and the ones it must not count."""
    roots = family_roots()
    caught = family_literals(
        'A = "/var/lib/smd-config/specs"\n'
        'B = "/run/smd-workspace-broker/" + "broker" + ".sock"\n'
        'C = Path("/opt/hermes/.venv/bin/python3")\n'
        'def f(env):\n    return env.get("X") or "/opt/data/customer.yaml"\n'
        'D = f"/opt/workspace-broker/{x}"\n',
        roots,
    )
    assert caught == {
        "/var/lib/smd-config/specs",
        "/run/smd-workspace-broker/",
        "/opt/hermes/.venv/bin/python3",
        "/opt/data/customer.yaml",
        "/opt/workspace-broker/",
    }
    ignored = family_literals(
        '"""Reads /var/lib/smd-config/customer.yaml."""\n'
        "# /opt/hermes/.venv/bin/python\n"
        "A = seat_layout.CUSTOMER_YAML\n"
        'B = "/opt/hermesx"\n'
        'C = "/var/lib/smd-configs/x"\n',
        roots,
    )
    assert ignored == set()


def test_every_allowlist_row_has_a_reason() -> None:
    assert all(why.strip() for _paths, why in ALLOWED.values())
