"""A firm's demand letter stocked in its library (``library_setup/demand_make``):
its tagged house reference, a demand of its own with one prototype of each
paragraph and table kind and sample words in each, becomes a prototypes file
with every prototype's formatting, only its tag, the firm's letterhead and
footer legend, and none of the matter's words; the root writer's check passes
it; and the demand job writes a demand in it. Every name, date and figure is
made up."""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import docx
import pytest

from demand_testkit import FOOTER, make_reference
from jobs.demand import house
from jobs.library_setup import demand_make as DM, stock as ST
from jobs.library_setup.make import Unusable

FIRM = "Example & Example, LLP"
IDENTITY = [FIRM, "One Example Way", "Exampletown, California 90000", "(000) 000-0000"]
CASE_WORDS = ("Rowan Examplar", "Widgetco", "March 3, 2025", "CLM-0000-77", "$48,210")


def _firm_demand(tagged: bool = True) -> bytes:
    """The firm's house reference as a person makes it from one of its own
    demands: each prototype keeps a sample of that letter's words."""
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "ref.docx"
        make_reference(p)
        d = docx.Document(str(p))
    d.sections[0].header.paragraphs[0].text = FIRM
    for para in d.paragraphs:
        if para.text.startswith("PROTO:"):
            para.add_run(" Rowan Examplar was struck by a Widgetco truck on March 3, 2025.")
            if not tagged:
                para.runs[0].text = ""
    for table in d.tables:
        table.cell(len(table.rows) - 1, 0).text = "Claim CLM-0000-77: $48,210"
        if not tagged:
            table.cell(0, 0).text = "Claim summary"
    d.add_paragraph("Please call Rowan Examplar's adjuster about claim CLM-0000-77.")  # not a prototype
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _all_text(blob: bytes) -> str:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml"))


def _blank_form():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hermes-smd-overlay"))
    from establish_intake import blank_form

    return blank_form


def test_a_firm_demand_becomes_a_prototypes_file_with_its_fixed_wording_and_no_case_fact():
    made = DM.make_prototypes(_firm_demand(), "house", IDENTITY)
    text = _all_text(made.data)
    for word in CASE_WORDS:
        assert word not in text, word
    assert FIRM.replace("&", "&amp;") in text and FOOTER.split(" under")[0] in text
    assert set(house.KINDS) <= set(made.kinds)
    assert made.counts["dropped_elements"] == 1 and made.counts["words_emptied"] > 0
    assert FIRM in made.fixed_strings and FOOTER in made.fixed_strings
    tags = [s for s in made.fixed_strings if s.startswith("PROTO:")]
    assert len(tags) == len(made.kinds)
    checked = _blank_form().check(made.data, made.fixed_strings, document_fixed=tags)
    assert checked.ok, checked.problems
    # the writer reads it: every kind it needs is there, and a body holds only tags
    with tempfile.TemporaryDirectory() as t:
        ref = Path(t) / "made.docx"
        ref.write_bytes(made.data)
        house.HouseWriter(ref)


def test_the_root_writers_check_refuses_a_prototype_that_keeps_its_sample_words():
    made = DM.make_prototypes(_firm_demand(), "house", IDENTITY)
    tags = [s for s in made.fixed_strings if s.startswith("PROTO:")]
    sample = "PROTO:body The client was hurt and treated."
    d = docx.Document(io.BytesIO(made.data))
    next(p for p in d.paragraphs if p.text == "PROTO:body").runs[0].text = sample
    buf = io.BytesIO()
    d.save(buf)
    # Declared as fixed wording, the sample is still refused in a prototypes file's body.
    checked = _blank_form().check(buf.getvalue(), [*made.fixed_strings, sample], document_fixed=tags)
    assert not checked.ok


def test_an_untagged_letter_or_a_missing_kind_is_refused():
    with pytest.raises(Unusable) as untagged:
        DM.make_prototypes(_firm_demand(tagged=False), "house", IDENTITY)
    assert untagged.value.code == "untagged"
    with pytest.raises(Unusable) as missing:
        DM.make_prototypes(_firm_demand(), "crabtree", IDENTITY)
    assert missing.value.code == "prototypes_missing"


def test_a_footer_line_with_a_case_fact_refuses_the_file():
    d = docx.Document(io.BytesIO(_firm_demand()))
    d.sections[0].footer.add_paragraph("Re: Rowan Examplar, claim CLM-0000-77")
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(Unusable) as refused:
        DM.make_prototypes(buf.getvalue(), "house", IDENTITY)
    assert refused.value.code == "case_words_remain"


def test_the_stock_command_writes_a_prototypes_sidecar(tmp_path):
    made, answer = ST.demand_made(_firm_demand(), "house", IDENTITY)
    assert answer["status"] == "made" and answer["contract"] == "prototypes" and answer["check"] == "passed"
    ST.write(tmp_path, made, "demand_letter.pre_suit", "file-0002", "filed", None, "prototypes")
    side = __import__("json").loads((tmp_path / ST.SIDECAR).read_text())
    assert side["contract"] == "prototypes" and side["kind"] == "demand_letter.pre_suit"


def test_the_demand_job_writes_its_demand_in_the_stocked_file(spec, tmp_path, monkeypatch, opened):
    from test_demand_library_pick import _accept, _demand, _run

    made = DM.make_prototypes(_firm_demand(), "house", IDENTITY)
    _accept(monkeypatch, spec, made.data)
    r, v, log, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered", (v.reason, log[-5:])
    assert opened == [made.data]
    demand = docx.Document(io.BytesIO(_demand(r)))
    assert demand.sections[0].header.paragraphs[0].text == FIRM
    body = "\n".join(p.text for p in demand.paragraphs)
    assert "PROTO:" not in body and "Rowan Examplar was struck" not in body


@pytest.fixture
def spec(tmp_path):

    (tmp_path / "specs").mkdir()
    return tmp_path / "specs"


@pytest.fixture
def opened(monkeypatch, tmp_path):
    from test_demand_library_pick import CHECKER

    from jobs.drafting import firm_rules

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))
    seen: list[bytes] = []
    real = house.HouseWriter.__init__

    def spy(self, reference, *a, **k):
        seen.append(Path(reference).read_bytes())
        real(self, reference, *a, **k)

    monkeypatch.setattr(house.HouseWriter, "__init__", spy)
    return seen
