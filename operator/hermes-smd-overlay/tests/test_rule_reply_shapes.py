"""The replay table: every reply shape the reviews found, and what it does.

Each row is one reply to the Operator, the person's own rules that are open,
the read-back the broker bound the reply to (or none), the sender, and the one
verdict the reply must produce. The table is run twice:

* through :func:`shared.rule_confirm.resolve`, the decision itself;
* through the establishment plugin's confirmation hook, with a fake broker, so
  that for every row that must NOT confirm, nothing was committed: no
  ``establish_submit``, nothing stashed for a submit, no act confirmed.

Then a property test: an affirmative first line followed by any one line of
ordinary words is never a confirmation. Every name, address, rule and id here
is invented.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pytest

from shared import inbound, reply_shape
from shared import rule_confirm as rc
from shared import rule_confirm_reply as rcr
from tests.rule_turns import bound_turn
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

SIGNATURE = (
    "Cordially,\n\nPatrick Q. Doe\nDoe & Roe, P.L.C.\n1234 N. Central Ave., Suite 500\n"
    "Phoenix, AZ 85004\nOffice: (602) 555-0100\nFax: (602) 555-0101"
)
#: Thirteen lines, every one signature-shaped.
LONG_SIGNATURE = (
    "Best regards,\n\nPat Q. Doe, Esq.\nPartner\nDoe & Roe LLP\n1234 N. Central Ave.\n"
    "Suite 500\nPhoenix, AZ 85004\nOffice: (602) 555-0100\nFax: (602) 555-0101\n"
    "Cell: 602-555-0102\npat@firm.example\nwww.doeroe.example\nSent from my iPhone"
)
NOTICE = (
    "CONFIDENTIALITY NOTICE: This email and any attachments are confidential and may be "
    "privileged. If you received it in error, delete it and tell the sender."
)
QUOTE_AB = (
    f"\n\nOn Tue, Riley <ops@firm.example> wrote:\n> [rule {A}] {TEXT[A]}\n"
    f"> [rule {B}] {TEXT[B]}\n> Reply yes to confirm both."
)


#: What the seat recorded for Pat: the closing lines of an earlier email of his.
RECORDED: tuple[str, ...] = (
    *SIGNATURE.splitlines(),
    *LONG_SIGNATURE.splitlines(),
    NOTICE,
    "Partner",
    "Cell: 602-555-0102 | Fax: 602-555-0101",
    "Patrick Doe",
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
    #: The signature the seat recorded for the sender from an earlier email.
    signature: tuple[str, ...] = ()


def _yes(name, body, **kw):
    """A row that confirms rule A (or ``confirms``) of a read-back bound to A."""
    return Row(name, body, rc.CONFIRMED, kw.pop("confirms", (A,)), **kw)


def _restate(name, body, **kw):
    return Row(name, body, rc.ASK, reason=rc.ASK_QUALIFIED, **kw)


ABC = (A, B, C)

ROWS: tuple[Row, ...] = (
    # ---- a plain yes to a read-back confirms every open rule it carried ----------
    _yes("yes", "yes"),
    _yes("Yes.", "Yes."),
    _yes("Yes!!!", "Yes!!!"),
    _yes("Yes, thanks", "Yes, thanks"),
    _yes("Thanks, yes", "Thanks, yes"),
    _yes("greeting line then yes", "Hi,\nYes"),
    _yes("yes then own first name", "Yes - Pat"),
    _yes("ok", "ok"),
    _yes("Confirmed.", "Confirmed."),
    _yes("yes please", "Yes please"),
    _yes("yes, addressing the Operator", "Good morning Riley,\nYes, thanks.\nPat"),
    _yes("yes to both, bound to two", "Yes to both", confirms=(A, B), open=(A, B), binding=(A, B)),
    _yes("yes to all three", "Yes to all three.", confirms=ABC, open=ABC, binding=ABC),
    _yes("cordially and a full block", f"Yes\n\n{SIGNATURE}", signature=RECORDED),
    _yes("a thirteen-line signature", f"Yes\n\n{LONG_SIGNATURE}", signature=RECORDED),
    _yes("a confidentiality footer", f"Yes\n\nThanks,\nPat\n\n{NOTICE}", signature=RECORDED),
    _yes(
        "a full block anchored by the quoted history",
        f"Yes\n\n{SIGNATURE}\n\nOn Tue, Nov 18, 2026 at 3:04 PM Riley <ops@firm.example> wrote:\n"
        + "\n".join(f"> {line}" for line in SIGNATURE.splitlines()),
    ),
    _restate("a full block nothing anchors", f"Yes\n\n{SIGNATURE}"),
    _restate("a notice nothing anchors", f"Yes\n\nThanks,\nPat\n\n{NOTICE}"),
    _yes("device footer", "Yes\n\nSent from my iPhone"),
    _yes("Outlook footer", "Yes\n\nGet Outlook for iOS"),
    _yes("a job title", "Yes\nPartner", signature=RECORDED),
    _restate("a job title nothing anchors", "Yes\nPartner"),
    _yes("the RFC separator then the name", "Yes\n-- \nPat Doe"),
    _yes("/s/ and Esq.", "Yes\n\nRegards,\n/s/ Pat Q. Doe, Esq."),
    _yes(
        "phones on one line",
        "Yes\nPat Doe\nCell: 602-555-0102 | Fax: 602-555-0101",
        signature=RECORDED,
    ),
    _yes("nickname for the display name", "Yes\nPatrick Doe", signature=RECORDED),
    _restate("a nickname nothing anchors", "Yes\nPatrick Doe"),
    _yes(
        "a quoted tag of another rule changes nothing",
        f"Yes.{QUOTE_AB}",
        open=(A, B),
        binding=(A,),
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
    # ---- a pick from the numbered list -----------------------------------------
    _yes("list: all", "all", confirms=ABC, open=ABC, binding=ABC, listed=True),
    _yes("list: a plain yes is all", "Yes", confirms=ABC, open=ABC, binding=ABC, listed=True),
    _yes("list: 1 and 3", "1 and 3", confirms=(A, C), open=ABC, binding=ABC, listed=True),
    _yes("list: 1, 3", "1, 3", confirms=(A, C), open=ABC, binding=ABC, listed=True),
    _yes("list: 1-3", "1-3", confirms=ABC, open=ABC, binding=ABC, listed=True),
    _yes("list: the first one", "the first one", open=ABC, binding=ABC, listed=True),
    _yes("list: 2", "2", confirms=(B,), open=ABC, binding=ABC, listed=True),
    _yes("list: both", "both", confirms=(A, B), open=(A, B), binding=(A, B), listed=True),
    _yes(
        "list: yes to 2, signed",
        f"Yes to 2\n\n{SIGNATURE}",
        confirms=(B,),
        signature=RECORDED,
        open=ABC,
        binding=ABC,
        listed=True,
    ),
    _yes("old list: 1 and 3, 3 since closed", "1 and 3", open=(A, B), binding=ABC, listed=True),
    # ---- tags the sender typed in their own text -------------------------------
    _yes(
        "[rule A] yes and [rule B] yes, no binding",
        f"[rule {A}] yes and [rule {B}] yes",
        confirms=(A, B),
        open=(A, B),
        binding=None,
    ),
    # ---- the review of 2026-11-20: instructions shaped like signature lines ----
    # Each confirmed before anchoring. The recorded signature is present, and
    # does not help: none of these lines is in it.
    *(
        _restate(f"disguised: {body}", f"Yes\n{body}", signature=RECORDED)
        for body in (
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
        )
    ),
    Row(
        "disguised on a list: a street-shaped line",
        "1\n12 Point Font Avenue",
        rc.ASK,
        open=ABC,
        binding=ABC,
        listed=True,
        reason=rc.ASK_PICK,
        signature=RECORDED,
    ),
    _restate("a request to confirm", "Please confirm."),
    _restate("a request to confirm, after a yes", "Yes, please confirm"),
    _yes("list: 1 to 3 is a range", "1 to 3", confirms=ABC, open=ABC, binding=ABC, listed=True),
    _yes("list: 1 through 2", "1 through 2", confirms=(A, B), open=ABC, binding=ABC, listed=True),
    Row(
        "list: both on three",
        "both",
        rc.ASK,
        open=ABC,
        binding=ABC,
        listed=True,
        reason=rc.ASK_PICK,
    ),
    Row("list: one", "one", rc.ASK, open=ABC, binding=ABC, listed=True, reason=rc.ASK_PICK),
    Row(
        "list: 1 only for Craig",
        "1 only for craig",
        rc.ASK,
        open=ABC,
        binding=ABC,
        listed=True,
        reason=rc.ASK_PICK,
    ),
    Row("list: 3 to 1", "3 to 1", rc.ASK, open=ABC, binding=ABC, listed=True, reason=rc.ASK_PICK),
    # ---- content anywhere: read back again ------------------------------------
    _restate("closing then a change", "Yes\nThanks,\nActually make that 12pt"),
    _restate("closing then a withdrawal", "Yes\nThanks\nNo, scratch that"),
    _restate("a P.S. under the name", "Yes\nBest,\nPat\nP.S. just Craig's cases"),
    _restate("a narrowing under the name", "Yes\nPat\nfor letters to Craig"),
    _restate("a change under the device footer", "Yes\nSent from my iPhone\nMake it 50"),
    _restate("qualified on one line", "Yes for memos"),
    _restate("a second line of real content", "Yes\nletters only"),
    _restate("a question then yes", "Is this right?\nyes"),
    _restate("yes then a new request", "Yes\nAlso please draft the Jones demand."),
    _restate("yes but", "Yes but make it letters only"),
    _restate("another person's name", "Yes\nChris"),
    _restate("a bare number line", "Yes\n12.5"),
    _restate("From: that is not a quote header", "Yes\nFrom: now on use 12pt\nThanks"),
    _restate("content after the RFC separator", "Yes\n-- \nActually make it 12pt"),
    _restate("a sentence opening with Confidentiality", "Yes\nConfidentiality matters, only Craig"),
    _restate("a firm suffix on a sentence", "Yes\nMake it Arial LLC"),
    _restate("a street-shaped sentence", "Yes\n12 point to the court"),
    _restate("yes to both, bound to one", "Yes to both"),
    _restate("2 when one exists", "2"),
    _restate("numbered edit under the block", f"Yes\n\n{SIGNATURE}\n1. use 12 point"),
    Row("a question", "Yes?", rc.ASK, reason=rc.ASK_QUALIFIED),
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
    # ---- not the person, or not an answer --------------------------------------
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
    Row("list: none", "none", rc.NONE, open=ABC, binding=ABC, listed=True),
    Row("list: out of range", "4", rc.ASK, open=ABC, binding=ABC, listed=True, reason=rc.ASK_PICK),
    Row(
        "old list: the pick closed",
        "2",
        rc.ASK,
        open=(A,),
        binding=ABC,
        listed=True,
        reason=rc.ASK_PICK,
    ),
    Row("a plain no to the read-back", "No.", rc.DECLINED),
    # ---- acts and administrators keep the tag path exactly ---------------------
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
    return (
        f"An inbound email arrived.\nfrom: {DISPLAY[row.sender]} <{row.sender}>\n"
        "to: Riley <ops@firm.example>\nsubject: Re: rules\nmessage_id: AAMkReply=\n"
        f"{inbound.UNTRUSTED_EMAIL_DELIMITER}\n{row.body}"
    )


def _binding(row: Row) -> rcr.ReadbackBinding | None:
    if row.binding is None:
        return None
    return rcr.ReadbackBinding(proposal_ids=row.binding, listed=row.listed)


def test_the_table_has_every_shape():
    assert len(ROWS) >= 60
    assert len({r.name for r in ROWS}) == len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
def test_the_decision(row: Row) -> None:
    who = reply_shape.people(_prompt(row), signature=row.signature)
    verdict = rc.resolve(_prompt(row), _rows(row), row.sender, True, binding=_binding(row), who=who)
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
    state["pending"] = _rows(row)
    state["binding"] = (
        {"proposal_ids": list(row.binding), "listed": row.listed, "signature": list(row.signature)}
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
)  # fmt: skip


def test_an_affirmative_and_one_line_of_words_never_confirms() -> None:
    rand = random.Random(20261120)
    who = reply_shape.people(_prompt(ROWS[0]))
    tried = 0
    for _ in range(3000):
        words = [rand.choice(_VOCABULARY) for _ in range(rand.randint(1, 6))]
        line = " ".join(words)
        alone = _prompt(Row("x", line, rc.NONE))
        if (
            reply_shape.signature_line(line, who)
            or reply_shape.affirmative_line(line, who, 2) is not None
            or rcr.read_pick(alone, 2) is not None
        ):
            continue  # a signature, a yes or a pick is not "a line of content"
        tried += 1
        body = (
            f"{rand.choice(_FIRST_LINES)}\n{rand.choice(['', 'Thanks,', 'Best,', 'Pat'])}\n{line}"
        )
        row = Row("p", body, rc.ASK, open=(A, B), binding=(A, B), listed=rand.random() < 0.5)
        verdict = rc.resolve(_prompt(row), _rows(row), PAT, True, binding=_binding(row))
        assert verdict.kind != rc.CONFIRMED, body
    assert tried > 2000


def test_the_shape_module_reads_the_same_affirmatives() -> None:
    assert reply_shape.AFFIRMATIVES == rc.BARE_AFFIRMATIVES


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
