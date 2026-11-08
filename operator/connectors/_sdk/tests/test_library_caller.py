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
    parse_slot,
    resolve,
    slot_name,
    slot_problems,
    validate_ledger,
)
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    BY_NAME,
    LAYOUT_PRECEDENCE,
    NOT_STARTED,
    PINNED,
    STARTER,
    BaseUsed,
    LazyStore,
    add_to_record,
    attorney_key,
    build_record,
    check_used,
    needs_matter_attorney,
    record_problems,
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


def _edit(**over):
    edit = {"status": "used", "code": "", "version": "f-1-v2", "replaces_version": "f-1-v1", "sha256": SHA,
            "accepted_at": WHEN, "install": "accepted_pending_install"}  # fmt: skip
    return {**edit, **over}


def test_a_considered_firm_edit_is_one_more_key_and_only_then():
    base = BaseUsed.of(ACCEPTED, DATA, contract="styles")
    plain = build_record("drafting_job", _resolved(), base)
    assert "firm_edit" not in plain
    rec = build_record("drafting_job", _resolved(), base, firm_edit=_edit())
    assert set(rec) - set(plain) == {"firm_edit"} and rec["firm_edit"] == _edit()
    assert record_problems(rec) == [] and record_problems(plain) == []
    refused = build_record(
        "drafting_job", _resolved(), base, firm_edit=_edit(status="refused", code="words_changed", sha256=None)
    )
    assert record_problems(refused) == []


@pytest.mark.parametrize(
    "edit",
    [_edit(status="maybe"), _edit(sha256=None), _edit(status="refused", code=""), {"status": "used"}],
)
def test_a_firm_edit_block_that_does_not_say_what_happened_is_refused(edit):
    with pytest.raises(ValueError):
        build_record("drafting_job", _resolved(), BaseUsed.of(ACCEPTED, DATA, contract="styles"), firm_edit=edit)
    rec = build_record("drafting_job", _resolved(), BaseUsed.of(ACCEPTED, DATA, contract="styles"))
    assert record_problems({**rec, "firm_edit": edit}) != []


_ORDER = {
    "winners": {
        "page_margins": {"winner": "firm_edit", "version": "f-1-v2", "at": WHEN, "value": [0.5, 0.5, 0.7, 1.6]}
    },
    "replaced": [{"rule_id": "0000000a", "key": "front_court_gap_lines", "by_version": "f-1-v2", "at": WHEN}],
    "unplaced": [],
}


def test_the_order_of_layout_precedence_is_added_to_a_written_record_and_only_then(tmp_path):
    rec = build_record("drafting_job", _resolved(), BaseUsed.of(ACCEPTED, DATA, contract="styles"))
    write_record(tmp_path, rec)
    add_to_record(tmp_path, LAYOUT_PRECEDENCE, _ORDER)
    got = json.loads((tmp_path / "library-resolution.json").read_text())
    assert got == {**rec, LAYOUT_PRECEDENCE: _ORDER} and record_problems(got) == []
    assert record_problems(rec) == []  # a record without it is the record it always was


@pytest.mark.parametrize(
    "block",
    [{}, {**_ORDER, "winners": {"page_margins": {"value": 1}}}, {**_ORDER, "replaced": "x"}, {**_ORDER, "more": 1}],
)
def test_an_order_block_that_does_not_name_its_winners_is_refused(tmp_path, block):
    write_record(tmp_path, build_record("drafting_job", _resolved(), BaseUsed.of(ACCEPTED, DATA, contract="styles")))
    with pytest.raises(ValueError):
        add_to_record(tmp_path, LAYOUT_PRECEDENCE, block)
    with pytest.raises(ValueError):
        add_to_record(tmp_path, "firm_edit", _ORDER)
    with pytest.raises(ValueError, match="cannot be read"):
        add_to_record(tmp_path / "nowhere", LAYOUT_PRECEDENCE, _ORDER)


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


REGISTRY = {"demand_letter": ("pre_suit", "litigation"), "memo": ()}


def test_a_variant_has_its_own_slot():
    assert slot_name("demand_letter", "pre_suit") == "demand_letter.pre_suit"
    assert slot_name("memo") == "memo"
    assert parse_slot("demand_letter.litigation") == ("demand_letter", "litigation")
    assert parse_slot("memo") == ("memo", None)
    with pytest.raises(ValueError):
        parse_slot("Demand Letter")
    with pytest.raises(ValueError):
        slot_name("memo", "a.b")


def test_a_slot_is_checked_against_the_registrys_variants():
    assert slot_problems("demand_letter.pre_suit", REGISTRY) == []
    assert slot_problems("memo", REGISTRY) == []
    assert slot_problems("demand_letter.settlement", REGISTRY) != []  # a variant the registry does not list
    assert slot_problems("demand_letter", REGISTRY) != []  # a kind with variants names one
    assert slot_problems("memo.pre_suit", REGISTRY) != []
    assert slot_problems("pleading", REGISTRY) != []


def test_the_ledger_refuses_a_kind_that_is_not_a_slot_name():
    lib = MemoryLibrary()
    raw = json.loads(dump_ledger(accept(Ledger(), _entry(lib))))
    raw["entries"][0]["kind"] = "Mediation Brief"
    assert any("not a slot kind" in p for p in validate_ledger(raw))


def test_a_prototypes_file_is_a_contract_the_ledger_holds():
    lib = MemoryLibrary()
    ledger = accept(Ledger(), _entry(lib, kind="demand_letter.pre_suit", contract=PROTOTYPES))
    assert parse_ledger(dump_ledger(ledger)).entries[0].contract == PROTOTYPES
