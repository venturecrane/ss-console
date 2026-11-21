#!/usr/bin/env python3
"""The goldens hold the OLD code's behaviour: regenerate every golden set from
the merge-base tree and fail on any difference its ``allowed-changes.json``
does not name.

WHY. A golden test compares the code with the golden file next to it. A PR
that changes the code AND rewrites the golden passes it, and then the golden
proves nothing about the change. This job closes that, for EVERY golden set
(``FAMILIES``): the job host matrix, the runner-to-broker wire, the broker
ledgers, the lane endings, the paid calls, the firm loaders, the text rules, the filing paths,
the reply relay's and trust wall's decisions and the seat-config caller table.

Four checks, each a failure on its own:

1. **Behaviour (H2).** The tree the PR branched from, with THIS PR's
   instrument laid over it (the measure is the PR's; the behaviour measured is
   the base's), regenerates each set; every difference from the goldens the
   PR commits must be named in that set's allowed list. A set the merge-base
   has no golden of is INTRODUCED by the PR: there is no recorded behaviour to
   hold it to, so nothing is regenerated against the base; its own tests must
   pass on the PR's tree instead, and the job says so in a NOTICE line. A set
   the base already has is never skipped.
2. **Nothing pinned is lost (H4).** The PR's committed goldens against the
   base's committed goldens: a golden file, a key, a step or a list item the
   base held and the PR drops, or a value it changes (an aged schema, a stored
   input, an answer), must be named too. The harness's own declarations
   (``golden_manifest.py``: the cells, cases and targets each set runs, the
   cells it declares not applicable, its normalisers) are compared the same
   way; a dropped case, a newly not-applicable cell or a changed normaliser
   must be named in ``allowed-instrument-changes.json``.
3. **Allowed lists stay honest (H3).** A difference is named by this
   change's own entries (the ones the merge-base did not carry), or by an
   entry the merge-base carried that this change DELETED: the deletion is in
   the PR's diff, so it names the change as an added entry would ("removal
   claims it", printed as a NOTICE). An inherited entry left in place names
   nothing: one that explains a difference fails, telling the author to delete
   it; one that explains nothing is a merged change's leftover and blocks no
   PR. An own entry that explains nothing is stale and fails. A wildcard is
   refused.
4. **Leftovers may sit on main.** The push to main runs the same checks
   against the commit before it, so a merged change's entries are its own
   there and pass; no follow-up PR clears them. Left on main they excuse
   nothing (rule 3), and the golden readers honour only a change's own entries
   too (``golden_diff.reviewed``).

Run from ``operator/`` (``uv`` on PATH; CI: the ``goldens`` job of
``.github/workflows/operator-substrate.yml``):

    python3 bin/goldens/golden-integrity.py --base <merge-base sha> [--only <family>...]

Exit 0 when every check passes; 1 otherwise, listing what failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
sys.path.insert(0, str(OPERATOR))

from bin.goldens.integrity_families import (  # noqa: E402 - imported only once sys.path holds the operator tree
    FAMILIES,
    INSTRUMENT_ALLOWED,
    OVERLAY,
    Family,
)
from bin.lib import golden_diff as gd  # noqa: E402 - imported only once sys.path holds the operator tree

RUNNER_TESTS = "runners/jobs/tests"
OVERLAY_DIR = "hermes-smd-overlay"
#: What the ``overlay`` CI job adds to the overlay's own dependencies: the
#: pinned Hermes source's imports (operator-substrate.yml, "Install the overlay").
OVERLAY_EXTRAS = ("ruamel.yaml==0.18.16", "psutil==7.2.2")


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, *, check: bool = True) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, env=env).returncode  # noqa: S603 - fixed tool argv over this repo's trees


def extract(sha: str, dest: Path) -> Path:
    """``operator/`` as it was at ``sha``, extracted (no worktree registered)."""
    dest.mkdir(parents=True, exist_ok=True)
    raw = subprocess.run(["git", "archive", sha, "operator"], cwd=REPO, check=True, capture_output=True).stdout  # noqa: S603 - a fixed git argv over this repo, no input in it
    subprocess.run(["tar", "-x", "-C", str(dest)], input=raw, check=True)  # noqa: S603 - this repo's own archive
    return dest / "operator"


def overlay(work: Path) -> None:
    """THIS PR's instrument over the base tree."""
    for rel in OVERLAY:
        src, dst = OPERATOR / rel, work / rel
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def clear_outputs(work: Path, families: list[Family]) -> None:
    """Every golden a set writes in place is removed first, so one the base no
    longer writes shows as missing instead of surviving as the PR's copy."""
    for f in families:
        if f.regen != "inplace":
            continue
        for path in gd.goldens(work / f.root, f.pattern).values():
            if path.name not in f.inputs and not any(path.match(k) for k in f.keep):
                path.unlink()


def index(root: Path) -> None:
    """A git index over the base tree with the instrument laid on it, before
    anything runs: the caller capture lists its files with ``git ls-files``."""
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "init.defaultBranch=base"]
    subprocess.run([*git, "init", "--quiet"], cwd=root, check=True)  # noqa: S603 - fixed git argv in a temp dir
    subprocess.run([*git, "add", "-A", "operator"], cwd=root, check=True)  # noqa: S603 - fixed git argv in a temp dir


def venv(work: Path, at: Path, python: str, reqs: list[list[str]]) -> Path:
    py = at / "bin" / "python"
    run(["uv", "venv", "--quiet", "--python", python, str(at)], work)
    for r in reqs:
        run(["uv", "pip", "install", "--quiet", "--python", str(py), *r], work)
    return py


def runner_venv(work: Path, at: Path) -> Path:
    """The base tree's runner installed as the seat installs it."""
    return venv(work, at, "3.13", [
        ["--no-config", "--require-hashes", "-r", "requirements/jobs.txt"],
        ["--no-deps", "./connectors/_sdk", "./connectors/_contract", "./connectors/smokeball", "./runners/jobs"],
        ["pytest", "pytest-xdist"],
    ])  # fmt: skip


def capture_venv(work: Path, at: Path) -> Path:
    """The interpreter the substrate job runs the caller capture with."""
    return venv(work, at, "3.14", [
        ["pytest", "pyyaml", "cryptography", "openpyxl==3.1.5", "httpx==0.28.1", "lxml==6.1.3", "pydantic==2.13.5",
         "mcp==2.2.0"],
        ["-e", "."],
        ["--no-deps", "-e", "./hermes-smd-overlay", "./connectors/_contract"],
    ])  # fmt: skip


def overlay_venv(tree: Path, at: Path) -> Path:
    """Python 3.14 with the overlay's dependencies and dev extras, read from
    ``tree``'s pyproject. The overlay itself is NOT installed: a run imports it
    from the tree it runs in (``overlay_env``), so a base run never reaches the
    PR's code through an installed copy."""
    project = tomllib.loads((tree / OVERLAY_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = [*project["dependencies"], *project["optional-dependencies"]["dev"], *OVERLAY_EXTRAS]
    return venv(tree, at, "3.14", [deps])


def overlay_env(tree: Path, **extra: str) -> dict[str, str]:
    """The tree's overlay and records contract ahead of anything installed."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    roots = [tree / OVERLAY_DIR, tree / "connectors/_contract"]
    return {**env, "PYTHONPATH": os.pathsep.join(str(r) for r in roots), **extra}


def regenerate_overlay(work: Path, ov: Path | None, names: set[str]) -> None:
    """The overlay-written sets (the decision corpus), from the base tree."""
    for f in FAMILIES:
        if f.name not in names or f.writes_with != "overlay" or ov is None:
            continue
        cmd = [str(ov), "-m", "pytest", "-q", "-p", "no:cacheprovider", *f.tests, "-k", "corpus_writes"]
        run(cmd, work / OVERLAY_DIR, overlay_env(work, SMD_DECISIONS_WRITE="1"), check=False)


def code_path(work: Path) -> dict[str, str]:
    """The base tree's own packages ahead of any installed copy."""
    roots = [
        work / "runners/jobs",
        work / "connectors/_sdk",
        work / "connectors/_contract",
        work / "connectors/smokeball",
    ]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(str(r) for r in [*roots, work])}


def regenerate(work: Path, py: Path, cap: Path | None, out: Path, names: set[str]) -> None:
    """Each set's harness, in the base tree; a harness's own pass or fail is
    not the verdict (the files it writes are compared below)."""
    env = {**code_path(work), "JOBS_GOLDENS_OUT": str(out)}
    runner = ["-c", "runners/jobs/pyproject.toml"]
    if names & {"host_matrix", "wire"}:
        run([str(py), "-m", "pytest", *runner, f"{RUNNER_TESTS}/test_host_matrix_goldens.py", "-q", "-n", "4", "-k",
             "not every"], work, env, check=False)  # fmt: skip
        run([str(py), "-m", "pytest", *runner, f"{RUNNER_TESTS}/test_wire_snapshot.py", "-q"], work, env, check=False)
    if "ledgers" in names:
        run([str(py), "-m", "pytest", "-p", "no:cacheprovider", "workspace_broker/tests/test_ledger_goldens.py", "-q",
             "-n", "4", "-k", "not every"], work, env, check=False)  # fmt: skip
    kit_env = {**code_path(work), "JOBS_WRITE_KIT_GOLDENS": "1", "JOBS_WRITE_LANE_GOLDENS": "1"}
    runner_tests = (t for f in FAMILIES if f.name in names and f.writes_with == "runner" for t in f.tests)
    for test in (f"{RUNNER_TESTS}/{t}" for t in runner_tests):
        # One file at a time, in the family's order: the model facts read the
        # paid-call goldens, which must be written first. Only the writers run:
        # a set's completeness test (``every_golden_has_a_case``) reads the dir
        # clear_outputs just emptied, before its writers have filled it, so it
        # always saw an empty set there (``assert set() == {...}``); the
        # committed set's completeness is judged below, file by file.
        writers = "not planted and not every_golden and not every_target and not every_checker_was_fed"
        run([str(py), "-m", "pytest", *runner, test, "-q", "-n", "4", "-k", writers], work, kit_env, check=False)
    if "seat_callers" in names and cap is not None:
        dest = out / "contracts" / "seat-config-callers.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        run([str(cap), "bin/capture-seat-config-callers.py", "--out", str(dest)], work, check=False)


def as_own_base(tree: Path) -> str:
    """Make the extracted base a one-commit git checkout and return that commit.

    The base's own goldens code reads its allowed lists against a merge-base
    (``bin/lib/golden_diff._base``). An extracted tree is no checkout, so a
    leftover entry on main (one no clearing PR removed) failed every golden PR's
    manifest step. The base IS the merge-base: committed as itself, every entry
    in its lists is inherited and none is counted as new."""
    top = tree.parent
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=goldens", "-c", "user.email=goldens@example.test"]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if not (top / ".git").exists():
        for args in (["init", "--quiet"], ["add", "-A"], ["commit", "--quiet", "-m", "base"]):
            subprocess.run([*git, *args], cwd=top, check=True, capture_output=True, env=clean)  # noqa: S603 - a fixed git argv in this job's scratch tree
    head = [*git, "rev-parse", "HEAD"]
    return subprocess.run(head, cwd=top, check=True, capture_output=True, text=True, env=clean).stdout.strip()  # noqa: S603 - same


def manifest(tree: Path, py: Path) -> dict[str, Any]:
    env = code_path(tree)
    if tree != OPERATOR:
        env["GOLDENS_BASE"] = as_own_base(tree)
    raw = subprocess.run(  # noqa: S603 - this repo's own manifest script over this repo's tree
        [str(py), str(OPERATOR / "bin" / "goldens" / "golden_manifest.py"), str(tree)],
        cwd=tree, check=False, capture_output=True, text=True, env=env,
    )  # fmt: skip
    if raw.returncode != 0:
        sys.exit(f"golden_manifest.py failed on {tree} (exit {raw.returncode}):\n{raw.stderr[-4000:]}")
    return json.loads(raw.stdout)


def manifest_losses(base: dict[str, Any], pr: dict[str, Any]) -> dict[str, list[str]]:
    """Cases the PR drops, cells it newly declares not applicable, and
    normalisers it adds, changes or removes, as ``<cases>`` / ``<normaliser>`` entries."""
    found: dict[str, list[str]] = {}
    for key, items in base.get("cases", {}).items():
        now = set(pr.get("cases", {}).get(key, []))
        if key.endswith("not_applicable"):
            gone = sorted(now - set(items))
        else:
            gone = sorted(set(items) - now)
        found.setdefault("<cases>", []).extend(f"{key}:{i}" for i in gone)
    b, p = base.get("normalisers", {}), pr.get("normalisers", {})
    # A module the base did not have normalises no golden the base pinned.
    modules = {k.split(".", 1)[0] for k in b}
    keys = {k for k in set(b) | set(p) if k.split(".", 1)[0] in modules}
    found["<normaliser>"] = sorted(k for k in keys if b.get(k) != p.get(k))
    return {k: v for k, v in found.items() if v}


def allowed_of(tree: Path, rel: str) -> dict[str, list[str]]:
    path = tree / rel
    return gd.parse_allowed(json.loads(path.read_text(encoding="utf-8")), rel) if path.is_file() else {}


#: How a notice names a difference a deleted base entry claimed.
CLAIM = "claimed by removal: "


def removed(allowed: dict[str, list[str]], base_allowed: dict[str, list[str]]) -> dict[str, list[str]]:
    """Entries the merge-base carried that this change deleted from the list.

    Only from a list that still names something: a list file deleted (read as
    ``{}``) or emptied removes nothing that can claim a difference, because
    wiping a list reviews no one path."""
    out: dict[str, list[str]] = {}
    if not any(allowed.values()):
        return out
    for name, paths in base_allowed.items():
        left = [p for p in paths if p not in set(allowed.get(name, []))]
        if left:
            out[name] = left
    return out


def claimed(allowed: dict[str, list[str]], base_allowed: dict[str, list[str]], *found: dict[str, list[str]]) -> list[str]:  # fmt: skip
    """``<name>/<path>`` for every difference a deleted base entry claims (the removal rule in ``judge``)."""
    gone = removed(allowed, base_allowed)
    seen = {(name, p) for f in found for name, paths in f.items() for p in paths}
    return [f"{name}/{p}" for name, paths in sorted(gone.items()) for p in paths if (name, p) in seen]


def claimed_notices(label: str, allowed: dict[str, list[str]], base_allowed: dict[str, list[str]], *found: dict[str, list[str]]) -> list[str]:  # fmt: skip
    return [f"{CLAIM}{label}/{x}" for x in claimed(allowed, base_allowed, *found)]


def judge(label: str, allowed: dict[str, list[str]], base_allowed: dict[str, list[str]], *found: dict[str, list[str]]) -> list[str]:  # fmt: skip
    """Every failure for one allowed list: an unnamed difference, a stale entry, or an inherited one reused.

    A difference at a path counts as NAMED by this change when the path is one
    of this change's own entries (``this_change``) or when the merge-base's
    list carried it and this change DELETED it: the deletion is in the PR's
    diff, so it names the change as an added entry would ("removal claims it";
    ``claimed`` lists those for a NOTICE). An inherited entry LEFT IN PLACE
    never names a difference: it was reviewed for the change that merged it,
    and a new change that reuses it fails, told to delete it. An inherited
    entry that explains nothing is a merged change's leftover and blocks no PR;
    a leftover may sit on main, and the next change to touch its path deletes it.
    """
    own = gd.this_change(allowed, base_allowed)
    inherited = gd.inherited(allowed, base_allowed)
    gone = removed(allowed, base_allowed)
    named = {n: own.get(n, []) + gone.get(n, []) for n in set(own) | set(gone)}
    problems = []
    for kind, f in zip(("behaviour", "lost"), found):
        for name, paths in gd.unallowed(f, named).items():
            reused = set(inherited.get(name, []))
            plain = [p for p in paths if p not in reused]
            if plain:
                problems.append(f"{label}/{name}: {kind}: {', '.join(plain[:20])}{' ...' if len(plain) > 20 else ''}")
            kept = [p for p in paths if p in reused]
            if kept:
                problems.append(
                    f"{label}/{name}: inherited allowed entries excuse a {kind} difference here (the merge-base already "
                    f"carried them, for the change that merged them): {kept}. Delete each from the list; the deletion "
                    "names your change."
                )
    stale = gd.stale(own, *found)
    for name, paths in stale.items():
        problems.append(f"{label}/{name}: stale allowed entries (they explain no difference; remove them): {paths}")
    return problems


def got_root(f: Family, work: Path, out: Path) -> Path:
    return out / f.out if f.regen == "out" else work / f.root


def view_of(f: Family) -> Any:
    """A set's own comparison form: the caller table is compared without line
    numbers (a site a peer's merge moved down a file is the same site)."""
    if f.name == "seat_callers":
        from bin.lib.seat_callers.diff import comparable

        return comparable
    return None


def check_family(f: Family, base: Path, work: Path, out: Path) -> tuple[list[str], list[str]]:
    """(problems, notices) for one present set."""
    committed = gd.goldens(OPERATOR / f.root, f.pattern)
    regenerated = gd.goldens(got_root(f, work, out), f.pattern, f.inputs)
    kept = {k: v for k, v in committed.items() if v.name not in f.inputs}
    behaviour = gd.tree_diffs(kept, regenerated, view=view_of(f))
    lost = gd.tree_diffs(gd.goldens(base / f.root, f.pattern), committed, only_losses=True, view=view_of(f))
    allowed, base_allowed = allowed_of(OPERATOR, f.allowed), allowed_of(base, f.allowed)
    found = (behaviour, lost)
    return judge(f.name, allowed, base_allowed, *found), claimed_notices(f.name, allowed, base_allowed, *found)


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the merge-base commit")
    ap.add_argument("--python", help="a venv python with the runner's dependencies (default: build one with uv)")
    ap.add_argument("--capture-python", help="a python for the seat-config caller capture (default: build one)")
    ap.add_argument("--overlay-python", help="a 3.14 python with the overlay's dependencies (default: build one)")
    ap.add_argument("--only", nargs="*", default=None, help="only these families (default: all)")
    return ap.parse_args(argv)


def introduced(f: Family, base: Path) -> bool:
    """A set the merge-base has no golden of: the PR introduces it, so the
    base has no recorded behaviour (and maybe no harness) to regenerate it from."""
    return not gd.goldens(base / f.root, f.pattern)


def own_tests(f: Family, py: Path, cap: Path | None, ov: Path | None = None) -> list[str]:
    """An introduced set's own tests, on the PR's tree: they must pass."""
    if f.check_with == "overlay":
        if ov is None:
            return [f"{f.name}: introduced, but no overlay interpreter to run its tests with"]
        checks = [c.removeprefix(f"{OVERLAY_DIR}/") for c in f.checks]
        cmd = [str(ov), "-m", "pytest", "-q", "-p", "no:cacheprovider", *checks]
        if run(cmd, OPERATOR / OVERLAY_DIR, overlay_env(OPERATOR), check=False) != 0:
            return [f"{f.name}: introduced by this PR, and its own tests fail on the PR's tree ({', '.join(f.checks)})"]
        return []
    if f.check_with == "capture":
        if cap is None:
            return [f"{f.name}: introduced, but no capture interpreter to run its tests with"]
        cmd = [str(cap), "-m", "pytest", *f.checks, "-q"]
    elif f.check_with == "broker":
        cmd = [str(py), "-m", "pytest", "-p", "no:cacheprovider", *f.checks, "-q", "-n", "4"]
    else:
        cmd = [str(py), "-m", "pytest", "-c", "runners/jobs/pyproject.toml", *f.checks, "-q", "-n", "4"]
    if run(cmd, OPERATOR, code_path(OPERATOR), check=False) != 0:
        return [f"{f.name}: introduced by this PR, and its own tests fail on the PR's tree ({', '.join(f.checks)})"]
    return []


def check(a: argparse.Namespace, tmp: Path) -> tuple[list[str], list[str]]:
    """(problems, notices)."""
    base, work = extract(a.base, tmp / "base"), extract(a.base, tmp / "work")
    families = [f for f in FAMILIES if a.only is None or f.name in a.only]
    new = [f for f in families if introduced(f, base)]
    present = [f for f in families if f not in new]
    overlay(work)
    clear_outputs(work, present)
    index(work.parent)
    py = Path(a.python) if a.python else runner_venv(work, tmp / "venv")
    cap = None
    if "seat_callers" in {f.name for f in families}:
        cap = Path(a.capture_python) if a.capture_python else capture_venv(work, tmp / "capv")
    ov = None
    if any("overlay" in (f.writes_with, f.check_with) for f in families):
        ov = Path(a.overlay_python) if a.overlay_python else overlay_venv(OPERATOR, tmp / "ovv")
    out = tmp / "out"
    regenerate(work, py, cap, out, {f.name for f in present})
    regenerate_overlay(work, ov, {f.name for f in present})
    problems, notices = [], []
    for f in present:
        got, said = check_family(f, base, work, out)
        problems += got
        notices += said
    for f in new:
        problems += own_tests(f, py, cap, ov) + judge(f.name, allowed_of(OPERATOR, f.allowed), {}, {}, {})
        notices.append(f"{f.name}: introduced by this PR (the merge-base has none); its own tests ran on the PR's tree")
    found = manifest_losses(manifest(base, py), manifest(OPERATOR, py))
    allowed, base_allowed = allowed_of(OPERATOR, INSTRUMENT_ALLOWED), allowed_of(base, INSTRUMENT_ALLOWED)
    problems += judge("instrument", allowed, base_allowed, {}, found)
    notices += claimed_notices("instrument", allowed, base_allowed, {}, found)
    return problems, notices


def announce(notices: list[str]) -> None:
    """Every notice as a log line; a removal claim also as a workflow warning,
    so it shows on the PR beside the deletion that made it."""
    for n in notices:
        if n.startswith(CLAIM):
            print(f"::warning::golden integrity: {n}")
        print(f"golden integrity: NOTICE {n}")


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    with tempfile.TemporaryDirectory(prefix="goldens-base-") as tmp:
        try:
            problems, notices = check(a, Path(tmp))
        except gd.AllowedListError as exc:
            problems, notices = [str(exc)], []
    announce(notices)
    if not problems:
        print("golden integrity: every golden equals the merge-base tree's behaviour, keeps what the base pinned, "
              "and every allowed entry explains a difference")  # fmt: skip
        return 0
    print("golden integrity: FAILED")
    for p in problems:
        print(f"  {p}")
    print("A behaviour change names each path in its set's allowed-changes.json (reviewed with the PR); a golden "
          "rewritten to match new behaviour without that is restored.")  # fmt: skip
    return 1


if __name__ == "__main__":
    sys.exit(main())
