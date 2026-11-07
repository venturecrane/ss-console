"""The firm's attorney roster (``operator_connector_sdk/library/roster.py``):
the file's shape, the three states a seat can be in, the lookups every reader
uses, the disagreement count, and the resolver's matter-attorney identity
(``caller.matter_attorney``). Every roster here is a real file in a real
directory read by ``load``; made-up people throughout."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from operator_connector_sdk.library import roster as R
from operator_connector_sdk.library.caller import (
    NOT_ROSTERED,
    ROSTER_UNREADABLE,
    Attorney,
    attorney_key,
    known_attorneys,
    matter_attorney,
)

TEMPLATE = Path(__file__).resolve().parents[3] / "customers" / "_template" / "attorneys" / R.FILE_NAME
DOC = json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _install(tmp_path: Path, doc: object, name: str = "attorneys") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / R.FILE_NAME).write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")
    return folder


def test_the_template_roster_validates_and_loads(tmp_path):
    assert R.problems(DOC) == []
    read = R.load(_install(tmp_path, DOC))
    assert read.status == R.OK
    assert [p.name for p in read.attorneys] == ["Jane Example", "Sam Sample"]
    assert [p.name for p in read.people if not p.attorney] == ["Pat Assistant"]


def test_no_directory_is_absent_and_never_unusable(tmp_path):
    assert R.load(tmp_path / "attorneys").status == R.ABSENT


def test_the_env_names_the_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(R.DIR_ENV, str(_install(tmp_path, DOC)))
    assert R.load().status == R.OK
    monkeypatch.setenv(R.DIR_ENV, str(tmp_path / "nowhere"))
    assert R.load().status == R.ABSENT


@pytest.mark.parametrize(
    "setup",
    ["no file in the directory", "not json", "a dangling link", "fails validation"],
)
def test_an_installed_roster_that_cannot_be_read_is_unusable(tmp_path, setup):
    """FALSIFIER: answer ``absent`` (or an empty roster) for any of these and
    every reader would fall back to its old list on a seat that has a roster."""
    folder = tmp_path / "attorneys"
    if setup == "no file in the directory":
        folder.mkdir()
    elif setup == "not json":
        _install(tmp_path, "{not json")
    elif setup == "a dangling link":
        folder.symlink_to(tmp_path / "attorneys.v00000000")
    else:
        _install(tmp_path, {"schema_version": 1, "attorneys": []})
    read = R.load(folder)
    assert read.status == R.UNUSABLE and read.people == () and read.reason


def _bad(mutate) -> list[str]:
    doc = copy.deepcopy(DOC)
    mutate(doc)
    return R.problems(doc)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d.update(schema_version=2),
        lambda d: d["attorneys"][0].update(nickname="J"),
        lambda d: d["attorneys"][0].update(name=" "),
        lambda d: d["attorneys"][0].update(emails=["not-an-email"]),
        lambda d: d["attorneys"][0].pop("emails"),
        lambda d: d["attorneys"][0].update(bar_number="999999"),  # the block does not print it
        lambda d: d["attorneys"][0].update(caption_block=[]),
        lambda d: d["attorneys"][0]["signs"].update(caption={"x": "y"}),
        lambda d: d["attorneys"][0]["signs"]["demand"].pop("initials"),
        lambda d: d["attorneys"][0]["signs"]["demand"].update(title="Attorney for {matter}"),
        lambda d: d["attorneys"][0]["signs"]["letters"].update(seal="x"),
        lambda d: d["attorneys"][1].update(name="jane  EXAMPLE"),  # the same person twice
        lambda d: d["attorneys"][1].update(emails=["JANE@firm.example"]),  # one address, two people
        lambda d: d["staff_signers"][0]["signs"].update(demand={"signer": "P", "title": "T", "initials": ""}),
        lambda d: d["staff_signers"][0].update(signs={}),
        lambda d: d["staff_signers"][0].update(emails=["pat@firm.example"]),
        lambda d: d["staff_signers"][0].update(name="Jane Example"),  # an attorney is not also a staff signer
    ],
)
def test_a_malformed_roster_refuses(mutate):
    assert _bad(mutate)


def test_the_lookups_fold_case_and_spacing_and_keep_staff_out_of_attorney_reads(tmp_path):
    read = R.load(_install(tmp_path, DOC))
    jane = read.by_name("  jane   EXAMPLE ")
    assert jane is not None and jane.key == attorney_key("Jane Example")
    assert read.by_email("JANE@firm.example") is jane
    assert read.is_attorney("Jane Example") and not read.is_attorney("Pat Assistant")
    assert read.by_name("Pat Assistant") is not None and read.by_email("pat@firm.example") is None
    assert read.by_name("Nobody Here") is None and read.by_name("") is None
    assert read.signature(jane, R.DEMAND) == {
        "signer": "Jane Q. Example",
        "title": "Attorney at Law",
        "initials": "JQE/ps",
    }
    assert read.signature(read.by_name("Sam Sample"), R.LETTERS) is None
    with pytest.raises(ValueError):
        read.signature(jane, "caption")


def _lists() -> dict:
    jane = DOC["attorneys"][0]
    return {
        "drafting": [{"email": "jane@firm.example", "block": list(jane["caption_block"])}],
        "demand": {
            "Jane Example": dict(jane["signs"]["demand"]),
            "Sam Sample": {**DOC["attorneys"][1]["signs"]["demand"], "format": "own"},
        },
        "letters": {
            "jane example": dict(jane["signs"]["letters"]),
            "pat assistant": {"name": "Pat Assistant", "title": "Legal Assistant"},
        },
    }


def test_lists_that_say_what_the_roster_says_have_no_disagreement(tmp_path):
    read = R.load(_install(tmp_path, DOC))
    assert R.disagreements(read, **_lists()) == []


def test_every_way_a_list_disagrees_is_counted_without_a_name(tmp_path):
    """FALSIFIER: drop any branch of ``disagreements`` and its line goes missing."""
    read = R.load(_install(tmp_path, DOC))
    lists = _lists()
    lists["drafting"] += [{"email": "other@firm.example", "block": ["X"]}]
    lists["drafting"][0]["block"] = ["Jane Example"]
    lists["demand"]["Jane Example"]["title"] = "Partner"
    lists["demand"]["Pat Assistant"] = {"signer": "P", "title": "T", "initials": ""}
    lists["letters"]["jane example"]["initials"] = "XX"
    lists["letters"]["staff-id-1"] = {"name": "Somebody"}
    found = R.disagreements(read, **lists)
    assert found == [
        "drafting attorneys[0]: its caption block is not the roster's",
        "drafting attorneys[1]: its email is no attorney's on the roster",
        "demand attorneys entry 1: its signature is not the roster's",
        "demand attorneys entry 3: not an attorney on the roster",
        "form letter signers entry 1: its signature is not the roster's",
        "form letter signers entry 3: nobody on the roster by that name",
    ]
    assert not any(n in line for line in found for n in ("Jane", "Pat", "Sam", "Somebody"))


# ---- the resolver's matter attorney ---------------------------------------------------


def test_with_no_roster_the_matter_attorney_is_exactly_todays(tmp_path):
    absent = R.load(tmp_path / "attorneys")
    for roster in (None, absent):
        got = matter_attorney("Paul Paralegal", "Paul P.", roster)
        assert got.hold == "" and got.attorney == Attorney(attorney_key("Paul Paralegal"), "Paul P.")
        assert matter_attorney("  ", "", roster).attorney is None
        assert known_attorneys(roster, [Attorney("k", "K")]) == [Attorney("k", "K")]


def test_with_a_roster_someone_not_on_it_as_an_attorney_holds(tmp_path):
    read = R.load(_install(tmp_path, DOC))
    assert matter_attorney("jane example", "Jane Q. Example", read).attorney == Attorney(
        "jane example", "Jane Q. Example"
    )
    for who in ("Pat Assistant", "Paul Paralegal"):
        got = matter_attorney(who, who, read)
        assert got.attorney is None and got.hold == NOT_ROSTERED and "roster" in got.reason
    assert matter_attorney(None, "", read).hold == ""  # no attorney on the matter: the firm's file, as today
    assert [a.key for a in known_attorneys(read, [])] == ["jane example", "sam sample"]


def test_an_unusable_roster_is_could_not_look_never_nobody(tmp_path):
    read = R.load(_install(tmp_path, "[]"))
    got = matter_attorney("Jane Example", "Jane", read)
    assert got.attorney is None and got.hold == ROSTER_UNREADABLE
