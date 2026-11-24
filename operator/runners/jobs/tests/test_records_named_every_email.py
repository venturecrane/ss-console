"""The records job, 2026-11-24: a record asked for as every document of a
kind is looked for in every candidate, and emails on the matter are
candidates, found by their subject and body and read as a PDF of their own.

The live case (A&P, 2026-11-24): "every parent statement in the file,
attached" was answered in the turn with a list of email names, the statements
sitting in 262 emails the job never looked at. Every name and text here is
invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs import proof_mode
from jobs.records import emails, parse as P
from jobs.records.run import RecordsRun
from records_testkit import (
    MATTER,
    FakeFinderModel,
    FakeParseClient,
    FakeSmokeball,
    fid,
    job_dir,
    pdf,
    setup_env,
)


def _item(asked: str, *, every: bool = False, provider: str = "") -> dict[str, Any]:
    return {
        "asked": asked,
        "hints": {"dates": [], "provider": provider, "pages": ""},
        "for_another_person": False,
        "every": every,
    }


@pytest.fixture
def model() -> FakeFinderModel:
    return FakeFinderModel()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel) -> dict[str, Path]:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    return setup_env(tmp_path, monkeypatch, model)


def _run(tmp_path: Path, env: dict[str, Path], sb: Any, items: list[dict[str, Any]], text: str) -> tuple[Any, dict]:
    jd = job_dir(tmp_path, request_text=text)
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient({"items": items}),
        log=lambda _m: None,
    )
    return r, json.loads(r.run().to_json())


# ---- every document of a kind ------------------------------------------------


def test_every_of_a_kind_is_looked_for_in_each_candidate(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    """FALSIFIER: locate stopped at the first file that answered, so "every
    parent statement" went out as one file."""
    sb = FakeSmokeball()
    a = sb.add(1, "Parent statement mother.pdf", pdf(4))
    b = sb.add(2, "Parent statement father.pdf", pdf(4))
    sb.add(3, "Statement of damages.pdf", pdf(4))  # one word of the record is not a candidate
    asked = "every parent statement"
    model.match = {asked: [{"asked": asked, "found": True, "sections": ["S1"], "reason": "S1"}]}
    text = "Please send me every parent statement in the file, attached."
    _r, v = _run(tmp_path, env, sb, [_item(asked, every=True)], text)
    assert v["verdict"] == "delivered", v
    assert sorted(c["file_id"] for c in v["cuts"]) == sorted([a, b])
    assert v["items"] == [{"n": 1, "outcome": "attached", "files": 2}]


def test_more_candidates_than_one_request_carries_holds_never_sends_some(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb = FakeSmokeball()
    for n in range(1, 12):
        sb.add(n, f"Parent statement {n}.pdf", pdf(1))
    text = "Please send me every parent statement in the file, attached."
    _r, v = _run(tmp_path, env, sb, [_item("every parent statement", every=True)], text)
    assert v["verdict"] == "held" and v["reason"].startswith("cut_refused:"), v
    assert v["cuts"] == [] and model.index_calls() == 0


def test_the_parse_flags_every_from_the_sender_s_own_words() -> None:
    text = "Please send me every parent statement in the file, attached."
    rows = P.check({"items": [_item("every parent statement")]}, text)
    assert rows[0]["every"] is True
    # The sender's own word, even when the model's flag is false.
    rows = P.check({"items": [{**_item("all the statements"), "every": False}]}, "Send all the statements.")
    assert rows[0]["every"] is True
    rows = P.check({"items": [_item("the Declaration of Jane Roe")]}, "Send the Declaration of Jane Roe.")
    assert rows[0]["every"] is False
    assert "every" in P.SCHEMA["properties"]["items"]["items"]["required"]


@pytest.mark.parametrize(
    "asked",
    [
        "the Smith declaration with all exhibits",
        "all pages of the police report",
        "every page of the deposition transcript",
        "all the deposition",  # "all" before a singular is not a kind
        # Re-review of #4210: a determiner or adjective before the part word.
        "the declaration with all its exhibits",
        "the complaint and all its exhibits",
        "the deposition and all of its exhibits",
        "every attached exhibit",
        "all those exhibits",
        "each of them whole",
    ],
)
def test_all_of_one_document_is_never_every(asked: str) -> None:
    """Review of #4210: "all" fired on the parts of one document. FALSIFIER:
    a bare every/all/each search marks each of these every."""
    rows = P.check({"items": [_item(asked)]}, f"Please send {asked}.")
    assert rows[0]["every"] is False


@pytest.mark.parametrize("asked", ["every parent statement", "all the emails from the adjuster", "each lien letter"])
def test_a_kind_is_every(asked: str) -> None:
    rows = P.check({"items": [_item(asked)]}, f"Please send {asked}.")
    assert rows[0]["every"] is True


# ---- emails are matter files too ---------------------------------------------


def _email_seat(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, str]) -> tuple[FakeSmokeball, dict[str, str]]:
    sb = FakeSmokeball()
    ids: dict[str, str] = {}
    for n, (subject, body) in enumerate(bodies.items(), start=1):
        f = fid(n)
        name = f"RE {subject}.msg"
        row = {"id": f, "name": name, "fileExtension": ".msg", "sizeBytes": 100, "dateModified": "2026-01-01"}
        sb.rows.append(row)
        sb.meta[f] = {**row, "matter": {"href": f"/matters/{MATTER}"}}
        sb.blobs[f"https://s3/{f}"] = emails.OLE_MAGIC + f.encode()
        ids[subject] = f

    by_blob = {emails.OLE_MAGIC + f.encode(): (s, bodies[s]) for s, f in ids.items()}

    def fake_open(path: Path) -> dict[str, Any]:
        subject, body = by_blob[Path(path).read_bytes()]
        return {
            "subject": subject,
            "from_display": "Pat Lane",
            "to_display": "Lee Example",
            "date": "2025-09-01",
            "body": body,
            "attachments": [("scan.pdf", pdf(2)), ("notes.docx", b"PK\x03\x04")],
        }

    monkeypatch.setattr(emails.demand_pull, "_open_msg", fake_open)
    return sb, ids


def test_an_email_whose_body_carries_the_words_is_found_read_and_attached(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FALSIFIER: the job ranked PDFs by name only, and refused anything else at
    fetch, so an email was never a candidate."""
    sb, ids = _email_seat(
        monkeypatch,
        {
            "Follow up": "Attached is the parent statement from the mother, as discussed.",
            "Scheduling": "Can we move the call to Thursday?",
        },
    )
    asked = "every parent statement"
    model.match = {asked: [{"asked": asked, "found": True, "sections": ["S1"], "reason": "S1"}]}
    text = "Please send me every parent statement in the file, attached."
    r, v = _run(tmp_path, env, sb, [_item(asked, every=True)], text)
    assert v["verdict"] == "delivered", v
    assert len(v["cuts"]) == 1
    (cut,) = json.loads((r.data / "cuts.json").read_text())
    assert cut["file_id"] == ids["Follow up"] and cut["name"].endswith(".pdf") and ".msg" not in cut["name"]
    files = json.loads((r.data / "files.json").read_text())
    assert files[ids["Follow up"]]["email"] is True
    # The email's PDF: its head and body page, then the attached scan's 2 pages.
    assert files[ids["Follow up"]]["page_count"] == 3


def test_an_ordinary_record_named_by_a_file_never_reads_the_emails(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #4210: the email search runs for an every record, or when no
    file and no read PDF answers for the record; never beside a named file."""
    sb, _ids = _email_seat(monkeypatch, {"Follow up": "The Northside Clinic visit notes are attached."})
    sb.add(9, "Northside Clinic records.pdf", pdf(10))
    item = _item("the 3/3/2021 Northside Clinic visit", provider="Northside Clinic")
    item["hints"]["dates"] = ["3/3/2021"]
    _r, _v = _run(tmp_path, env, sb, [item], "Please send the 3/3/2021 Northside Clinic visit.")
    assert not [d for d in sb.downloads if d.startswith("00000001")]  # the email was never fetched


def test_over_600_emails_holds_only_an_every_record(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobs.records import candidates

    monkeypatch.setattr(candidates, "MAX_EMAILS_SEARCHED", 1)
    sb, _ids = _email_seat(monkeypatch, {"One": "a parent statement", "Two": "another parent statement"})
    text = "Please send me every parent statement in the file, attached."
    _r, v = _run(tmp_path, env, sb, [_item("every parent statement", every=True)], text)
    assert v["verdict"] == "held" and v["reason"].startswith("scope_unresolved:"), v
    # An ordinary record on the same matter reads no email and is not held for it.
    sb2, _ = _email_seat(monkeypatch, {"One": "a parent statement", "Two": "another parent statement"})
    _r2, v2 = _run(tmp_path / "b", env, sb2, [_item("the parent statement")], "Send the parent statement.")
    assert "more than the 1 one records job reads" not in str(v2.get("reason"))


def test_the_email_pdf_carries_the_head_the_body_and_names_what_is_left_out(tmp_path: Path) -> None:
    import pymupdf

    out = tmp_path / "e.pdf"
    msg = {
        "subject": "Statement",
        "from_display": "Pat Lane",
        "to_display": "Lee",
        "date": "2025-09-01",
        "body": "The statement is attached.",
        "attachments": [("a.pdf", pdf(2)), ("b.docx", b"PK")],
    }
    got = emails.to_pdf(msg, out)
    assert got == {"included": ["a.pdf"], "left_out": ["b.docx"]}
    with pymupdf.open(out) as doc:
        first = doc[0].get_text()
        assert doc.page_count == 3
    for words in ("From: Pat Lane", "Subject: Statement", "The statement is attached.", "b.docx"):
        assert words in first
