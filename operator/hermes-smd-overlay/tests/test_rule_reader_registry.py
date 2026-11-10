"""A firm rule is called "in effect" only where something reads it (2026-11-04).

THE DEFECT. Audited on the A&P seat: an administrator installed a rule about
the firm's documents, the seat wrote it to ``specs/classes/work_product/``, and
the person who asked was sent "Your rule is in effect ... work of that kind is
written to it." The firm's documents are rendered by the jobs runner, and
nothing under ``operator/runners/jobs`` reads the spec store, so the sentence
was false the moment it was sent.

THE FIX AND WHAT PINS IT. :data:`shared.output_classes.LIVE_RULE_READER_CLASSES`
names the classes a live reader applies. The install letter says "in effect"
for those and only those; every other class is told the rule is recorded and
passed to SMD, and SMD is paged. The parity tests below are what keep the
registry from drifting into the same false claim one level up.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from shared import layout_rules, output_classes, rule_dispatch, seat_layout, spec_gate

RULE = "7f3a2c1d"
REQUESTER = "paralegal@firm.example"
ADMIN = "admin@firm.example"

_JOBS_RUNNER = Path(__file__).resolve().parents[2] / "runners" / "jobs"

#: Anything that would mean a lane reads the installed spec store. A reader names
#: the store either by its literal path or, since the seat-layout migration, by
#: the generated constant ``seat_layout.SPECS``; both count.
_SPEC_STORE_READS = re.compile(
    r"spec_manifest|spec_stamp|SMD_SPEC_DIR|specs/classes|output-classes\.json|"
    r"ADJUSTMENTS_HEADING|entries_for_class|smd-config/specs|layout\.json|wording\.json|"
    r"\bseat_layout\.SPECS\b"
)

#: The spec applier's install dir (``spec_applier/__main__.py``), as the seat
#: layout declares it (operator/contracts/seat-layout.yaml ``specs``).
_SPEC_DIR_DEFAULT = "/var/lib/smd-config/specs"
#: How each reader spells its default since the seat-layout migration.
_SPEC_DIR_CONSTANT = "seat_layout.SPECS"


class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"sent": True, "reason": "", "outcome_unknown": False})()


@pytest.fixture
def pages(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        rule_dispatch,
        "_page_unread_rule",
        lambda proposal_id, output_class: seen.append((proposal_id, output_class)),
    )
    return seen


def _install(output_class: str) -> dict:
    send = _Recorder()
    result = rule_dispatch.notify_outcome(
        kind="installed",
        proposal_id=RULE,
        text="Briefs open with the procedural posture.",
        requester=REQUESTER,
        by=ADMIN,
        send=send,
        output_class=output_class,
    )
    assert result.sent is True
    (call,) = send.calls
    return call


@pytest.mark.parametrize("output_class", ["work_product", "record", "", "letter"])
def test_a_rule_no_live_reader_applies_is_never_called_in_effect(output_class, pages):
    """FAILS ON THE PRE-FIX CODE: it sent "in effect" for every class alike."""
    call = _install(output_class)
    letter = f"{call['subject']}\n{call['text']}"
    assert "in effect from now" not in letter
    assert "is in effect" not in letter
    assert call["subject"] == f"Your rule is recorded [rule {RULE}]"
    assert "passed to SMD" in call["text"]
    assert call["to"] == [REQUESTER]
    assert pages == [(RULE, output_class)]


def test_the_recorded_letter_names_the_work_in_plain_words(pages):
    text = _install("work_product")["text"]
    assert output_classes.describe("work_product") in text
    assert "—" not in text  # voice rule: no em dashes in client copy


def test_the_recorded_letter_states_no_date(pages):
    text = _install("work_product")["text"]
    assert not re.search(r"\b(\d{4}-\d{2}-\d{2}|today|tomorrow|this week|within)\b", text)


@pytest.mark.parametrize("output_class", sorted(output_classes.LIVE_RULE_READER_CLASSES))
def test_a_rule_a_live_reader_applies_is_still_in_effect(output_class, pages):
    call = _install(output_class)
    assert call["subject"] == f"Your rule is in effect [rule {RULE}]"
    assert "in effect from now" in call["text"]
    assert pages == []


def test_the_registry_is_exactly_the_send_sites_class_map():
    """PARITY. Every class the registry calls live is resolved at a send site a
    turn writes through, and every such class is in the registry."""
    assert output_classes.LIVE_RULE_READER_CLASSES == frozenset(
        spec_gate.RECIPIENT_OUTPUT_CLASSES.values()
    )
    for output_class in output_classes.LIVE_RULE_READER_CLASSES:
        assert output_classes.is_output_class(output_class)
        assert any(
            spec_gate.resolve_output_class(value) == output_class
            for value in spec_gate.RECIPIENT_OUTPUT_CLASSES
        )


def test_lane_document_classes_are_not_in_the_registry():
    assert not output_classes.has_live_rule_reader("work_product")
    assert not output_classes.has_live_rule_reader("record")


def test_the_jobs_runner_reads_the_spec_store_only_through_its_two_readers():
    """The other half of the parity (rewritten 2026-11-05, when the drafting
    lane gained its first reader, and 2026-11-06, when the job lanes gained the
    wording reader). The ONLY jobs-runner modules that read the installed spec
    store are ``drafting/firm_rules.py`` (the ``layout.json`` of each class
    :data:`shared.layout_rules.LAYOUT_RULE_CLASSES` names) and ``firm_wording.py``
    (``work_product/wording.json``), each through its byte twin of the overlay's
    reader. A third reader, or a reader of another file, fails here: give its
    class a letter that says what it reads, in the same PR."""
    assert _JOBS_RUNNER.is_dir(), f"the jobs runner is not at {_JOBS_RUNNER}"
    readers = sorted(
        str(path.relative_to(_JOBS_RUNNER))
        for path in _JOBS_RUNNER.rglob("*.py")
        if "/tests/" not in str(path)
        # jobs/seat_layout.py is generated (operator/bin/gen-seat-layout.py): it
        # names the spec store's path as a constant and reads nothing.
        and not path.read_text("utf-8").startswith("# GENERATED by ")
        and _SPEC_STORE_READS.search(path.read_text("utf-8"))
    )
    # layout_store.py is the reader both sides share, by byte, and it names only
    # the file below (tests/test_drafting_firm_rules.py pins the copy).
    # rule_times.py (2026-11-07, design piece 5) reads one more thing from the
    # same store: WHEN each layout rule was said, the id and the date of each
    # bullet in the work_product format spec, only when the manifest's digest
    # vouches for it. It applies nothing; the order of layout precedence needs
    # the date to say which of a rule and a firm's later edit is the later word.
    assert readers == [
        "jobs/drafting/firm_rules.py",
        "jobs/drafting/layout_store.py",
        "jobs/drafting/rule_times.py",
        "jobs/firm_wording.py",
        "jobs/wording_rules_store.py",
    ], f"spec store readers: {readers}"
    times = (_JOBS_RUNNER / "jobs" / "drafting" / "rule_times.py").read_text("utf-8")
    assert 'SPEC_REL = "classes/work_product/format.md"' in times
    assert 'entry.get("sha256") != hashlib.sha256(blob).hexdigest()' in times
    wording = (_JOBS_RUNNER / "jobs" / "firm_wording.py").read_text("utf-8")
    assert f"DEFAULT_DIR = {_SPEC_DIR_CONSTANT}" in wording
    assert "wording_rules_store.read(_spec_dir())" in wording
    twin = (_JOBS_RUNNER / "jobs" / "wording_rules_store.py").read_bytes()
    assert (
        twin
        == (Path(__file__).resolve().parents[1] / "shared" / "wording_rules_store.py").read_bytes()
    )
    reader = (_JOBS_RUNNER / readers[0]).read_text("utf-8")
    for output_class in layout_rules.LAYOUT_RULE_CLASSES:
        assert f'RULE_CLASS = "{output_class}"' in reader
    assert 'LAYOUT_REL = f"classes/{RULE_CLASS}/layout.json"' in reader
    assert f"DEFAULT_DIR = {_SPEC_DIR_CONSTANT}" in reader
    applier_main = Path(__file__).resolve().parents[1] / "spec_applier" / "__main__.py"
    assert f"_DEFAULT_SPEC_DIR = {_SPEC_DIR_CONSTANT}" in applier_main.read_text("utf-8")
    # The constant both sides name resolves to the one install dir, in each
    # tree's generated copy (the jobs bundle and the overlay never share one).
    for layout in (_JOBS_RUNNER / "jobs" / "seat_layout.py", Path(seat_layout.__file__)):
        assert f'SPECS = "{_SPEC_DIR_DEFAULT}"' in layout.read_text("utf-8"), layout


def test_the_spec_store_scan_sees_a_reader_that_names_only_the_constant():
    """FALSIFIER for the scan above: a module whose only tie to the store is the
    generated constant still reads as a spec-store reader."""
    assert _SPEC_STORE_READS.search("DEFAULT_DIR = seat_layout.SPECS\n")
    assert _SPEC_STORE_READS.search('DEFAULT_DIR = "/var/lib/smd-config/specs"\n')
    assert not _SPEC_STORE_READS.search("DEFAULT_DIR = seat_layout.SPECS_ARCHIVE\n")


def _layout(spec_dir: Path, rule_ids: list[str]) -> None:
    path = spec_dir / layout_rules.layout_rel_path("work_product")
    path.parent.mkdir(parents=True)
    clause = {
        "text": "Two blank lines before the court name.",
        "layout": {"front_court_gap_lines": 2},
        "relative": {},
        "base_owned": [],
        "carried": False,
    }
    rules = [{"rule_id": r, "doc_classes": [], "compiled": [clause], "prose": []} for r in rule_ids]
    blob = json.dumps({"schema_version": layout_rules.SCHEMA_VERSION, "rules": rules}).encode()
    path.write_bytes(blob)
    rel = layout_rules.layout_rel_path("work_product")
    manifest = {"layouts": {rel: {"sha256": hashlib.sha256(blob).hexdigest()}}}
    (spec_dir / "manifest.json").write_text(json.dumps(manifest))


def test_a_work_product_rule_the_seat_compiled_is_in_effect(tmp_path, monkeypatch, pages):
    """FAILS ON THE PRE-FIX CODE: every work_product rule was "recorded"."""
    _layout(tmp_path, [RULE])
    monkeypatch.setenv("SMD_SPEC_DIR", str(tmp_path))
    call = _install("work_product")
    assert call["subject"] == f"Your rule is in effect [rule {RULE}]"
    assert pages == []


def test_a_work_product_rule_the_seat_left_as_prose_is_recorded(tmp_path, monkeypatch, pages):
    _layout(tmp_path, ["00000000"])  # another rule compiled; this one did not
    monkeypatch.setenv("SMD_SPEC_DIR", str(tmp_path))
    call = _install("work_product")
    assert call["subject"] == f"Your rule is recorded [rule {RULE}]"
    assert pages == [(RULE, "work_product")]


def test_a_compiled_rule_of_a_class_with_no_layout_reader_is_still_recorded(
    tmp_path, monkeypatch, pages
):
    _layout(tmp_path, [RULE])
    monkeypatch.setenv("SMD_SPEC_DIR", str(tmp_path))
    assert _install("record")["subject"] == f"Your rule is recorded [rule {RULE}]"


def test_the_layout_classes_have_no_send_site_reader():
    """A layout class is live per rule, never as a class."""
    assert not (layout_rules.LAYOUT_RULE_CLASSES & output_classes.LIVE_RULE_READER_CLASSES)
    assert layout_rules.LAYOUT_RULE_CLASSES <= output_classes.OUTPUT_CLASSES


# ---- the letter tells the truth clause by clause (2026-11-05) --------------------------------
def _letter(tmp_path, monkeypatch, text):
    """A work_product rule installed by the real applier, then the real letter."""
    import io
    import json

    from spec_applier.applier import SCHEMA_VERSION, apply

    doc = {
        "schema_version": SCHEMA_VERSION,
        "classes": {
            "work_product": {
                "format": {
                    "adjustments": [
                        {
                            "id": RULE,
                            "text": text,
                            "instructed_by": REQUESTER,
                            "at": "2026-12-01T00:00:00Z",
                        }
                    ]
                }
            }
        },
    }

    class S3:
        def get_object(self, **_k):
            return {"Body": io.BytesIO(json.dumps(doc).encode())}

    assert apply(s3_client=S3(), bucket="b", slug="s", spec_dir=tmp_path).applied
    monkeypatch.setenv("SMD_SPEC_DIR", str(tmp_path))
    send = _Recorder()
    rule_dispatch.notify_outcome(
        kind="installed",
        proposal_id=RULE,
        text=text,
        requester=REQUESTER,
        by=ADMIN,
        send=send,
        output_class="work_product",
    )
    (call,) = send.calls
    return call


def test_every_clause_compiled_says_in_effect_and_lists_them(tmp_path, monkeypatch, pages):
    call = _letter(tmp_path, monkeypatch, "Two blank lines before the court name.")
    assert call["subject"] == f"Your rule is in effect [rule {RULE}]"
    assert "- Two blank lines before the court name." in call["text"]
    assert "Recorded and passed to SMD" not in call["text"]
    assert pages == []


def test_a_mixed_rule_says_which_clause_is_which_and_pages_the_rest(tmp_path, monkeypatch, pages):
    call = _letter(
        tmp_path, monkeypatch, "Never use the word plainly; two blank lines before the court name."
    )
    assert call["subject"] == f"Your rule is partly in effect [rule {RULE}]"
    effect, recorded = call["text"].split("Recorded and passed to SMD")
    assert "- two blank lines before the court name." in effect
    assert "- Never use the word plainly" in recorded
    assert pages == [(RULE, "work_product")]
    assert "\u2014" not in call["text"]
    assert "we " in call["text"].lower()


def test_a_relative_clause_says_the_note_will_state_the_value(tmp_path, monkeypatch, pages):
    call = _letter(
        tmp_path, monkeypatch, "Leave extra space between the court name and the caption."
    )
    assert "note with the document states the value we used" in " ".join(call["text"].split())


def test_a_clause_the_base_owns_is_not_called_in_effect_on_a_brief(tmp_path, monkeypatch, pages):
    call = _letter(tmp_path, monkeypatch, "Charts in 12 point.")
    assert "your own Word file" in " ".join(call["text"].split())


def test_a_damaged_store_is_recorded_not_in_effect(tmp_path, monkeypatch, pages):
    from shared import layout_rules

    _letter(tmp_path, monkeypatch, "Two blank lines before the court name.")
    (tmp_path / "manifest.json").write_text("{")
    assert layout_rules.clause_truth(RULE, "work_product", tmp_path) is None
    send = _Recorder()
    rule_dispatch.notify_outcome(
        kind="installed",
        proposal_id=RULE,
        text="x",
        requester=REQUESTER,
        by=ADMIN,
        send=send,
        output_class="work_product",
    )
    assert send.calls[0]["subject"] == f"Your rule is recorded [rule {RULE}]"


# ---- a wording rule is "in effect" only for a kind a job lane reads it for (2026-11-06) ------
def test_the_lane_kinds_are_exactly_the_kinds_the_job_lanes_read_rules_for():
    """PARITY, the wording half. :data:`shared.wording_rules_store.LANE_KINDS` is
    what the install letter may call in effect. It must be the drafting lane's
    classes plus the demand lane's kind, and both lanes must freeze rules on
    their way to compose, so a kind added to the set with no lane reading it
    (a letter, a chronology) fails here."""
    from shared import wording_rules_store

    assert _JOBS_RUNNER.is_dir(), f"the jobs runner is not at {_JOBS_RUNNER}"
    lanes = Path(__file__).resolve().parents[1] / "plugins" / "hermes-smd-lanes" / "drafting.py"
    found = re.search(r"DOCUMENT_CLASSES = \(([^)]*)\)", lanes.read_text("utf-8"))
    assert found is not None
    drafting = set(re.findall(r'"([a-z_]+)"', found.group(1)))
    firm_wording = (_JOBS_RUNNER / "jobs" / "firm_wording.py").read_text("utf-8")
    demand = set(re.findall(r'^DEMAND_KIND = "([a-z_]+)"$', firm_wording, re.M))
    assert wording_rules_store.LANE_KINDS == frozenset(drafting | demand)
    demand_run = (_JOBS_RUNNER / "jobs" / "demand" / "run.py").read_text("utf-8")
    # Each lane freezes for its document's reader (piece 7): a demand once its
    # variant is read, a drafting job by its class.
    assert "kind = firm_wording.demand_kind(self.job)" in demand_run
    assert (
        'firm_wording.freeze_or_hold(self.data, kind, self._json("facts.json"), DemandHold, reader)'
        in demand_run
    )
    drafting_run = (_JOBS_RUNNER / "jobs" / "drafting" / "run.py").read_text("utf-8")
    assert (
        "firm_wording.freeze_or_hold(self.data, self.cls, f, DraftingHold, reader)" in drafting_run
    )
    # ...and each lane hands the frozen rules to every drafter it builds.
    for rel in ("drafting/finish.py", "drafting/evidence_lane.py", "demand/run.py"):
        text = (_JOBS_RUNNER / "jobs" / rel).read_text("utf-8")
        assert "firm_wording.attach(" in text, rel


def test_record_products_and_letters_are_no_lane_kind():
    from shared import wording_rules_store

    assert "letter" not in wording_rules_store.LANE_KINDS
    assert wording_rules_store.LANE_KINDS < frozenset(wording_rules_store.KINDS)
    assert not output_classes.has_live_rule_reader("record")


@pytest.mark.parametrize("output_class", ["work_product", "record"])
def test_no_letter_promises_a_second_letter_nothing_sends(output_class, pages):
    """FAILS ON #3784: "The Operator will write to you again" had no sender."""
    assert "write to you again" not in " ".join(_install(output_class)["text"].split())
