"""The update's repeats read (``stages/update_repeats.py``): an added
paragraph that only states again what another entry of its date (or an
earlier paragraph of its own entry) states is removed when two reads agree
and the code's veto passes; the veto can only keep. Every test runs the
REAL stage, Doorway, parsers and veto; only the SDK client is scripted, and
in the keep tests it answers ``repeats`` on both reads so that the CODE is
what keeps the text. Every name, date and value here is invented."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

import update_testkit as K
from jobs.medchron import dag
from jobs.medchron.update import body as B, collect as C, proofs as PR, repeat_read as RR, repeat_veto as V
from jobs.stages import entry_audit_io as IO, update_repeats as UR
from test_audit import Usage, _sr

D = "03/04/2026"
NEX = [
    {"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 9},
    {"exhibit": 3, "title": "Exhibit 3 - Example Imaging (Medical Records)", "pages": 9},
]


def _entry(provider: str, *blocks: str) -> str:
    return f"{D}\n{provider} | Medical Diagnoses\n\n" + "\n\n".join(blocks)


def _dx(text: str) -> str:
    return f"Medical Diagnoses\n{text}"


def _other(text: str) -> str:
    return f"All Other Information\n{text}"


ADDS = _other("Follow up with the spine clinic in two weeks was arranged. (Exhibit 2 - p. 3)")


# ---- the scripted client --------------------------------------------------------------
class _Text:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Message:
    stop_reason = "end_turn"

    def __init__(self, text: str) -> None:
        self.content = [_Text(text)]
        self.usage = Usage()


class _Stream:
    def __init__(self, text: str) -> None:
        self.m = _Message(text)

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def get_final_message(self) -> _Message:
        return self.m


_LABELLED = re.compile(r"\[(P\d+)\] (.*?)(?=\n\n\[P\d+\] |\Z)", re.S)


class Client:
    """The SDK client, scripted. `pairs` is [(text in the paragraph, the
    sentence it is said to repeat)]: a paragraph holding the first is
    answered ``repeats`` against the keeper holding the second, by the first
    read AND the second (``confirm`` False makes the second disagree). Every
    other paragraph ``adds``. `garbage` answers every first read unparsably."""

    def __init__(self, pairs: list[tuple[str, str]], confirm: bool = True, garbage: bool = False) -> None:
        self.pairs, self.confirm, self.garbage = pairs, confirm, garbage
        self.calls: list[str] = []
        self.messages = self

    def stream(self, **kw: Any) -> _Stream:
        system = kw["system"] if isinstance(kw["system"], str) else kw["system"][0]["text"]
        texts = [str(b.get("text", "")) for b in kw["messages"][0]["content"]]
        kind = "confirm" if "REPEATS CONFIRM" in system else "read"
        self.calls.append(kind)
        if kind == "confirm":
            return _Stream(self._confirm(texts))
        if self.garbage:
            return _Stream("I could not decide.")
        return _Stream(self._read(texts, within="REPEATS WITHIN" in system))

    def _keepers(self, texts: list[str], within: bool) -> list[tuple[str, str]]:
        if within:
            return _LABELLED.findall(texts[0])
        return [(t[1 : t.index("]")], t) for t in texts[2:]]

    def _read(self, texts: list[str], within: bool) -> str:
        out: dict[str, Any] = {}
        paras = _LABELLED.findall(texts[0])
        keepers = self._keepers(texts, within)
        for n, (label, text) in enumerate(paras):
            out[label] = {"verdict": "adds"}
            for trigger, quote in self.pairs:
                pool = keepers[:n] if within else keepers
                hit = next((kid for kid, kt in pool if quote in kt), None)
                if trigger in text and hit:
                    out[label] = {"verdict": "repeats", "keeper": hit, "quotes": [quote]}
        return json.dumps({"paragraphs": out, "complete": True})

    def _confirm(self, texts: list[str]) -> str:
        quotes = [q for t, q in self.pairs if t in texts[0] and q in texts[1]]
        return json.dumps({"states_all": self.confirm and bool(quotes), "quotes": quotes, "complete": True})


# ---- the scene ------------------------------------------------------------------------
@pytest.fixture
def scene(job_dir: Path, firm_config_path: Path, data_root: Path):
    def make(added: list[str], delivered: tuple[str, ...] = (), client: Client | None = None, fresh: bool = True):
        log: list[str] = []
        sr = _sr(job_dir, firm_config_path, data_root, client, log)
        rd = sr.slug_dir / "runs" / "alpha"
        if fresh:
            (rd / "update").mkdir(parents=True, exist_ok=True)
            base = K.base_md(entries=(K.ENTRY_PRE, *delivered, K.ENTRY_POST))
            (rd / "update" / "base.md").write_text(base, encoding="utf-8")
            (rd / "final-chronology.md").write_text(C.audit_document(base, added, NEX), encoding="utf-8")
        sr.doorway.sleep = lambda _s: None
        return sr, rd, log

    return make


def _added(rd: Path) -> list[str]:
    return B.entries_of((rd / "final-chronology.md").read_text(encoding="utf-8"))[1]


def _record(rd: Path) -> dict[str, Any]:
    return json.loads((rd / UR.RECORD).read_text(encoding="utf-8"))


def _kept_by_code(scene: Any, delivered: str, added: str, trigger: str, quote: str, leg: str) -> None:
    """Both reads say repeats; the code keeps the document byte for byte."""
    client = Client([(trigger, quote)])
    sr, rd, log = scene([added], (delivered,), client)
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before
    rec = _record(rd)
    assert rec["verdicts"]["repeats"] == 1 and rec["dropped"] == 0 and rec["kept_by"][leg] == 1, rec
    assert client.calls == ["read"]  # the code kept it: no second read is paid for


# ---- the code keeps (fixtures of the designs broken in review) --------------------------
def test_no_disc_herniation_is_kept_against_a_keeper_stating_a_herniation(scene) -> None:
    keeper = "Lumbar MRI showed a disc herniation at L4-5."
    delivered = _entry("Example Imaging", _dx(f"{keeper} (Exhibit 1 - p. 2)"))
    added = _entry("Example Imaging Center", _dx("No disc herniation at L4-5. (Exhibit 2 - p. 1)"), ADDS)
    _kept_by_code(scene, delivered, added, "No disc herniation", keeper, "words")


def test_a_discontinued_drug_is_kept_against_it_continued_and_another_discontinued(scene) -> None:
    keeper = "Gabapentin 300 mg three times daily was continued. Tramadol 50 mg as needed was discontinued."
    delivered = _entry("Example Clinic", _other(f"{keeper} (Exhibit 1 - p. 2)"))
    added = _entry(
        "Example Clinic East", _other("Gabapentin 300 mg three times daily was discontinued. (Exhibit 2 - p. 2)"), ADDS
    )
    _kept_by_code(scene, delivered, added, "was discontinued", keeper, "sentence")


def test_a_one_to_fifteen_dilution_is_kept_against_one_to_ten(scene) -> None:
    keeper = "Mix at 1:10 dilution for the injection."
    delivered = _entry("Example Clinic", _other(f"{keeper} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _other("Mix at 1:15 dilution for the injection. (Exhibit 2 - p. 2)"), ADDS)
    _kept_by_code(scene, delivered, added, "1:15", keeper, "number")


def test_pain_in_the_right_knee_is_kept_against_the_left(scene) -> None:
    keeper = "Pain in the left knee rated 5 of 10."
    delivered = _entry("Example Clinic", _dx(f"{keeper} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx("Pain in the right knee rated 5 of 10. (Exhibit 2 - p. 2)"), ADDS)
    _kept_by_code(scene, delivered, added, "right knee", keeper, "words")


VISIT = "Neck pain rated 6 of 10 with reduced rotation; assessment cervical strain."
PLAN = "Plan ibuprofen 600 mg as needed."


def test_only_the_paragraph_adding_a_later_dated_stop_stays(scene) -> None:
    """An otherwise identical visit: its true repeat goes, the paragraph adding
    that the medicine was stopped on a later date stays."""
    delivered = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 1 - p. 2)"), _other(f"{PLAN} (Exhibit 1 - p. 2)"))
    dated = _other(f"{PLAN} Ibuprofen was stopped on 03/18/2026. (Exhibit 2 - p. 2)")
    added = _entry("Example Clinic East", _dx(f"{VISIT} (Exhibit 2 - p. 1)"), dated)
    client = Client([(VISIT, VISIT), ("stopped on", PLAN)])
    sr, rd, log = scene([added], (delivered,), client)
    assert UR.run(sr) == 0, log
    (left,) = _added(rd)
    assert dated in left and VISIT not in left
    rec = _record(rd)
    assert rec["dropped"] == 1 and rec["kept_by"]["date"] == 1 and rec["kept_by"]["number"] == 1, rec


def test_a_true_verbatim_repeat_is_dropped_and_the_delivered_keeper_is_unchanged(scene) -> None:
    keeper_block = _dx(f"{VISIT} (Exhibit 1 - p. 2)")
    delivered = _entry("Example Clinic", keeper_block)
    repeat = _dx(f"{VISIT} (Exhibit 2 - p. 1)")
    added = _entry("Example Clinic East", repeat, ADDS)
    client = Client([(VISIT, VISIT)])
    sr, rd, log = scene([added], (delivered,), client)
    base_before = (rd / "update" / "base.md").read_bytes()
    assert UR.run(sr) == 0, log
    (left,) = _added(rd)
    assert VISIT not in left and ADDS in left
    assert (rd / "update" / "base.md").read_bytes() == base_before
    assert delivered in B.entries_of(base_before.decode())[1]
    rec = _record(rd)
    assert rec["dropped"] == 1 and rec["dropped_sha256"] == [hashlib.sha256(repeat.encode()).hexdigest()]
    assert client.calls == ["read", "confirm"]
    assert VISIT not in json.dumps(rec)  # the record carries counts and hashes, never text


def test_a_second_read_that_disagrees_keeps_the_paragraph(scene) -> None:
    delivered = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx(f"{VISIT} (Exhibit 2 - p. 1)"), ADDS)
    sr, rd, log = scene([added], (delivered,), Client([(VISIT, VISIT)], confirm=False))
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before and _record(rd)["kept_by"]["second_read"] == 1


def test_a_repeat_whose_exhibit_is_cited_nowhere_else_is_kept(scene) -> None:
    delivered = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 2 - p. 1)"))  # its provider line: the keeper's
    sr, rd, log = scene([added], (delivered,), Client([(VISIT, VISIT)]))
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before and _record(rd)["kept_by"]["exhibit_uncited"] == 1


def test_two_added_copies_drop_one_way_only(scene) -> None:
    """Keepers are a fixed set: the longer entry keeps, the shorter's copy
    goes, and the longer is never asked about the shorter."""
    long_ = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 2 - p. 1)"), ADDS)
    short = _entry(
        "Example Clinic East", _dx(f"{VISIT} (Exhibit 2 - p. 4)"), _other("Ice was advised. (Exhibit 3 - p. 1)")
    )
    sr, rd, log = scene([long_, short], (), Client([(VISIT, VISIT)]))
    assert UR.run(sr) == 0, log
    got = _added(rd)
    assert got[0] == long_ and VISIT not in got[1] and "Ice was advised." in got[1]
    rec = _record(rd)
    assert rec["candidates"] == 2 and rec["candidates_with_keepers"] == 1 and rec["dropped"] == 1, rec


# ---- in one entry: one visit written twice from two exhibits ----------------------------
STRAIN = "Cervical strain with paraspinal spasm; range of motion reduced."
THERAPY = "Physical therapy twice weekly for six weeks was ordered."


def test_one_visit_stated_twice_from_two_exhibits_ends_stated_once_with_both_exhibits_cited(scene) -> None:
    twice = _entry(
        "Example Clinic",
        _dx(f"{STRAIN} (Exhibit 2 - p. 1)"),
        _other(f"{THERAPY} (Exhibit 2 - p. 2)"),
        _dx(f"{STRAIN} (Exhibit 3 - p. 1)"),
        _other(f"{THERAPY} (Exhibit 3 - p. 2)"),
    )
    sr, rd, log = scene([twice], (), Client([(STRAIN, STRAIN), (THERAPY, THERAPY)]))
    assert UR.run(sr) == 0, log
    (once,) = _added(rd)
    assert once.count(STRAIN) == 1 and once.count(THERAPY) == 1, once
    assert B.cited(once) == {2, 3}
    # The citation of the dropped copy joins its keeper's, no sentence edited.
    assert f"{STRAIN} (Exhibit 2 - p. 1) (Exhibit 3 - p. 1)" in once
    rec = _record(rd)
    assert rec["in_entry_candidates"] == 1 and rec["dropped"] == 2 and rec["citations_merged"] == 1, rec


def test_an_in_entry_pair_that_differs_by_a_negation_stays(scene) -> None:
    keeper = "Spurling test was positive on the right."
    twice = _entry(
        "Example Clinic",
        _dx(f"{keeper} (Exhibit 2 - p. 1)"),
        _dx("Spurling test was not positive on the right. (Exhibit 3 - p. 1)"),
    )
    client = Client([("not positive", keeper)])
    sr, rd, log = scene([twice], (), client)
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before and _record(rd)["kept_by"]["words"] == 1


def test_an_in_entry_later_paragraph_is_never_a_keeper(scene) -> None:
    """The earlier paragraph always keeps: a reply naming a later one keeps both."""
    c = RR.within(
        0,
        _entry("Example Clinic", _dx(f"{STRAIN} (Exhibit 2 - p. 1)"), _dx(f"{STRAIN} (Exhibit 3 - p. 1)")),
        set(K.HEADINGS),
    )
    assert c is not None and [k.block for k in c.keepers] == [1, 2]
    first = c.paras[0]
    v = RR.Verdict("repeats", "P2", [STRAIN])
    ids = {k.kid: k for k in c.keepers if k.block < first.index}
    assert V.code_legs(first, ids.get(v.keeper), v) == ["keeper_unknown"]


# ---- proof 10 still holds -----------------------------------------------------------------
def test_proof_ten_passes_after_the_stage_with_no_repeat_excused(scene) -> None:
    delivered = _entry("Example Clinic", _dx(f"{VISIT} Blood pressure 128/82. (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx(f"{VISIT} Blood pressure 128/82. (Exhibit 2 - p. 1)"), ADDS)
    quote = f"{VISIT} Blood pressure 128/82."
    sr, rd, log = scene([added], (delivered,), Client([(VISIT, quote)]))
    assert UR.run(sr) == 0, log
    assert _record(rd)["dropped"] == 1
    base = (rd / "update" / "base.md").read_text(encoding="utf-8")
    updated = B.entries_of(base)[1] + _added(rd)
    assert PR.dropped_values([added], updated, {2}, set()) == []


# ---- endings and resume ---------------------------------------------------------------------
def _repeat_scene(scene: Any, client: Client, fresh: bool = True) -> tuple[Any, Path, list[str]]:
    delivered = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx(f"{VISIT} (Exhibit 2 - p. 1)"), ADDS)
    return scene([added], (delivered,), client, fresh)


def test_a_resume_pays_for_nothing_answered_and_reads_its_own_input(scene) -> None:
    sr, rd, log = _repeat_scene(scene, Client([(VISIT, VISIT)]))
    assert UR.run(sr) == 0, log
    after = (rd / "final-chronology.md").read_bytes()
    again = Client([(VISIT, VISIT)])
    sr2, _rd, log2 = _repeat_scene(scene, again, fresh=False)
    assert UR.run(sr2) == 0, log2
    assert again.calls == [] and (rd / "final-chronology.md").read_bytes() == after


def test_a_reply_unreadable_twice_keeps_everything_and_is_never_paid_for_again(scene) -> None:
    client = Client([], garbage=True)
    sr, rd, log = _repeat_scene(scene, client)
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert client.calls == ["read", "read"] and (rd / "final-chronology.md").read_bytes() == before
    assert _record(rd)["reads_unparsed"] == 1 and _record(rd)["dropped"] == 0
    again = Client([], garbage=True)
    sr2, _rd, log2 = _repeat_scene(scene, again, fresh=False)
    assert UR.run(sr2) == 0, log2
    assert again.calls == []  # the saved retry is the answer: a rerun pays for nothing


def test_the_network_is_exit_75(scene) -> None:
    import anthropic
    import httpx

    class Down(Client):
        def stream(self, **kw: Any) -> _Stream:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.invalid"))

    sr, _rd, log = _repeat_scene(scene, Down([]))
    assert UR.run(sr) == UR.EXIT_INFRA, log


def test_the_replay_answers_from_files_at_no_cost(scene, monkeypatch) -> None:
    monkeypatch.setenv(IO.REPLAY_ENV, "1")
    client = Client([(VISIT, VISIT)])
    sr, rd, log = _repeat_scene(scene, client)
    d = rd / UR.DIR
    for phase in ("read", "confirm"):
        assert UR.run(sr) == UR.EXIT_PENDING, log
        for req in sorted(d.glob(f"*.{phase}1.request.json")):
            r = json.loads(req.read_text(encoding="utf-8"))
            texts = [b["text"] for b in r["content"]]
            reply = client._confirm(texts) if phase == "confirm" else client._read(texts, within=False)
            out = {"hash": r["hash"], "text": reply, "stop_reason": "end_turn", "model": r["model"]}
            (d / f"{r['name']}.{r['kind']}.json").write_text(json.dumps(out), encoding="utf-8")
    assert UR.run(sr) == 0, log
    assert client.calls == [] and _record(rd)["dropped"] == 1


def test_the_stage_sits_after_the_audit_and_before_the_final_set() -> None:
    names = [s.name for s in dag.stages(update=True)]
    st = {s.name: s for s in dag.stages(update=True)}["update_repeats"]
    assert names.index("audit") < names.index("update_repeats") == names.index("update_final_set") - 1
    assert st.paid and st.requires == ("audit",) and set(UR.EXITS) == {3, 75, 9}
    assert st.exit_map[1][1].startswith("audit coverage:")
    assert st.invalidates == ()  # as the final set: a no-op on an already-sent resume rebuilds nothing


class _DateConflicts(Client):
    """Against its date's entries every paragraph is a conflict; within the entry, as Client."""

    def _read(self, texts: list[str], within: bool) -> str:
        if within:
            return super()._read(texts, within)
        labels = [lb for lb, _t in _LABELLED.findall(texts[0])]
        verdicts = {lb: {"verdict": "conflict", "keeper": "K1"} for lb in labels}
        return json.dumps({"paragraphs": verdicts, "complete": True})


def test_a_conflict_found_by_either_read_keeps_the_paragraph(scene) -> None:
    delivered = _entry("Example Clinic", _dx("Cervical strain without spasm. (Exhibit 1 - p. 2)"))
    twice = _entry("Example Clinic East", _dx(f"{STRAIN} (Exhibit 2 - p. 1)"), _dx(f"{STRAIN} (Exhibit 3 - p. 1)"))
    sr, rd, log = scene([twice], (delivered,), _DateConflicts([(STRAIN, STRAIN)]))
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before
    assert _record(rd)["kept_by"]["conflict_elsewhere"] == 1


# ---- the bug review's cases (2026-11-20): both reads say repeats, the CODE keeps --------------
def _drop_case(scene: Any, keeper: str, para: str, trigger: str, quote: str | None = None) -> tuple[bool, dict]:
    delivered = _entry("Example Clinic", _dx(f"{keeper} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx(f"{para} (Exhibit 2 - p. 1)"), ADDS)
    sr, rd, log = scene([added], (delivered,), Client([(trigger, quote or keeper)]))
    assert UR.run(sr) == 0, log
    (left,) = _added(rd)
    return para in left, _record(rd)


@pytest.mark.parametrize(
    ("keeper", "para", "trigger"),
    [
        ("MRI of the lumbar spine was ordered.", "MRI of the cervical spine was ordered.", "cervical"),
        ("Patient continues gabapentin nightly.", "Patient discontinues gabapentin nightly.", "discontinues"),
        ("The knee is tender to palpation.", "The knee is non-tender to palpation.", "non-tender"),
        ("Gave 5 mg of the drug, three doses.", "Gave 5 mcg of the drug, two doses.", "mcg"),
        ("Gave 5 mg of the drug daily.", "Gave 5 mcg of the drug daily.", "mcg"),  # the unit alone differs
    ],
)
def test_a_word_the_keeper_does_not_say_keeps_the_paragraph(scene, keeper: str, para: str, trigger: str) -> None:
    kept, rec = _drop_case(scene, keeper, para, trigger)
    assert kept and rec["dropped"] == 0 and rec["kept_by"]["words"] == 1, rec


def test_a_one_word_quote_is_refused(scene) -> None:
    para = "Referred to orthopedics and scheduled for surgical repair of the rotator cuff."
    kept, rec = _drop_case(scene, "Referred to orthopedics.", para, "surgical repair", quote="Referred")
    assert kept and rec["kept_by"]["quote_short"] == 1, rec


def test_a_quote_that_is_only_part_of_a_sentence_is_refused(scene) -> None:
    keeper = "Referred to orthopedics for the shoulder and to therapy for the knee."
    kept, rec = _drop_case(
        scene, keeper, "Referred to orthopedics for the shoulder.", "Referred", "Referred to orthopedics"
    )
    assert kept and rec["kept_by"]["quote_not_found"] == 1, rec


def test_the_last_two_citations_of_one_exhibit_are_never_both_dropped(scene) -> None:
    v1, v2 = "Neck pain rated 6 of 10.", "Plan ibuprofen 600 mg as needed."
    delivered = _entry("Example Clinic", _dx(f"{v1} (Exhibit 1 - p. 2)"), _other(f"{v2} (Exhibit 1 - p. 3)"))
    a = _entry("Example Clinic East", _dx(f"{v1} (Exhibit 3 - p. 1)"), ADDS)
    b = _entry(
        "Example Clinic West Annex", _other(f"{v2} (Exhibit 3 - p. 2)"), _other("Ice was advised. (Exhibit 2 - p. 5)")
    )
    sr, rd, log = scene([a, b], (delivered,), Client([(v1, v1), (v2, v2)]))
    assert UR.run(sr) == 0, log
    assert 3 in B.cited((rd / "final-chronology.md").read_text(encoding="utf-8"))


def test_in_an_entry_a_discharge_result_is_kept_against_the_arrival_result_and_nothing_merges(scene) -> None:
    p1, p2 = "Glucose 110 mg/dl on arrival.", "Glucose 110 mg/dl at discharge after insulin."
    e = _entry("Example Clinic", _dx(f"{p1} (Exhibit 2 - p. 1)"), _dx(f"{p2} (Exhibit 3 - p. 1)"))
    sr, rd, log = scene([e], (), Client([("at discharge", p1)]))
    before = (rd / "final-chronology.md").read_bytes()
    assert UR.run(sr) == 0, log
    assert (rd / "final-chronology.md").read_bytes() == before
    rec = _record(rd)
    assert rec["kept_by"]["words"] == 1 and rec["citations_merged"] == 0, rec


def test_a_chain_never_removes_the_last_copy_of_a_fact(scene) -> None:
    c_text = "Shoulder strain; sling given."
    delivered = _entry("Example Clinic", _dx(f"{c_text} (Exhibit 1 - p. 2)"))
    b_para = "Shoulder strain; sling given; follow up booked."
    big = _entry(
        "Example Clinic East Campus",
        _dx(f"{b_para} (Exhibit 2 - p. 1)"),
        ADDS,
        _other("Long filler text here. (Exhibit 2 - p. 9)"),
    )
    a = _entry("Example Clinic North", _dx(f"{b_para} (Exhibit 3 - p. 1)"), _other("Ice advised. (Exhibit 3 - p. 2)"))
    sr, rd, log = scene([big, a], (delivered,), Client([("follow up booked", b_para), (b_para, c_text)]))
    assert UR.run(sr) == 0, log
    assert "follow up booked" in (rd / "final-chronology.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("para", "quote"),
    [
        ("No herniation at L4-5.", "Herniation at L4-5."),
        ("Patient discontinues gabapentin.", "Patient continues gabapentin."),
        ("Gabapentin was stopped.", "Gabapentin was started."),
        ("Pain worsened.", "Pain improved."),
        ("Knee is non-tender.", "Knee is tender."),
        ("Lungs unremarkable.", "Lungs remarkable for wheeze."),
        ("Fracture ruled out.", "Fracture confirmed."),
        ("Afebrile.", "Febrile."),
        ("Abnormal EKG.", "Normal EKG."),
        ("Dose was decreasing.", "Dose was increasing."),
        ("Neither nausea nor vomiting.", "Nausea and vomiting."),
        ("Two injections given.", "Three injections given."),
        ("Ratio 1:10.", "Ratio 1:100."),
        ("Given 5 mcg.", "Given 5 mg."),
        ("Seen at 10:30.", "Seen at 10:35."),
        ("MRI of the cervical spine.", "MRI of the lumbar spine."),
        ("Pain in the left knee.", "Pain in the right knee."),
        ("Pain in the upper back.", "Pain in the lower back."),
        ("Prescribed meloxicam.", "Prescribed naproxen."),
        ("Seen by Dr. Alpha.", "Seen by Dr. Beta."),
        ("Diagnosed with sprain.", "Diagnosed with fracture."),
        ("Severe pain.", "Mild pain."),
        ("Acute injury.", "Chronic injury."),
        ("Elevated glucose.", "Low glucose."),
        ("Symptoms resolved.", "Symptoms persist."),
        ("Pain level 2.5.", "Pain level 2."),
        ("Seen 03/04/2026.", "Seen March 4, 2026."),
        ("No left knee pain; right knee pain.", "Left knee pain; no right knee pain."),
        ("Prescribed gabapentin and referred to surgery.", "Prescribed"),
        ("Patient denies chest pain.", "Patient reports chest pain."),
        ("Negative for fracture.", "Positive for fracture."),
        ("Range 5-10 mg.", "Range 5 mg."),
        ("5 mg.", "5-10 mg."),
    ],
)
def test_the_code_keeps_every_reviewed_difference(para: str, quote: str) -> None:
    p = RR.Para("P1", 1, para, para, 0)
    k = RR.Keeper("K1", RR.DELIVERED, 0, f"{D}\nExample Clinic | Medical Diagnoses\n\n{quote}")
    assert V.code_legs(p, k, RR.Verdict("repeats", "K1", [quote])), (para, quote)


def test_a_quote_differing_in_case_or_absent_is_refused() -> None:
    p = RR.Para("P1", 1, "Herniation at L4-5.", "Herniation at L4-5.", 0)
    k = RR.Keeper("K1", RR.DELIVERED, 0, f"{D}\nExample Clinic | Medical Diagnoses\n\nHERNIATION   at l4-5.")
    assert V.code_legs(p, k, RR.Verdict("repeats", "K1", ["herniation at L4-5."]))
    assert V.code_legs(p, k, RR.Verdict("repeats", "K1", []))


def test_a_whole_entry_is_kept_when_its_provider_is_not_the_keepers(scene) -> None:
    delivered = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic", _dx(f"{VISIT} (Exhibit 2 - p. 1)"), _dx("Ice advised. (Exhibit 2 - p. 2)"))
    other = _entry("Sample Orthopedics", _dx(f"{VISIT} (Exhibit 2 - p. 3)"))  # every paragraph a repeat
    sr, rd, log = scene([added, other], (delivered,), Client([(VISIT, VISIT)]))
    assert UR.run(sr) == 0, log
    got = _added(rd)
    assert any(e.startswith(f"{D}\nSample Orthopedics") and VISIT in e for e in got), got
    assert _record(rd)["kept_by"]["header"] == 1


# ---- what must still go ---------------------------------------------------------------------
def test_a_copy_with_its_sentences_in_another_order_is_dropped(scene) -> None:
    s1, s2 = "Neck pain rated 6 of 10 at the visit.", "Physical therapy twice weekly was ordered."
    delivered = _entry("Example Clinic", _dx(f"{s1} {s2} (Exhibit 1 - p. 2)"))
    added = _entry("Example Clinic East", _dx(f"{s2} {s1} (Exhibit 2 - p. 1)"), ADDS)

    class Both(Client):
        def _read(self, texts: list[str], within: bool) -> str:
            paras = _LABELLED.findall(texts[0])
            out = {lb: {"verdict": "adds"} for lb, _t in paras}
            out[paras[0][0]] = {"verdict": "repeats", "keeper": "K1", "quotes": [s1, s2]}
            return json.dumps({"paragraphs": out, "complete": True})

        def _confirm(self, texts: list[str]) -> str:
            return json.dumps({"states_all": True, "quotes": [s1, s2], "complete": True})

    sr, rd, log = scene([added], (delivered,), Both([]))
    assert UR.run(sr) == 0, log
    (left,) = _added(rd)
    assert s1 not in left and s2 not in left and ADDS in left and _record(rd)["dropped"] == 1
