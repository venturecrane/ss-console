"""The wording contract (2026-10-23): every phrase a writer prompt makes the
model write lives in ``medchron.wording``, and no checker, model or code,
grades it a defect.

Three of the Curtis chronology's eight stops were the pre-delivery review
flagging "(provider not stated in the record)" and the "[NTD: ...]" house note,
both of which the map prompt REQUIRES. These tests are the falsifiers: a
checker that starts flagging the firm's wording fails here, and so does a
writer prompt that mandates a new phrase without putting it in wording.py.
"""

from __future__ import annotations

import re

import pytest

from medchron import meta_markers, prompts, wording
from medchron.proof import checks, review as PR
from medchron.stages import assemble, merge_falsify, review, review_policy
from medchron.update import finisher, handwritten, illegible

#: Prompts whose output is chronology text the firm reads (or the next pass
#: turns into it).
WRITERS = ("map-system", "merge-system", "condense-system", "summarize-system", "update-finish-system")
#: Prompts that grade that text.
CHECKERS = ("review-system", "update-review-system")
#: Every other prompt is still scanned for mandated phrases.
OTHERS = ("classify-system", "filter-system")

_LITERAL = re.compile(r"`([^`]+)`|\"([^\"\n]+)\"")
_PAREN = re.compile(r"\([^()<>]*\)|\[[^\[\]<>]*\]")
#: Input syntax and citation formats a prompt quotes; never output wording.
_SYNTAX = re.compile(r"^(?:\(FILE:|\(Exhibit |\[end context\]$|\[end note heading\]$)")
_EXACTLY = re.compile(r"(?i)\bexactly as:\s*([^.\n]+)\.")


def mandated(raw: str) -> list[str]:
    """Phrases a prompt mandates verbatim that are not ``{{WORDING:...}}``:
    a parenthetical or bracketed span inside a quoted literal, a quoted whole
    sentence, or the words after "exactly as:". Templates (``<...>``, "...")
    and the citation and input syntax are not wording."""
    out: list[str] = []
    for m in _LITERAL.finditer(raw):
        s = m.group(1) or m.group(2)
        if "..." in s or "<" in s:
            continue
        bare = wording.TOKEN.sub("", s)
        out += [p for p in _PAREN.findall(bare) if re.search(r"[A-Za-z]", p) and not _SYNTAX.match(p)]
        if re.fullmatch(r"[A-Z][^.]*\.", bare.strip()):
            out.append(bare.strip())
    for m in _EXACTLY.finditer(raw):
        if not wording.TOKEN.search(m.group(1)):
            out.append(m.group(1).strip())
    return out


@pytest.mark.parametrize("name", WRITERS + OTHERS)
def test_no_prompt_mandates_wording_that_is_not_in_wording_py(name: str) -> None:
    assert mandated(prompts.raw(name)) == [], name


def test_the_detector_catches_a_phrase_added_to_a_writer_prompt() -> None:
    """The falsifier: each shape of a mandated phrase, written into a prompt
    without a token, is found."""
    assert mandated('write the facility followed by "(date not stated in the record)"') == [
        "(date not stated in the record)"
    ]
    assert mandated("append: `The page is torn here.`") == ["The page is torn here."]
    assert mandated("is written exactly as: columns as circled.") == ["columns as circled"]
    assert mandated("insert verbatim `[NTD: something new.]`") == ["[NTD: something new.]"]
    # The same phrases through a token are not.
    assert mandated('followed by "{{WORDING:PROVIDER_NOT_STATED}}"') == []
    assert mandated("written exactly as: a value {{WORDING:AS_WRITTEN}}.") == []


@pytest.mark.parametrize("name", WRITERS + CHECKERS + OTHERS)
def test_every_token_resolves_and_none_is_left_unfilled(name: str, firm_config_path) -> None:
    from medchron import config as config_mod

    text = prompts.load(name, config_mod.load(str(firm_config_path)))
    assert "{{" not in text, name
    for t in wording.TOKEN.findall(prompts.raw(name)):
        assert wording.ALL[t] in text


def test_an_unknown_token_never_reaches_a_model() -> None:
    with pytest.raises(KeyError):
        wording.fill("write {{WORDING:NOT_A_PHRASE}}")


def test_the_writers_still_mandate_the_firms_phrases(firm_config_path) -> None:
    from medchron import config as config_mod

    cfg = config_mod.load(str(firm_config_path))
    m = prompts.load("map-system", cfg)
    for p in (
        wording.PROVIDER_NOT_STATED,
        wording.NTD_ILLEGIBLE,
        wording.YEAR_NOT_STATED,
        wording.BILLING_DATE,
        wording.SUBJECT_INCIDENT,
        wording.CONTINUES,
    ):
        assert p in m
    assert wording.RECORDS_DIFFER in prompts.load("merge-system", cfg)
    f = prompts.load("update-finish-system", cfg)
    assert wording.MARKED_AS_SHOWN in f and wording.AS_WRITTEN in f


@pytest.mark.parametrize("name", CHECKERS)
def test_every_firm_phrase_is_exempt_in_every_checker_prompt(name: str, firm_config_path) -> None:
    from medchron import config as config_mod

    text = prompts.load(name, config_mod.load(str(firm_config_path)))
    assert "never a finding" in text
    for p in wording.FIRM.values():
        assert f'"{p}"' in text, (name, p)
    # The review keeps its adversarial stance.
    if name == "review-system":
        assert "Assume at least one serious error" in text


@pytest.mark.parametrize("phrase", list(wording.FIRM.values()))
def test_no_code_checker_flags_a_firm_phrase(phrase: str) -> None:
    line = f"Example Clinic {phrase} | Patient Complaints & Limitations"
    assert not review._CHUNK.search(line)
    assert checks.system_vocabulary(line) == []
    assert checks.sentinels(line) == []
    assert meta_markers.found(line) == []
    assert meta_markers.strip(line) == line
    assert review._system_findings("the chronology", line) == []


@pytest.mark.parametrize("phrase", [wording.YEAR_NOT_STATED, wording.BILLING_DATE, wording.PROVIDER_NOT_STATED])
def test_the_update_lanes_date_line_cleaner_keeps_the_firms_date_and_provider_labels(phrase: str) -> None:
    entry = f"07/18/2026 {phrase}\nExample Clinic | Treatment\n\nNeck pain. (Exhibit 1 - p. 2)"
    clean, notes = meta_markers.process_notes(entry)
    assert notes == [] and clean == entry


def test_the_update_lane_reauthors_the_ntd_note_from_the_same_constant() -> None:
    """``process_notes`` takes compose's NTD out of an added entry (it may be
    mangled); ``update/illegible.py`` puts the firm's note back where the page
    is handwritten, from wording.py, verbatim."""
    assert illegible.HOUSE_NOTE == wording.NTD_ILLEGIBLE
    assert illegible.has_note(f"Neck pain. {wording.NTD_ILLEGIBLE} (Exhibit 1 - p. 2)")


def test_the_duplicated_constants_point_at_wording_py() -> None:
    assert handwritten.NO_YEAR == wording.YEAR_NOT_STATED
    assert finisher.AS_READ == (wording.MARKED_AS_SHOWN, wording.AS_WRITTEN)


def test_the_internal_marker_is_stripped_before_render() -> None:
    """INTERNAL wording is never exempt: the passes that remove it match it."""
    assert merge_falsify.CONTINUE.search(wording.CONTINUES)
    assert re.search(r"(?m)^\[entry may continue in next chunk\]\s*$", wording.CONTINUES)
    assert "CONTINUES" not in wording.FIRM and assemble is not None


@pytest.mark.parametrize("name", list(wording.FIRM))
def test_a_model_check3_finding_on_a_firm_phrase_is_dropped(name: str) -> None:
    f = PR.Finding(PR.MUST, "3 system language", f'the entry says "{wording.FIRM[name]}"', "entry 01/20/2026", "model")
    kept, dropped = review_policy.wording_and_notes([f])
    assert kept == [] and dropped == [f]


def test_a_check3_finding_that_also_names_system_words_is_kept() -> None:
    f = PR.Finding(
        PR.MUST,
        "3 system language",
        f'"{wording.PROVIDER_NOT_STATED}" and "in this chunk" in one entry',
        "entry 01/20/2026",
        "model",
    )
    kept, dropped = review_policy.wording_and_notes([f])
    assert kept == [f] and dropped == []
