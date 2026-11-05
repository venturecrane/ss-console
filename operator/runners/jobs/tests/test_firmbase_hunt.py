"""The hunt for a class's base: what it lists, what it refuses, how it ranks. Every
fixture is synthetic; nothing here reaches a document system."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone

import docx
import pytest

from jobs.firmbase import Source, hunt as H

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
STAFF = ["Alex Firmperson", "Christopher Counsel"]


def _ago(years: float) -> str:
    return (NOW - timedelta(days=365.25 * years)).isoformat().replace("+00:00", "Z")


def _docx(creator: str = "Alex Firmperson", modified_by: str = "", words: int = 1000, sign: str = "") -> bytes:
    d = docx.Document()
    d.core_properties.author = creator
    d.core_properties.last_modified_by = modified_by or creator
    d.add_paragraph(" ".join(["word"] * words))
    if sign:
        d.add_paragraph(sign)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _row(fid: str, name: str, years: float = 1.0, matter: str = "m1", **more) -> dict:
    return {"id": fid, "name": name, "matter_id": matter, "dateModified": _ago(years), "sizeBytes": 1000, **more}


def _rank(rows, files, cls="memo", **kw):
    def read(_m, f):
        if isinstance(files[f], Exception):
            raise files[f]
        return files[f]

    return H.rank(cls, rows, read, staff=STAFF, now=NOW, **kw)


@pytest.mark.parametrize(
    "name",
    [
        "Case Evaluation Memo.docx",
        "MEMORANDUM re causation.docx",
        "2026_memo_final.docx",
        "Memos.docx",
        "Case Summary - A Client.docx",
        "A Client Liability Analysis.docx",
    ],
)
def test_a_memo_by_its_name(name):
    assert H.classify("memo", name)


@pytest.mark.parametrize(
    "name",
    [
        "Memo of Points and Authorities.docx",
        "Memorandum of Costs.docx",
        "MPA memo.docx",
        "Memory care intake.docx",
        "Opposition memorandum.docx",
        "Motion memo.docx",
        "Mediation Brief.docx",
        "Mediation Memorandum.docx",
        "Case Law.docx",
    ],
)
def test_what_also_says_memo_is_something_else(name):
    assert not H.classify("memo", name)


@pytest.mark.parametrize("name", ["Mediation Brief 2026.docx", "Plaintiff MSC Statement.docx", "Mediation stmt.docx"])
def test_a_brief_by_its_name(name):
    assert H.classify("mediation_brief", name)


@pytest.mark.parametrize("name", ["Notice of Mediation.docx", "Mediator invoice.docx", "Mediation Agreement.docx"])
def test_what_also_says_mediation_is_something_else(name):
    assert not H.classify("mediation_brief", name)


def test_an_operator_file_is_out_whoever_the_listing_names():
    files = {"a": _docx(creator="SMD Operator"), "b": _docx(creator="python-docx"), "c": _docx()}
    rows = [_row(f, "A memo.docx", ownerId="staff") for f in files]
    out = _rank(rows, files)
    assert [c.file_id for c in out.candidates] == ["c"]
    assert out.counts["operator_authored"] == 2


def test_a_firm_save_of_our_file_stays_out_by_its_styles():
    d = docx.Document(io.BytesIO(_docx()))
    d.styles.add_style("SMD Body", 1)
    buf = io.BytesIO()
    d.save(buf)
    out = _rank([_row("a", "A memo.docx")], {"a": buf.getvalue()})
    assert out.candidates == [] and out.counts["operator_authored"] == 1


def test_firm_authorship_outranks_recency_and_length():
    files = {"new": _docx(creator="Un-named"), "old": _docx()}
    out = _rank([_row("new", "A memo.docx", 0.0), _row("old", "B memo.docx", 4.0)], files)
    assert [c.file_id for c in out.candidates] == ["old", "new"]
    assert "firm staff in the core properties" in out.candidates[0].reasons
    assert "no firm staff named" in out.candidates[1].reasons


def test_a_signature_counts_when_the_properties_name_no_one():
    files = {"signed": _docx(creator="Un-named", sign="Chris Counsel"), "bare": _docx(creator="Un-named", words=1001)}
    out = _rank([_row("signed", "A memo.docx"), _row("bare", "B memo.docx")], files)
    assert [c.file_id for c in out.candidates] == ["signed", "bare"]
    assert "firm staff in the signature" in out.candidates[0].reasons


def test_a_name_in_the_body_is_not_a_signature():
    d = docx.Document(io.BytesIO(_docx(creator="Un-named")))
    d.paragraphs[0].insert_paragraph_before("Alex Firmperson")
    for _ in range(H.SIGNATURE_LINES):
        d.add_paragraph("more")
    buf = io.BytesIO()
    d.save(buf)
    out = _rank([_row("a", "A memo.docx")], {"a": buf.getvalue()})
    assert "no firm staff named" in out.candidates[0].reasons


def test_recency_and_length_rank_within_authorship():
    files = {"recent": _docx(), "older": _docx(words=1001), "short": _docx(words=20)}
    rows = [_row("older", "A memo.docx", 3.0), _row("recent", "B memo.docx", 0.5), _row("short", "C memo.docx", 0.5)]
    out = _rank(rows, files)
    assert [c.file_id for c in out.candidates][:2] == ["recent", "older"]
    short = next(c for c in out.candidates if c.file_id == "short")
    assert any("outside the band" in r for r in short.reasons)
    assert out.candidates[0].score - short.score == H.IN_BAND


def test_an_accepted_save_outranks_every_firm_file():
    ours = _docx(creator="SMD Operator", modified_by="Alex Firmperson")
    src = Source("m9", "acc", "v7.docx", hashlib.sha256(ours).hexdigest(), "v7", "his Word save")
    firm_best = _docx(sign="Alex Firmperson")
    files = {"acc": ours, "firm": firm_best}
    rows = [_row("firm", "A memo.docx", 0.0), _row("acc", "Our memo.docx", 3.0, matter="m9")]
    out = _rank(rows, files, accepted=[src])
    assert [c.file_id for c in out.candidates] == ["acc", "firm"]
    assert out.candidates[0].score >= H.ACCEPTED
    assert out.candidates[0].reasons[0] == "accepted save, version v7"


def test_an_acceptance_of_other_bytes_counts_for_nothing():
    ours = _docx(creator="SMD Operator")
    src = Source("m9", "acc", "v7.docx", "0" * 64, "v7", "his Word save")
    out = _rank([_row("acc", "Our memo.docx", matter="m9")], {"acc": ours}, accepted=[src])
    assert out.candidates == []
    assert out.counts["accepted_not_these_bytes"] == 1 and out.counts["operator_authored"] == 1


def test_an_acceptance_with_no_evidence_is_no_acceptance():
    ours = _docx(creator="SMD Operator")
    src = Source("m9", "acc", "v7.docx", hashlib.sha256(ours).hexdigest(), "v7", "")
    assert _rank([], {"acc": ours}, accepted=[src]).candidates == []


def test_the_same_bytes_on_two_matters_are_one_candidate():
    blob = _docx()
    rows = [_row("a", "A memo.docx", matter="m1"), _row("b", "A memo.docx", matter="m2")]
    out = _rank(rows, {"a": blob, "b": blob})
    assert len(out.candidates) == 1 and out.counts["same_bytes"] == 1


def test_could_not_look_is_counted_never_none():
    files = {"gone": ConnectionError("x"), "junk": b"not a zip", "good": _docx()}
    out = _rank([_row(f, "A memo.docx") for f in files], files)
    assert [c.file_id for c in out.candidates] == ["good"]
    assert out.counts["unreadable"] == 2


def test_a_read_cap_is_newest_first_and_counted():
    files = {str(i): _docx(words=1000 + i) for i in range(5)}
    rows = [_row(f, "A memo.docx", float(f)) for f in files]
    out = _rank(rows, files, max_reads=2)
    assert {c.file_id for c in out.candidates} == {"0", "1"}
    assert out.counts["not_read"] == 3


def test_reasons_name_no_one():
    files = {"a": _docx(sign="Alex Firmperson")}
    out = _rank([_row("a", "Client Name memo.docx")], files)
    text = " ".join(out.candidates[0].reasons)
    assert "Alex" not in text and "Client" not in text


class Client:
    """The connector's read calls; any other call is an error, so the hunt stays read-only."""

    def __init__(self, listing, files, fail=()):
        self.listing, self.files, self.fail, self.calls = listing, files, set(fail), []

    def get(self, path, **params):
        self.calls.append(path)
        if path == "/staff":
            return {"value": [{"firstName": "Alex", "lastName": "Firmperson"}]}
        if path == "/matters":
            rows = {
                "Open": [{"id": "m1"}, {"id": "m2"}],
                "Pending": [],
                "Closed": [{"id": "old", "closedDate": "2020-01-01"}, {"id": "m3", "closedDate": "2026-02-01"}],
            }
            return {"value": rows[params["Status"]] if params["Offset"] == 0 else []}
        mid = path.split("/")[2]
        if mid in self.fail:
            raise ConnectionError("no")
        return {"value": self.listing.get(mid, []) if params["Offset"] == 0 else []}

    def download_file(self, matter_id, file_id):
        self.calls.append(f"download {matter_id} {file_id}")
        return {}, self.files[file_id]


def test_the_whole_hunt_lists_the_docx_of_the_firms_matters_and_reads_only_what_it_ranks():
    def row(fid, name, ext=".docx", **more):
        return {"id": fid, "name": name, "fileExtension": ext, "dateModified": _ago(1), "sizeBytes": 9, **more}

    client = Client(
        {
            "m1": [row("a", "A memo.docx"), row("pdf", "A memo.pdf", ".pdf"), row("o", "Other.docx")],
            "m2": [row("del", "Gone memo.docx", isDeleted=True), row("can", "Dead memo.docx", isCancelled=True)],
            "m3": [row("b", "B memo.docx")],
            "old": [row("never", "Old memo.docx")],
        },
        {"a": _docx(), "b": _docx(words=1001)},
        fail=["m2"],
    )
    out = H.hunt("memo", client, closed_since="2026-01-01", now=NOW)
    assert {c.file_id for c in out.candidates} == {"a", "b"}
    assert out.counts["matters_listed"] == 3 and out.counts["matters_unlisted"] == 1
    assert out.counts["docx_listed"] == 3 and out.counts["name_matched"] == 2
    downloads = [c for c in client.calls if c.startswith("download")]
    assert sorted(downloads) == ["download m1 a", "download m3 b"]
    assert all(
        c == "/staff" or c == "/matters" or c.endswith("/documents/files") or c in downloads for c in client.calls
    )


def test_a_deleted_or_cancelled_file_is_not_listed():
    def row(fid, **more):
        return {"id": fid, "name": "A memo.docx", "fileExtension": ".docx", **more}

    client = Client({"m1": [row("a"), row("d", isDeleted=True), row("c", isCancelled=True)]}, {})
    out = H.Hunted()
    assert [r["id"] for r in H.docx_rows(client, ["m1"], out)] == ["a"]


def test_a_person_matches_by_first_and_last_name_only():
    p = H._person("Christopher Counsel")
    assert p.search("Chris Counsel") and p.search("christopher q. counsel") and p.search("By: Christopher Counsel")
    assert not p.search("Counsel") and not p.search("Christopher Other") and not p.search("Chris Counselor")
