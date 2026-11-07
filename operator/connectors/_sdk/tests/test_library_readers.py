"""The reader of a document and the profile in effect (``library/readers.py``),
and the record beside a document that names them (``caller.build_record``)."""

from __future__ import annotations

import pytest

from operator_connector_sdk.library import readers
from operator_connector_sdk.library.caller import CHAT_TOOL, NOT_STARTED, BaseUsed, build_record, record_problems
from operator_connector_sdk.library.resolver import REFUSED, Resolution


def test_every_kind_reader_is_a_known_reader_or_the_addressee():
    for kind, reader in readers.KIND_READERS.items():
        assert reader in (*readers.READERS, readers.ADDRESSEE), kind
    assert readers.reader_for("demand_letter", "litigation") == "opposing_counsel"
    assert readers.reader_for("demand_letter", "pre_suit") == "adjuster"
    assert readers.reader_for("demand_letter") == "adjuster"
    assert readers.reader_for("no_such_kind") is None


def test_a_reader_with_no_profile_of_its_own_is_carried_by_the_paths_base():
    for path in (readers.DEMAND_JOB, readers.DRAFTING_JOB, readers.CHAT_TOOL):
        got = readers.record(path, "letter")
        assert got["profile"] == readers.FIRM_BASE and got["reader"] is None
    with pytest.raises(ValueError):
        readers.record("somewhere", "memo")


def test_the_record_carries_the_reader_and_refuses_a_malformed_one():
    res = Resolution(REFUSED, "memo", "x")
    reader = readers.record(readers.CHAT_TOOL, "memo")
    rec = build_record(CHAT_TOOL, res, BaseUsed(NOT_STARTED), reader=reader)
    assert rec["reader"] == reader and record_problems(rec) == []
    assert "reader" not in build_record(CHAT_TOOL, res, BaseUsed(NOT_STARTED))
    for bad in ({**reader, "reader": "jury"}, {**reader, "profile": "invented"}, {"reader": "internal"}):
        with pytest.raises(ValueError):
            build_record(CHAT_TOOL, res, BaseUsed(NOT_STARTED), reader=bad)
        assert record_problems({**rec, "reader": bad})
