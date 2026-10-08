"""The privilege wall ignores text the walled emails share with documents
outside the wall (the firm's letterhead), and still sees private text."""

from __future__ import annotations

from medchron.drafting import gate

LETTERHEAD = "Example & Partners LLP 100 Main Street Suite 200 Exampletown CA 90000 Telephone 555 0100"
PLEADING = f"{LETTERHEAD}\nAttorneys for Plaintiff\nSUPERIOR COURT OF THE STATE OF CALIFORNIA"
PRIVATE = "Between us the client told me the surgery may not have been needed and we should settle low"
EMAIL = f"From the attorney\n{LETTERHEAD}\n\n{PRIVATE}.\n"


def test_letterhead_shared_with_a_pleading_is_blanked():
    out = gate.public_only(EMAIL, gate._shingles(PLEADING))
    assert "Main Street" not in out
    assert PRIVATE in out


def test_nothing_is_blanked_without_a_public_copy():
    assert gate.public_only(EMAIL, gate._shingles("an unrelated record about something else entirely")) == EMAIL


def test_a_private_sentence_still_reaches_the_leakage_check():
    from smokeball_connector.record_check import run_record_check

    held = [("held-out email", gate.public_only(EMAIL, gate._shingles(PLEADING)))]
    leak = f"# I. INTRODUCTION\n\n{PRIVATE}.\n"
    v = run_record_check(
        leak, [("pleading", PLEADING), *held], held_out_names={"held-out email"}, unextractable=[], vision_sources=[]
    )
    assert not v.passed
    clean = f"# I. INTRODUCTION\n\n{LETTERHEAD}\n"
    v2 = run_record_check(
        clean, [("pleading", PLEADING), *held], held_out_names={"held-out email"}, unextractable=[], vision_sources=[]
    )
    assert not any("held-out" in r for r in v2.refusals)
