"""The champion wall at the reply relay (``shared.champion``, ``relay_checks``).

A client champion is enrolled on the guide seat with a champion code. Every
reply to them passes the relay's wall, keyed on the RECIPIENT: a reply that
carries a representative marker, cites a library document the index does not
open to champions, or names a heading that document does not have, is held,
and the champion receives the one fixed sentence at the end of the turn. A
representative's reply is untouched, whatever it carries.

Driven through the real reply plugin (``on_post_tool_call`` for each draft,
``on_post_llm_call`` for the turn's end), the real relay, held-notice path and
audit rows, a real enrolled list and a fixture library whose INDEX carries the
Audience column; only the broker transmit is a recording stub.

FALSIFIER: remove the ``_champion_walled`` step from ``relay_checks.body_and_facts``
and every "held" test below sends the rep-only reply to the champion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from shared import answered_messages, champion, outbound_gate, output_checklist
from shared import sender_enrollment as se
from shared.pending_acts import PENDING_ACTS
from tests.conftest import load_plugin
from tests.test_reply import _draft, _record_origin, relay_mod  # noqa: F401 - fixture reuse
from tests.test_reply_text_delivery import gate_calls  # noqa: F401 - fixture reuse

CHAMPION = "dana@example-brokerage.test"
REP = "rep@example.test"
SESSION = "s-champ"

#: A library index as the library workstream authors it: the Audience column,
#: values exactly ``rep`` or ``rep, champion``.
INDEX = """# The product library

## The documents

| Document | What it covers | Questions it answers | Audience |
| --- | --- | --- | --- |
| [jobs.md](jobs.md) | Queued jobs | How is long work run? | rep, champion |
| [industry-packs.md](industry-packs.md) | Each industry | What does it do here? | rep, champion |
| [demo-meetings.md](demo-meetings.md) | Running a demo | How do I run a demo? | rep |
| [who-smd-sells-to.md](who-smd-sells-to.md) | The buyer | Does this prospect fit? | rep |

## How to read the labels

Text.
"""


def write_library(root: Path, index: str = INDEX) -> Path:
    """A product-guide skill directory with the fixture index and documents."""
    refs = root / "product-guide" / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (root / "product-guide" / "SKILL.md").write_text("# product-guide\n", encoding="utf-8")
    (refs / "INDEX.md").write_text(index, encoding="utf-8")
    docs = {
        "jobs.md": ["Kinds of queued job", "Holds"],
        "industry-packs.md": ["Real estate", "Insurance agencies"],
        "demo-meetings.md": ["The seven acts"],
        "who-smd-sells-to.md": ["The buyer"],
    }
    for name, heads in docs.items():
        body = "".join(f"## {h}\n\nWords.\n\n" for h in heads)
        (refs / name).write_text(f"# {name}\n\n{body}", encoding="utf-8")
    return root / "product-guide"


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill = write_library(tmp_path / "skills")
    monkeypatch.setenv(champion.DIR_ENV, str(skill))
    return skill


def enroll(path: Path, *entries: tuple[str, str]) -> None:
    rows = [{"address": a, "label": label, "code_id": "c_1"} for a, label in entries]
    path.write_text(json.dumps({"schema": 1, "enrolled": rows}), encoding="utf-8")


@pytest.fixture
def seat(relay_mod, gate_calls, library, tmp_path, monkeypatch):  # noqa: F811 - fixture reuse
    mod, d1, sent = relay_mod
    enrolled = tmp_path / "enrolled.json"
    monkeypatch.setenv(se.ENROLLED_ENV, str(enrolled))
    enroll(enrolled, (CHAMPION, se.CHAMPION_LABEL), (REP, "admin-issued"))
    doc = yaml.safe_load(mod._YAML_PATH.read_text())
    doc["vertical"] = "mixed"
    doc["scope"]["inbound_allow_from"] = [CHAMPION, REP]
    mod._YAML_PATH.write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(mod, "_HOLD_COUNTS", mod.held_notice.HoldCounter(), raising=False)
    monkeypatch.setattr(mod, "_HELD_LINES", mod.held_notice.Once(), raising=False)
    monkeypatch.setattr(mod, "_TURN_SENDS", mod.text_delivery.TurnSends(), raising=False)
    monkeypatch.setattr(mod, "_TEXT_DELIVERED", mod.text_delivery.DeliveredOnce(), raising=False)
    champion.WALLED._reset_for_tests()
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()
    yield mod, d1, sent
    champion.WALLED._reset_for_tests()
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()


GOOD = (
    "The Operator can take a long piece of work as a queued job and tell the person "
    'when it is done.\n\nSource: jobs.md, "Kinds of queued job"'
)
HELD = {
    "practice": "Practice (playing: an owner, scenario s1, objection 1 of 4)\n\nWhat worries you?",
    "drill": "Drill (theme: pricing questions, drill 1 of 3)\n\nAnswer this one.",
    "lesson": "Lesson 3 of 11: How a request flows\n\nRead the section first.",
    "quiz_choices": "1. What is a seat?\nA. A machine\nB. A person\nC. A plan\nD. A price",
    "quiz_answers": "Here are my letters:\n1 B\n2 C\n3 A",
    "rep_document": 'Bring the demo seat to the meeting.\n\nSource: demo-meetings.md, "The seven acts"',
    "bad_heading": 'The Operator runs queued jobs.\n\nSource: jobs.md, "Pricing for jobs"',
    "named_in_text": "The playbook in who-smd-sells-to.md says who to call first.",
    "unparseable_source": "The Operator runs queued jobs.\n\nSource: the jobs page",
}


def _turn(mod, sender: str, text: str, mid: str, call: str = "c1") -> None:
    """One email's turn, in its own session, as a seat runs it."""
    session = f"{SESSION}-{mid}"
    _record_origin(sender=sender, message_id=mid, session=session)
    mod.on_pre_llm_call(session_id=session, user_message="(prompt)")
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft([sender], text=text, subject="Re: question"),
        session_id=session,
        tool_call_id=call,
    )
    mod.on_post_llm_call(session_id=session, assistant_response="Done.")


def _held(d1) -> list[dict]:
    return [m for a, m in d1.events() if a == "REPLY_HELD"]


def test_an_allowed_champion_reply_is_sent(seat) -> None:
    mod, d1, sent = seat
    _turn(mod, CHAMPION, GOOD, "m-good")
    assert [s["text"] for s in sent] == [GOOD]
    assert _held(d1) == []


@pytest.mark.parametrize("case", sorted(HELD))
def test_a_rep_only_reply_to_a_champion_is_held_and_the_sentence_sent(seat, case) -> None:
    mod, d1, sent = seat
    _turn(mod, CHAMPION, HELD[case], f"m-{case}")
    assert [s["text"] for s in sent] == [champion.SENTENCE], "only the fixed sentence goes"
    [row] = _held(d1)
    assert row["reason"] == champion.HOLD_REASON
    assert row["rule"] in {
        champion.REP_MARKER,
        champion.SOURCE_NOT_ALLOWED,
        champion.HEADING_NOT_FOUND,
        champion.SOURCE_UNREADABLE,
    }


def test_each_reason_is_the_one_recorded(seat) -> None:
    mod, d1, _sent = seat
    expected = {
        "practice": champion.REP_MARKER,
        "rep_document": champion.SOURCE_NOT_ALLOWED,
        "bad_heading": champion.HEADING_NOT_FOUND,
        "unparseable_source": champion.SOURCE_UNREADABLE,
    }
    for case, reason in expected.items():
        _turn(mod, CHAMPION, HELD[case], f"r-{case}", call=f"c-{case}")
        assert _held(d1)[-1]["rule"] == reason, case


@pytest.mark.parametrize("case", sorted(HELD))
def test_a_representatives_reply_is_untouched(seat, case) -> None:
    mod, d1, sent = seat
    _turn(mod, REP, HELD[case], f"rep-{case}")
    assert [s["text"] for s in sent] == [HELD[case]]
    assert [r for r in _held(d1) if r["reason"] == champion.HOLD_REASON] == []


def test_an_index_without_the_audience_column_opens_nothing_to_champions(
    seat, library, monkeypatch
) -> None:
    mod, _d1, sent = seat
    plain = "\n".join(
        line.rsplit("|", 2)[0] + "|" if line.startswith("|") else line
        for line in INDEX.splitlines()
    )
    assert "Audience" not in plain and "| [jobs.md](jobs.md) |" in plain
    (library / "references" / "INDEX.md").write_text(plain, encoding="utf-8")
    assert champion.allowlist() == frozenset()
    _turn(mod, CHAMPION, GOOD, "m-closed")
    assert [s["text"] for s in sent] == [champion.SENTENCE]
    sent.clear()
    _turn(mod, REP, GOOD, "m-rep-open", call="c2")
    assert [s["text"] for s in sent] == [GOOD], "a representative never reads the allowlist"


def test_a_champion_hold_sends_the_sentence_even_where_the_seat_authors_no_line(seat) -> None:
    mod, _d1, sent = seat
    assert mod.held_notice.policy(yaml.safe_load(mod._YAML_PATH.read_text())).enabled is False
    _turn(mod, CHAMPION, HELD["practice"], "m-nopolicy")
    assert [s["text"] for s in sent] == [champion.SENTENCE]


# ---------------------------------------------------------------------------
# the allowlist and the wall, as functions
# ---------------------------------------------------------------------------


def test_the_allowlist_is_parsed_from_the_audience_column(library) -> None:
    assert champion.allowlist() == {"jobs.md", "industry-packs.md"}
    assert champion.parse_allowlist("no table here") == frozenset()
    assert champion.parse_allowlist(None) == frozenset()  # type: ignore[arg-type]


def test_an_unreadable_index_opens_nothing(library) -> None:
    (library / "references" / "INDEX.md").unlink()
    assert champion.allowlist() == frozenset()
    assert champion.wall_reason(GOOD) == champion.SOURCE_NOT_ALLOWED


def test_the_real_index_parses_without_error() -> None:
    real = Path(__file__).resolve().parents[2] / "skills" / "product-guide" / "references"
    allowed = champion.allowlist(real)
    assert "demo-meetings.md" not in allowed and "who-smd-sells-to.md" not in allowed
    assert allowed <= {p.name for p in real.glob("*.md")}


# ---------------------------------------------------------------------------
# the fixed words pass the seat's own reply checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        champion.SENTENCE,
        se.welcome_text("Dana Reyes", champion=True),
        se.welcome_text(None, champion=True),
    ],
)
def test_the_champion_words_pass_the_reply_checks(text, library) -> None:
    for surface in (output_checklist.STAFF_SEND, output_checklist.EVERY_OUTPUT):
        assert output_checklist.check(text, surface) == [], (surface, text)
    assert outbound_gate.evaluate(text, None, None).allowed, text
    assert champion.wall_reason(text) == ""
    assert "—" not in text and "!" not in text


def test_the_champion_welcome_says_what_the_guide_is_for_them() -> None:
    text = se.welcome_text("Dana Reyes", champion=True)
    assert text.startswith("Hello Dana Reyes,\n")
    lowered = text.lower()
    for word in ("practice", "training", "notes", "lesson", "remind", "quiz", "pitch"):
        assert word not in lowered, word
    for said in (
        "smd services product guide",
        "kind of business",
        "making the case inside your business",
        "document and section",
        "conversation with smd services",
        "first question in a reply",
    ):
        assert said in lowered, said
    # The demo seat never gives a champion welcome.
    assert se.welcome_text("Dana Reyes", champion=True, demo=True) == se.welcome_text(
        "Dana Reyes", demo=True
    )


def test_the_plugin_module_is_the_relay_the_seat_runs() -> None:
    mod = load_plugin("hermes-smd-reply")
    assert mod.relay_checks.champion is champion
