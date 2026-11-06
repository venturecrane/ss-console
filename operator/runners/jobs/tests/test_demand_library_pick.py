"""The demand job asks the library resolver which reference file a demand is
written in (design 2026-11-06, piece 2; ``jobs/demand/library_pick.py``).

NOTHING CHANGES WITH NOTHING ACCEPTED (every seat today): a whole job is run
through the resolver and again with the reference chosen the way origin/main
chooses it (the pinned reference of the variant and attorney: ``_as_on_main``),
and the filed demands are compared member by member, after the lane's own
format check passed on both. THEN: an accepted prototypes file is the
reference the same writer clones; a variant and an attorney each have their
own slot; every other answer fails the job for SMD before anything is spent.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
from pathlib import Path

import docx
import pytest

import library_testkit as lt
from demand_testkit import ScriptedClient, make_inputs, make_job, make_reference, seat_with, standard_docs
from jobs_testkit import PRICING
from operator_connector_sdk.library import PROTOTYPES, STYLES, MemoryLibrary
from operator_connector_sdk.library.caller import record_problems
from jobs.demand import house, library_pick
from jobs.demand.run import DemandRun
from jobs.drafting import firm_rules

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
PRE_SUIT = "demand_letter.pre_suit"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    (tmp_path / "specs").mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))


@pytest.fixture
def spec(tmp_path) -> Path:
    return tmp_path / "specs"


@pytest.fixture
def opened(monkeypatch) -> list[bytes]:
    """Every reference the writer cloned."""
    seen: list[bytes] = []
    real = house.HouseWriter.__init__

    def spy(self, reference, *a, **k):
        seen.append(Path(reference).read_bytes())
        real(self, reference, *a, **k)

    monkeypatch.setattr(house.HouseWriter, "__init__", spy)
    return seen


def _as_on_main(run):
    """The reference origin/main writes in: the pinned one."""
    return run.firm.input_path("house_reference")


def _run(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    pricing = root / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs")
    client, log = ScriptedClient(), []
    r = DemandRun(
        make_job(root / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=log.append,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run(), log, client


def _demand(r: DemandRun) -> bytes:
    manifest = json.loads((r.data / "out" / "demand" / "upload_manifest.json").read_text())
    return Path(next(m["local_path"] for m in manifest if m["role"] == "demand")).read_bytes()


def _reference(header: str) -> bytes:
    """A prototypes file like the pinned one, with a header of its own."""
    out = io.BytesIO()
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "ref.docx"
        make_reference(p)
        doc = docx.Document(str(p))
        doc.sections[0].header.paragraphs[0].text = header
        doc.save(out)
    return out.getvalue()


def _accept(monkeypatch, spec: Path, data: bytes, kind: str = PRE_SUIT, **kw) -> MemoryLibrary:
    lib = MemoryLibrary()
    lt.install(spec, lt.entry(lib, "acc-1", kind, data, contract=kw.pop("contract", PROTOTYPES), **kw))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    return lib


# ---- nothing accepted: the same demand ---------------------------------------------------


@pytest.mark.parametrize("state", lt.STATES)
def test_with_nothing_accepted_the_demand_is_written_in_the_pinned_reference_as_today(
    state, spec, tmp_path, monkeypatch, opened
):
    lt.state(spec, state)
    r, v, log, _ = _run(tmp_path / "new")
    assert v.outcome == "delivered", (v.reason, log[-5:])
    pinned = r.firm.input_path("house_reference").read_bytes()
    assert opened == [pinned]
    rec = lt.record(r.data)
    assert record_problems(rec) == [] and rec["path"] == "demand_job" and rec["kind"] == PRE_SUIT
    assert rec["resolution"]["status"] == "none_accepted"
    assert rec["resolution"]["signer"] == {"key": "example lawyer", "name": "Example A. Lawyer", "source": "matter"}
    assert rec["base"] == {
        "source": "pinned_firm_input",
        "sha256": hashlib.sha256(pinned).hexdigest(),
        "name": "house-reference.docx",
        "contract": "prototypes",
    }
    monkeypatch.setattr(library_pick, "pick_reference", _as_on_main)
    old, v2, _, _ = _run(tmp_path / "old")
    assert v2.outcome == "delivered"
    assert lt.members(_demand(r)) == lt.members(_demand(old))


# ---- an accepted file is the reference ---------------------------------------------------


def test_an_accepted_prototypes_file_is_the_reference_the_writer_clones(spec, tmp_path, monkeypatch, opened):
    accepted = _reference("ACCEPTED DEMAND FILE")
    _accept(monkeypatch, spec, accepted)
    r, v, log, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered", (v.reason, log[-5:])
    assert opened == [accepted]
    assert docx.Document(io.BytesIO(_demand(r))).sections[0].header.paragraphs[0].text == "ACCEPTED DEMAND FILE"
    rec = lt.record(r.data)
    assert rec["base"]["sha256"] == hashlib.sha256(accepted).hexdigest() == rec["resolution"]["file"]["sha256"]
    assert rec["overridden"]["source"] == "pinned_firm_input"  # the pin still exists; the accepted file won


def test_the_responsible_attorneys_own_file_is_used_over_the_firms(spec, tmp_path, monkeypatch, opened):
    lib = MemoryLibrary()
    firm_file, own = _reference("FIRM FILE"), _reference("OWN FILE")
    lt.install(
        spec,
        lt.entry(lib, "firm-1", PRE_SUIT, firm_file, contract=PROTOTYPES),
        lt.entry(lib, "own-1", PRE_SUIT, own, attorney="example lawyer", contract=PROTOTYPES),
    )
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    r, v, _, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered" and opened == [own]
    assert lt.record(r.data)["resolution"]["file"]["scope"] == "attorney"


def test_another_variants_file_is_not_this_variants(spec, tmp_path, monkeypatch, opened):
    _accept(monkeypatch, spec, _reference("LITIGATION FILE"), kind="demand_letter.litigation")
    r, v, _, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered"
    assert lt.record(r.data)["resolution"]["status"] == "none_accepted"
    assert opened == [r.firm.input_path("house_reference").read_bytes()]


# ---- every other answer fails the job before anything is spent ---------------------------


def _stopped(r, v, client, status: str) -> None:
    assert v.outcome == "failed" and "the firm's document library cannot give this demand's file" in v.reason
    assert status in v.reason and v.reason.endswith("Nothing was spent.")
    assert client.calls == []
    rec = lt.record(r.data)
    assert rec["resolution"]["status"] == status and rec["base"]["source"] == "not_started"


def test_an_accepted_file_whose_bytes_changed_fails_the_job(spec, tmp_path, monkeypatch, opened):
    lib = _accept(monkeypatch, spec, _reference("A"))
    lib.tamper("acc-1", "acc-1-v1", _reference("B"))
    r, v, _, client = _run(tmp_path / "job")
    _stopped(r, v, client, "integrity_failure")
    assert opened == []


def test_a_seat_that_cannot_read_the_library_fails_the_job_never_the_pin(spec, tmp_path, opened):
    lt.install(spec, lt.entry(MemoryLibrary(), "acc-1", PRE_SUIT, b"x", contract=PROTOTYPES))
    r, v, _, client = _run(tmp_path / "job")
    _stopped(r, v, client, "cannot_look")


def test_a_styles_file_is_not_a_demand_reference(spec, tmp_path, monkeypatch):
    _accept(monkeypatch, spec, _reference("A"), contract=STYLES)
    r, v, _, client = _run(tmp_path / "job")
    _stopped(r, v, client, "refused")
