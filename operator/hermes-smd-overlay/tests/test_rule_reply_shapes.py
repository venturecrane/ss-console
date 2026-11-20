"""The replay table: every reply shape, and what the plain-yes grammar does with it.

The grammar (``shared/reply_shape.py``, approved 2026-11-20): after quoted
history is removed, the sender's own text is exactly
``[G] A+ [C [N]] [B] [F]``: greeting, answer lines, closing, his name, HIS learned
signature block (whole), a device footer. Nothing else. Each row is one reply,
the person's open rules, the read-back the broker bound it to (or none), the
sender, his learned block, and the one verdict it must produce. The table runs
twice:

* through :func:`shared.rule_confirm.resolve`, the decision itself;
* through the establishment plugin's confirmation hook, with a fake broker, so
  that for every row that must NOT confirm, nothing was committed: no
  ``establish_submit``, nothing stashed for a submit, no act confirmed.

Then a property test: an affirmative first line followed by any one line of
ordinary words is never a confirmation, whatever block is learned. Every name,
address, rule and id here is invented; the block follows the SHAPE of the firm
attorney's real one (the replay of his 40 real emails), with placeholder values.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pytest

from shared import inbound, reply_shape
from shared import rule_confirm as rc
from shared import rule_confirm_reply as rcr
from tests.rule_turns import bound_turn
from tests.test_confirm_by_reply import _Cfg as _ShapeCfg
from tests.test_confirm_by_reply import seat  # noqa: F401 - the fixture

PAT = "pat@firm.example"
SAM = "sam@firm.example"
A, B, C, D, W, ACT = "1a1a1a1a", "2b2b2b2b", "3c3c3c3c", "5e5e5e5e", "4d4d4d4d", "6f6f6f6f"
TEXT = {
    A: "In memos, object under the cited section, see Example v. Sample.",
    B: "In memos, put the answer in the first line.",
    C: "In memos, no headings.",
    D: "In memos, put the answer in the first two lines.",
    W: "In client letters, be brief.",
    ACT: 'Place a records order on matter 4417. Reply "yes, place it" to proceed.',
}
DISPLAY = {PAT: "Pat Q. Doe", SAM: "Sam Roe"}
#: His roster name (users[].full_name); on the Graph lane the from: line is the
#: bare address, so it is the only source of his name.
ROSTER_NAME = "Patrick Q. Doe"

#: The shape of his real block: closing, full name with a middle initial, firm
#: line, street, city, two phones with the label after the number.
REAL_BLOCK = (
    "Cordially,\n\nPatrick Q. Doe\nDoe & Roe LLP\n1234 N. Central Ave., Suite 500\n"
    "Phoenix, AZ 85004\n(602) 555-0100 office\n(602) 555-0101 fax"
)
#: Thirteen lines, a title, a web line and a notice in it: learned whole, it is his.
LONG_BLOCK = (
    "Best regards,\n\nPat Q. Doe, Esq.\nPartner\nDoe & Roe LLP\n1234 N. Central Ave.\n"
    "Suite 500\nPhoenix, AZ 85004\nOffice: (602) 555-0100\nFax: (602) 555-0101\n"
    "pat@firm.example\nwww.doeroe.example\nCONFIDENTIALITY NOTICE: This email may be privileged."
)
LEARNED = tuple(line for line in REAL_BLOCK.splitlines() if line.strip())
LONG_LEARNED = tuple(line for line in LONG_BLOCK.splitlines() if line.strip())
#: A block that starts with his name, as many firms' do.
NAME_BLOCK = "Patrick Q. Doe\nPartner\nDoe & Roe LLP\n100 N. Central Ave.\nPhoenix, AZ 85004"
NAME_LEARNED = tuple(NAME_BLOCK.splitlines())
#: The plain yeses the replay found confirming nothing (S08 S10 S12 S14 S20 S23
#: S30 S31 S33 S35 S38 S39 by shape; the client workspace holds the texts).
REAL_YESES = (
    "Yes", "Yes.", "yes", "Yes, thanks", "Yes please", "Yes, that's right",
    "Confirmed.", "OK", "Correct", "Yes - thank you", "Sounds good, thanks", "Hi,\nYes",
)  # fmt: skip
QUOTE_AB = (
    f"\n\nOn Tue, Riley <ops@firm.example> wrote:\n> [rule {A}] {TEXT[A]}\n"
    f"> [rule {B}] {TEXT[B]}\n> Reply yes to confirm both."
)


@dataclass(frozen=True)
class Row:
    name: str
    body: str
    expect: str  # rc.CONFIRMED / rc.ASK / rc.NONE / rc.DECLINED
    confirms: tuple[str, ...] = ()
    open: tuple[str, ...] = (A,)
    binding: tuple[str, ...] | None = (A,)
    listed: bool = False
    sender: str = PAT
    reason: str | None = None
    rows: tuple[dict, ...] = field(default_factory=tuple)
    #: His learned block (workspace_broker/sender_block), as the broker returns it.
    block: tuple[str, ...] = LEARNED
    #: The Graph lane: from: is the bare address, his name is the roster's.
    bare_from: bool = True
    full_name: str = ROSTER_NAME


def _yes(name, body, **kw):
    """A row that confirms rule A (or ``confirms``) of a read-back bound to A."""
    return Row(name, body, rc.CONFIRMED, kw.pop("confirms", (A,)), **kw)


def _restate(name, body, **kw):
    return Row(name, body, rc.ASK, reason=rc.ASK_QUALIFIED, **kw)


ABC = (A, B, C)
LISTED = {"open": ABC, "binding": ABC, "listed": True}

ROWS: tuple[Row, ...] = (
    # ---- the firm's real plain yeses with his real block (the client's loop) ---
    *(_yes(f"real: {yes!r} and his block", f"{yes}\n\n{REAL_BLOCK}") for yes in REAL_YESES),
    _yes(
        "real: a yes to two and his block",
        f"Yes\n\n{REAL_BLOCK}",
        confirms=(A, B),
        open=(A, B),
        binding=(A, B),
    ),
    _yes(
        "real: from: with a display name too", f"Yes\n\n{REAL_BLOCK}", bare_from=False, full_name=""
    ),
    # ---- a block that starts with his name, after a closing (review 2026-11-20):
    # read as C B, never as C N and then a block missing its first line ---------
    _yes("name-first block after a closing", f"Yes\n\nThanks,\n{NAME_BLOCK}", block=NAME_LEARNED),
    _yes(
        "name-first block after a closing and his first name",
        f"Yes\n\nThanks,\nPatrick\n{NAME_BLOCK}",
        block=NAME_LEARNED,
    ),
    _yes("name-first block alone", f"Yes\n\n{NAME_BLOCK}", block=NAME_LEARNED),
    _restate(
        "name-first block after a closing, then a line",
        f"Yes\n\nThanks,\n{NAME_BLOCK}\nOnly for Craig",
        block=NAME_LEARNED,
    ),
    _restate(
        "name-first block missing its name after a closing and his name",
        "Yes\n\nThanks,\nPatrick Q. Doe\n" + "\n".join(NAME_LEARNED[1:-1]),
        block=NAME_LEARNED,
    ),
    # ---- a plain yes with no block -------------------------------------------
    _yes("yes", "yes"),
    _yes("Yes.", "Yes."),
    _yes("Yes!!!", "Yes!!!"),
    _yes("Yes, thanks", "Yes, thanks"),
    _yes("Thanks, yes", "Thanks, yes"),
    _yes("greeting line then yes", "Hi,\nYes"),
    _yes("yes then his first name", "Yes - Patrick"),
    _yes("ok", "ok"),
    _yes("Confirmed.", "Confirmed."),
    _yes("yes please", "Yes please"),
    _yes("yep thx", "yep thx"),
    _yes("ok cool", "ok cool"),
    _yes("yes, addressing the Operator", "Good morning Riley,\nYes, thanks."),
    _yes("yes to both, bound to two", "Yes to both", confirms=(A, B), open=(A, B), binding=(A, B)),
    _yes("yes to all three", "Yes to all three.", confirms=ABC, open=ABC, binding=ABC),
    _yes("a closing", "Yes\nThanks,"),
    _yes("a closing then his name", "Yes\nThanks,\nPatrick"),
    _yes("a closing with his full name", "Yes\nBest regards, Patrick Doe"),
    _yes("device footer", "Yes\n\nSent from my iPhone"),
    _yes("block then device footer", f"Yes\n\n{REAL_BLOCK}\n\nSent from my iPhone"),
    _yes("Outlook footer", "Yes\n\nGet Outlook for iOS"),
    _yes("a thirteen-line block learned whole", f"Yes\n\n{LONG_BLOCK}", block=LONG_LEARNED),
    _yes(
        "Gmail attribution wrapped over two lines",
        "Yes\n\nOn Tue, Nov 18, 2026 at 3:04 PM Riley <\nops@firm.example> wrote:\n> anything quoted",
    ),
    _yes(
        "a quoted tag of another rule changes nothing", f"Yes.{QUOTE_AB}", open=(A, B), binding=(A,)
    ),
    _yes("a carried rule since closed is skipped", "Yes", open=(A,), binding=(A, B)),
    _yes(
        "the newer of a restatement pair, the older closed",
        "Yes",
        confirms=(D,),
        open=(D,),
        binding=(B, D),
        listed=True,
    ),
    # ---- a pick from the numbered list ---------------------------------------
    _yes("list: all", "all", confirms=ABC, **LISTED),
    _yes("list: a plain yes is all", "Yes", confirms=ABC, **LISTED),
    _yes("list: 1 and 3", "1 and 3", confirms=(A, C), **LISTED),
    _yes("list: 1, 3", "1, 3", confirms=(A, C), **LISTED),
    _yes("list: 1-3", "1-3", confirms=ABC, **LISTED),
    _yes("list: 1 to 3 is a range", "1 to 3", confirms=ABC, **LISTED),
    _yes("list: 1 through 2", "1 through 2", confirms=(A, B), **LISTED),
    _yes("list: the first one", "the first one", **LISTED),
    _yes("list: 2", "2", confirms=(B,), **LISTED),
    _yes("list: both", "both", confirms=(A, B), open=(A, B), binding=(A, B), listed=True),
    _yes("list: yes to 2, and his block", f"Yes to 2\n\n{REAL_BLOCK}", confirms=(B,), **LISTED),
    _yes("old list: 1 and 3, 3 since closed", "1 and 3", open=(A, B), binding=ABC, listed=True),
    _yes(
        "[rule A] yes and [rule B] yes, no binding",
        f"[rule {A}] yes and [rule {B}] yes",
        confirms=(A, B),
        open=(A, B),
        binding=None,
    ),
    # ---- instructions shaped like signature lines: never his block -----------
    *(
        _restate(f"disguised: {extra}", f"Yes\n{extra}\n\n{REAL_BLOCK}")
        for extra in (
            "Only For Craig LLC",
            "But Only In PA",
            "Use Twelve Point LP",
            "Not For Doug's Cases PC",
            "Craig & Associates",
            "Pat Doe | Only For Craig LLC",
            "12 Point Font Avenue",
            "14 Point Bold Center",
            "2 Copies To The Court",
            "This email and any attachments should go to Craig too",
            "This message and any files I send later use 12pt",
            "Confidentiality Notice: actually make it 12pt",
            "On second thought as Craig wrote: make it 14pt",
            "_____\nmake it 14",
            "P: 1.5 1.5 1.5 1.5",
            "Craig only, az 85004",
            "Pat Never Doe",
            "Patrick Never Doe",
            "Partner",
        )
    ),
    _restate("his block with an instruction under it", f"Yes\n\n{REAL_BLOCK}\nOnly For Craig LLC"),
    _restate(
        "his block with a line inserted",
        f"Yes\n\n{REAL_BLOCK.replace('Doe & Roe LLP', 'Doe & Roe LLP' + chr(10) + 'Only for Craig')}",
    ),
    _restate("his block missing a line", f"Yes\n\n{REAL_BLOCK.replace('(602) 555-0101 fax', '')}"),
    _restate("a block nobody learned", f"Yes\n\n{REAL_BLOCK}", block=()),
    _restate("a learned block of someone else's", f"Yes\n\n{REAL_BLOCK}", block=LONG_LEARNED),
    _restate(
        "a qualifier from his own quoted email, re-typed",
        f"Yes\nCraig & Associates\n\n{REAL_BLOCK}\n\nOn Tue, Nov 18, 2026, Pat <{PAT}> wrote:\n> Craig & Associates",
    ),
    # ---- content anywhere: read back again ------------------------------------
    _restate("closing then a change", "Yes\nThanks,\nActually make that 12pt"),
    _restate("closing then a withdrawal", "Yes\nThanks\nNo, scratch that"),
    _restate("a P.S. under the name", "Yes\nBest,\nPatrick\nP.S. just Craig's cases"),
    _restate("a narrowing under the name", "Yes\nThanks,\nPatrick\nfor letters to Craig"),
    _restate("a change under the device footer", "Yes\nSent from my iPhone\nMake it 50"),
    _restate("his name with no closing", "Yes\nPatrick"),
    _restate("qualified on one line", "Yes for memos"),
    _restate("a second line of real content", "Yes\nletters only"),
    _restate("a question then yes", "Is this right?\nyes"),
    _restate("yes then a new request", "Yes\nAlso please draft the Jones demand."),
    _restate("yes but", "Yes but make it letters only"),
    _restate("another person's name", "Yes\nThanks,\nChris"),
    _restate("a bare number line", "Yes\n12.5"),
    _restate("From: that is not a quote header", "Yes\nFrom: now on use 12pt\nThanks"),
    _restate("content after the RFC separator", "Yes\n-- \nActually make it 12pt"),
    _restate("yes to both, bound to one", "Yes to both"),
    _restate("2 when one exists", "2"),
    _restate("numbered edit under the block", f"Yes\n\n{REAL_BLOCK}\n1. use 12 point"),
    _restate("a request to confirm", "Please confirm."),
    _restate("a request to confirm, after a yes", "Yes, please confirm"),
    _restate("a question", "Yes?"),
    # ---- the numbered list: a failed pick lists again -------------------------
    Row("list: both on three", "both", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row("list: one", "one", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row("list: 1 only for Craig", "1 only for craig", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row("list: 3 to 1", "3 to 1", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row("list: 1 / Suite 3", "1\nSuite 3", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row(
        "list: 1 / a street-shaped line",
        "1\n12 Point Font Avenue",
        rc.ASK,
        reason=rc.ASK_PICK,
        **LISTED,
    ),
    Row("list: out of range", "4", rc.ASK, reason=rc.ASK_PICK, **LISTED),
    Row(
        "old list: the pick closed",
        "2",
        rc.ASK,
        open=(A,),
        binding=ABC,
        listed=True,
        reason=rc.ASK_PICK,
    ),
    Row("list: none", "none", rc.NONE, **LISTED),
    # ---- no binding: a plain yes or a pick gets the list, nothing else confirms -
    Row("reply to a different email", "Yes", rc.ASK, open=(A, B), binding=None, reason=rc.ASK_PICK),
    Row("ok, thanks unbound", "ok, thanks", rc.ASK, open=(A, B), binding=None, reason=rc.ASK_PICK),
    Row("unbound pick", "1 and 3", rc.ASK, open=ABC, binding=None, reason=rc.ASK_PICK),
    Row(
        "quoted tags of two rules, no binding",
        f"Is this right?\nyes{QUOTE_AB}",
        rc.ASK,
        open=(A, B),
        binding=None,
        reason=rc.ASK_QUALIFIED,
    ),
    Row(
        "quoted tags and a plain yes, no binding",
        f"yes{QUOTE_AB}",
        rc.ASK,
        open=(A, B),
        binding=None,
        reason=rc.ASK_PICK,
    ),
    Row(
        "a request, no binding",
        "Ok, please draft the Jones demand.",
        rc.NONE,
        open=(A, B),
        binding=None,
    ),
    Row("a greeting and content", "Hi, no headings", rc.NONE, binding=None),
    # ---- not the person, or not an answer ------------------------------------
    Row("an assistant replying", "Yes", rc.ASK, binding=None, sender=SAM, reason=rc.ASK_NEEDS_TAG),
    Row(
        "a cc'd colleague", "Yes, thanks", rc.ASK, binding=None, sender=SAM, reason=rc.ASK_NEEDS_TAG
    ),
    Row(
        "In-Reply-To matches but From differs",
        "Yes.",
        rc.ASK,
        binding=None,
        sender=SAM,
        reason=rc.ASK_NEEDS_TAG,
    ),
    Row(
        "a forwarded read-back",
        "FYI, see below.\n\n---------- Forwarded message ---------\nFrom: Riley <ops@firm.example>\n"
        f"> {TEXT[A]}\n> Reply yes to confirm.",
        rc.NONE,
        binding=None,
        sender=SAM,
    ),
    Row("an auto-reply", "I am out of the office until Monday and will reply then.", rc.NONE),
    Row("an auto-reply thanking", "Thank you for your email. I will respond shortly.", rc.NONE),
    Row("a plain no to the read-back", "No.", rc.DECLINED),
    # ---- acts keep the tag path exactly --------------------------------------
    Row(
        "an act's phrase without its tag",
        "yes, place it",
        rc.ASK,
        binding=None,
        open=(),
        reason=rc.ASK_NEEDS_TAG,
        rows=({"proposal_id": ACT, "kind": "tool_call", "text": TEXT[ACT], "instructed_by": PAT},),
    ),
)


def _rows(row: Row) -> list[dict]:
    own = [
        {
            "proposal_id": p,
            "kind": "rule",
            "scope": "firm_adjust",
            "subject": {"output_class": "work_product", "property": "voice", "doc_kinds": ["memo"]},
            "text": TEXT[p],
            "readback": TEXT[p],
            "instructed_by": PAT,
            "for_admin": False,
            "state": "open",
        }
        for p in row.open
    ]
    return own + [dict(r, state="open") for r in row.rows]


def _prompt(row: Row) -> str:
    sender = row.sender if row.bare_from else f"{DISPLAY[row.sender]} <{row.sender}>"
    return (
        f"An inbound email arrived.\nfrom: {sender}\n"
        "to: Riley <ops@firm.example>\nsubject: Re: rules\nmessage_id: AAMkReply=\n"
        f"{inbound.UNTRUSTED_EMAIL_DELIMITER}\n{row.body}"
    )


def _binding(row: Row) -> rcr.ReadbackBinding | None:
    if row.binding is None:
        return None
    return rcr.ReadbackBinding(proposal_ids=row.binding, listed=row.listed)


def _who(row: Row) -> reply_shape.People:
    """What the seat feeds the grammar: his roster name and his learned block.
    The block rides only a reply from him (the broker returns his)."""
    mine = row.sender == PAT
    return reply_shape.people(
        _prompt(row),
        addressed=["Riley"],
        block=row.block if mine else (),
        full_name=row.full_name if mine else "",
    )


def test_the_table_has_every_shape():
    assert len(ROWS) >= 90
    assert len({r.name for r in ROWS}) == len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
def test_the_decision(row: Row) -> None:
    verdict = rc.resolve(
        _prompt(row), _rows(row), row.sender, True, binding=_binding(row), who=_who(row)
    )
    assert verdict.kind == row.expect, verdict
    if row.expect == rc.CONFIRMED:
        assert tuple(verdict.proposal_ids or (verdict.proposal_id,)) == row.confirms
    if row.reason is not None:
        assert verdict.reason == row.reason, verdict


@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
def test_the_hook_commits_exactly_what_the_table_says(seat, monkeypatch, row: Row) -> None:  # noqa: F811
    """Driven through the plugin: what reached the broker as a commit, what was
    stashed for a submit, and whether an act was confirmed. A row that does not
    confirm commits nothing at all."""
    mod, state = seat
    name = row.full_name
    monkeypatch.setattr(
        _ShapeCfg, "authored_person_name", lambda _self, a: name if a == PAT else None
    )
    monkeypatch.setattr(_ShapeCfg, "personas", [{"name": "Riley"}], raising=False)
    state["pending"] = _rows(row)
    state["binding"] = (
        {"proposal_ids": list(row.binding), "listed": row.listed, "block": list(row.block)}
        if row.binding
        else None
    )
    acts: list[str] = []
    monkeypatch.setattr(mod, "_act_confirmation_note", lambda *a, **k: acts.append(a[3]) or "")
    bound_turn("sess-t", row.sender, "AAMkReply=")
    mod.on_pre_llm_call(session_id="sess-t", sender_id=row.sender, user_message=_prompt(row))
    committed = [
        r["proposal_id"] for r in state["requests"] if r.get("action") == "establish_submit"
    ]
    stashed = mod._CONFIRMED_STASH.get("sess-t", set())
    if row.expect == rc.CONFIRMED:
        assert committed == list(row.confirms) and not acts
    else:
        assert committed == [] and not stashed and acts == [], (committed, stashed, acts)


# ---------------------------------------------------------------------------
# Property: an affirmative line plus ANY one line of ordinary words never confirms
# ---------------------------------------------------------------------------

_FIRST_LINES = ("Yes", "Yes.", "ok", "Yes, thanks", "Confirmed.", "Thanks, yes", "Yes please")
_VOCABULARY = (
    "make", "it", "only", "letters", "memos", "craig", "for", "the", "no", "headings", "use",
    "12pt", "bold", "instead", "scratch", "that", "also", "draft", "demand", "jones", "send",
    "to", "every", "brief", "court", "point", "font", "arial", "change", "later", "not",
    "except", "just", "cases", "with", "a", "copy", "paralegal", "line", "first", "two",
    "actually", "wait", "maybe", "keep", "drop", "add", "remove", "monday", "50", "pages",
    "llc", "avenue", "suite", "office", "fax", "partner", "associates", "&",
)  # fmt: skip


def test_an_affirmative_and_one_line_of_words_never_confirms() -> None:
    rand = random.Random(20261120)
    tried = 0
    for _ in range(3000):
        line = " ".join(rand.choice(_VOCABULARY) for _ in range(rand.randint(1, 6)))
        probe = Row("x", line, rc.NONE)
        who = _who(probe)
        if reply_shape.affirmative_line(reply_shape.norm(line), who, 2) is not None:
            continue  # a yes or a courtesy is not "a line of content"
        if rcr.read_pick(_prompt(probe), 2, who) is not None:
            continue  # nor is a pick
        tried += 1
        tail = rand.choice(["", "Thanks,", "Best,", f"\n{REAL_BLOCK}"])
        where = rand.choice(["before", "after"])
        body = (
            f"{rand.choice(_FIRST_LINES)}\n{line}\n{tail}"
            if where == "before"
            else f"{rand.choice(_FIRST_LINES)}\n{tail}\n{line}"
        )
        row = Row("p", body, rc.ASK, open=(A, B), binding=(A, B), listed=rand.random() < 0.5)
        verdict = rc.resolve(
            _prompt(row), _rows(row), PAT, True, binding=_binding(row), who=_who(row)
        )
        assert verdict.kind != rc.CONFIRMED, body
    assert tried > 2000


def test_the_grammar_reads_the_same_affirmatives_and_normal_form_as_the_broker() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from workspace_broker import sender_block

    assert reply_shape.AFFIRMATIVES == rc.BARE_AFFIRMATIVES
    for line in ("  Patrick Q. Doe ", "That’s “right”", "(602) 555-0100 office"):
        assert reply_shape.norm(line) == sender_block.norm_line(line)


@pytest.mark.parametrize(
    "body", ["Yes\nThanks,\nActually make that 12pt", "Yes\nAlso please draft the Jones demand."]
)
def test_a_reply_that_is_not_a_plain_yes_is_read_back_again_by_code(seat, body) -> None:  # noqa: F811
    """The restate path: nothing recorded, the rule read back again exactly as
    code renders it (owed and registered, so the next yes binds), and the rest
    of the email still answered."""
    mod, state = seat
    row = Row("r", body, rc.ASK)
    state["pending"] = _rows(row)
    state["binding"] = {"proposal_ids": [A], "listed": False}
    bound_turn("sess-r", PAT, "AAMkReply=")
    context = mod.on_pre_llm_call(session_id="sess-r", sender_id=PAT, user_message=_prompt(row))[
        "context"
    ]
    assert not [r for r in state["requests"] if r.get("action") == "establish_submit"]
    assert "Reply yes to confirm." in context and A not in context
    assert "Answer anything else the email asked." in context
    owed = [r for r in state["requests"] if r.get("action") == "establish_readback_owed"]
    assert owed and owed[-1]["proposal_ids"] == [A]


def test_a_rule_read_back_twice_is_handed_to_smd_not_read_back_again(seat) -> None:  # noqa: F811
    """The cap: a second reply that is not a plain yes gets the stopgap
    sentence, the rule is handed to SMD, and no third read-back is owed.
    FALSIFIER: drop the cap check in own_rules.own_ask_note."""
    mod, state = seat
    row = Row("r", "Yes\nletters only", rc.ASK)
    state["pending"] = _rows(row)
    state["binding"] = {"proposal_ids": [A], "listed": False, "rounds": {A: 2}}
    real = mod._broker_request

    def broker(payload):
        if payload.get("action") == "establish_readback_handoff":
            state["requests"].append(payload)
            return {"ok": True, "smd_told": True, "handed_off": payload["proposal_ids"]}
        return real(payload)

    mod._broker_request = broker
    try:
        bound_turn("sess-c", PAT, "AAMkReply=")
        context = mod.on_pre_llm_call(
            session_id="sess-c", sender_id=PAT, user_message=_prompt(row)
        )["context"]
    finally:
        mod._broker_request = real
    assert [
        r["proposal_ids"]
        for r in state["requests"]
        if r.get("action") == "establish_readback_handoff"
    ] == [[A]]
    assert not [r for r in state["requests"] if r.get("action") == "establish_readback_owed"]
    assert "once that's in place" in context and "Reply yes to confirm" not in context
