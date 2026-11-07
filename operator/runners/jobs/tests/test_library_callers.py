"""Every path that makes a firm's Word document goes through the library
resolver, and starts from what it answered (design 2026-11-06, piece 2).

Built in the style of ``hermes-smd-overlay/tests/test_rule_reader_registry.py``:
the facts are DERIVED from the code and the registry, never declared beside
them and trusted.

GUARD 1, THROUGH THE RESOLVER. Read from the source with ``ast``:

* every producing path the kind registry (``operator/contracts/document-kinds.yaml``)
  names has a caller here, and each caller calls its path's adapter;
* ``resolve`` is imported by exactly the three adapters and nothing else;
* from each function that writes a document's Word bytes (the renderers named in
  :data:`ENTRY_POINTS`), every chain of callers is walked upward through the
  whole connector and jobs runner, and must end at a caller, pass only through
  functions that carry the resolver's answer as a parameter, or end at an
  exemption that names its reason. A NEW LANE THAT RENDERS A .docx WITHOUT THE
  RESOLVER FAILS HERE: its function is a chain end none of the lists names;
* every module that writes Word bytes by hand (python-docx ``save``, a zip
  written) is a renderer above or an exemption with its reason.

GUARD 2, FROM WHAT IT RETURNED. Each caller is run with an accepted file and
with nothing accepted; the bytes its renderer actually opened are compared with
the record it wrote: for ``resolved`` they are the accepted file's, for
``none_accepted`` they are the fallback the record names. A falsifier proves the
comparison fails on a caller that substitutes another file.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest
import yaml

import library_testkit as lt
from operator_connector_sdk.library import STYLES, MemoryLibrary

OP = Path(__file__).resolve().parents[3]
TREES = {
    "smokeball_connector": OP / "connectors" / "smokeball" / "smokeball_connector",
    "jobs": OP / "runners" / "jobs" / "jobs",
}
REGISTRY = OP / "contracts" / "document-kinds.yaml"
CONNECTOR = "connectors/smokeball/smokeball_connector"
JOBS = "runners/jobs/jobs"

#: Each producing path's adapter: the one function in it that calls ``resolve``.
ADAPTERS = {
    "chat_tool": (f"{CONNECTOR}/library_pick.py", "chat_base"),
    "drafting_job": (f"{JOBS}/drafting/library_pick.py", "pick_base"),
    "demand_job": (f"{JOBS}/demand/library_pick.py", "select"),
}
#: Where each path makes its documents: the functions that ask the adapter.
CALLERS = {
    "chat_tool": [(f"{CONNECTOR}/server.py", "_render_with_format")],
    "drafting_job": [(f"{JOBS}/drafting/finish.py", "_render_unit"), (f"{JOBS}/drafting/placing.py", "_place_unit")],
    "demand_job": [(f"{JOBS}/demand/run.py", "_select_format"), (f"{JOBS}/demand/run.py", "_build_deliverables")],
}
#: The functions that write a document's Word bytes.
ENTRY_POINTS = {
    (f"{CONNECTOR}/docx_format.py", "render_document"),
    (f"{CONNECTOR}/render.py", "render_markdown_to_docx"),
    (f"{JOBS}/drafting/brief_base.py", "render_into"),
    (f"{JOBS}/demand/house.py", "render"),
    (f"{JOBS}/demand/house_numbered.py", "render"),
    (f"{JOBS}/demand/plain_docx.py", "render"),
}
#: Between a caller and a renderer: each carries the resolver's answer in the
#: parameter named, so it renders on what its caller was given.
THROUGH = {
    (f"{JOBS}/drafting/render.py", "render"): "base",
    (f"{JOBS}/drafting/base_lay.py", "lay_out"): "styles",
    (f"{JOBS}/demand/deliver.py", "render_demand"): "reference",
}
#: Chain ends that are not a firm's document made from a library file, each with its reason.
EXEMPT = {
    (f"{JOBS}/demand/deliver.py", "render_plain"): (
        "the plain work notes (gap audit, attorney notes, coverage report): our own working "
        "papers to the attorney, never a firm document made in a library file"
    ),
    (f"{JOBS}/drafting/firm_rules.py", "_probe_fails"): (
        "a layout rule's dry probe on a throwaway path: proves a rule can be laid, files nothing"
    ),
    (f"{JOBS}/drafting/revise/rehearse.py", "rehearse"): (
        "revise mode proves the filed brief renders back as filed: a revision is made in the "
        "filed document itself, never a new document from a library file"
    ),
}
#: Modules that write Word bytes by hand, each with why that is not a firm
#: document made from a library file (or which renderer it is).
WORD_WRITERS = {
    f"{CONNECTOR}/docx_format.py": "the renderer (ENTRY_POINTS)",
    f"{CONNECTOR}/render.py": "the classless stock render (ENTRY_POINTS); a call with no document_class names no kind",
    f"{CONNECTOR}/docx_base.py": "opens a base and builds the starter for the renderer",
    f"{CONNECTOR}/form_docx.py": (
        "firm forms: render_firm_form_letter and its siblings open the firm's own form from the "
        "library by its authored name and refuse when it is missing; their file is the form itself"
    ),
    f"{JOBS}/demand/house.py": "the demand writer (ENTRY_POINTS)",
    f"{JOBS}/demand/plain_docx.py": "the plain work notes (EXEMPT render_plain)",
    f"{JOBS}/demand/review.py": "the review's own report to SMD, not a firm document",
    f"{JOBS}/drafting/brief_base.py": "the firm-base plan: fills the base the resolver chose",
    f"{JOBS}/drafting/memo_base.py": "the firm-base plan for a memo: fills the base the resolver chose",
    f"{JOBS}/drafting/pleading.py": "edits the rendered file in place (pleading paper)",
    f"{JOBS}/drafting/frontpage.py": "edits the rendered file in place (front-page spacing)",
    f"{JOBS}/drafting/briefpage.py": "edits the rendered file in place (brief page rules)",
    f"{JOBS}/drafting/revise/inplace.py": "a revision, edited in the filed document itself",
    f"{JOBS}/firmbase/adopt.py": "makes a firm base from a firm document (setup, a later piece); files nothing",
    f"{JOBS}/library_setup/make.py": (
        "makes the firm's library file from a document the administrator chose (drops its custom XML); "
        "it is the file later drafts are made in, never a document made from one"
    ),
    f"{JOBS}/firmbase/base.py": "repackages a firm base's document part",
    f"{JOBS}/stages/render.py": "a record product (the chronology), which reads no library file",
    f"{JOBS}/stages/billing_docx.py": "a record product (the billing summary)",
    f"{JOBS}/stages/review.py": "the chronology review's report",
    f"{JOBS}/medchron/update/docx_md.py": "a record product (the chronology update)",
    f"{JOBS}/medchron/update/review_list.py": "a record product (the chronology update's review list)",
}


# ---- reading the source -------------------------------------------------------------------


def _rel(path: Path) -> str:
    return str(path.relative_to(OP))


def _files() -> dict[str, Path]:
    """Every source module, by dotted name."""
    out: dict[str, Path] = {}
    for pkg, root in TREES.items():
        for p in root.rglob("*.py"):
            parts = list(p.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            out[".".join([pkg, *parts])] = p
    return out


def _package(dotted: str, path: Path) -> str:
    return dotted if path.name == "__init__.py" else dotted.rsplit(".", 1)[0]


def _absolute(pkg: str, module: str | None, level: int) -> str:
    if not level:
        return module or ""
    base = pkg.split(".")[: len(pkg.split(".")) - level + 1]
    return ".".join([*base, *([module] if module else [])])


def _imports(tree: ast.Module, pkg: str, modules: dict[str, Path]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(local alias -> module, local name -> (module, name)) for this file."""
    mods, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods[a.asname or a.name.split(".")[-1]] = a.name
        elif isinstance(node, ast.ImportFrom):
            src = _absolute(pkg, node.module, node.level)
            for a in node.names:
                full = f"{src}.{a.name}"
                if full in modules:
                    mods[a.asname or a.name] = full
                else:
                    names[a.asname or a.name] = (src, a.name)
    return mods, names


class _Defs(ast.NodeVisitor):
    """Each top-level function or method, and the calls inside it (lambdas included)."""

    def __init__(self) -> None:
        self.calls: dict[str, list[ast.Call]] = {}
        self.args: dict[str, list[str]] = {}
        self._stack: list[str] = []

    def _def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = self._stack[0] if self._stack else node.name
        if not self._stack:
            self.args[name] = [a.arg for a in [*node.args.args, *node.args.kwonlyargs]]
        self._stack.append(name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _def

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for item in node.body:
            self.visit(item)

    def visit_Call(self, node: ast.Call) -> None:
        if self._stack:
            self.calls.setdefault(self._stack[0], []).append(node)
        self.generic_visit(node)


def _graph() -> tuple[dict[tuple[str, str], set[tuple[str, str]]], dict[tuple[str, str], list[str]]]:
    """callee -> its callers, each as (repo path, function); and each function's parameters."""
    modules = _files()
    by_mod = {d: _rel(p) for d, p in modules.items()}
    callers: dict[tuple[str, str], set[tuple[str, str]]] = {}
    params: dict[tuple[str, str], list[str]] = {}
    for dotted, path in modules.items():
        tree = ast.parse(path.read_text("utf-8"))
        mods, names = _imports(tree, _package(dotted, path), modules)
        defs = _Defs()
        defs.visit(tree)
        here = _rel(path)
        for fn, args in defs.args.items():
            params[(here, fn)] = args
        for fn, calls in defs.calls.items():
            for call in calls:
                f = call.func
                target = None
                if isinstance(f, ast.Name):
                    if f.id in names and names[f.id][0] in by_mod:
                        target = (by_mod[names[f.id][0]], names[f.id][1])
                    elif f.id in defs.args:
                        target = (here, f.id)
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
                    if mods[f.value.id] in by_mod:
                        target = (by_mod[mods[f.value.id]], f.attr)
                if target is not None:
                    callers.setdefault(target, set()).add((here, fn))
    return callers, params


@pytest.fixture(scope="module")
def graph():
    return _graph()


# ---- guard 1: through the resolver ------------------------------------------------------


def test_every_producing_path_in_the_registry_has_a_caller_and_an_adapter():
    kinds = yaml.safe_load(REGISTRY.read_text("utf-8"))["kinds"]
    paths = {p for k in kinds for p in k["produced_by"]}
    assert paths == set(ADAPTERS) == set(CALLERS), (
        f"registry paths {sorted(paths)}: a new producing path needs its adapter and caller here"
    )


def test_each_caller_asks_its_paths_adapter(graph):
    callers, _params = graph
    for path, (adapter_file, adapter) in ADAPTERS.items():
        asked = callers.get((adapter_file, adapter), set())
        for caller in CALLERS[path]:
            if caller[1] == "_build_deliverables":
                continue  # renders on what _select_format asked; the chain test below checks the hop
            assert caller in asked, f"{caller} makes {path} documents and does not call {adapter}"


def test_only_the_three_adapters_import_the_resolver():
    importers = []
    for dotted, path in _files().items():
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("operator_connector_sdk.library"):
                if any(a.name == "resolve" for a in node.names):
                    importers.append(_rel(path))
    assert sorted(importers) == sorted(f for f, _ in ADAPTERS.values())


#: Each caller hands the renderer exactly what its adapter answered: (file,
#: function, the renderer's call, the argument text that must carry the answer).
HANDS_ON = [
    (f"{CONNECTOR}/server.py", "_render_with_format", "render_document", "base"),
    (f"{JOBS}/drafting/finish.py", "_render_unit", "lay_out", "made_in.held"),
    (f"{JOBS}/drafting/finish.py", "_render_unit", "lay_out", "styles=made_in.styles"),
    (f"{JOBS}/drafting/placing.py", "_place_unit", "render", "made_in.styles"),
    (f"{JOBS}/demand/run.py", "_build_deliverables", "render_demand", "self._reference"),
    (f"{JOBS}/demand/run.py", "_select_format", "select", "self"),
]


@pytest.mark.parametrize(
    "where,fn,callee,arg", HANDS_ON, ids=[f"{w.rsplit('/', 1)[1]}:{f}:{a}" for w, f, _c, a in HANDS_ON]
)
def test_each_caller_hands_the_renderer_the_adapters_answer(where, fn, callee, arg):
    tree = ast.parse((OP / where).read_text("utf-8"))
    defs = _Defs()
    defs.visit(tree)
    calls = [c for c in defs.calls.get(fn, []) if getattr(c.func, "attr", getattr(c.func, "id", "")) == callee]
    assert calls, f"{where}:{fn} no longer calls {callee}"
    texts = [ast.unparse(a) for c in calls for a in c.args] + [
        f"{k.arg}={ast.unparse(k.value)}" for c in calls for k in c.keywords
    ]
    assert arg in texts, f"{where}:{fn} calls {callee} without {arg!r}: {texts}"


def _ends(graph, start: tuple[str, str], seen: set) -> list[tuple[str, str]]:
    """Every chain end above ``start`` that is not a caller or an exemption."""
    callers, params = graph
    out = []
    for c in sorted(callers.get(start, set())):
        if c in seen:
            continue
        seen.add(c)
        if c in {x for v in CALLERS.values() for x in v} or c in EXEMPT:
            continue
        if c in THROUGH:
            assert THROUGH[c] in params.get(c, []), f"{c} passes on the answer but has no {THROUGH[c]!r} parameter"
            out += _ends(graph, c, seen)
        elif c in ENTRY_POINTS or c[0].endswith(("docx_format.py", "house.py", "house_numbered.py")):
            out += _ends(graph, c, seen)  # inside a renderer: its own callers decide
        else:
            out.append(c)
    return out


def test_every_word_render_reaches_the_resolver_or_names_its_exemption(graph):
    stray = sorted({end for entry in ENTRY_POINTS for end in _ends(graph, entry, set())})
    assert stray == [], (
        "these functions render a Word document and neither ask the resolver nor are exempt; "
        f"route them through a path's adapter or add an exemption that names its reason: {stray}"
    )


def test_a_new_lane_that_renders_without_the_resolver_fails(graph):
    """The falsifier: one more function calling the renderer, as a new lane would."""
    callers, params = graph
    lane = (f"{JOBS}/new_lane.py", "render_a_letter")
    render_document = (f"{CONNECTOR}/docx_format.py", "render_document")
    widened = ({**callers, render_document: callers[render_document] | {lane}}, params)
    assert _ends(widened, render_document, set()) == [lane]
    through = (f"{JOBS}/drafting/render.py", "render")
    widened = ({**callers, through: callers[through] | {lane}}, params)
    assert _ends(widened, render_document, set()) == [lane]


def test_the_lists_name_only_what_the_code_has(graph):
    callers, params = graph
    every = set(params)
    for name, group in (("ENTRY_POINTS", ENTRY_POINTS), ("THROUGH", set(THROUGH)), ("EXEMPT", set(EXEMPT))):
        assert group <= every, f"{name} names functions the code does not have: {sorted(group - every)}"
    reached = {c for entry in ENTRY_POINTS for c in callers.get(entry, set())}
    assert set(EXEMPT) <= reached | {c for t in THROUGH for c in callers.get(t, set())}, (
        "an exemption no render reaches"
    )


def test_every_module_that_writes_word_bytes_is_named():
    writers = set()
    for path in _files().values():
        src = path.read_text("utf-8")
        tree = ast.parse(src)
        uses_docx = any(
            (isinstance(n, ast.Import) and any(a.name == "docx" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "docx")
            for n in ast.walk(tree)
        )
        zip_write = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", "")) == "ZipFile"
            and any(isinstance(a, ast.Constant) and a.value == "w" for a in [*n.args, *[k.value for k in n.keywords]])
            for n in ast.walk(tree)
        )
        if (uses_docx and ".save(" in src) or zip_write:
            writers.add(_rel(path))
    assert writers == set(WORD_WRITERS), (
        f"unnamed Word writers {sorted(writers - set(WORD_WRITERS))}; stale names {sorted(set(WORD_WRITERS) - writers)}"
    )


# ---- guard 2: from what it returned -------------------------------------------------------


def started_from(opened: bytes | None, record: dict) -> list[str]:
    """Why the bytes a caller opened are not what its record says it used."""
    res, base = record["resolution"], record["base"]
    got = hashlib.sha256(opened).hexdigest() if opened is not None else None
    if res["status"] == "resolved" and got != res["file"]["sha256"]:
        return [f"resolved to {res['file']['sha256']} and opened {got}"]
    if base["sha256"] != got:
        return [f"the record names {base['source']} {base['sha256']} and the caller opened {got}"]
    return []


def _styles_file(header: str) -> bytes:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = header
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def spec(tmp_path, monkeypatch) -> Path:
    from jobs.drafting import firm_rules
    from smokeball_connector import library_pick as chat

    d = tmp_path / "specs"
    d.mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(d))
    monkeypatch.setenv(chat.SPEC_DIR_ENV, str(d))
    return d


def _chat(monkeypatch, tmp_path) -> tuple[bytes | None, dict]:
    from smokeball_connector import docx_format, library, server

    seen: list[bytes | None] = []
    real = docx_format.render_document
    monkeypatch.setattr(
        docx_format, "render_document", lambda md, c, b, *a, **k: seen.append(b) or real(md, c, b, *a, **k)
    )
    monkeypatch.setenv(library.CUSTOMER_YAML_ENV, str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(server, "_get_client", lambda: SimpleNamespace())
    _data, _fmt, refusal, record = server._render_with_format("# Heading\n\nBody.", "memo")
    assert refusal is None
    return seen[0], record


def _drafting(monkeypatch, tmp_path) -> tuple[bytes | None, dict]:
    from smokeball_connector import docx_format

    from jobs.drafting import library_pick, render

    run = SimpleNamespace(
        cls="memo", data=tmp_path / "data", seat=None, firm=SimpleNamespace(firm_base=lambda _c: None)
    )
    run.data.mkdir()
    made_in = library_pick.pick_base(run)
    seen: list[bytes | None] = []
    real = docx_format.render_document
    monkeypatch.setattr(
        docx_format, "render_document", lambda md, c, b, *a, **k: seen.append(b) or real(md, c, b, *a, **k)
    )
    from drafting_testkit import firm_data

    render.render("# I. Heading\n\nBody.", "memo", tmp_path / "out.docx", firm_data()["format"], made_in.styles)
    return seen[0], lt.record(run.data)


@pytest.mark.parametrize("caller", [_chat, _drafting], ids=["chat_tool", "drafting_job"])
@pytest.mark.parametrize("accepted", [False, True], ids=["none_accepted", "resolved"])
def test_a_caller_starts_from_what_the_resolver_returned(caller, accepted, spec, tmp_path, monkeypatch):
    if accepted:
        from jobs.drafting import library_pick as drafting
        from smokeball_connector import library_pick as chat

        lib = MemoryLibrary()
        lt.install(spec, lt.entry(lib, "acc-1", "memo", _styles_file("ACCEPTED"), contract=STYLES))
        monkeypatch.setattr(chat, "open_store", lambda *_a: lib)
        monkeypatch.setattr(drafting, "open_store", lambda *_a: lib)
    opened, record = caller(monkeypatch, tmp_path)
    assert record["resolution"]["status"] == ("resolved" if accepted else "none_accepted")
    assert started_from(opened, record) == []


def test_the_demand_job_starts_from_what_the_resolver_returned(spec, tmp_path, monkeypatch):
    """The demand's full run is in ``test_demand_library_pick.py``; here, the
    reference the adapter hands the writer against the record, both answers."""
    from demand_testkit import make_inputs
    from jobs.demand import firm as firm_mod, library_pick

    firm = firm_mod.load(make_inputs(tmp_path / "inputs"))
    firm = firm.select("pre_suit", firm.attorney_for("Example Lawyer"))
    data = tmp_path / "data"
    data.mkdir()
    (data / "facts.json").write_text(json.dumps({"responsible_attorney": "Example Lawyer"}))
    run = SimpleNamespace(
        firm=firm, data=data, seat=None, _is_done=lambda _s: False, _json=lambda n: json.loads((data / n).read_text())
    )
    for accepted in (False, True):
        if accepted:
            lib = MemoryLibrary()
            lt.install(spec, lt.entry(lib, "acc-1", "demand_letter.pre_suit", _styles_file("X"), contract="prototypes"))
            monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
        path = library_pick.pick_reference(run)
        assert started_from(path.read_bytes(), lt.record(data)) == []


def test_the_guard_fails_on_a_caller_that_substitutes_another_file(spec, tmp_path, monkeypatch):
    """The falsifier: the record says the accepted file, the caller opened the starter."""
    from smokeball_connector import library_pick as chat

    lib = MemoryLibrary()
    lt.install(spec, lt.entry(lib, "acc-1", "memo", _styles_file("ACCEPTED"), contract=STYLES))
    monkeypatch.setattr(chat, "open_store", lambda *_a: lib)
    _opened, record = _chat(monkeypatch, tmp_path)
    assert started_from(None, record) != []
    assert started_from(_styles_file("SOMETHING ELSE"), record) != []
    nothing = dict(
        record,
        resolution={**record["resolution"], "status": "none_accepted"},
        base={**record["base"], "source": "starter", "sha256": None},
    )
    assert started_from(_styles_file("A FILE"), nothing) != []
