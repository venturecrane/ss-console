"""The drafting job imports the demand job's kind-agnostic stages read-only.
The demand files are owned and changed by the demand lane; this suite pins
every call drafting makes, in the exact shape drafting makes it, so a change
there fails CI HERE instead of failing a drafting job on a seat.

Each entry binds the arguments drafting passes (positional and keyword, as in
``medchron/drafting/*.py``) against the live signature, and the behavioral
cases pin the return shapes drafting reads. When one of these fails, the fix
is in ``medchron/drafting/``, never a demand edit for drafting's sake.
"""

from __future__ import annotations

import inspect

import pytest

from medchron.demand import (
    deliver,
    draft,
    facts,
    finalpass,
    firm as demand_firm,
    gapaudit,
    gate,
    house,
    preflight,
    pull,
    quotefix,
    seed,
    summarize,
    transcribe,
)
from medchron.drafting import compose, render
from medchron.drafting import run as run_mod

X = object()  # any value: these bind the SHAPE of the call


def _binds(fn, *args, **kw) -> None:
    inspect.signature(fn).bind(*args, **kw)


CALLS = [
    # run.py
    (facts.read, (X, X), {}),
    (facts.blocking_errors, (X,), {}),
    (facts.matter_number, (X, X), {}),
    (pull.run, (X, X, X, X, X, X), {}),
    (preflight.run, (X, X, X, X), {}),
    (preflight.wall_printed_emails, (X, X, X, X), {}),
    (transcribe.run, (X, X, X, X, X), {}),
    (summarize.corpus_files, (X,), {}),
    (summarize.build_chunks, (X, X), {}),
    (summarize.Summarizer, (X, X, X, X, X, X, X), {}),
    (summarize.Summarizer.run, (X, X), {}),
    (summarize.condense, (X, X, X, X, X, X, X), {}),
    (finalpass.settle, (X, X), {}),
    (quotefix.quote_findings, (X,), {}),
    (quotefix.repair, (X, X, X), {}),
    (deliver.out_dir, (X,), {}),
    (deliver.write_manifest, (X, X, X), {}),
    (deliver.render_plain, (X, X, X, X), {}),
    (deliver.file_to_matter, (X, X, X, X), {"pause": 0.0}),
    (deliver.file_to_matter, (X, X, X, X), {}),
    # gate.py, the lane
    (gate.strip_names, (X, X), {}),
    (seed.seed, (X, X), {}),
    # compose.py
    (draft.mechanical_checks, (X, X), {}),
    (draft.auditable, (X,), {}),
    (draft.sections, (X,), {}),
    (draft.tally, (X,), {}),
    (draft.audit_complete, (X, X), {}),
    (draft.split_paragraphs, (X,), {}),
    (draft.merge_repair, (X, X, X), {}),
    (draft.audit_sections, (X,), {}),
    (draft.blocking_findings, (X,), {}),
    (gapaudit.output_max, (X, X), {}),
    (summarize.sha, (X,), {}),
]


@pytest.mark.parametrize("fn, args, kw", CALLS, ids=[f"{c[0].__module__}.{c[0].__qualname__}" for c in CALLS])
def test_every_demand_call_drafting_makes_still_binds(fn, args, kw):
    _binds(fn, *args, **kw)


def test_the_demand_firm_view_is_built_and_read_the_way_drafting_builds_it(tmp_path):
    from drafting_testkit import make_inputs
    from medchron.drafting import firm as drafting_firm

    view = run_mod.demand_view(drafting_firm.load(make_inputs(tmp_path / "in")))
    assert isinstance(view, demand_firm.DemandFirm)
    # what pull.select, pull's wall and preflight's estimate read
    assert view.get("selection", "doc_extensions") and view.data["selection"]["exclude_folder_patterns"]
    assert view.firm_domains == ("firm.example",) and view.consumer_domains == ("mail.example",)
    assert view.get("premise", "scan") == {}
    for k in ("usd_per_million_chars", "usd_per_scanned_page", "usd_drafting_fixed", "usd_per_million_condense_chars"):
        assert isinstance(view.data["budget"][k], (int, float))
    assert int(view.get("levers", "digest_budget_chars")) > 0
    # the estimate runs on it
    est = preflight.estimate([{"chars": 1000, "pages": 2, "transcribe": []}], view)
    assert set(est) >= {"usd", "pages", "characters", "transcription_pages"}


def test_the_shapes_drafting_reads_back():
    assert isinstance(draft.CONTINUATIONS, int) and "{text}" in draft.CONTINUE
    assert house.NOTES_MARK == render.NOTES_MARK
    t = draft.tally("- c | INVENTED | x\nSUPPORTED=1 DRIFTS=0 INVENTED=1 ARITHMETIC=0")
    assert set(t) >= {"SUPPORTED", "DRIFTS", "INVENTED", "ARITHMETIC"} and t["INVENTED"] == 1
    # the class audits' longer tally line still reads as a complete audit
    assert draft.audit_complete("end_turn", "SUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0 COMPOUND=0 UNDEFINED=0")
    assert draft.blocking_findings({"tallies": {"INVENTED": 1}, "quotes_not_found": 0}) == 1
    assert compose.blocking_findings({"tallies": {"COMPOUND": 2}, "quotes_not_found": 0}) == 2
    md, notes = finalpass.settle("# A\n\nAll good.\n", "# Audit v1\n\n## AUDIT: A\n\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0\n")
    assert isinstance(md, str) and isinstance(notes, list)
    assert issubclass(finalpass.Unlocated, Exception)
    assert quotefix.quote_findings(["[9] something else"]) == []
    merged, missing = draft.merge_repair("# A\n\nx\n", "# A\n\ny\n", ["A"])
    assert "y" in merged and missing == []


def test_the_upload_record_carries_what_drafting_reads(tmp_path):
    from medchron_testkit import FakeSeat

    out = deliver.out_dir(tmp_path)
    p = out / "Doc.docx"
    p.write_bytes(b"PK..")
    m = deliver.write_manifest(out, "Folder (Operator ABC123)", [("draft", p)])
    assert {"name", "role", "bytes", "folder"} <= set(m[0])
    seat = FakeSeat([], [], {})
    rec = deliver.file_to_matter(tmp_path, seat, "matter-1", lambda _l: None, pause=0.0)
    assert {"exit", "said"} <= set(rec) and rec["exit"] == 0 and rec.get("folder_id")


def test_transcribe_and_summarize_take_a_doorway_shaped_object():
    # drafting passes its Doorway; the demand stages call .call(stage, model=, ...)
    assert "doorway" in inspect.signature(transcribe.run).parameters
    assert "doorway" in inspect.signature(summarize.Summarizer).parameters
