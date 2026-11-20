"""No sentence the CODE tells the model to send may trip the mechanics check
(review 2026-11-19, a replay of the firm's real messages).

Tier 1 (``shared.turn_says.TIER1_PHRASES``) runs on every reply to a firm
address on every seat. If a code-authored sentence the Operator is told to send
verbatim, to include, or to relay as it comes back carried a tier-1 phrase,
every such exchange would be held. This test collects them by source, not by
memory: every non-docstring string literal (and every f-string's fixed text) in
the modules that author firm-facing words, found by grepping the overlay and
the broker for the keys the relay treats as text to send (``say``,
``confirmation_text``, read-backs, held notices, refusal reasons a lane relays):

* the drafting, records and help lanes (``hermes-smd-lanes/drafting.py``,
  ``records.py``, ``hermes-smd-initiation/help_guide.py``, ``help_line.py``);
* the establishment plugin's ask-notes and read-back wording as origin/main has
  it (``hermes-smd-establishment/__init__.py``, broker ``establishment_rules``);
* the escalation digest and casework confirmations (``digest_reply.py``,
  ``casework_reply.py``, ``reply_items.py``);
* act read-backs (broker ``act_records_order``, ``act_event_set``);
* held-notice texts (``hermes-smd-reply/notice.py``, ``held_notice.py``);
* the records lane's broker acknowledgment and reasons (``records_verbs``), and
  the library-setup lane's say (``library_setup_wording``, ``library_setup_choose``,
  ``library_setup_verbs``; ``hermes-smd-lanes/library_setup.py``);
* the desk and training plugins, the champion wall and material check, and the
  broker's act, operations and send-as wording (``establishment_acts``,
  ``establishment_ops``, ``send_as_acts``).

And no fixed sentence trips tier 2 when it is itself the fixed sentence.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from shared import turn_says
from tests.conftest import load_plugin

OVERLAY = Path(__file__).resolve().parents[1]
BROKER = OVERLAY.parent / "workspace_broker"

MODULES = (
    OVERLAY / "plugins/hermes-smd-lanes/drafting.py",
    OVERLAY / "plugins/hermes-smd-lanes/records.py",
    OVERLAY / "plugins/hermes-smd-lanes/library_setup.py",
    OVERLAY / "plugins/hermes-smd-initiation/help_guide.py",
    OVERLAY / "plugins/hermes-smd-initiation/help_line.py",
    OVERLAY / "plugins/hermes-smd-establishment/__init__.py",
    OVERLAY / "plugins/hermes-smd-establishment/nudges.py",
    OVERLAY / "plugins/hermes-smd-escalation/digest_reply.py",
    OVERLAY / "plugins/hermes-smd-escalation/casework_reply.py",
    OVERLAY / "plugins/hermes-smd-escalation/reply_items.py",
    OVERLAY / "plugins/hermes-smd-reply/notice.py",
    OVERLAY / "plugins/hermes-smd-reply/held_notice.py",
    OVERLAY / "plugins/hermes-smd-desk/__init__.py",
    OVERLAY / "plugins/hermes-smd-training/__init__.py",
    OVERLAY / "shared/champion.py",
    OVERLAY / "shared/material_check.py",
    OVERLAY / "shared/rules_by_hand.py",
    BROKER / "establishment_rules.py",
    BROKER / "act_records_order.py",
    BROKER / "act_event_set.py",
    BROKER / "records_verbs.py",
    BROKER / "library_setup_wording.py",
    BROKER / "library_setup_choose.py",
    BROKER / "library_setup_verbs.py",
    BROKER / "establishment_acts.py",
    BROKER / "establishment_ops.py",
    BROKER / "send_as_acts.py",
)


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _texts(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            fixed = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            out.append((node.lineno, fixed))
    return out


@pytest.mark.parametrize(
    "path", MODULES, ids=lambda p: p.parent.name if p.name == "__init__.py" else p.name
)
def test_no_code_authored_text_trips_tier_one(path: Path) -> None:
    """FALSIFIER: put "[rule" or "this seat" back in tier 1 and the
    establishment and escalation modules fail here."""
    assert path.is_file(), path
    hits = [(line, text[:80]) for line, text in _texts(path) if turn_says.tier1_phrase(text, "")]
    assert hits == [], hits


def _fixed_sentences() -> list[str]:
    drafting = load_plugin("hermes-smd-lanes").drafting
    help_guide = load_plugin("hermes-smd-initiation").help_guide
    from shared import rules_by_hand

    says: list[str] = [help_guide.NOT_IN_GUIDE_SAY, drafting.MATTER_NUMBER_SAY, rules_by_hand.SAY]
    for klass in drafting.DOCUMENT_CLASSES:
        says.append(drafting.acknowledgment(klass, "4417"))
        says.append(drafting.acknowledgment(klass, "4417", revising=True))
        for state in ("submitted", "delivered", "held"):
            says.append(drafting.duplicate_say(klass, "4417", "draft", state))
        for code in drafting.FIRM_SAY:
            says.append(drafting.refusal_say(code, klass, "4417"))
        says.append(
            drafting.SECOND_MATTER_SAY.format(queued=drafting._doc(klass, "4417"), number="5520")
        )
    for result, state in (
        ("applied", ""),
        ("compose_started", ""),
        ("job_ended", "held"),
        ("job_ended", "failed"),
        ("not_requester", ""),
        ("other_thread", ""),
        ("could_not_read", ""),
    ):
        says.append(
            drafting._say(
                result,
                {
                    "matter_number": "4417",
                    "document_class": "memo",
                    "state": state,
                    "smd_told": True,
                },
            )
        )
    return says


def test_every_fixed_sentence_passes_both_tiers_as_itself() -> None:
    says = _fixed_sentences()
    assert len(says) > 30
    for say in says:
        assert turn_says.tier1_phrase(say, "") == "", say
        assert turn_says.block_problem((say,), say) == "", say
