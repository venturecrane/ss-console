"""The accepted-file ledger: its format, its validator, the pure acceptance, and
the installed reader that never reads "cannot look" as "none"."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from operator_connector_sdk.library import ledger as L

SHA_A = hashlib.sha256(b"a").hexdigest()
SHA_B = hashlib.sha256(b"b").hexdigest()


def _acc(**kw) -> L.Entry:
    base = dict(
        kind="mediation_brief",
        attorney=None,
        file_id="f-1",
        version="f-1-v1",
        sha256=SHA_A,
        contract=L.STYLES,
        accepted_by="admin@firm.example",
        accepted_at="2026-11-06T17:00:00Z",
    )
    base.update(kw)
    return L.Entry(**base)


def test_accept_into_an_empty_ledger_places_the_entry_with_nothing_replaced() -> None:
    out = L.accept(L.Ledger(), _acc())
    assert out.entries == (_acc(),)
    assert out.superseded == ()
    assert out.entry_for("mediation_brief", None) == _acc()


def test_accept_is_pure_and_records_what_it_replaced() -> None:
    first = L.accept(L.Ledger(), _acc())
    second = L.accept(first, _acc(file_id="f-2", version="f-2-v3", sha256=SHA_B, contract=L.MARKED))
    assert first.entries == (_acc(),), "the input ledger is never changed"
    entry = second.entry_for("mediation_brief", None)
    assert entry is not None and entry.file_id == "f-2"
    assert entry.replaces == L.Replaced("f-1", "f-1-v1", SHA_A)
    assert second.superseded == (_acc(),)


def test_an_attorney_slot_is_separate_from_the_firm_slot() -> None:
    led = L.accept(L.accept(L.Ledger(), _acc()), _acc(attorney="atty-dana", file_id="f-9"))
    assert led.entry_for("mediation_brief", None).file_id == "f-1"  # type: ignore[union-attr]
    assert led.entry_for("mediation_brief", "atty-dana").file_id == "f-9"  # type: ignore[union-attr]
    assert led.superseded == ()


def test_accepting_the_same_bytes_again_changes_nothing() -> None:
    led = L.accept(L.Ledger(), _acc())
    assert L.accept(led, _acc(accepted_at="2026-11-07T09:00:00Z")) is led


@pytest.mark.parametrize(
    "bad",
    [
        {"sha256": "ABC"},
        {"contract": "pinned"},
        {"accepted_at": "yesterday"},
        {"accepted_by": " "},
        {"file_id": ""},
        {"attorney": ""},
        {"replaces": L.Replaced("f-0", "v", SHA_A)},
    ],
)
def test_an_invalid_acceptance_is_refused_and_nothing_changes(bad) -> None:
    led = L.accept(L.Ledger(), _acc())
    with pytest.raises(L.AcceptanceInvalid):
        L.accept(led, _acc(**bad))


def test_dump_and_parse_round_trip_exactly() -> None:
    led = L.accept(L.accept(L.Ledger(), _acc()), _acc(file_id="f-2", sha256=SHA_B))
    blob = L.dump_ledger(led)
    assert L.parse_ledger(blob) == led
    assert L.dump_ledger(L.parse_ledger(blob)) == blob, "deterministic bytes, so root can digest them"


def _doc(**top) -> dict:
    doc = json.loads(L.dump_ledger(L.accept(L.Ledger(), _acc())))
    doc.update(top)
    return doc


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"schema_version": 2, "entries": [], "superseded": []},
        {"schema_version": 1, "entries": []},
        {"schema_version": 1, "entries": [], "superseded": [], "extra": 1},
        {"schema_version": 1, "entries": "no", "superseded": []},
    ],
)
def test_validator_names_a_malformed_top_level(doc) -> None:
    assert L.validate_ledger(doc)


def test_validator_refuses_two_files_in_one_slot() -> None:
    doc = _doc()
    doc["entries"].append(dict(doc["entries"][0], file_id="f-2"))
    assert any("two accepted files" in p for p in L.validate_ledger(doc))


def test_validator_refuses_an_unknown_entry_key() -> None:
    doc = _doc()
    doc["entries"][0]["reader"] = "adjuster"
    assert L.validate_ledger(doc)


def test_parse_refuses_bytes_that_are_not_json() -> None:
    with pytest.raises(L.LedgerInvalid):
        L.parse_ledger(b"\xff not json")


# ---- the installed ledger -----------------------------------------------------------


def _install(tmp_path, blob: bytes | None, *, manifest: object = "auto") -> None:
    if blob is not None:
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / L.LEDGER_REL).write_bytes(blob)
    if manifest == "auto":
        manifest = {L.MANIFEST_KEY: {L.LEDGER_REL: {"sha256": hashlib.sha256(blob or b"").hexdigest()}}}
    if manifest is not None:
        (tmp_path / L.MANIFEST).write_text(manifest if isinstance(manifest, str) else json.dumps(manifest))


def test_no_store_at_all_is_empty() -> None:
    read = L.read_installed(Path("/nonexistent-spec-dir-for-test"))
    assert read.status == L.EMPTY and read.ledger == L.Ledger()


def test_a_manifest_that_names_no_ledger_and_no_file_is_empty(tmp_path) -> None:
    _install(tmp_path, None, manifest={"specs": {}})
    assert L.read_installed(tmp_path).status == L.EMPTY


def test_a_vouched_ledger_reads_ok(tmp_path) -> None:
    led = L.accept(L.Ledger(), _acc())
    _install(tmp_path, L.dump_ledger(led))
    read = L.read_installed(tmp_path)
    assert read.status == L.OK and read.ledger == led


@pytest.mark.parametrize(
    "case",
    ["no_manifest", "unvouched", "torn", "gone", "invalid", "manifest_garbage"],
)
def test_every_broken_store_is_unusable_never_empty(tmp_path, case) -> None:
    good = L.dump_ledger(L.accept(L.Ledger(), _acc()))
    if case == "no_manifest":
        _install(tmp_path, good, manifest=None)
    elif case == "unvouched":
        _install(tmp_path, good, manifest={"specs": {}})
    elif case == "torn":
        _install(tmp_path, good, manifest={L.MANIFEST_KEY: {L.LEDGER_REL: {"sha256": SHA_B}}})
    elif case == "gone":
        _install(tmp_path, None, manifest={L.MANIFEST_KEY: {L.LEDGER_REL: {"sha256": SHA_A}}})
    elif case == "invalid":
        _install(tmp_path, b'{"schema_version": 1}')
    else:
        _install(tmp_path, good, manifest="{not json")
    read = L.read_installed(tmp_path)
    assert read.status == L.UNUSABLE
    assert read.ledger is None, "an unusable read carries no ledger to be mistaken for an empty one"
    assert read.reason


# ---- a memo's chart source (the coordinator's decision on #3876) --------------------------

CHART = {"from": "firm_memo", "file_id": "d-2", "sha256": SHA_B}


def test_a_chart_source_is_optional_and_a_ledger_without_one_dumps_as_before() -> None:
    plain = L.accept(L.Ledger(), _acc())
    raw = L.dump_ledger(plain)
    assert b"chart_source" not in raw and L.parse_ledger(raw) == plain
    memo = L.accept(L.Ledger(), _acc(kind="memo", chart_source=CHART))
    back = L.parse_ledger(L.dump_ledger(memo))
    assert back.entry_for("memo", None).chart_source == CHART
    body = L.accept(L.Ledger(), _acc(kind="memo", chart_source={"from": "body_look", "file_id": "", "sha256": ""}))
    assert L.parse_ledger(L.dump_ledger(body)).entry_for("memo", None).chart_source["from"] == "body_look"


@pytest.mark.parametrize(
    "bad",
    [
        {"from": "firm_memo", "file_id": "", "sha256": SHA_B},
        {"from": "firm_memo", "file_id": "d-2", "sha256": "nope"},
        {"from": "invented", "file_id": "", "sha256": ""},
        {"from": "body_look"},
        "body_look",
    ],
)
def test_a_chart_source_that_does_not_say_where_is_refused(bad) -> None:
    with pytest.raises(L.AcceptanceInvalid):
        L.accept(L.Ledger(), _acc(kind="memo", chart_source=bad))


def test_the_same_file_with_another_chart_source_is_a_new_acceptance() -> None:
    first = L.accept(L.Ledger(), _acc(kind="memo", chart_source=None))
    again = L.accept(first, _acc(kind="memo", chart_source=CHART))
    assert again != first and again.entry_for("memo", None).chart_source == CHART


def test_the_resolver_carries_the_chart_source_into_the_record() -> None:
    from operator_connector_sdk.library import LedgerRead, MemoryLibrary, resolve

    lib = MemoryLibrary()
    version = lib.add("f-1", "Template - Memo.docx", "memo", b"a")
    ledger = L.accept(L.Ledger(), _acc(kind="memo", version=version, sha256=SHA_A, chart_source=CHART))
    res = resolve(lib, LedgerRead("ok", ledger), "memo", matter_attorney=None)
    assert res.status == "resolved" and res.to_json()["file"]["chart_source"] == CHART
