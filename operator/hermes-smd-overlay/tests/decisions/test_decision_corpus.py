"""The decision corpus: what ``_relay_draft`` and ``_evaluate_tool_call`` decide
today, held so a split of either function cannot change a decision unseen.

The goldens under ``goldens/`` are recorded from the code by ``recorder.py``
(see its docstring). Two families:

* ``synthetic/<decision>.<family>``: one case per guard, the operands of every
  compound condition, every pair of guards applied together, and an error raised
  at each direct call in turn (``cases.py``, ``drive.py``).
* ``existing/<test file>``: every overlay test that reaches either function,
  replayed under the recorder (``replay.py``).

Regenerate (only for a reviewed behaviour change, each path then named in
``goldens/allowed-changes.json``)::

    SMD_DECISIONS_WRITE=1 python -m pytest tests/decisions -k corpus_writes

The integrity job (``operator/bin/goldens/golden-integrity.py``, family
``decisions``) regenerates them from the merge-base tree with this PR's
instrument and fails on any difference the allowed list does not name.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tests.decisions import census, drive, recorder, replay, tables

HERE = Path(__file__).resolve().parent
GOLDENS = HERE / "goldens"
WRITE = os.environ.get("SMD_DECISIONS_WRITE") == "1"
KNOWN = HERE / "known-defects.json"


def _functions() -> dict[str, types.FunctionType]:
    return {
        "relay": drive.plugin("hermes-smd-reply")._relay_draft,
        "wall": drive.plugin("hermes-smd-trust").enforce._evaluate_tool_call,
    }


def _differences(want: Any, got: Any, path: str = "", limit: int = 12) -> list[str]:
    if isinstance(want, dict) and isinstance(got, dict):
        out: list[str] = []
        for k in sorted(set(want) | set(got), key=str):
            sub = f"{path}.{k}" if path else str(k)
            if (k in want) != (k in got):
                out.append(sub)
            else:
                out += _differences(want[k], got[k], sub, limit)
            if len(out) >= limit:
                break
        return out
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        out = []
        for i, (a, b) in enumerate(zip(want, got, strict=True)):
            out += _differences(a, b, f"{path}.{i}", limit)
            if len(out) >= limit:
                break
        return out
    return [] if want == got else [path or "<root>"]


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_dirs(want: Path, got: Path) -> list[str]:
    names = {
        p.relative_to(want).as_posix()
        for p in want.rglob("*.json")
        if p.name != "allowed-changes.json"
    }
    names |= {p.relative_to(got).as_posix() for p in got.rglob("*.json")}
    problems = []
    for name in sorted(names):
        a, b = want / name, got / name
        if not a.is_file() or not b.is_file():
            problems.append(f"{name}: only in {'the recording' if b.is_file() else 'the goldens'}")
            continue
        found = _differences(_read(a), _read(b))
        if found:
            problems.append(f"{name}: {found}")
    return problems


# --------------------------------------------------------------------------
# The instrument can fail
# --------------------------------------------------------------------------


@pytest.mark.parametrize("decision", tables.DECISIONS, ids=lambda d: d.key)
def test_census_every_call_is_classified(decision: tables.Decision) -> None:
    """MUTATION: add a call to either function (or to a helper a split gives it)
    that no table names, and this fails: the recorder would not see it."""
    walk = census.walk(decision, _functions()[decision.key])
    assert not walk.problems, "calls the recorder does not wrap:\n" + "\n".join(walk.problems)
    used = {s.callee for s in walk.sites}
    listed = (
        set(decision.wrap) | {n for n, _ in decision.local} | {n for n, _ in decision.unwrapped}
    )
    assert not listed - used, f"table entries no call uses (remove them): {sorted(listed - used)}"


@pytest.mark.parametrize(
    ("decision", "dropped"),
    [(tables.RELAY, "relay.draft_body"), (tables.WALL, "matter_gate.content_read_refusal")],
    ids=["relay", "wall"],
)
def test_the_census_can_fail(decision: tables.Decision, dropped: str) -> None:
    """FALSIFIER: the census with one recorded name taken out of the table
    reports that call as unwrapped."""
    import dataclasses

    narrowed = dataclasses.replace(decision, wrap=tuple(n for n in decision.wrap if n != dropped))
    walk = census.walk(narrowed, _functions()[decision.key])
    assert any(dropped in p for p in walk.problems), walk.problems


def test_every_table_name_resolves_on_a_running_seat() -> None:
    """A name that resolves to nothing is a call the recorder silently misses."""
    sc_relay = drive.run_relay(drive.C.RELAY[0])
    sc_wall = drive.run_wall(drive.C.WALL[0])
    assert sc_relay.decisions == 1 and sc_wall.decisions == 1
    assert not recorder.INSTALLED.failed, (
        f"names the recorder could not wrap: {sorted(recorder.INSTALLED.failed)}"
    )
    for decision, fn in (
        (tables.RELAY, _functions()["relay"]),
        (tables.WALL, _functions()["wall"]),
    ):
        globs = fn.__globals__
        missing = []
        for name in (*decision.wrap, *(h for _, h in decision.local), *decision.edges):
            root = name.split(".")[0]
            if root in ("_D1_CLIENT", "_LIMITER", "_HELD_STORE") and globs.get(root) is None:
                continue
            if recorder.resolve(name, globs) is None:
                missing.append(name)
        assert not missing, f"{decision.key}: names that resolve to nothing: {missing}"
    for name in (*tables.SHARED_EDGES, *tables.SHARED_REGISTERS):
        assert recorder.resolve(name, {}) is not None, name


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synthetic() -> tuple[dict[str, dict[str, Any]], recorder.Scope]:
    return drive.corpus()


@pytest.fixture(scope="module")
def forward(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("decisions-forward")
    problems = replay.replay(out, "forward")
    assert not problems, "the replayed tests failed under the recorder:\n" + "\n".join(problems)
    return out


def test_synthetic_corpus_writes_or_matches(
    synthetic: tuple[dict[str, dict[str, Any]], recorder.Scope],
) -> None:
    docs, _cov = synthetic
    if WRITE:
        for name, doc in docs.items():
            recorder.write_json(GOLDENS / f"{name}.json", doc)
        return
    problems = []
    for name, doc in sorted(docs.items()):
        path = GOLDENS / f"{name}.json"
        if not path.is_file():
            problems.append(f"{name}: no golden")
            continue
        found = _differences(_read(path), json.loads(json.dumps(doc)))
        if found:
            problems.append(f"{name}: {found}")
    on_disk = {
        p.relative_to(GOLDENS).with_suffix("").as_posix()
        for p in (GOLDENS / "synthetic").glob("*.json")
    }
    problems += [f"{n}: a golden no case writes" for n in sorted(on_disk - set(docs))]
    assert not problems, "the synthetic decisions differ from the corpus:\n" + "\n".join(problems)


def test_existing_corpus_writes_or_matches(forward: Path) -> None:
    if WRITE:
        shutil.rmtree(GOLDENS / "existing", ignore_errors=True)
        shutil.copytree(forward / "existing", GOLDENS / "existing")
        return
    problems = _compare_dirs(GOLDENS / "existing", forward / "existing")
    assert not problems, "the replayed tests decide differently from the corpus:\n" + "\n".join(
        problems
    )


@pytest.mark.parametrize("order", ["reverse", "per-file"])
def test_order_independence(forward: Path, tmp_path: Path, order: str) -> None:
    """The record of a test never depends on what ran before it: the same
    selection run reversed, and one file per process, records the same."""
    problems = replay.replay(tmp_path, order)
    assert not problems, "\n".join(problems)
    diff = _compare_dirs(forward / "existing", tmp_path / "existing")
    assert not diff, f"the {order} run recorded differently:\n" + "\n".join(diff)


# --------------------------------------------------------------------------
# Every guard, call site and branch has a case
# --------------------------------------------------------------------------


def test_every_guard_call_site_and_branch_has_a_case(
    synthetic: tuple[dict[str, dict[str, Any]], recorder.Scope], forward: Path
) -> None:
    """MUTATION: add a guard, a call or a branch that no case reaches, and this
    fails. Reached means: every recorded call site is called and has an error
    raised at it; every return and every exception handler runs; every branch
    goes both ways (Python 3.14's branch events)."""
    _docs, cov = synthetic
    replayed = _read(forward / "coverage.json")
    sites = cov.sites | {tuple(x) for x in replayed["sites"]}
    raised = cov.raised_sites | {tuple(x) for x in replayed["raised_sites"]}
    lines = recorder.COVERAGE["lines"] | {tuple(x) for x in replayed["lines"]}
    branches = recorder.COVERAGE["branches"] | {tuple(x) for x in replayed["branches"]}
    problems = []
    used: set[tuple[str, str, str]] = set()
    for decision in tables.DECISIONS:
        walk = census.walk(decision, _functions()[decision.key])
        for site in walk.sites:
            if site.kind not in ("wrap", "local"):
                continue
            where = f"{site.file}:{site.line} {site.callee}"
            if site.key not in sites:
                problems.append(f"{decision.key}: no case calls {where}")
            elif site.key not in raised:
                problems.append(f"{decision.key}: no case raises at {where}")
        for kind, found in (("return", walk.returns), ("handler", walk.handlers)):
            problems += [
                f"{decision.key}: no case runs the {kind} at {f}:{ln}"
                for f, ln in found
                if (f, ln) not in lines
            ]
        if sys.version_info >= (3, 14):
            exempt = {
                (t, side): why for key, t, side, why in tables.INFEASIBLE if key == decision.key
            }
            for b in census.branches(walk.functions):
                if (b.file, b.offset, b.dest) in branches:
                    continue
                if (b.text, b.side) in exempt:
                    used.add((decision.key, b.text, b.side))
                    continue
                problems.append(
                    f"{decision.key}: no case takes the {b.side} branch of {b.file}: {b.text}"
                )
    stale = {(k, t, side) for k, t, side, _ in tables.INFEASIBLE} - used
    problems += [
        f"tables.INFEASIBLE names a branch a case does take, or none: {s}" for s in sorted(stale)
    ]
    assert not problems, f"{len(problems)} unreached:\n" + "\n".join(problems)


# --------------------------------------------------------------------------
# What the corpus pins that is wrong today
# --------------------------------------------------------------------------


def _at(doc: Any, path: list[Any]) -> Any:
    for part in path:
        doc = doc[part]
    return doc


def test_known_defects_are_visible_in_the_corpus() -> None:
    """Each defect ``known-defects.json`` names is in the corpus as recorded, so
    the split PRs reproduce it rather than fix it unseen; the PR that fixes one
    changes its golden (named in the allowed list) and removes its entry here.
    An empty list is the end state once every recorded defect is fixed (wave 4:
    the read-volume row #4039, the allow left on a blocked call #4041, the fault
    after a send #4042); the read itself must still be there."""
    known = _read(KNOWN)
    assert "defects" in known and isinstance(known["defects"], list), (
        "known-defects.json has no defects list"
    )
    defects = known["defects"]
    for d in defects:
        record = _at(_read(GOLDENS / d["golden"]), d["record"])
        if "steps" in d:
            assert record["steps"] == d["steps"], f"{d['id']}: {record['steps']}"
            continue
        values = record.get("values", [])
        events = [drive._deref(e, values) for e in record["events"]]
        calls = [e for e in events if e.get("call") == d["call"]]
        assert len(calls) == d["count"], (
            f"{d['id']}: {len(calls)} {d['call']} calls, expected {d['count']}"
        )
        reasons = [c.get("kwargs", {}).get("reason", "") for c in calls]
        for want, got in zip(d["reasons_start"], reasons, strict=True):
            assert got.startswith(want), f"{d['id']}: {got!r} does not start {want!r}"
        if "returned_action" in d:
            assert (record.get("returned") or {}).get("action") == d["returned_action"], d["id"]
        if "register_keeps" in d:
            name, reason = d["register_keeps"]
            kept = json.dumps(drive._deref(record["registers"][name], values))
            assert reason in kept and d["reasons_start"][0] not in kept, f"{d['id']}: {kept[:300]}"
