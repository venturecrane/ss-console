"""Draft quality, wave 1 (2026-11-04): a quotation's page cite names the page
its words are on (gate 2c, drafting/pincheck.py) and a wrong one on exactly one
other page is moved; the digest's block-range cites are measured; the
skeleton's court line comes from the court's caption; the notes state only
what the record check measured; a repaired quote keeps the cited source's own
punctuation. Every word is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medchron.demand import quotefix, summarize
from medchron.drafting import caption_stage, gate, notes, pincheck

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


CLINIC = "\n".join(
    [
        "[p.1]",
        "Alpha Clinic intake. Patient arrived by private car.",
        "[p.2]",
        "Bravo charlie reports the zulu yankee pain began after the event.",
        "[p.3]",
        "Delta echo notes the foxtrot golf improved with rest and ice.",
        "[p.4]",
        "Hotel india plan: return in two weeks for kilo lima review.",
    ]
)
TWICE = "\n".join(
    [
        "[p.1]",
        "Mike november oscar papa quebec romeo.",
        "[p.2]",
        "Sierra tango repeated: mike november oscar papa quebec romeo.",
        "[p.3]",
        "Uniform victor.",
    ]
)
EMAIL = "From the adjuster: whiskey xray yankee zulu alpha bravo, received."
NAMED = [("clinic note.pdf", CLINIC), ("twice record.pdf", TWICE), ("adjuster email", EMAIL)]


def _brief(sentence: str) -> str:
    return f"# I. INTRODUCTION\n\n{sentence}\n"


def _one(md: str) -> pincheck.Pin:
    pins, _ = pincheck.check(md, NAMED)
    assert len(pins) == 1
    return pins[0]


def test_a_quote_on_its_cited_page_passes():
    p = _one(_brief('She "reports the zulu yankee pain began" (FILE: clinic note.pdf, p. 2).'))
    assert p.status == "ok" and p.resolved == "clinic note.pdf"


def test_a_quote_pinned_to_an_encounters_first_page_is_moved_to_its_own_page():
    # The digest cited the encounter as pp. 1-4; the drafter took p. 1.
    md = _brief('The note says "the foxtrot golf improved with rest" (FILE: clinic note, p. 1).')
    p = _one(md)
    assert p.status == "moved" and p.found == [(3, 3)]
    refused, _ = pincheck.findings([p])
    assert refused and refused[0].startswith("[2c]") and "p. 3" in refused[0]
    out, log = pincheck.repair(md, NAMED)
    assert "(FILE: clinic note, p. 3)" in out and log and "moved to p. 3" in log[0]
    assert pincheck.check(out, NAMED)[0][0].status == "ok"


def test_a_quote_over_a_page_break_gets_the_page_pair():
    md = _brief('It reads "with rest and ice. Hotel india plan" (FILE: clinic note.pdf, p. 1).')
    p = _one(md)
    assert p.status == "moved" and p.found == [(3, 4)]
    out, _ = pincheck.repair(md, NAMED)
    assert "(FILE: clinic note.pdf, pp. 3-4)" in out


def test_a_cited_range_holding_the_quote_passes():
    assert _one(_brief('"the foxtrot golf improved with rest" (FILE: clinic note.pdf, pp. 2-4).')).status == "ok"


def test_a_quote_on_two_other_pages_is_refused_not_guessed():
    md = _brief('It says "mike november oscar papa quebec" (FILE: twice record.pdf, p. 3).')
    p = _one(md)
    assert p.status == "wrong_page" and p.found == [(1, 1), (2, 2)]
    out, log = pincheck.repair(md, NAMED)
    assert out == md and log == []


def test_a_quote_from_another_document_is_refused():
    p = _one(_brief('He wrote "whiskey xray yankee zulu alpha" (FILE: clinic note.pdf, p. 2).'))
    assert p.status == "wrong_file"
    assert pincheck.findings([p])[0]


def test_an_unresolvable_file_name_is_refused():
    p = _one(_brief('She "reports the zulu yankee pain began" (FILE: some other chart, p. 2).'))
    assert p.status == "unresolved" and pincheck.findings([p])[0]


def test_an_unmarked_document_past_page_one_is_counted_untested_never_silent():
    pins, counts = pincheck.check(
        _brief('He wrote "whiskey xray yankee zulu alpha" (FILE: adjuster email, p. 3).'), NAMED
    )
    assert pins[0].status == "untestable" and counts == {"checked": 1, "untestable": 1}
    refused, infos = pincheck.findings(pins)
    assert refused == [] and infos and "not tested" in infos[0]


def test_a_deposition_cite_is_left_to_2b():
    pins, _ = pincheck.check(_brief('"reports the zulu yankee pain began" (Bravo Dep. 12:3-5; FILE: x, p. 1).'), NAMED)
    assert pins == []


class _Firm:
    style = "House style."

    def skeleton(self, cls):
        return "Skeleton."

    def attachment(self, key):
        return "Attachment."


def _data(tmp_path: Path) -> Path:
    rows = []
    for i, (name, text) in enumerate(NAMED):
        p = tmp_path / f"{i}.txt"
        p.write_text(text, encoding="utf-8")
        rows.append(json.dumps({"id": str(i), "name": name, "text_path": str(p)}))
    (tmp_path / "extracted.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path


def test_the_drafting_gate_refuses_a_wrong_pin_and_counts_every_pin(tmp_path):
    data = _data(tmp_path)
    good = gate.run(
        data, _Firm(), "mediation_brief", _brief('"the foxtrot golf improved with rest" (FILE: clinic note.pdf, p. 3).')
    )
    assert good["passed"] and good["pins"] == {"checked": 1, "ok": 1}
    bad = gate.run(
        data, _Firm(), "mediation_brief", _brief('"the foxtrot golf improved with rest" (FILE: clinic note.pdf, p. 1).')
    )
    assert not bad["passed"] and any(r.startswith("[2c]") for r in bad["refusals"])
    assert bad["pins"] == {"checked": 1, "moved": 1}


# ---- the digest's cites, measured ---------------------------------------------------


def test_digest_block_range_cites_are_counted():
    dg = (
        "Pain began after the event (FILE: clinic note.pdf, p. 2).\n"
        "Encounter summary (FILE: clinic note.pdf, pp. 1-14).\n"
        "Two pages (FILE: clinic note.pdf, pp. 3-4).\n"
        "Testimony (Bravo Dep. 12:3-9).\n"
    )
    assert summarize.cite_widths(dg) == {"cites": 3, "wide_cites": 1, "wide_pages_over": 3}


# ---- the skeleton's court line, from the court's caption ------------------------------


def test_the_court_line_is_the_courts_own_words():
    skel = "**`{{FILL: CAPTION court_name | THE CAPTION}}`**\n\n**{{FILL: COUNTY | pleading}}**"
    cap = {"fields": {"court_name": {"value": "SUPERIOR COURT OF CALIFORNIA", "verified": True}}}
    out = caption_stage.fill_skeleton(skel, cap)
    assert out.startswith("**SUPERIOR COURT OF CALIFORNIA**") and "{{FILL: COUNTY" in out
    assert "{{NOT IN RECORD: court name" in caption_stage.fill_skeleton(skel, None)


# ---- the notes state only what the check measured -------------------------------------


def test_the_scanned_warning_states_no_document_total(tmp_path):
    w = "3 of 9 sources are machine transcriptions of scanned documents and have not been read by a human: A, B, C"
    said = notes._plain_warning(w)
    assert said.startswith("3 documents have pages that were scanned images") and "9" not in said
    assert notes._plain_warning(w.replace("3 of 9", "1 of 9")).startswith("1 document has pages")


# ---- a repaired quote keeps the cited source's own punctuation ------------------------

CITED = "[p.1]\nThe witness said the lamp was dim, the floor was wet and nobody had warned her.\n"
LATER = "[p.1]\nA later letter restated it: the lamp was dim; the floor was wet and nobody had warned her.\n"


def test_a_repair_takes_the_cited_documents_words_and_punctuation():
    from smokeball_connector.record_check import run_record_check

    md = _brief(
        'She said "the lamp was dim, the floor was soaked and nobody had warned her" (FILE: incident report, p. 1).'
    )
    named = [("later letter", LATER), ("incident report", CITED)]
    v = run_record_check(md, named, held_out_names=set(), unextractable=[], vision_sources=[])
    assert not v.passed
    out, log = quotefix.repair(md, v.refusals, [t for _n, t in named], pincheck.cited_text(named))
    assert '"the lamp was dim, the floor was wet and nobody had warned her"' in out, out
    assert log and log[0].startswith("quote normalized to source")


def test_a_repair_never_writes_a_mark_the_source_does_not_carry():
    ck = quotefix.checker()
    src = quotefix._Source("He said 'stop'. Then left.", ck)
    assert quotefix.exact("He said 'stop.", src) == "He said 'stop"
    assert quotefix.exact("He sang 'stop", src) is None
