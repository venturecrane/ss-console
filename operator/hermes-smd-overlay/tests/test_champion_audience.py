"""A seat whose product-guide entry authors ``audience: champion`` (``shared.champion_audience``).

The first such seat is a hosted agent whose own skill captures and recalls
open-house visitors, and it also enables the product library. Nobody there is
enrolled; the wall and the fence are keyed by PATH, for everyone:

* its own replies (a capture confirmation, a recall answer) pass untouched;
* a reply citing a library document the index does not open to champions is
  held, and so is a reply after the turn read product-guide's procedure that
  cites no open library document;
* reads under ``product-guide/references/`` reach only the open documents and
  the index; the seat's own skill, its shelf and its records are untouched;
* a seat without the setting is unchanged.

Driven through the real reply plugin and the real initiation plugin, with a real
customer.yaml and a fixture library carrying the Audience column.

FALSIFIER: make ``champion.walls`` answer ``""`` for an audience seat and the
held tests send the reply; drop ``audience_check`` from the fence and the
references reads go through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared import answered_messages, champion, champion_audience, provenance
from shared.pending_acts import PENDING_ACTS
from tests.conftest import load_plugin
from tests.test_champion_wall import library  # noqa: F401 - the fixture library is a fixture
from tests.test_reply import _draft, _record_origin, relay_mod  # noqa: F401 - fixture reuse
from tests.test_reply_text_delivery import gate_calls  # noqa: F401 - fixture reuse

AGENT = "agent@example-brokerage.test"

GUIDE_ENTRY = {
    "name": "product-guide",
    "version": "pending",
    "initiation": {"manual": True, "scheduled": False, "webhook": True},
    "enabled": True,
    "settings": {"audience": "champion"},
}
CAPTURE_ENTRY = {
    "name": "open-house-visitor-capture",
    "version": "pending",
    "initiation": {"manual": True, "scheduled": True, "webhook": True},
    "enabled": True,
    "settings": {"recipients": "roster"},
}

#: A capture confirmation in the open-house skill's own shape (SKILL.md,
#: Capture step 8 and "How you write").
CAPTURE_REPLY = """## Stored from Sunday's open house

- **Visitor:** Priya Shah
- **Property:** 1420 E 4th St
- **Contact:** priya@example.test, 602 555 0177
- **Stated intent:** buying in spring, wants a yard
- **Follow-ups:** Monday, November 17; Monday, November 24; Monday, December 8
- **Record:** priya-shah-1420-e-4th-2026-11-16

Brokery Agent
"""
#: A recall answer in its shape (Recall steps 1 to 3).
RECALL_REPLY = """## Who you met at 1420 E 4th St

- **Priya Shah**, Sunday, November 16: "Wants a yard; asked about the school district."
- **The Nguyens**, Sunday, November 16: "Selling a condo first; 3 bedrooms."

I searched your records for 1420 E 4th St from November 1 to today.

Brokery Agent
"""
GOOD_LIBRARY = (
    "An Operator can take a long piece of work as a queued job for a brokerage.\n\n"
    'Source: jobs.md, "Kinds of queued job"'
)
REP_ONLY = 'Bring the demo seat to the meeting.\n\nSource: demo-meetings.md, "The seven acts"'
NO_SOURCE = "An Operator can take on the coordination a brokerage's staff repeat every day."


def _author(path: Path, *, audience: bool) -> None:
    doc = yaml.safe_load(path.read_text())
    doc["vertical"] = "real-estate"
    doc["scope"]["inbound_allow_from"] = [AGENT]
    skills = [CAPTURE_ENTRY, {**GUIDE_ENTRY, "settings": {}} if not audience else GUIDE_ENTRY]
    doc["personas"] = [{"slug": "brokery-agent", "skills": skills}]
    path.write_text(yaml.safe_dump(doc))


@pytest.fixture
def seat(relay_mod, gate_calls, library, monkeypatch, tmp_path):  # noqa: F811 - fixture reuse
    mod, d1, sent = relay_mod
    _author(mod._YAML_PATH, audience=True)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(mod._YAML_PATH))
    monkeypatch.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(tmp_path / "enrolled.json"))
    monkeypatch.setattr(mod, "_HOLD_COUNTS", mod.held_notice.HoldCounter(), raising=False)
    monkeypatch.setattr(mod, "_HELD_LINES", mod.held_notice.Once(), raising=False)
    monkeypatch.setattr(mod, "_TURN_SENDS", mod.text_delivery.TurnSends(), raising=False)
    monkeypatch.setattr(mod, "_TEXT_DELIVERED", mod.text_delivery.DeliveredOnce(), raising=False)
    champion_audience.GUIDE_READ._reset_for_tests()
    champion.WALLED._reset_for_tests()
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()
    yield mod, d1, sent
    champion_audience.GUIDE_READ._reset_for_tests()
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()


def _turn(mod, text: str, mid: str, *, read_guide: bool = False) -> str:
    session = f"s-aud-{mid}"
    _record_origin(sender=AGENT, message_id=mid, session=session)
    mod.on_pre_llm_call(session_id=session, user_message="(prompt)")
    if read_guide:
        load_plugin("hermes-smd-initiation").champion_fence.on_tool_done(
            session_id=session,
            tool_name="read_file",
            args={"path": "/app/skills/product-guide/SKILL.md"},
        )
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft([AGENT], text=text, subject="Re: question"),
        session_id=session,
        tool_call_id=f"c-{mid}",
    )
    mod.on_post_llm_call(session_id=session, assistant_response="Done.")
    return session


def _held(d1) -> list[dict]:
    return [m for a, m in d1.events() if a == "REPLY_HELD"]


@pytest.mark.parametrize("text", [CAPTURE_REPLY, RECALL_REPLY, GOOD_LIBRARY, NO_SOURCE])
def test_the_seats_own_replies_and_an_open_citation_go_out(seat, text) -> None:
    mod, d1, sent = seat
    _turn(mod, text, f"m-{abs(hash(text))}")
    assert [s["text"] for s in sent] == [text]
    assert _held(d1) == []


def test_a_rep_only_citation_is_held_and_the_seats_own_line_is_unchanged(seat) -> None:
    mod, d1, sent = seat
    _turn(mod, REP_ONLY, "m-rep-only")
    assert sent == [], "no sentence swap: this seat authors no held line"
    [row] = _held(d1)
    assert row["reason"] == champion.HOLD_REASON and row["rule"] == champion.SOURCE_NOT_ALLOWED


def test_a_library_answer_must_cite_the_library(seat) -> None:
    mod, d1, sent = seat
    _turn(mod, NO_SOURCE, "m-uncited", read_guide=True)
    assert sent == []
    assert _held(d1)[-1]["rule"] == champion_audience.SOURCE_MISSING
    _turn(mod, GOOD_LIBRARY, "m-cited", read_guide=True)
    assert [s["text"] for s in sent] == [GOOD_LIBRARY]


def test_a_new_turn_starts_with_nothing_read(seat) -> None:
    fence = load_plugin("hermes-smd-initiation").champion_fence
    champion_audience.GUIDE_READ.mark("s-next")
    fence.on_turn_start(session_id="s-next")
    assert not champion_audience.GUIDE_READ.has("s-next")


def test_a_seat_without_the_setting_is_unchanged(seat) -> None:
    mod, d1, sent = seat
    _author(mod._YAML_PATH, audience=False)
    _turn(mod, REP_ONLY, "m-plain")
    assert [s["text"] for s in sent] == [REP_ONLY]
    assert _held(d1) == []


# ---------------------------------------------------------------------------
# the fence, keyed by path
# ---------------------------------------------------------------------------


def _fence(tool: str, args: dict) -> dict | None:
    return load_plugin("hermes-smd-initiation").champion_fence.on_pre_tool_call(
        tool_name=tool, args=args
    )


@pytest.mark.parametrize(
    ("tool", "args", "allowed"),
    [
        ("read_file", {"path": "/app/skills/product-guide/SKILL.md"}, True),
        ("read_file", {"path": "/app/skills/product-guide/references/INDEX.md"}, True),
        ("read_file", {"path": "/app/skills/product-guide/references/jobs.md"}, True),
        ("read_file", {"path": "/app/skills/product-guide/references/demo-meetings.md"}, False),
        (
            "read_file",
            {
                "path": "/opt/data/profiles/brokery-agent/skills/product-guide/references/demo-meetings.md"
            },
            False,
        ),
        ("read_file", {"path": "/app/skills/open-house-visitor-capture/SKILL.md"}, True),
        ("read_file", {"path": "/opt/data/reference/pilot-program.md"}, True),
        (
            "skill_view",
            {"name": "product-guide", "file_path": "references/demo-meetings.md"},
            False,
        ),
        ("skill_view", {"name": "product-guide"}, True),
        ("skill_view", {"name": "open-house-visitor-capture"}, True),
        (
            "search_files",
            {"pattern": "demo", "path": "/app/skills/product-guide/references"},
            False,
        ),
        (
            "terminal",
            {"command": "cat /app/skills/product-guide/references/demo-meetings.md"},
            False,
        ),
    ],
)
def test_on_an_audience_seat_reads_of_the_library_are_limited_for_everyone(
    seat, tool, args, allowed
) -> None:
    provenance.note_session("s-anyone")
    verdict = _fence(tool, args)
    if allowed:
        assert verdict is None, (tool, args)
    else:
        assert verdict is not None and verdict["action"] == "block", (tool, args)


def test_a_seat_without_the_setting_reads_as_before(seat) -> None:
    mod, _d1, _sent = seat
    _author(mod._YAML_PATH, audience=False)
    path = "/app/skills/product-guide/references/demo-meetings.md"
    assert _fence("read_file", {"path": path}) is None


def test_the_record_store_and_training_guards_do_not_apply(seat) -> None:
    """The champion predicate is not involved: nobody here is a champion."""
    assert not champion.is_champion(AGENT)
    assert champion.walls(AGENT, {"personas": []}) == ""


def test_the_plugin_registers_the_audience_hooks_on_such_a_seat(seat) -> None:
    class Ctx:
        def __init__(self) -> None:
            self.hooks: list[tuple[str, object]] = []

        def register_hook(self, name, fn) -> None:
            self.hooks.append((name, fn))

        def register_tool(self, **_kw) -> None:
            pass

    mod = load_plugin("hermes-smd-initiation")
    ctx = Ctx()
    mod.register(ctx)
    assert ("post_tool_call", mod.champion_fence.on_tool_done) in ctx.hooks
    assert ("pre_tool_call", mod.champion_fence.on_pre_tool_call) in ctx.hooks


def test_the_setting_is_read_from_the_product_guide_entry_only() -> None:
    raw = {"personas": [{"skills": [{**CAPTURE_ENTRY, "settings": {"audience": "champion"}}]}]}
    assert not champion_audience.seat_has_audience(raw)
    assert champion_audience.seat_has_audience({"personas": [{"skills": [GUIDE_ENTRY]}]})
    off = {**GUIDE_ENTRY, "enabled": False}
    assert not champion_audience.seat_has_audience({"personas": [{"skills": [off]}]})


# ---------------------------------------------------------------------------
# every citation is checked; the skill's own fixed replies are not uncited
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'Queued jobs.\n\nSource: jobs.md, "Kinds of queued job"; demo-meetings.md, "The seven acts"',
        "As the who-smd-sells-to.md document says, call the owner first.\n\n"
        'Source: jobs.md, "Kinds of queued job"',
    ],
)
def test_a_rep_only_document_anywhere_is_held(library, text) -> None:  # noqa: F811 - the library fixture
    assert champion_audience.source_reason(text, guide_read=True) == champion.SOURCE_NOT_ALLOWED


@pytest.mark.parametrize("sentence", champion_audience.FIXED_REPLIES)
def test_each_fixed_reply_is_not_held_as_uncited(library, sentence) -> None:  # noqa: F811 - the library fixture
    assert champion_audience.source_reason(sentence, guide_read=True) == ""
    signed = f"{sentence}\n\nBrokery Agent\n"
    assert champion_audience.source_reason(signed, guide_read=True) == ""


def test_the_could_not_open_reply_is_not_held_as_uncited(library) -> None:  # noqa: F811 - the library fixture
    text = (
        "I could not open the library just now. Please try again, or ask SMD at team@smd.services."
    )
    assert champion_audience.source_reason(text, guide_read=True) == ""


def test_an_uncited_free_answer_is_still_held(library) -> None:  # noqa: F811 - the library fixture
    for text in (
        NO_SOURCE,
        "The product library doesn't cover that. SMD can answer it at team@smd.services. "
        "But an Operator can also run your showings.",
    ):
        assert champion_audience.source_reason(text, guide_read=True) == (
            champion_audience.SOURCE_MISSING
        ), text
