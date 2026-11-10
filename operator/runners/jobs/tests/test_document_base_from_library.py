"""Every firm-format document a job lane delivers takes its base from the library.

ADR 0083 made the document library the one home of a firm's format. The demand
lane (09-28) and the drafting lane (10-07) were then built with their own pinned
copies, and from 10-07 A&P's briefs and discovery sets ignored the library: no
test said that a lane's renderer call must go through the library resolver
(``drafting/library_pick``). This test says it.

It walks every module under ``jobs/`` and finds each call to a firm-format
renderer (``FIRM_RENDERERS``). Each call site must be named in ``SITES`` with how
its base is chosen:

* ``library:<call>``: the enclosing function itself calls ``library_pick.<call>``.
* ``via:<module>::<function>:<call>``: the base arrives as an argument, and that
  other function (which hands it over) calls ``library_pick.<call>``.
* ``layer:<why>``: the renderer layer itself, which only takes the base it is given.
* ``not_delivered:<why>``: a probe or rehearsal; nothing it renders reaches a firm.

A new call site that is not registered fails, so a new lane cannot render a firm
document without saying where its base comes from; a registered site that no
longer exists fails too, so the list cannot rot.
"""

from __future__ import annotations

import ast
from pathlib import Path

JOBS = Path(__file__).resolve().parents[1] / "jobs"

#: (receiver name, attribute) of every call that renders a document in a firm's format.
FIRM_RENDERERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("render", "render"),  # drafting/render.render(md, cls, out, fmt, base)
        ("base_lay", "lay_out"),  # drafting/base_lay.lay_out(..., styles=...)
        ("house", "render"),  # demand/house.render(md, reference, ...)
        ("house_numbered", "render"),  # demand/house_numbered.render(md, reference, ...)
        ("deliver", "render_demand"),  # demand/deliver.render_demand(..., reference)
    }
)

SITES: dict[str, str] = {
    # The renderer layer: takes the base it is handed, chooses none.
    "drafting/base_lay.py::lay_out": "layer:renders on the styles file its caller resolved",
    "demand/deliver.py::render_demand": "layer:renders on the reference its caller resolved",
    # Delivered documents.
    "drafting/finish.py::_render_unit": "library:ranked",
    "drafting/placing.py::_place_unit": "library:pick_base",
    "demand/run.py::_build_deliverables": "via:demand/run.py::_select_format:select",
    # Probes and rehearsals: nothing reaches a firm.
    "drafting/firm_rules.py::_probe_fails": "not_delivered:the seat's PROBE_MD format probe",
    "drafting/layout_rehearse.py::_margins_hold": "not_delivered:$0 layout rehearsal",
    "drafting/revise/rehearse.py::rehearse": "not_delivered:$0 revise rehearsal",
}


def _functions(path: Path) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _calls(node: ast.AST) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
            found.add((n.func.value.id, n.func.attr))
    return found


def _render_sites() -> set[str]:
    sites: set[str] = set()
    for path in sorted(JOBS.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(JOBS).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Calls directly in this function, not in a function nested inside it.
            nested = {
                id(n)
                for inner in ast.walk(fn)
                if inner is not fn and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                for n in ast.walk(inner)
            }
            for n in ast.walk(fn):
                if id(n) in nested or not isinstance(n, ast.Call):
                    continue
                f = n.func
                if (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and (f.value.id, f.attr) in FIRM_RENDERERS
                ):
                    sites.add(f"{rel}::{fn.name}")
    return sites


def _calls_library(site: str, call: str) -> bool:
    rel, fn = site.split("::")
    node = _functions(JOBS / rel).get(fn)
    return node is not None and ("library_pick", call) in _calls(node)


def test_every_firm_renderer_call_is_registered() -> None:
    unregistered = sorted(_render_sites() - SITES.keys())
    assert not unregistered, (
        "a job renders a firm document without saying where its base comes from; "
        f"resolve it through drafting/library_pick and register it in SITES: {unregistered}"
    )


def test_no_registered_site_is_stale() -> None:
    stale = sorted(SITES.keys() - _render_sites())
    assert not stale, f"SITES names call sites that no longer render: {stale}"


def test_each_delivered_site_resolves_through_the_library() -> None:
    bad: list[str] = []
    for site, how in SITES.items():
        kind, _, rest = how.partition(":")
        if kind == "library":
            if not _calls_library(site, rest):
                bad.append(f"{site} does not call library_pick.{rest}")
        elif kind == "via":
            upstream, _, call = rest.rpartition(":")
            if not _calls_library(upstream, call):
                bad.append(f"{site}: {upstream} does not call library_pick.{call}")
        elif kind not in {"layer", "not_delivered"} or not rest:
            bad.append(f"{site}: unknown or unexplained kind {how!r}")
    assert not bad, bad


def test_demand_hands_the_resolved_reference_to_the_renderer() -> None:
    """The ``via`` site must actually pass what the resolver chose."""
    node = _functions(JOBS / "demand/run.py")["_build_deliverables"]
    passed = [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "render_demand"
        and any(
            isinstance(a, ast.Attribute) and a.attr == "_reference" for a in [*n.args, *(k.value for k in n.keywords)]
        )
    ]
    assert passed, "demand/run.py::_build_deliverables no longer passes self._reference to render_demand"


def test_the_guard_can_fail(tmp_path: Path, monkeypatch) -> None:
    """A new unregistered render site is caught (the check is not vacuous)."""
    import shutil

    copy = tmp_path / "jobs"
    shutil.copytree(JOBS, copy)
    (copy / "drafting" / "rogue_lane.py").write_text(
        "from . import render\n\ndef deliver(md, out, fmt):\n    return render.render(md, 'memo', out, fmt, None)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__import__(__name__), "JOBS", copy)
    assert "drafting/rogue_lane.py::deliver" in _render_sites() - SITES.keys()
