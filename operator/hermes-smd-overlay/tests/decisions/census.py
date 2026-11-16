"""Every call the two decision functions make, read from their source.

FROZEN with ``recorder.py``. The census is what makes the recorder able to fail:
each call site must be in one of the decision's tables (recorded, a local's
method recorded on its class, or not recorded with a stated reason), or be a
builtin. A call to anything else fails ``test_census`` until it is classified.

A call to an unlisted function defined in the decision's own plugin directory is
a HELPER (what a split creates): the census follows into it and splices its call
sites in at the point of the call, so the order of sites is the order the
unsplit function had.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import textwrap
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.decisions import tables

OVERLAY = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Site:
    """One call in the source: who it calls, how the recorder treats it, where."""

    callee: str
    kind: str  # wrap | local | unwrapped | builtin
    file: str
    line: int
    end_line: int
    end_col: int

    @property
    def key(self) -> tuple[str, int, int]:
        """What the recorder observes for a direct call (file, end line, end col)."""
        return (self.file, self.end_line, self.end_col)


@dataclass
class Walk:
    sites: list[Site] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    functions: list[types.FunctionType] = field(default_factory=list)
    returns: list[tuple[str, int]] = field(default_factory=list)
    handlers: list[tuple[str, int]] = field(default_factory=list)


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = node.value
        if isinstance(base, ast.Call):
            return f"<call>.{node.attr}"
        if isinstance(base, ast.Constant) and isinstance(base.value, str):
            return f"<str>.{node.attr}"
        head = dotted(base)
        return f"{head}.{node.attr}" if head else None
    return None


def _locals(fn: ast.FunctionDef) -> set[str]:
    a = fn.args
    names = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    for node in _own_nodes(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """The function's nodes, not those of a nested def or lambda."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _parse(fn: types.FunctionType) -> tuple[ast.FunctionDef, str, int, int]:
    lines, first = inspect.getsourcelines(fn)
    raw = "".join(lines)
    indent = len(raw) - len(raw.lstrip())
    tree = ast.parse(textwrap.dedent(raw))
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    file = Path(inspect.getsourcefile(fn) or "").resolve()
    rel = file.relative_to(OVERLAY).as_posix() if file.is_relative_to(OVERLAY) else str(file)
    return node, rel, first - 1, indent  # type: ignore[return-value]


def _resolve(name: str, globs: dict[str, Any]) -> Any:
    head, *rest = name.split(".")
    obj = globs.get(head, getattr(builtins, head, None))
    for part in rest:
        obj = getattr(obj, part, None)
    return obj


def _ident(obj: Any) -> Any:
    """What a callee IS: a bound method by its function and instance (each
    attribute read makes a new method object), anything else by itself."""
    func = getattr(obj, "__func__", None)
    if func is not None and hasattr(obj, "__self__"):
        return (id(func), id(obj.__self__))
    return id(obj)


def _is_helper(obj: Any, package: Path, library: tuple[str, ...]) -> bool:
    if not isinstance(obj, types.FunctionType):
        return False
    try:
        src = Path(inspect.getsourcefile(obj) or "").resolve()
    except TypeError:
        return False
    return src.parent == package and src.name not in library


def walk(decision: tables.Decision, fn: types.FunctionType) -> Walk:
    """The decision function's call sites in source order, helpers spliced in."""
    out = Walk()
    package = (OVERLAY / decision.file).resolve().parent
    wrap = set(decision.wrap)
    local = {n for n, _ in decision.local}
    unwrapped = {n for n, _ in decision.unwrapped}
    seen: set[int] = set()
    # What each recorded name is, in the decision's own module: a helper (a
    # split's sibling module) must reach the same object, by any spelling, for
    # the recorder's wrapper (installed where that object lives) to see it.
    canon: dict[Any, str] = {}
    for n in wrap:
        obj = _resolve(n, fn.__globals__)
        if obj is not None:
            canon.setdefault(_ident(obj), n)

    def visit(f: types.FunctionType) -> None:
        if id(f) in seen:
            out.problems.append(f"{f.__qualname__}: recursive helper")
            return
        seen.add(id(f))
        out.functions.append(f)
        node, rel, offset, indent = _parse(f)
        names = _locals(node)
        nodes = _own_nodes(node)
        out.returns += [(rel, n.lineno + offset) for n in nodes if isinstance(n, ast.Return)]
        out.handlers += [
            (rel, n.body[0].lineno + offset) for n in nodes if isinstance(n, ast.ExceptHandler)
        ]
        calls = sorted(
            (n for n in nodes if isinstance(n, ast.Call)),
            key=lambda c: (c.lineno, c.col_offset, -(c.end_lineno or 0), -(c.end_col_offset or 0)),
        )
        for call in calls:
            name = dotted(call.func) or f"<{type(call.func).__name__}>"
            site = lambda kind, n=name, c=call: Site(  # noqa: E731
                n,
                kind,
                rel,
                c.lineno + offset,
                (c.end_lineno or c.lineno) + offset,
                (c.end_col_offset or 0) + indent,
            )
            root = name.split(".")[0]
            if f is not fn and name in wrap and root not in names:
                obj = _resolve(name, f.__globals__)
                if obj is not None and canon.get(_ident(obj)) != name:
                    out.problems.append(
                        f"{rel}:{call.lineno + offset}: {name}: not the object the recorder wraps"
                    )
                    continue
            if name in wrap:
                out.sites.append(site("wrap"))
            elif name in local:
                out.sites.append(site("local"))
            elif name in unwrapped:
                out.sites.append(site("unwrapped"))
            elif root in names:
                out.problems.append(
                    f"{rel}:{call.lineno + offset}: {name}: a local's method no table names"
                )
            elif root not in f.__globals__ and hasattr(builtins, root):
                out.sites.append(site("builtin"))
            else:
                obj = _resolve(name, f.__globals__)
                same = canon.get(_ident(obj)) if obj is not None else None
                if same is not None:
                    out.sites.append(site("wrap", same))
                elif _is_helper(obj, package, decision.library):
                    visit(obj)
                else:
                    out.problems.append(
                        f"{rel}:{call.lineno + offset}: {name}: called, but no table names it"
                    )

    visit(fn)
    return out


@dataclass(frozen=True)
class Branch:
    """One direction of one conditional jump: ``left`` falls through (the test
    held), ``right`` jumps."""

    file: str
    offset: int
    dest: int
    side: str
    text: str


def branches(functions: list[types.FunctionType]) -> list[Branch]:
    """Every direction the functions' conditional jumps can take."""
    out = []
    for f in functions:
        code = f.__code__
        rel = Path(code.co_filename).resolve()
        name = rel.relative_to(OVERLAY).as_posix() if rel.is_relative_to(OVERLAY) else str(rel)
        for src, left, right in code.co_branches():  # type: ignore[attr-defined]
            text = branch_text(f, src).split(": ", 1)[-1]
            out += [Branch(name, src, left, "left", text), Branch(name, src, right, "right", text)]
    return out


def branch_text(fn: types.FunctionType, offset: int) -> str:
    """The source line a branch offset sits on, for a readable failure."""
    pos = list(fn.__code__.co_positions())[offset // 2]
    line = pos[0] or 0
    lines, first = inspect.getsourcelines(fn)
    return (
        f"{line}: {lines[line - first].strip()}"
        if first <= line < first + len(lines)
        else str(line)
    )


__all__ = ["Branch", "Site", "Walk", "branch_text", "branches", "dotted", "walk"]
