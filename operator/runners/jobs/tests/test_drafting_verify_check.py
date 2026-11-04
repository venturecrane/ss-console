"""The source check over marked-up text (``drafting/verify``): synthetic
record, scripted model, no network, no spend.

Each test is built to fail on the property it names: a checker that confirms
everything must make the controls INVALID; a lone reader's "wrong" must not
count; a grade resting on anything but the pages shown must not confirm; an
authority with no primary text must never be confirmed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import pytest

from jobs.drafting.evidence.shapes import DocPages, Fact, Ledger
from jobs.drafting.verify import adjudicate, authorities, check, controls_adapter, prompts, view
from jobs.drafting.verify.items import Item, apply_grade
from jobs.drafting.verify.sources import Sources

CLAIM_SENTINEL = "ZZCLAIMSENTINEL"

PAGES = {
    "ER Record.pdf": {
        1: "Patient arrived by ambulance after a rear-end collision on the freeway. Complained of neck pain radiating to the left shoulder.",
        2: "Cervical spine imaging showed no acute fracture. Discharged with muscle relaxants and advised to follow up with orthopedics.",
        3: "Prior history: patient reports an earlier lumbar strain from lifting at work, resolved.",
    },
    "Ortho Notes.pdf": {
        1: "Orthopedic evaluation: persistent cervical radiculopathy, recommended epidural steroid injection.",
        2: "Patient underwent epidural steroid injection at C5-C6 with partial relief of radicular symptoms.",
        3: "Patient missed two scheduled appointments in the spring; gap in treatment noted by the clinic.",
    },
    "Hospital Bill.pdf": {1: "Statement of charges. Total charges billed: $8,300.00 for emergency services rendered."},
    "Pain Clinic.pdf": {
        1: "Pain management intake: chronic headaches, tinnitus, and insomnia reported after the collision."
    },
}


def _facts() -> Ledger:
    def f(i: int, file: str, page: int, kind: str, excerpt: str, **kw: object) -> Fact:
        return Fact(
            id=f"F{i:04d}",
            file=file,
            page=page,
            kind=kind,
            excerpt=excerpt,
            claim=f"{CLAIM_SENTINEL} model paraphrase {i}",
            **kw,  # type: ignore[arg-type]
        )

    return Ledger(
        (
            f(1, "ER Record.pdf", 1, "fact", "Complained of neck pain radiating to the left shoulder.", tags=("neck",)),
            f(
                2,
                "ER Record.pdf",
                2,
                "fact",
                "Cervical spine imaging showed no acute fracture.",
                tags=("neck", "imaging"),
            ),
            f(
                3,
                "Ortho Notes.pdf",
                1,
                "fact",
                "persistent cervical radiculopathy, recommended epidural steroid injection.",
                tags=("neck",),
            ),
            f(
                4,
                "Ortho Notes.pdf",
                2,
                "fact",
                "Patient underwent epidural steroid injection at C5-C6 with partial relief of radicular symptoms.",
                tags=("neck", "injection"),
            ),
            f(
                5,
                "Hospital Bill.pdf",
                1,
                "figure",
                "Total charges billed: $8,300.00 for emergency services rendered.",
                role="billed",
                value="8300.00",
                unit="usd",
            ),
            f(
                6,
                "Ortho Notes.pdf",
                3,
                "fact",
                "Patient missed two scheduled appointments in the spring; gap in treatment noted by the clinic.",
                tags=("injection",),
            ),
            f(
                7,
                "ER Record.pdf",
                3,
                "fact",
                "patient reports an earlier lumbar strain from lifting at work, resolved.",
                tags=("history",),
            ),
            f(
                8,
                "Pain Clinic.pdf",
                1,
                "fact",
                "chronic headaches, tinnitus, and insomnia reported after the collision.",
                tags=("pain",),
            ),
        )
    )


MARKUP = """# Facts

The emergency physicians recorded neck pain radiating to the left shoulder {{F:F0001}}. Cervical imaging showed no acute fracture {{F:F0002}}.

# Treatment

The orthopedist diagnosed persistent cervical radiculopathy and recommended an epidural steroid injection {{F:F0003}}. The injection at C5-C6 brought partial relief of radicular symptoms {{F:F0004}}. The hospital billed {{N:F0005}} for emergency services {{F:F0005}}. The pain clinic recorded chronic headaches, tinnitus, and insomnia after the collision {{F:F0008}}.

# Law

Under {{L:howell-v-hamilton}}, recovery of past medical expenses is limited to amounts paid or incurred. Damages for future care follow {{L:?|Corenbaum v. Lampkin (2013) 215 Cal.App.4th 1308}}.
"""


def _sources() -> Sources:
    return Sources.from_docs(DocPages(n, p) for n, p in PAGES.items())


def _store() -> authorities.Store:
    return authorities.Store(
        [
            authorities.Entry(
                "howell-v-hamilton",
                "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541",
                "https://example.test/howell",
                "2026-11-04T00:00:00Z",
                "An injured plaintiff whose medical expenses are paid through private insurance may recover as "
                "economic damages no more than the amounts paid by the plaintiff or his or her insurer.",
            )
        ]
    )


# ---- a scripted model ------------------------------------------------------------------------


def _items_of(user: str) -> list[tuple[int, str, list[tuple[str, str]]]]:
    """(n, sentence, [(E-id, text)]) for each ``## ITEM n`` block."""
    out = []
    for blk in re.split(r"(?m)^## ITEM ", user)[1:]:
        n = int(blk.split("\n", 1)[0])
        m = re.search(r"(?m)^SENTENCE: (.*)$", blk)
        sent = m.group(1) if m else ""
        ev = re.findall(r"(?ms)^\[([EP]\d+)\] (.*?)(?=^\[[EP]\d+\] |\Z)", blk)
        out.append((n, sent, ev))
    return out


_STOP = {"which", "their", "there", "after", "under", "about", "recorded", "brought", "physicians"}


def honest(sentence: str, evidence: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Confirmed when every dollar figure and most content words of the
    sentence are in the evidence shown; else wrong."""
    text = " ".join(t for _e, t in evidence).lower()
    plain = re.sub(r"\[[^\]]*\]", " ", sentence)
    money = re.findall(r"\$[\d,]+\.\d{2}", plain)
    words = [w for w in re.findall(r"[a-z]{5,}", plain.lower()) if w not in _STOP]
    hit = sum(1 for w in words if w in text)
    if all(m.lower() in text for m in money) and words and hit / len(words) >= 0.6:
        return "confirmed", [evidence[0][0]] if evidence else []
    return "wrong", [e for e, _t in evidence[:1]]


class Model:
    """Answers by call kind; records every prompt it was sent."""

    def __init__(
        self,
        grader: Callable[[str, list[tuple[str, str]]], tuple[str, list[str]]] = honest,
        second_wrong: Callable[[str], bool] = lambda claim: False,
        omits: bool = True,
    ) -> None:
        self.grader, self.second_wrong, self.omits = grader, second_wrong, omits
        self.sent: list[tuple[str, str]] = []

    def kinds(self) -> list[str]:
        return [s.split("\n", 1)[0] for s, _u in self.sent]

    def __call__(self, system: str, user: str) -> str:
        self.sent.append((system, user))
        head = system.split("\n", 1)[0]
        if head in (prompts.CHARACTERIZE, prompts.AUTHORITY):
            grades = []
            for n, sent, ev in _items_of(user):
                g, used = self.grader(sent, ev)
                grades.append(
                    {"n": n, "grade": g, "severity": "serious", "evidence": used, "note": "scripted", "fix": ""}
                )
            return json.dumps({"grades": grades})
        if head == prompts.OMISSION:
            out = []
            if self.omits:
                for blk in re.split(r"(?m)^## SENTENCE ", user)[1:]:
                    n = int(blk.split("\n", 1)[0])
                    against = [c for c, t in re.findall(r"(?m)^\[(C\d+)\] (.*)$", blk) if "missed" in t]
                    if against:
                        out.append({"n": n, "against": against, "note": "a gap in treatment"})
            return json.dumps({"omissions": out})
        if head == prompts.ADVERSARY:
            return json.dumps({"findings": []})
        if head == prompts.SECOND:
            claim = user.split("THE CLAIM:\n", 1)[1].split("\n\nTHE SOURCE", 1)[0]
            return json.dumps({"wrong": self.second_wrong(claim), "severity": "serious", "why": "s", "fix": ""})
        raise AssertionError(f"unexpected call {head}")


def _run(model: Model, tmp: Path, **kw: object) -> check.Report:
    return check.run(MARKUP, _facts(), _sources(), model, store=_store(), cache_dir=tmp / "cache", out_dir=tmp, **kw)  # type: ignore[arg-type]


# ---- the tests -------------------------------------------------------------------------------


def test_honest_checker_passes_its_controls_and_confirms_true_sentences(tmp_path: Path) -> None:
    m = Model()
    r = _run(m, tmp_path)
    assert r.controls["invalid"] is False
    assert r.controls["controls"] >= controls_adapter.MIN
    chars = [i for i in r.real() if i.part == "characterization"]
    assert chars and all(i.grade == "confirmed" for i in chars), [(i.preview, i.grade, i.note) for i in chars]
    assert (tmp_path / "check.json").is_file() and (tmp_path / controls_adapter.FILE).is_file()


def test_a_checker_that_confirms_everything_fails_the_job(tmp_path: Path) -> None:
    stamp = Model(grader=lambda s, ev: ("confirmed", [ev[0][0]] if ev else []))
    with pytest.raises(controls_adapter.ControlsInvalid) as e:
        _run(stamp, tmp_path)
    assert e.value.assessment["invalid"] is True
    # the report is still written for the reader of the failure
    assert json.loads((tmp_path / controls_adapter.FILE).read_text())["assessment"]["invalid"] is True


def test_a_lone_readers_wrong_is_dropped_two_of_two_keeps_one_both_agree_on(tmp_path: Path) -> None:
    def grader(s: str, ev: list[tuple[str, str]]) -> tuple[str, list[str]]:
        if "radiating" in s or "fracture" in s:  # the first reader is mistaken about both
            return "wrong", [ev[0][0]]
        return honest(s, ev)

    m = Model(grader=grader, second_wrong=lambda claim: "fracture" in claim)
    r = _run(m, tmp_path)
    by = {i.ids: i for i in r.real() if i.part == "characterization"}
    lone, agreed = by[("F0001",)], by[("F0002",)]
    assert lone.grade == "could_not_verify" and not lone.counted and not lone.blocking
    assert "second read did not" in lone.note
    assert agreed.grade == "wrong" and agreed.counted and agreed.blocking
    assert [i.ids for i in r.blocking()] == [("F0002",)]
    assert "fracture" in r.repair_brief() and "radiating" not in r.repair_brief()


def test_the_second_reader_sees_only_the_claim_and_its_source(tmp_path: Path) -> None:
    m = Model(
        grader=lambda s, ev: ("wrong", [ev[0][0]]) if "fracture" in s else honest(s, ev),
        second_wrong=lambda c: "fracture" in c,
    )
    _run(m, tmp_path)
    seconds = [u for s, u in m.sent if s.startswith(prompts.SECOND)]
    assert seconds and all("scripted" not in u and "serious" not in u for u in seconds)


def test_digest_and_claims_are_never_evidence(tmp_path: Path) -> None:
    docs = [DocPages(n, p) for n, p in PAGES.items()] + [DocPages("digest.md", {1: "The bill was $1.00."})]
    src = Sources.from_docs(docs)
    assert "digest.md" not in src.docs and src.refused == ["digest.md"]
    m = Model()
    r = check.run(MARKUP, _facts(), src, m, store=_store(), cache_dir=tmp_path / "c")
    assert r.refused_sources == ["digest.md"]
    # the extraction model's claim text is never shown to any checker
    assert all(CLAIM_SENTINEL not in u for _s, u in m.sent)
    # a confirmation that names no evidence shown (a digest, a memory) is not one
    it = Item("k", "characterization", "", "x", "x", ("F0001",))
    apply_grade(it, {"grade": "confirmed", "evidence": ["digest.md"]}, {"E1": "F0001 excerpt"})
    assert it.grade == "could_not_verify"
    apply_grade(it, {"grade": "confirmed", "evidence": ["E1"]}, {"E1": "F0001 excerpt"})
    assert it.grade == "confirmed" and it.evidence == ("F0001 excerpt",)


def test_authority_absent_is_could_not_verify_never_confirmed(tmp_path: Path) -> None:
    yes = Model(grader=lambda s, ev: ("confirmed", [ev[0][0]] if ev else ["P1"]))
    with pytest.raises(controls_adapter.ControlsInvalid):
        _run(yes, tmp_path)  # a yes-model is caught by its controls, and...
    m = Model(grader=lambda s, ev: honest(s, ev) if not s.startswith(("Under", "Damages")) else ("confirmed", ["P1"]))
    r = _run(m, tmp_path / "b")
    auth_items = {i.slugs: i for i in r.real() if i.part == "authority"}
    assert auth_items[("howell-v-hamilton",)].grade == "confirmed"
    absent = auth_items[("?",)]
    assert absent.grade == "could_not_verify" and "no primary text" in absent.note
    # the absent authority never reached a model, even one that confirms everything
    assert all("Corenbaum" not in u for s, u in m.sent if s.startswith(prompts.AUTHORITY))
    wanted = json.loads((tmp_path / "b" / authorities.WANTED_FILE).read_text())
    assert [w["citation"] for w in wanted["wanted"]] == ["Corenbaum v. Lampkin (2013) 215 Cal.App.4th 1308"]
    assert "Corenbaum" in r.notes_md()


def test_an_authority_graded_without_its_primary_text_is_not_confirmed() -> None:
    it = Item("k", "authority", "", "x", "x", (), ("howell-v-hamilton",))
    apply_grade(it, {"grade": "confirmed", "evidence": []}, {"P1": "P: howell-v-hamilton"})
    assert it.grade == "could_not_verify"


def test_omission_candidates_come_from_the_full_ledger_and_are_disclosed_not_blocking(tmp_path: Path) -> None:
    r = _run(Model(), tmp_path)
    disclosed = [i for i in r.real() if i.grade == "disclosed"]
    assert disclosed and all(i.against == ("F0006",) for i in disclosed)
    assert not any(i.blocking for i in disclosed)
    assert "Ortho Notes.pdf" in r.notes_md()


def test_contrary_candidates_skip_facts_the_brief_cites() -> None:
    led = _facts()
    got = check.contrary(("F0004",), led, cited_anywhere={"F0003", "F0004"})
    assert [c.id for c in got] == ["F0006"]
    assert check.contrary(("F0004",), led, cited_anywhere={"F0003", "F0004", "F0006"}) == []


def test_a_resumed_check_reuses_every_graded_claim(tmp_path: Path) -> None:
    _run(Model(), tmp_path)
    again = Model()
    r = _run(again, tmp_path)
    chars = [i for i in r.real() if i.part == "characterization"]
    assert all(i.cached for i in chars)
    # only the controls (never cached) went to the characterization checker
    sent = sum(u.count("\nSENTENCE: ") for s, u in again.sent if s.startswith(prompts.CHARACTERIZE))
    assert sent == len([i for i in r.items if i.control])
    assert not any(s.startswith((prompts.OMISSION, prompts.ADVERSARY, prompts.AUTHORITY)) for s, _u in again.sent)


def test_a_changed_page_invalidates_the_cache(tmp_path: Path) -> None:
    _run(Model(), tmp_path)
    pages = {n: dict(p) for n, p in PAGES.items()}
    pages["ER Record.pdf"][2] += " Addendum."
    src = Sources.from_docs(DocPages(n, p) for n, p in pages.items())
    r = check.run(MARKUP, _facts(), src, Model(), store=_store(), cache_dir=tmp_path / "cache")
    by = {i.ids: i for i in r.real() if i.part == "characterization"}
    assert not by[("F0002",)].cached and by[("F0004",)].cached


def test_sentences_and_previews_read_tokens_not_their_dots() -> None:
    s = view.sentences(
        "Dr. Smith saw her {{F:F0001}}. She paid {{N:F0005}} on time {{F:F0005}}.\n\n- a list item {{F:F0002}}"
    )
    assert len(s) == 3, s
    pv = view.preview(s[1], _facts())
    assert "$8,300.00 [F0005]" in pv and CLAIM_SENTINEL not in pv
    assert view.preview("x {{F:F0001}}", _facts(), label={"F0001": "F0009"}) == "x [F0009]"


def test_settle_rules() -> None:
    a = Item("k", "characterization", "", "x", "x", grade="wrong", severity="serious", note="n")
    adjudicate.settle(a, {"wrong": True, "severity": "minor"})
    assert a.counted and a.severity == "minor"
    b = Item("k", "characterization", "", "x", "x", grade="wrong", severity="serious", note="n")
    adjudicate.settle(b, None)
    assert b.grade == "could_not_verify" and not b.counted


def test_authority_store_files_round_trip(tmp_path: Path) -> None:
    d = tmp_path / "authorities"
    d.mkdir()
    cite = "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541"
    (d / f"{authorities.slug(cite)}.md").write_text(
        authorities.render_entry(cite, "https://x.test", "2026-11-04", "Text.")
    )
    (d / "broken.md").write_text("no frontmatter")
    with pytest.raises(authorities.AuthorityError):
        authorities.Store.from_dir(d)
    (d / "broken.md").unlink()
    st = authorities.Store.from_dir(d)
    assert st.lookup(cite) is not None and st.citation(authorities.slug(cite)) == cite
    assert st.passage("nope", ["x"]) == ""
    long = "a" * 40_000 + " paid or incurred " + "b" * 40_000
    assert (
        "paid or incurred" in authorities.cut(long, ["incurred"]) and len(authorities.cut(long, ["incurred"])) < 10_000
    )
