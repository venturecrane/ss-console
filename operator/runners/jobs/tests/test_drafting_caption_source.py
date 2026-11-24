"""2026-11-23: a discovery request named four individual defendants; the
caption was read from a filing's fee summary, whose short caption printed one
of them, because the summary's name carried "Complaint" and a later date than
the complaint. The party plan stopped after the job was paid.

Two rules, as falsifiers: a filing's paperwork never outranks a pleading, a
summons or a notice; and when the caption read omits a party the request names
that the matter record holds, the stage reads the next court document. A name in
neither still stops (``parties``). Invented names throughout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from drafting_testkit import CAPTION, COMPLAINT
from jobs.drafting import caption, caption_read, caption_stage, parties

FOUR = ["QUILLFEATHER, ORSON", "MARROWDALE, PETRA", "VANTREKKER, HOLLIS", "ZEBULON-ASH, WREN"]
REQUEST = (
    "Please prepare special interrogatories, requests for admission and requests for production to "
    "Orson Quillfeather, Petra Marrowdale, Hollis Vantrekker and Wren Zebulon-Ash."
)
RECORD = {
    "parties": [
        {"side": "client", "name": "Gamma Example"},
        *({"side": "other", "name": n} for n in ("Orson Quillfeather", "Petra Marrowdale", "Hollis Vantrekker")),
        {"side": "other", "name": "Wren Zebulon-Ash"},
    ]
}


def _files(tmp_path: Path, names: list[str]) -> None:
    rows = []
    for i, name in enumerate(names):
        src, txt = tmp_path / f"{i}.pdf", tmp_path / f"{i}.txt"
        src.write_bytes(b"%PDF")
        txt.write_text(COMPLAINT, encoding="utf-8")
        rows.append({"id": str(i), "name": name, "text_path": str(txt), "source_path": str(src)})
    (tmp_path / "extracted.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _picked(tmp_path: Path, names: list[str]) -> list[str]:
    _files(tmp_path, names)
    return [f"{d['name']} ({d['kind']})" for d in caption.source_documents(tmp_path)]


def test_a_fee_summary_never_outranks_the_complaint_it_names(tmp_path):
    got = _picked(tmp_path, ["Complaint 2-1-26", "Complaint Fee Summary 2-3-26", "Filing Receipt 2-3-26"])
    assert got[0] == "Complaint 2-1-26 (pleading)"
    assert set(got[1:]) == {"Complaint Fee Summary 2-3-26 (filing)", "Filing Receipt 2-3-26 (filing)"}


def test_a_summons_outranks_a_notice_and_a_fee_summary_is_read_only_when_alone(tmp_path):
    assert _picked(tmp_path, ["Notice of Hearing 3-1-26", "Summons 2-1-26"])[0] == "Summons 2-1-26 (summons)"
    assert _picked(tmp_path, ["Complaint Fee Summary 2-3-26"]) == ["Complaint Fee Summary 2-3-26 (filing)"]
    assert caption.source_document(tmp_path)["kind"] == "filing"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Complaint for Damages and Attorney Fees.pdf", "pleading"),  # a fee word in a pleading's title
        ("First Amended Complaint; Proof of Service.pdf", "pleading"),  # service attached to the pleading
        ("Summons and Complaint.pdf", "pleading"),  # never a summons below an answer
        ("Answer to Complaint; Proof of Service.pdf", "answer"),
        ("Complaint Fee Summary.pdf", "filing"),  # the 11-23 paper: its paperwork words win
        ("Complaint Filing Receipt.pdf", "filing"),
        ("Proof of Service of Summons.pdf", "filing"),  # paperwork: the POS form prints a short title
        ("Summons on Complaint.pdf", "pleading"),
        ("Summons.pdf", "summons"),
        ("Proof of Service.pdf", "filing"),
        ("Motion for Attorney Fees.pdf", "filing"),
        ("Notice of Hearing.pdf", "notice"),
    ],
)
def test_a_pleading_or_answer_word_wins_over_a_fee_or_summons_word(name, kind):
    assert caption._kind(name) == kind


def test_summons_and_complaint_outranks_an_answer(tmp_path):
    got = _picked(tmp_path, ["Answer 4-1-26", "Summons and Complaint 2-1-26"])
    assert got[0] == "Summons and Complaint 2-1-26 (pleading)"


def _got(defendants: list[str]) -> dict[str, Any]:
    cap = caption_read.parse(json.dumps({**CAPTION, "defendants": defendants}))
    return {"caption": cap, "text_chars": len(COMPLAINT), "text": COMPLAINT}


def _choose(tmp_path: Path, names: list[str], reads: dict[str, dict[str, Any]], request: str = REQUEST):
    _files(tmp_path, names)
    asked: list[str] = []

    def read(doc: dict[str, Any]) -> dict[str, Any]:
        asked.append(doc["name"])
        return reads[doc["name"]]

    doc, _got_, fields = caption_stage.choose(tmp_path, read, RECORD, request, ("firm.example",))
    return (doc or {}).get("name"), [v["value"] for v in fields.get("defendants") or []], asked


def test_a_caption_short_of_a_named_record_party_reads_the_next_document(tmp_path):
    reads = {"Complaint Fee Summary 2-3-26": _got(FOUR[:1]), "Summons 2-1-26": _got(["; ".join(FOUR)])}
    name, defendants, asked = _choose(tmp_path, list(reads), reads)
    assert asked == ["Summons 2-1-26"]  # ranked first now: the fee summary is never read
    # The real shape: the short caption ranks first (no pleading in the file), so the next is read.
    reads = {"Notice of Hearing 3-1-26": _got(FOUR[:1]), "Summons 2-1-26": _got(["; ".join(FOUR)])}
    name, defendants, asked = _choose(tmp_path, list(reads), reads)
    assert asked == ["Summons 2-1-26"]
    reads = {"Order 3-1-26": _got(FOUR[:1]), "Notice of Hearing 2-1-26": _got(["; ".join(FOUR)])}
    name, defendants, asked = _choose(tmp_path, list(reads), reads)
    assert asked == ["Order 3-1-26", "Notice of Hearing 2-1-26"] and name == "Notice of Hearing 2-1-26"
    assert parties.resolve(REQUEST, defendants).planned == FOUR


def test_one_read_when_the_caption_prints_every_named_party_or_the_request_names_none_off_it(tmp_path):
    reads = {"Complaint 2-1-26": _got(["; ".join(FOUR)]), "Summons 2-1-26": _got(FOUR[:1])}
    assert _choose(tmp_path, list(reads), reads)[2] == ["Complaint 2-1-26"]
    short = {"Complaint 2-1-26": _got(FOUR[:1]), "Summons 2-1-26": _got(["; ".join(FOUR)])}
    assert _choose(tmp_path, list(short), short, "SROG to Orson Quillfeather.")[2] == ["Complaint 2-1-26"]


def test_a_name_in_neither_the_caption_nor_the_record_reads_nothing_more_and_still_stops(tmp_path):
    reads = {"Complaint 2-1-26": _got(FOUR[:2]), "Summons 2-1-26": _got(["; ".join(FOUR)])}
    request = "SROG to Ingrid Halvorsen."
    name, defendants, asked = _choose(tmp_path, list(reads), reads, request)
    assert asked == ["Complaint 2-1-26"]
    with pytest.raises(parties.PartyUnknown, match="names none"):
        parties.resolve(request, defendants)


def test_an_unread_next_document_never_replaces_a_read_one(tmp_path):
    reads = {"Order 3-1-26": _got(FOUR[:1]), "Notice of Hearing 2-1-26": {"unread": "page 1 could not be rendered"}}
    name, defendants, asked = _choose(tmp_path, list(reads), reads)
    assert name == "Order 3-1-26" and defendants == FOUR[:1] and len(asked) == 2


def test_each_document_read_is_cached_on_its_own_and_an_unkeyed_read_is_never_reused(tmp_path):
    (tmp_path / "caption-read.json").write_text(json.dumps(_got(FOUR[:1])), encoding="utf-8")
    a, b = {"id": "a", "name": "Complaint"}, {"id": "b", "name": "Summons"}
    assert caption_read.cached(tmp_path, a) is None
    caption_read.save(tmp_path, a, _got(FOUR))
    assert caption_read.cached(tmp_path, a) == _got(FOUR) and caption_read.cached(tmp_path, b) is None
