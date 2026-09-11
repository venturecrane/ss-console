#!/usr/bin/env python3
"""Tests for the drafting gate checker.

Runnable either way:

    python3 -m unittest discover operator/templates/drafting/tests
    python3 -m pytest operator/templates/drafting/tests
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "drafting_gate_check.py"
_SPEC = importlib.util.spec_from_file_location("drafting_gate_check", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


# A synthetic transcript excerpt carrying the two structures gate 2b needs:
# page markers and a numbered line gutter.
TRANSCRIPT = """# Deposition of Kenneth Draper (Excerpt)

```
                                                    Page 23

15   Q.  Did you signal before you changed lanes?
16   A.  I would have. That is what I do.
17   Q.  But do you have a specific memory of moving the
18       lever that afternoon?
19   A.  I do not have a picture of it in my head.
20   Q.  So you cannot tell this jury that you signaled,
21       can you?
22   A.  No, I cannot. I could not tell you for sure about
23       that particular lane change.
24   Q.  Were you running late that afternoon?
```
"""

# A source with no transcript structure at all, so gate 2b must stay silent.
REPORT = """# Traffic Collision Report 24-VV-0517742

Party 2 statement: The white truck came across without any warning at all and
there was nowhere for me to go.

Party 1 statement: He said he always signals and could not say for certain that
he had on this occasion.
"""

ANSWER_QUOTE = "No, I cannot. I could not tell you for sure about that particular lane change"

CLEAN_DRAFT = f"""# DRAFT FOR ATTORNEY REVIEW

## II. Liability

Asked whether he could tell the jury he signaled, defendant answered,
"{ANSWER_QUOTE}" (Draper 23:20 to 23:23).

The independent witness described the movement in his own words: "The white
truck came across without any warning at all" (Traffic Collision Report
24-VV-0517742, Party 2 statement).

## III. Damages

Past medical specials are {{{{FILL: itemized billed total | billing ledger}}}}.
Future care is {{{{NOT IN RECORD: a future care estimate, searched the treating
provider notes and the medical chronology}}}}. Whether to demand policy limits
is {{{{ATTORNEY: decision reserved}}}}.

We reviewed the client file, the medical chronology, and both deposition
transcripts, and produced 214 pages of records.

## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW

| Document | Date | Why flagged |
| --- | --- | --- |
| Fee agreement between the Firm and the client | May 28, 2024 | Attorney-client
communication. Not quoted, cited, or incorporated in the draft. |
"""


class GateTestCase(unittest.TestCase):
    """Shared fixture plumbing: write a case to a tmpdir, run the CLI, read JSON."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        (self.sources / "depo-draper.md").write_text(TRANSCRIPT, encoding="utf-8")
        (self.sources / "collision-report.md").write_text(REPORT, encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def write_draft(self, text: str) -> Path:
        path = self.root / "draft.md"
        path.write_text(text, encoding="utf-8")
        return path

    def write_file(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_gate(
        self,
        draft: Path,
        *,
        sources: str | None = None,
        held_out: str | None = None,
        propounded: str | None = None,
        sprog_lint: bool = False,
    ) -> tuple[int, dict]:
        argv = [
            "--draft",
            str(draft),
            "--sources",
            sources if sources is not None else str(self.sources),
            "--json",
        ]
        if held_out:
            argv += ["--held-out", held_out]
        if propounded:
            argv += ["--propounded", propounded]
        if sprog_lint:
            argv.append("--sprog-lint")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = gate.main(argv)
        raw = buffer.getvalue()
        payload = json.loads(raw) if raw.strip() else {}
        return code, payload

    @staticmethod
    def findings(payload: dict, gate_id: str, severity: str | None = None) -> list[dict]:
        return [
            f
            for f in payload.get("findings", [])
            if f["gate"] == gate_id and (severity is None or f["severity"] == severity)
        ]

    @staticmethod
    def failures(payload: dict) -> list[dict]:
        return [f for f in payload.get("findings", []) if f["severity"] == "FAIL"]


class TestCleanDraft(GateTestCase):
    def test_clean_draft_passes(self) -> None:
        code, payload = self.run_gate(self.write_draft(CLEAN_DRAFT))
        self.assertEqual(code, 0, msg=f"unexpected failures: {self.failures(payload)}")
        self.assertEqual(payload["result"], "pass")
        self.assertEqual(self.failures(payload), [])

    def test_human_report_renders_and_names_the_verdict(self) -> None:
        draft = self.write_draft(CLEAN_DRAFT)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = gate.main(["--draft", str(draft), "--sources", str(self.sources)])
        self.assertEqual(code, 0)
        self.assertIn("RESULT: PASS", buffer.getvalue())


class TestQuoteContiguity(GateTestCase):
    def test_spliced_quote_fails(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nHe testified, "I would have. That is what I do. No, I cannot"\n(Draper 23:15 to 23:23).\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        misses = self.findings(payload, "2a", "FAIL")
        self.assertEqual(len(misses), 1)
        self.assertIn("not contiguous", misses[0]["message"])
        self.assertIn("closest region", misses[0]["detail"])

    def test_short_quotes_are_not_checked(self) -> None:
        draft = self.write_draft('# DRAFT\n\nHe said "flat out no" to the request.\n')
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a"), [])
        self.assertEqual(code, 0)

    def test_leading_case_fold_and_trailing_comma_are_tolerated(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            'The witness recalled that "the white truck came across without any '
            'warning at all," which is the whole of it.\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_quoted_text_inside_a_marker_is_not_checked(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\n{{FILL: transmission method, for example "Via Certified Mail and Email" | transmission log}}\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a"), [])
        self.assertEqual(code, 0)

    def test_markdown_emphasis_in_the_source_is_tolerated(self) -> None:
        (self.sources / "defense-responses.md").write_text(
            "### REQUEST NO. 3:\n\nAdmit that YOU did not see Plaintiff's vehicle "
            "before YOU changed lanes.\n\n**RESPONSE:** Admit.\n",
            encoding="utf-8",
        )
        draft = self.write_draft(
            '# DRAFT\n\nDefendant answered: "Admit that YOU did not see '
            "Plaintiff's vehicle before YOU changed lanes. RESPONSE: Admit.\"\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_nested_quotation_punctuation_is_tolerated(self) -> None:
        (self.sources / "witness.md").write_text(
            "Lindqvist stated she heard him say something like, 'I thought I had room.' He said it more than once.\n",
            encoding="utf-8",
        )
        draft = self.write_draft(
            "# DRAFT\n\nShe heard him say \"something like, 'I thought I had room,'\" as he walked back.\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_words_added_inside_quotation_marks_still_fail(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nThe witness said "The white truck came across without '
            'any warning at all and in the correct laterality" at the scene.\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        self.assertTrue(self.findings(payload, "2a", "FAIL"))

    def test_bracketed_leading_letter_is_tolerated(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nHe conceded that "[n]o, I cannot. I could not tell you '
            'for sure about that particular lane change" (Draper 23:20 to 23:23).\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_marked_elision_warns_and_names_the_omitted_words(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nHe testified, "No, I cannot. I could not tell you . . . '
            'about that particular lane change" (Draper 23:20 to 23:23).\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        warns = self.findings(payload, "2a", "WARN")
        self.assertEqual(len(warns), 1)
        self.assertIn("elides source text", warns[0]["message"])
        self.assertIn("for sure", warns[0]["detail"])
        self.assertEqual(code, 0)

    def test_ellipsis_spanning_a_different_answer_still_fails(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nHe testified, "I would have. That is what I do . . . '
            'that particular lane change" (Draper 23:15 to 23:23).\n'
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        fails = self.findings(payload, "2a", "FAIL")
        self.assertEqual(len(fails), 1)
        self.assertIn("spans an intervening question", fails[0]["message"])

    def test_curly_quotes_are_normalized(self) -> None:
        draft = self.write_draft("# DRAFT\n\nHe answered, “" + ANSWER_QUOTE + ".”\n")
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2a", "FAIL"), [])
        self.assertEqual(code, 0)


class TestQuestionPairing(GateTestCase):
    def test_range_including_the_question_passes(self) -> None:
        draft = self.write_draft(f'# DRAFT\n\nHe answered, "{ANSWER_QUOTE}" (Draper 23:20 to 23:23).\n')
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2b", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_range_excluding_the_question_fails(self) -> None:
        draft = self.write_draft(f'# DRAFT\n\nHe answered, "{ANSWER_QUOTE}" (Draper 23:22 to 23:23).\n')
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        pairing = self.findings(payload, "2b", "FAIL")
        self.assertEqual(len(pairing), 1)
        self.assertIn("excludes the question", pairing[0]["message"])
        self.assertIn("23:20", pairing[0]["message"])

    def test_quote_absent_from_the_cited_range_fails(self) -> None:
        draft = self.write_draft(f'# DRAFT\n\nHe answered, "{ANSWER_QUOTE}" (Draper 15:1 to 15:5).\n')
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        fails = self.findings(payload, "2b", "FAIL")
        self.assertEqual(len(fails), 1)
        self.assertIn("does not appear within the cited range", fails[0]["message"])
        self.assertIn("23:22", fails[0]["detail"])

    def test_non_transcript_source_is_silent(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nThe witness said "The white truck came across without any '
            'warning at all" (TCR, Party 2 statement).\n'
        )
        _, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2b"), [])

    def test_source_without_page_markers_fails_open_with_a_note(self) -> None:
        stripped = TRANSCRIPT.replace("Page 23", "Excerpt continues")
        alt = self.root / "alt-sources"
        alt.mkdir()
        (alt / "depo-nopages.md").write_text(stripped, encoding="utf-8")
        draft = self.write_draft(f'# DRAFT\n\nHe answered, "{ANSWER_QUOTE}" (Draper 23:22 to 23:23).\n')
        code, payload = self.run_gate(draft, sources=str(alt))
        self.assertEqual(self.findings(payload, "2b", "FAIL"), [])
        notes = self.findings(payload, "2b", "INFO")
        self.assertTrue(notes)
        self.assertIn("fail-open", notes[0]["message"])
        self.assertEqual(code, 0)

    def test_single_point_cite_is_a_note_not_a_failure(self) -> None:
        draft = self.write_draft(f'# DRAFT\n\nHe answered, "{ANSWER_QUOTE}" (Draper 23:22).\n')
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "2b", "FAIL"), [])
        notes = self.findings(payload, "2b", "INFO")
        self.assertTrue(notes)
        self.assertIn("single point", notes[0]["message"])
        self.assertEqual(code, 0)


class TestHeldOutAndWall(GateTestCase):
    def test_held_out_overlap_fails(self) -> None:
        held = self.write_file(
            "held-out.md",
            "Firm analysis: the comparative fault argument will not survive a "
            "motion in limine given the officer's own qualifier.\n",
        )
        draft = self.write_draft(
            "# DRAFT\n\nThe comparative fault argument will not survive a motion "
            "in limine given the officer's own qualifier.\n"
        )
        code, payload = self.run_gate(draft, held_out=str(held))
        self.assertEqual(code, 1)
        leaks = self.findings(payload, "1", "FAIL")
        self.assertEqual(len(leaks), 1)
        self.assertIn("consecutive words", leaks[0]["message"])

    def test_unrelated_held_out_document_is_clean(self) -> None:
        held = self.write_file(
            "held-out.md",
            "Firm analysis: settlement posture and authority discussion, wholly "
            "distinct in wording from anything in the draft body.\n",
        )
        code, payload = self.run_gate(self.write_draft(CLEAN_DRAFT), held_out=str(held))
        self.assertEqual(self.findings(payload, "1", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_hold_out_section_content_repeated_in_the_body_fails(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "The analyst concluded that defendant's late lane change was the sole "
            "proximate cause of this collision.\n\n"
            "## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW\n\n"
            "| Document | Date | Why flagged |\n"
            "| --- | --- | --- |\n"
            "| Analyst memorandum, which concluded that defendant's late lane "
            "change was the sole proximate cause of this collision | Undated | "
            "Attorney work product. |\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        leaks = self.findings(payload, "1", "FAIL")
        self.assertTrue(leaks)
        self.assertIn("HELD OUT", leaks[0]["message"])

    def test_internal_paths_fail(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "See `prior-records.md` for the underlying chart.\n"
            "Sourced from operator/customers/ashton-price/dossier.md.\n"
            "Cached at r2://skills/demand-letter-drafter.\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        wall = self.findings(payload, "6", "FAIL")
        self.assertEqual(len(wall), 3)
        messages = " ".join(f["message"] for f in wall)
        self.assertIn("prior-records.md", messages)
        self.assertIn("operator/customers", messages)
        self.assertIn("r2://", messages)

    def test_hold_out_section_may_name_internal_files(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\nNothing internal here.\n\n"
            "## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW\n\n"
            "| `prior-records.md`, notes for the file | Undated | Work product. |\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "6", "FAIL"), [])
        self.assertEqual(code, 0)


class TestSelfCertification(GateTestCase):
    def test_each_seed_pattern_fires(self) -> None:
        cases = {
            "all responsive documents": (
                "Responding party has produced all responsive documents in its possession, custody, or control."
            ),
            "fully complies": "This response fully complies with the request.",
            "complete and accurate": ("The foregoing responses are complete and accurate."),
            "no responsive documents exist": "No responsive documents exist.",
            "this draft is complete": ("This draft is complete. Nothing further is outstanding."),
        }
        for label, sentence in cases.items():
            with self.subTest(pattern=label):
                draft = self.write_draft(f"# DRAFT\n\n{sentence}\n")
                code, payload = self.run_gate(draft)
                self.assertEqual(code, 1, msg=label)
                self.assertTrue(self.findings(payload, "3", "FAIL"), msg=label)

    def test_itemized_report_does_not_trigger(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "We searched the client file, the medical chronology, the billing "
            "ledger, and the records from three treating providers. We produced "
            "214 pages, Bates AP-0001 through AP-0214. Two categories remain "
            "outstanding: the Valley Imaging report and the employer wage "
            "records, both requested and not yet received.\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "3"), [])
        self.assertEqual(code, 0)

    def test_cited_nonexistence_is_permitted(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\nNo responsive documents exist (Alvarez 38:4 to 38:7). The search is described above.\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "3"), [])
        self.assertEqual(code, 0)


class TestCoverage(GateTestCase):
    DRAFT = (
        "# DRAFT\n\n"
        "### REQUEST FOR PRODUCTION NO. 1:\n\nResponse text.\n\n"
        "### REQUEST FOR PRODUCTION NO. 2:\n\nResponse text.\n"
    )

    def test_missing_item_fails(self) -> None:
        items = self.write_file("items.txt", "RFP 1\nRFP 2\nSROG 3\n")
        code, payload = self.run_gate(self.write_draft(self.DRAFT), propounded=str(items))
        self.assertEqual(code, 1)
        missing = self.findings(payload, "7", "FAIL")
        self.assertEqual(len(missing), 1)
        self.assertIn("SROG 3", missing[0]["message"])

    def test_full_coverage_passes(self) -> None:
        items = self.write_file("items.txt", "RFP 1\n# a comment\n\nRFP 2\n")
        code, payload = self.run_gate(self.write_draft(self.DRAFT), propounded=str(items))
        self.assertEqual(self.findings(payload, "7", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_extra_response_heading_warns(self) -> None:
        items = self.write_file("items.txt", "RFP 1\n")
        code, payload = self.run_gate(self.write_draft(self.DRAFT), propounded=str(items))
        warns = self.findings(payload, "7", "WARN")
        self.assertTrue(warns)
        self.assertIn("NO. 2", warns[0]["message"])
        self.assertEqual(code, 0)

    def test_coverage_does_not_run_without_the_flag(self) -> None:
        _, payload = self.run_gate(self.write_draft(self.DRAFT))
        self.assertEqual(self.findings(payload, "7"), [])


class TestSprogLint(GateTestCase):
    def test_lettered_subparts_fail(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "### SPECIAL INTERROGATORY NO. 5\n\n"
            "State the following for each vehicle you owned on May 23, 2024:\n"
            "(a) the year, make, and model;\n"
            "(b) the registered owner.\n"
        )
        code, payload = self.run_gate(draft, sprog_lint=True)
        self.assertEqual(code, 1)
        fails = self.findings(payload, "8", "FAIL")
        self.assertTrue(fails)
        self.assertIn("2030.060(f)", fails[0]["message"])

    def test_single_fact_interrogatory_passes(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "### SPECIAL INTERROGATORY NO. 6\n\n"
            "State the year of the vehicle you were driving on May 23, 2024.\n"
        )
        code, payload = self.run_gate(draft, sprog_lint=True)
        self.assertEqual(self.findings(payload, "8", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_compound_directives_warn(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "### SPECIAL INTERROGATORY NO. 7\n\n"
            "State the name of each person who witnessed the collision; and "
            "identify each written statement taken from that person.\n"
        )
        code, payload = self.run_gate(draft, sprog_lint=True)
        warns = self.findings(payload, "8", "WARN")
        self.assertTrue(warns)
        self.assertEqual(code, 0)

    def test_lint_does_not_run_without_the_flag(self) -> None:
        draft = self.write_draft("# DRAFT\n\n### SPECIAL INTERROGATORY NO. 5\n\n(a) one;\n(b) two.\n")
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "8"), [])
        self.assertEqual(code, 0)

    def test_absent_special_interrogatories_are_noted(self) -> None:
        code, payload = self.run_gate(self.write_draft(CLEAN_DRAFT), sprog_lint=True)
        notes = self.findings(payload, "8", "INFO")
        self.assertTrue(notes)
        self.assertEqual(code, 0)


class TestVisibleMarkers(GateTestCase):
    def test_marker_inside_html_comment_fails(self) -> None:
        draft = self.write_draft("# DRAFT\n\n<!-- {{ATTORNEY: whether to demand limits}} -->\n")
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        fails = self.findings(payload, "9", "FAIL")
        self.assertTrue(fails)
        self.assertIn("HTML comment", fails[0]["message"])

    def test_unclosed_marker_fails(self) -> None:
        draft = self.write_draft("# DRAFT\n\n{{NOT IN RECORD: the date of loss age, searched intake\n")
        code, payload = self.run_gate(draft)
        self.assertEqual(code, 1)
        fails = self.findings(payload, "9", "FAIL")
        self.assertTrue(fails)
        self.assertIn("unclosed marker", fails[0]["message"])

    def test_nested_markers_are_not_reported_as_unclosed(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n"
            "{{CANDIDATE OBJECTION: premature | Request No. 8 seeks trial "
            "documents. Basis in the record: {{NOT IN RECORD: a trial setting "
            "order, sought in the matter file}} | Request 8}}\n"
        )
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "9", "FAIL"), [])
        self.assertEqual(code, 0)

    def test_marker_inside_code_fence_warns(self) -> None:
        draft = self.write_draft("# DRAFT\n\n```\nCaption: {{FILL: case number | operative pleading}}\n```\n")
        code, payload = self.run_gate(draft)
        self.assertEqual(self.findings(payload, "9", "FAIL"), [])
        self.assertTrue(self.findings(payload, "9", "WARN"))
        self.assertEqual(code, 0)

    def test_fill_without_source_note_warns(self) -> None:
        draft = self.write_draft("# DRAFT\n\n{{FILL: the demand figure}}\n")
        code, payload = self.run_gate(draft)
        self.assertTrue(self.findings(payload, "MI", "WARN"))
        self.assertEqual(code, 0)


class TestCliBehavior(GateTestCase):
    def test_missing_draft_exits_two(self) -> None:
        code, payload = self.run_gate(self.root / "nope.md")
        self.assertEqual(code, 2)
        self.assertEqual(payload, {})

    def test_missing_sources_exit_two(self) -> None:
        code, _ = self.run_gate(self.write_draft(CLEAN_DRAFT), sources=str(self.root / "nowhere"))
        self.assertEqual(code, 2)

    def test_empty_source_directory_exits_two(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, _ = self.run_gate(self.write_draft(CLEAN_DRAFT), sources=str(empty))
        self.assertEqual(code, 2)

    def test_malformed_draft_does_not_crash(self) -> None:
        draft = self.write_draft(
            '# DRAFT\n\nUnbalanced " quote character and a stray {{ and \x00 byte and a lone “ curly open.\n'
        )
        code, payload = self.run_gate(draft)
        self.assertIn(code, (0, 1))
        self.assertIn("result", payload)

    def test_json_shape(self) -> None:
        _, payload = self.run_gate(self.write_draft(CLEAN_DRAFT))
        self.assertEqual(
            sorted(payload.keys()),
            ["counts", "draft", "findings", "heldOut", "result", "sources"],
        )
        self.assertEqual(sorted(payload["counts"].keys()), ["fail", "info", "warn"])

    def test_no_output_string_carries_an_em_dash(self) -> None:
        draft = self.write_draft(
            "# DRAFT\n\n<!-- {{ATTORNEY: reserved}} -->\n"
            "See `prior-records.md`.\n"
            "The foregoing responses are complete and accurate.\n"
            '"I would have. That is what I do. No, I cannot" (Draper 23:15).\n'
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            gate.main(["--draft", str(draft), "--sources", str(self.sources)])
        self.assertNotIn("—", buffer.getvalue())


class TestNormalization(unittest.TestCase):
    def test_smart_characters_collapse(self) -> None:
        self.assertEqual(gate.normalize("“A’s”  — \n b"), '"A\'s" - b')

    def test_quote_variants_cover_fold_and_trailing_punctuation(self) -> None:
        variants = gate.quote_variants("Right around when I moved over,")
        self.assertIn("Right around when I moved over", variants)
        self.assertIn("right around when I moved over", variants)


if __name__ == "__main__":
    unittest.main()
