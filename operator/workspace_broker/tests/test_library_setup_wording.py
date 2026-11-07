"""Every sentence a firm reads about "set up our ..." lives in ONE file,
``workspace_broker/library_setup_wording.py``, the file the Captain reads before
any seat is switched on. This fails when another file of the piece carries a
sentence, and it proves it can fail (the planted file below)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

OP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OP))

from workspace_broker import library_setup_wording as W  # noqa: E402 - after the path shim, as every broker test

#: Every file of the piece but the wording file.
PIECE = (
    "runners/jobs/jobs/library_setup/__init__.py",
    "runners/jobs/jobs/library_setup/cli.py",
    "runners/jobs/jobs/library_setup/job.py",
    "runners/jobs/jobs/library_setup/roster.py",
    "runners/jobs/jobs/library_setup/accept.py",
    "runners/jobs/jobs/library_setup/file.py",
    "runners/jobs/jobs/library_setup/make.py",
    "runners/jobs/jobs/library_setup/plan.py",
    "runners/jobs/jobs/library_setup_lane.py",
    "runners/jobs/jobs/library_setup_handoff.py",
    "workspace_broker/library_setup_choose.py",
    "runners/jobs/tools/library_setup_rehearse.py",
    "workspace_broker/library_setup_ledger.py",
    "workspace_broker/library_setup_verbs.py",
    "hermes-smd-overlay/plugins/hermes-smd-lanes/library_setup.py",
)
#: A sentence: a capital, words, and a stop. What a firm would read as one.
SENTENCE = re.compile(r"^[A-Z][^\n]{18,}[.?!:]$")
#: Calls whose words go to SMD, the model or a log, never to a firm.
NOT_TO_A_FIRM = {"debug", "info", "warning", "error", "exception", "add_argument", "add_parser", "ArgumentParser"}
#: Functions whose words go to the model, never relayed: the DELIVER wake's lines.
TO_THE_MODEL = {"compose_wake"}
#: A query the ledger runs, not words anyone reads.
QUERY = re.compile(r"^(SELECT|UPDATE|INSERT) ")


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _exempt(tree: ast.AST) -> set[int]:
    """Strings inside a log call, a raise, an argparse call, or the overlay's
    TOOLS table (the model's tool description, never relayed)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        inside = None
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            inside = node if name in NOT_TO_A_FIRM else None
        elif isinstance(node, ast.Raise):
            inside = node
        elif isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TOOLS":
            inside = node
        elif isinstance(node, ast.FunctionDef) and node.name in TO_THE_MODEL:
            inside = node
        if inside is not None:
            out |= {id(n) for n in ast.walk(inside) if isinstance(n, ast.Constant)}
    return out


def sentences(source: str) -> list[str]:
    tree = ast.parse(source)
    skip = _docstrings(tree) | _exempt(tree)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
        and SENTENCE.match(n.value)
        and not QUERY.match(n.value)
    ]


@pytest.mark.parametrize("rel", PIECE)
def test_no_other_file_of_the_piece_carries_a_sentence_a_firm_reads(rel):
    found = sentences((OP / rel).read_text(encoding="utf-8"))
    assert found == [], f"{rel} carries firm-readable sentences; they belong in library_setup_wording.py: {found}"


def test_the_check_finds_a_planted_sentence():
    planted = 'def reply():\n    return "We found the firm\'s memos and will set one up."\n'
    assert sentences(planted) == ["We found the firm's memos and will set one up."]
    assert sentences('logger.info("We found the firm\'s memos and will set one up.")') == []


def test_every_sentence_has_a_short_key_and_no_banned_marker():
    for key, text in W.SENTENCES.items():
        assert re.fullmatch(r"[a-z_]{2,24}", key), key
        assert "—" not in text and "–" not in text, key  # no em or en dashes
        assert "coming soon" not in text.lower(), key
        for word in (" day", " week", " hour", " minute", " soon", " shortly", " today", " tomorrow"):
            assert word not in text.lower(), (key, word)  # no timeframes


def test_the_wording_file_says_who_reads_it():
    doc = (OP / "workspace_broker/library_setup_wording.py").read_text(encoding="utf-8").split('"""')[1]
    assert "The Captain reads this file before any seat is switched on" in doc
