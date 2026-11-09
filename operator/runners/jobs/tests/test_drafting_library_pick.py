"""The drafting job asks the library resolver which Word file a document is
made in (design 2026-11-06, piece 2; ``jobs/drafting/library_pick.py``).

NOTHING CHANGES WITH NOTHING ACCEPTED (every seat today). A whole job is run
twice on the same made-up inputs: once through the resolver, once with the
choice made the way origin/main makes it (the pinned firm base, else the
starter: ``_as_on_main``). The filed documents are compared member by member
(a .docx's zip headers carry the save time). Both today's shapes are run: a
brief on SMD's starter and a brief in a pinned firm base.

THEN: an accepted styles file is the renderer's base, an accepted marked file
is the firm base the class's plan fills, the accepted file wins over a pin,
and every other answer stops the job for SMD before anything is filed.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest

import library_testkit as lt
from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from operator_connector_sdk.library import MARKED, PROTOTYPES, MemoryLibrary, Resolution
from operator_connector_sdk.library.caller import record_problems
from jobs.drafting import brief_base, firm_rules, library_pick
from jobs.drafting.outcome import DraftingFailed
from jobs.drafting.run import DraftingRun
from test_drafting_brief_base import _job_brief, _with_base, accepted, tmpl  # noqa: F401 - pytest fixtures used by name in the tests below

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
CLS = "mediation_brief"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    spec = tmp_path / "specs"
    spec.mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(spec))


@pytest.fixture
def spec(tmp_path) -> Path:
    return tmp_path / "specs"


@pytest.fixture
def opened(monkeypatch) -> list[bytes | None]:
    """Every base the starter renderer opened."""
    from smokeball_connector import docx_format

    seen: list[bytes | None] = []
    real = docx_format.render_document

    def spy(md, cls, base, *a, **k):
        seen.append(base)
        return real(md, cls, base, *a, **k)

    monkeypatch.setattr(docx_format, "render_document", spy)
    return seen


@pytest.fixture
def laid(monkeypatch) -> list[bytes]:
    """Every firm base the class's plan filled."""
    seen: list[bytes] = []
    real = brief_base.load_base

    def spy(path, source, cls=brief_base.CLS):
        seen.append(Path(path).read_bytes())
        return real(path, source, cls)

    monkeypatch.setattr(brief_base, "load_base", spy)
    return seen


def _as_on_main(run, *, marked_ok: bool = True) -> library_pick.DraftBase:
    """The base origin/main chooses: the pinned firm base, else the starter."""
    return library_pick.DraftBase(
        Resolution("none_accepted", run.cls), held=run.firm.firm_base(run.cls) if marked_ok else None
    )


def _run(
    root: Path, *, base: bytes | None = None, draft: str | None = None, seat=None
) -> tuple[DraftingRun, object, list[str]]:
    pricing = root / "pricing.json"
    root.mkdir(parents=True, exist_ok=True)
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs")
    if base is not None:
        _with_base(inputs, base)
    log: list[str] = []
    r = DraftingRun(
        make_job(root / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat or seat_with(standard_docs()),
        client=ScriptedClient(draft=draft) if draft else ScriptedClient(),
        log=log.append,
        readback_pause=0.0,
    )
    return r, r.run(), log


def _filed(r: DraftingRun) -> bytes:
    return Path(json.loads((r.data / "rendered.json").read_text())["path"]).read_bytes()


# ---- nothing accepted: the same document -------------------------------------------------


@pytest.mark.parametrize("state", lt.STATES)
def test_with_nothing_accepted_the_brief_on_the_starter_is_the_same(state, spec, tmp_path, monkeypatch, opened):
    lt.state(spec, state)
    r, v, log = _run(tmp_path / "new")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    rec = lt.record(r.data)
    assert record_problems(rec) == [] and rec["path"] == "drafting_job"
    assert rec["resolution"]["status"] == "none_accepted"
    assert rec["base"] == {"source": "starter", "sha256": None, "name": "", "contract": ""}
    assert opened == [None]
    monkeypatch.setattr(library_pick, "pick_base", _as_on_main)
    old, v2, _ = _run(tmp_path / "old")
    assert v2.outcome == "delivered"
    assert lt.members(_filed(r)) == lt.members(_filed(old))


@pytest.mark.parametrize("state", lt.STATES)
def test_with_nothing_accepted_the_brief_in_the_pinned_firm_file_is_the_same(
    state, spec, tmp_path, monkeypatch, tmpl, laid
):
    lt.state(spec, state)
    r, v, log = _run(tmp_path / "new", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    rec = lt.record(r.data)
    assert rec["base"]["source"] == "pinned_firm_input"
    assert rec["base"]["sha256"] == hashlib.sha256(tmpl.bytes).hexdigest()
    assert laid == [tmpl.bytes]
    monkeypatch.setattr(library_pick, "pick_base", _as_on_main)
    old, v2, _ = _run(tmp_path / "old", base=tmpl.bytes, draft=_job_brief())
    assert v2.outcome == "delivered"
    assert lt.members(_filed(r)) == lt.members(_filed(old))


# ---- an accepted file is what the document is made in ------------------------------------


def _styles_file(header: str) -> bytes:
    """A firm's styles file: page setup, a header, Word's own named styles."""
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = header
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _accept(monkeypatch, spec: Path, data: bytes, **kw) -> MemoryLibrary:
    lib = MemoryLibrary()
    lt.install(spec, lt.entry(lib, "acc-1", CLS, data, **kw))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    return lib


def test_an_accepted_styles_file_is_the_renderers_base(spec, tmp_path, monkeypatch, opened):
    accepted_file = _styles_file("ACCEPTED EXAMPLE LAW")
    _accept(monkeypatch, spec, accepted_file)
    r, v, log = _run(tmp_path / "job")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert opened == [accepted_file]
    filed = docx.Document(io.BytesIO(_filed(r)))
    assert filed.sections[0].header.paragraphs[0].text == "ACCEPTED EXAMPLE LAW"
    rec = lt.record(r.data)
    assert rec["resolution"]["status"] == "resolved"
    assert rec["base"]["sha256"] == hashlib.sha256(accepted_file).hexdigest() == rec["resolution"]["file"]["sha256"]


def test_an_accepted_styles_file_wins_over_a_pinned_base_and_says_so(spec, tmp_path, monkeypatch, tmpl, opened, laid):
    accepted_file = _styles_file("ACCEPTED EXAMPLE LAW")
    _accept(monkeypatch, spec, accepted_file)
    r, v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    rec = lt.record(r.data)
    assert laid == [] and opened == [accepted_file]
    assert rec["overridden"] == {
        "source": "pinned_firm_input",
        "sha256": hashlib.sha256(tmpl.bytes).hexdigest(),
        "name": "n.docx",
        "contract": "marked",
    }


def test_an_accepted_marked_file_is_the_firm_base_the_plan_fills(spec, tmp_path, monkeypatch, tmpl, laid):
    _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert laid == [tmpl.bytes]
    assert (r.data / library_pick.BASE_NAME).read_bytes() == tmpl.bytes
    rec = lt.record(r.data)
    assert rec["base"]["source"] == "accepted_library_file" and rec["base"]["contract"] == "marked"
    assert rec["overridden"]["source"] == "pinned_firm_input"


# ---- every other answer stops the job ------------------------------------------------------


def _stopped(r: DraftingRun, v, status: str) -> None:
    assert v.outcome == "failed" and v.reason.startswith("firm_base_unfit: the firm's document library"), v.reason
    assert status in v.reason
    rec = lt.record(r.data)
    assert rec["resolution"]["status"] == status and rec["base"]["source"] == "not_started"
    assert not (r.data / "rendered.json").exists()


def test_a_seat_copy_whose_bytes_changed_stops_the_job(spec, tmp_path, monkeypatch, opened):
    accepted_file = _styles_file("A")
    _accept(monkeypatch, spec, accepted_file)
    lt.spoil_copy(spec, accepted_file, _styles_file("B"))
    r, v, _ = _run(tmp_path / "job")
    _stopped(r, v, "integrity_failure")
    assert opened == []


def test_an_accepted_file_the_seat_holds_no_copy_of_stops_the_job(spec, tmp_path, monkeypatch, opened):
    lib = MemoryLibrary()
    lt.install(spec, lt.entry(lib, "acc-1", CLS, _styles_file("A")), copies=False)
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    r, v, _ = _run(tmp_path / "job")
    _stopped(r, v, "copy_missing")
    assert opened == []


def test_the_accepted_bytes_come_from_the_seat_copy_after_the_firm_saves_again(spec, tmp_path, monkeypatch, opened):
    accepted_file = _styles_file("ACCEPTED EXAMPLE LAW")
    lib = _accept(monkeypatch, spec, accepted_file)
    lib.save("acc-1", _styles_file("SAVED AGAIN"))
    r, v, log = _run(tmp_path / "job")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert opened == [accepted_file]
    assert lt.record(r.data)["resolution"]["newer_version"] == {
        "accepted_version": "acc-1-v1",
        "newest_version": "acc-1-v2",
    }


def test_a_seat_that_cannot_read_the_library_stops_the_job_never_the_starter(spec, tmp_path, opened):
    lt.install(spec, lt.entry(MemoryLibrary(), "acc-1", CLS, b"anything"))  # the fake seat has no case-system client
    r, v, _ = _run(tmp_path / "job")
    _stopped(r, v, "cannot_look")
    assert opened == []


def test_a_torn_ledger_stops_the_job(spec, tmp_path):
    lt.install(spec, lt.entry(MemoryLibrary(), "acc-1", CLS, b"anything"))
    (spec / "library" / "accepted.json").write_text("{}")
    r, v, _ = _run(tmp_path / "job")
    _stopped(r, v, "cannot_look")


def test_a_prototypes_file_is_not_a_drafting_base(spec, tmp_path, monkeypatch):
    _accept(monkeypatch, spec, _styles_file("A"), contract=PROTOTYPES)
    r, v, _ = _run(tmp_path / "job")
    _stopped(r, v, "refused")


# ---- the adapter on its own: placing, and one answer per job ---------------------------


def _fake_run(tmp_path: Path, cls: str, held=None) -> SimpleNamespace:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        cls=cls, data=data, seat=None, firm=SimpleNamespace(firm_base=lambda _c: held, styles_base=lambda _c: None)
    )


def test_placing_lays_no_firm_base_today_and_takes_no_marked_file(spec, tmp_path, monkeypatch, tmpl):
    pin = tmp_path / "pin.docx"
    pin.write_bytes(tmpl.bytes)
    run = _fake_run(tmp_path, "discovery_set", held=(pin, {"name": "pin.docx"}))
    out = library_pick.pick_base(run, marked_ok=False)
    assert out.held is None and out.styles is None
    assert lt.record(run.data)["base"]["source"] == "starter"
    lib = MemoryLibrary()
    lt.install(spec, lt.entry(lib, "acc-1", "discovery_set", tmpl.bytes, contract=MARKED))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    with pytest.raises(DraftingFailed, match="firm_base_unfit: .*no plan for a marked file"):
        library_pick.pick_base(_fake_run(tmp_path / "2", "discovery_set"), marked_ok=False)


def test_the_answer_is_asked_once_per_job_run(spec, tmp_path, monkeypatch):
    lib = _accept(monkeypatch, spec, _styles_file("A"))
    run = _fake_run(tmp_path, CLS)
    first = library_pick.pick_base(run)
    lib.save("acc-1", _styles_file("B"))  # saved again between two units
    assert library_pick.pick_base(run) is first


# ---- a known disagreement, pinned until the piece that settles it --------------------------


def test_marker_for_the_layout_rules_piece_firm_rules_still_decides_on_base_from_the_pinned_file(
    spec, tmp_path, monkeypatch, tmpl
):
    """NOT A WISH: A MARKER. The layout-rule code (``firm_rules.start``) decides
    whether a firm's layout rule is laid onto a firm base from the PINNED base
    (``firm.firm_base``), not from the accepted library file this job is made
    in. With a marked file accepted and no pin, the document is made in the
    accepted firm base while the rules are computed as for SMD's starter. The
    design's piece 5 (layout rules applied into the library file) settles it;
    this test FAILS the day firm_rules reads the accepted file, and that PR
    deletes or inverts it."""
    seen: list[bool] = []
    real = firm_rules.for_class

    def spy(rules, cls, on_base, **kw):
        seen.append(on_base)
        return real(rules, cls, on_base, **kw)

    monkeypatch.setattr(firm_rules, "for_class", spy)
    _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    root = tmp_path / "job"
    root.mkdir()
    (root / "pricing.json").write_text(json.dumps(PRICING), encoding="utf-8")
    r = DraftingRun(
        make_job(root / "job"),
        inputs_dir=str(make_inputs(root / "inputs")),
        pricing=str(root / "pricing.json"),
        seat_factory=lambda: seat_with(standard_docs()),
        client=ScriptedClient(),
        log=lambda _m: None,
        readback_pause=0.0,
    )
    assert library_pick.pick_base(r).held is not None  # the document is made in the accepted firm base
    assert seen == [False]  # and the rules were decided as though it were not


def test_the_record_names_where_an_accepted_memos_tables_look_came_from(spec, tmp_path, monkeypatch, tmpl):
    """The coordinator's decision on #3876: the read-back of a memo made in a file
    whose chart look came from the firm's own chart memo names that memo."""
    from dataclasses import replace

    chart = {"from": "firm_memo", "file_id": "d-2", "sha256": "d" * 64}
    lib = MemoryLibrary()
    lt.install(spec, replace(lt.entry(lib, "acc-1", "memo", tmpl.bytes, contract=MARKED), chart_source=chart))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    run = _fake_run(tmp_path, "memo")
    library_pick.pick_base(run)
    assert lt.record(run.data)["resolution"]["file"]["chart_source"] == chart
