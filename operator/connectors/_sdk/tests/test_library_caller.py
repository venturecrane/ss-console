"""What the three callers share (``library/caller.py``): the record written
beside a document, the rule that it names what the document started from, and
a store that a seat with nothing accepted never opens."""

from __future__ import annotations

import hashlib
import json

import pytest

from operator_connector_sdk.library import (
    PROTOTYPES,
    Attorney,
    Entry,
    Ledger,
    LedgerRead,
    LibraryUnavailable,
    MemoryLibrary,
    Resolution,
    accept,
    parse_ledger,
    dump_ledger,
    resolve,
)
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    BY_NAME,
    NOT_STARTED,
    PINNED,
    STARTER,
    BaseUsed,
    LazyStore,
    attorney_key,
    build_record,
    check_used,
    needs_matter_attorney,
    record_problems,
    slot_kind,
    write_record,
)

DATA = b"accepted bytes"
SHA = hashlib.sha256(DATA).hexdigest()
WHEN = "2026-11-01T00:00:00Z"


def _entry(lib: MemoryLibrary, kind="memo", attorney=None, contract="styles") -> Entry:
    version = lib.add("f-1", "Template - Memo.docx", kind, DATA, attorney=attorney)
    return Entry(kind, attorney, "f-1", version, SHA, contract, "admin@firm.example", WHEN)


def _resolved() -> Resolution:
    lib = MemoryLibrary()
    ledger = accept(Ledger(), _entry(lib))
    return resolve(lib, LedgerRead("ok", ledger), "memo", matter_attorney=None)


def _none() -> Resolution:
    return resolve(MemoryLibrary(), LedgerRead("empty", Ledger()), "memo", matter_attorney=None)


def test_the_record_carries_the_answer_and_the_base_and_nothing_else():
    rec = build_record("drafting_job", _resolved(), BaseUsed.of(ACCEPTED, DATA, contract="styles"))
    assert set(rec) == {"schema_version", "path", "kind", "resolution", "base", "overridden"}
    assert rec["base"] == {"source": ACCEPTED, "sha256": SHA, "name": "", "contract": "styles"}
    assert rec["resolution"]["file"]["sha256"] == SHA
    assert record_problems(rec) == []
    assert json.loads(json.dumps(rec)) == rec  # plain JSON, no bytes


def test_a_resolved_answer_must_be_started_from_the_accepted_bytes():
    res = _resolved()
    with pytest.raises(ValueError, match="started from"):
        build_record("chat_tool", res, BaseUsed.of(ACCEPTED, b"other bytes"))
    with pytest.raises(ValueError, match="started from"):
        build_record("chat_tool", res, BaseUsed.of(PINNED, DATA))
    with pytest.raises(ValueError, match="started from"):
        build_record("chat_tool", res, BaseUsed(STARTER))


def test_nothing_accepted_must_name_one_of_todays_fallbacks():
    res = _none()
    for base in (BaseUsed(STARTER), BaseUsed.of(PINNED, DATA), BaseUsed.of(BY_NAME, DATA)):
        assert check_used(res, base) == []
    assert check_used(res, BaseUsed.of(ACCEPTED, DATA))
    assert check_used(res, BaseUsed(PINNED))  # a pinned base names its digest


def test_any_other_answer_starts_nothing():
    res = resolve(MemoryLibrary(), LedgerRead("unusable", None, "torn"), "memo", matter_attorney=None)
    assert res.status == "cannot_look"
    assert check_used(res, BaseUsed(NOT_STARTED)) == []
    assert check_used(res, BaseUsed(STARTER))


def test_only_an_accepted_file_overrides_a_pin():
    with pytest.raises(ValueError, match="overrides"):
        build_record("demand_job", _none(), BaseUsed.of(PINNED, DATA), overridden=BaseUsed.of(PINNED, DATA))
    rec = build_record("demand_job", _resolved(), BaseUsed.of(ACCEPTED, DATA), overridden=BaseUsed.of(PINNED, b"pin"))
    assert rec["overridden"]["source"] == PINNED


def test_the_record_is_written_whole_beside_the_job(tmp_path):
    rec = build_record("demand_job", _none(), BaseUsed.of(PINNED, DATA, name="ref.docx"))
    path = write_record(tmp_path / "data", rec)
    assert path.name == "library-resolution.json"
    assert json.loads(path.read_text()) == rec
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_record_problems_names_a_broken_record():
    assert record_problems({}) != []
    rec = build_record("chat_tool", _none(), BaseUsed(STARTER))
    assert record_problems({**rec, "base": {"source": "elsewhere"}}) != []
    assert record_problems({**rec, "kind": "letter"}) != []


def test_a_store_is_never_opened_when_nothing_is_accepted():
    def boom():
        raise AssertionError("opened")

    store = LazyStore(boom)
    res = resolve(store, LedgerRead("empty", Ledger()), "memo", matter_attorney=None)
    assert res.status == "none_accepted" and not store.opened


def test_a_store_that_cannot_open_is_cannot_look_never_none():
    lib = MemoryLibrary()
    ledger = accept(Ledger(), _entry(lib))

    def no_client():
        raise AttributeError("seat has no client")

    res = resolve(LazyStore(no_client), LedgerRead("ok", ledger), "memo", matter_attorney=None)
    assert res.status == "cannot_look" and "could not be opened" in res.reason
    with pytest.raises(LibraryUnavailable):
        LazyStore(no_client).list_versions("f-1")


def test_the_matter_attorney_is_needed_only_for_an_attorneys_own_file():
    lib = MemoryLibrary()
    firm_only = accept(Ledger(), _entry(lib))
    assert not needs_matter_attorney(LedgerRead("ok", firm_only), "memo")
    own = accept(firm_only, _entry(lib, attorney="dana example"))
    assert needs_matter_attorney(LedgerRead("ok", own), "memo")
    assert not needs_matter_attorney(LedgerRead("ok", own), "letter")
    assert not needs_matter_attorney(LedgerRead("unusable", None, "x"), "memo")


def test_the_attorney_key_folds_case_and_spacing():
    assert attorney_key("  Dana   EXAMPLE ") == "dana example" == Attorney(attorney_key("Dana Example"), "x").key
    assert attorney_key(None) == ""


def test_a_variant_has_its_own_slot():
    assert slot_kind("demand_letter", "pre_suit") == "demand_letter.pre_suit"
    assert slot_kind("memo") == "memo"


def test_a_prototypes_file_is_a_contract_the_ledger_holds():
    lib = MemoryLibrary()
    ledger = accept(Ledger(), _entry(lib, kind="demand_letter.pre_suit", contract=PROTOTYPES))
    assert parse_ledger(dump_ledger(ledger)).entries[0].contract == PROTOTYPES
