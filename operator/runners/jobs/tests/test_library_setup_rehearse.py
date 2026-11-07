"""The $0 rehearsal of "set up our ...": it reads only, prints ids, codes and
counts, and its adopt, no-client-text and look steps run on a made-up memo."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

from test_drafting_memo_base import _firm_memo
from test_library_setup import MATTER_A, ReadOnlyClient, _row

TOOL = Path(__file__).resolve().parents[1] / "tools" / "library_setup_rehearse.py"


def _tool():
    spec = importlib.util.spec_from_file_location("library_setup_rehearse", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _long_memo() -> bytes:
    """The made-up firm memo, long enough to be in a memo's length band."""
    import docx

    d = docx.Document(io.BytesIO(_firm_memo()))
    d.add_paragraph(" ".join(["word"] * 600))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _client() -> ReadOnlyClient:
    row = _row("f-memo", "Other Client Case Evaluation Memo.docx", 0.3)
    return ReadOnlyClient({"f-memo": (MATTER_A, row, _long_memo())})


def test_the_rehearsal_reads_only_and_prints_no_name_or_word(capsys):
    tool = _tool()
    assert tool.main(["--kind", "memo"], client=_client()) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["candidates"][0]["file_id"] == "f-memo" and report["chosen"] == "f-memo"
    assert report["adopted"] is True
    assert "Other Client" not in out and "Case Evaluation" not in out and "Firm Person" not in out


def test_the_made_memo_keeps_the_source_look_and_the_check_names_what_clean_leaves(capsys):
    """Found by this rehearsal (2026-11-07): ``clean_package`` keeps a package's
    custom XML parts (Word's bibliography store here; a document system's
    metadata in a real file), and the no-client-text check refuses them. So the
    made file is not yet acceptable as it comes from adopt and clean: piece 3b
    drops them or proves them empty before anything is accepted."""
    tool = _tool()
    tool.main(["--kind", "memo"], client=_client())
    report = json.loads(capsys.readouterr().out)
    assert report["blank"]["problems"] > 0
    assert all(p.startswith("customXml/") for p in report["blank"]["in"]), report["blank"]
    look = report["look"]
    assert look["page_setup_same"] is True
    assert look["parts_only_in_made"] == []
    assert look["body_elements_keeping_a_source_look"] >= 1


def test_any_write_is_refused():
    tool = _tool()
    ro = tool.ReadOnly(_client())
    with pytest.raises(PermissionError):
        ro.add_file_version("m", "f", b"", expected_version="v")
    assert ro.get("/staff")["value"]


def test_the_look_compare_sees_a_changed_page_setup():
    tool = _tool()
    import docx
    from docx.shared import Inches

    src = _firm_memo()
    d = docx.Document(io.BytesIO(src))
    d.sections[0].left_margin = Inches(2)
    buf = io.BytesIO()
    d.save(buf)
    assert tool.look_compare(src, buf.getvalue())["page_setup_same"] is False
