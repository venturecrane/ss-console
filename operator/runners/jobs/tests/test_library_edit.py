"""A firm edits the look of its library file in Word, saves, and the next draft of
that kind has it, with nobody at SMD doing anything (design 2026-11-06, piece 4;
``jobs/library_edit``).

THE FIRM'S SAVE IN THESE TESTS IS CONSTRUCTED, NOT SAVED BY WORD: no file saved
by real Word is in the repo. :func:`word_save` does to an accepted file what a
Word save does that matters here, made up and stated as such: the change the
firm made (a left margin, a font), the saver's name in ``docProps/core.xml``,
and an attached-template link in ``word/settings.xml`` with its external
relationship. The accepted marked file is the brief the brief-plan tests adopt
(``test_drafting_brief_base.tmpl``); the styles file is Word's own blank
document. Every name in them is made up.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest

import library_testkit as lt
from operator_connector_sdk.library import MARKED, STYLES, MemoryLibrary
from operator_connector_sdk.library.caller import record_problems
from jobs import library_edit as E
from jobs.library_edit import check as C, handoff as H
from test_drafting_brief_base import _job_brief, accepted, tmpl  # noqa: F401 - pytest fixtures used by name
from test_drafting_library_pick import _accept, _env, _filed, _run, laid, spec  # noqa: F401 - fixtures used by name
from test_library_setup_accept import firm_brief  # noqa: F401 - the fixture, used by name

CLS = "mediation_brief"
SAVER = "Example Saver"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
TEMPLATE_REL = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdT" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
    'Target="file:///C:/Users/ExampleSaver/Templates/Normal.dotm" TargetMode="External"/></Relationships>'
)


def _before_sect(data: bytes, frag: bytes) -> bytes:
    """``frag`` as the body's last paragraph, before the body's own section properties."""
    i = data.rfind(b"<w:sectPr")
    return data[:i] + frag + data[i:]


def word_save(blob: bytes, *, left: str | None = "2333", font: str | None = None, header: tuple[str, str] | None = None,
              body_word: str | None = None, tracked: bool = False) -> bytes:  # fmt: skip
    """The firm's save, constructed (the module docstring)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        names = src.namelist()
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "word/document.xml":
                if left is not None:
                    data = re.sub(rb'(<w:pgMar\b[^>]*\bw:left=")\d+', rb"\g<1>" + left.encode(), data, count=1)
                if body_word is not None:
                    data = _before_sect(data, f"<w:p><w:r><w:t>{body_word}</w:t></w:r></w:p>".encode())
                if tracked:
                    data = _before_sect(
                        data, b'<w:p><w:del w:id="9" w:author="x"><w:r><w:delText>gone</w:delText></w:r></w:del></w:p>'
                    )
            elif info.filename == "word/styles.xml" and font is not None:
                data = re.sub(rb'w:ascii="[^"]*"', f'w:ascii="{font}"'.encode(), data)
            elif info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dc:creator>)[^<]*", f"\\g<1>{SAVER}".encode(), data)
                data = re.sub(rb"(<cp:lastModifiedBy>)[^<]*", f"\\g<1>{SAVER}".encode(), data)
            elif info.filename == "word/settings.xml":
                data = data.replace(
                    b"</w:settings>", f'<w:attachedTemplate xmlns:r="{_R}" r:id="rIdT"/></w:settings>'.encode()
                )
            elif header is not None and re.match(r"word/(header|footer)\d*\.xml", info.filename):
                data = data.replace(header[0].encode(), header[1].encode())
            out.writestr(info, data)
        if "word/_rels/settings.xml.rels" not in names:
            out.writestr("word/_rels/settings.xml.rels", TEMPLATE_REL)
    return buf.getvalue()


def _member(blob: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(name)


def _left(blob: bytes) -> str:
    m = re.search(rb'<w:pgMar\b[^>]*\bw:left="(\d+)"', _member(blob, "word/document.xml"))
    return m.group(1).decode() if m else ""


def _header_word(blob: bytes) -> str:
    """One fixed word the accepted file carries in a header or footer."""
    for name in zipfile.ZipFile(io.BytesIO(blob)).namelist():
        if re.match(r"word/(header|footer)\d*\.xml", name):
            for text in C._paragraph_texts(__import__("lxml.etree").etree.fromstring(_member(blob, name))):
                if re.search(r"[A-Za-z]{4,}", text) and not C._MARK.search(text):
                    return re.search(r"[A-Za-z]{4,}", text).group(0)
    raise AssertionError("the accepted file carries no fixed header or footer word")


# ---- the cleaning ------------------------------------------------------------------------


def test_the_savers_name_and_template_link_go_and_nothing_a_firm_sees_differs(tmpl):
    saved = word_save(tmpl.bytes)
    assert SAVER.encode() in _member(saved, "docProps/core.xml")
    kept = C.clean(saved)
    assert SAVER.encode() not in _member(kept, "docProps/core.xml")
    assert b"attachedTemplate" not in _member(kept, "word/settings.xml")
    assert b"External" not in _member(kept, "word/_rels/settings.xml.rels")
    seen = [n for n in zipfile.ZipFile(io.BytesIO(saved)).namelist()
            if re.match(r"word/(document|styles|numbering|fontTable|header\d*|footer\d*|theme/theme\d*)\.xml$", n)]  # fmt: skip
    assert "word/document.xml" in seen and any("header" in n or "footer" in n for n in seen)
    assert all(_member(kept, n) == _member(saved, n) for n in seen)


def test_the_cleaning_is_the_same_bytes_every_time(tmpl):
    saved = word_save(tmpl.bytes)
    assert C.clean(saved) == C.clean(saved)


# ---- the checks, against the accepted copy -------------------------------------------------


def test_a_look_change_passes_and_the_accepted_copy_passes_its_own_check(tmpl):
    assert C.problems(CLS, MARKED, tmpl.bytes, C.clean(tmpl.bytes)) == []
    assert C.problems(CLS, MARKED, tmpl.bytes, C.clean(word_save(tmpl.bytes))) == []


def test_a_changed_fixed_word_is_refused(tmpl):
    word = _header_word(tmpl.bytes)
    saved = word_save(tmpl.bytes, header=(word, word + "x"))
    assert "words_changed" in C.problems(CLS, MARKED, tmpl.bytes, C.clean(saved))


def test_a_word_typed_in_the_body_and_a_tracked_change_are_refused(tmpl):
    assert "words_changed" in C.problems(CLS, MARKED, tmpl.bytes, C.clean(word_save(tmpl.bytes, body_word="Gamma")))
    assert "tracked_changes" in C.problems(CLS, MARKED, tmpl.bytes, C.clean(word_save(tmpl.bytes, tracked=True)))


def test_a_marked_place_taken_out_is_refused(tmpl):
    first = re.search(rb"\[\[[a-z0-9._]+\]\]", _member(tmpl.bytes, "word/document.xml")).group(0)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(word_save(tmpl.bytes))) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i.filename)
            out.writestr(i, data.replace(first, b"", 1) if i.filename == "word/document.xml" else data)
    assert "marked_places_changed" in C.problems(CLS, MARKED, tmpl.bytes, C.clean(buf.getvalue()))


def _styles_file(header: str = "EXAMPLE LAW") -> bytes:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = header
    d.styles.add_style("SMD Body", 1)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_a_styles_file_keeps_its_page_setup_and_the_renderers_styles(tmpl):
    acc = _styles_file()
    assert C.problems(CLS, STYLES, acc, C.clean(word_save(acc))) == []
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(word_save(acc))) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i.filename)
            out.writestr(
                i, data.replace(b'w:val="SMD Body"', b'w:val="Other Body"') if i.filename == "word/styles.xml" else data
            )
    assert C.problems(CLS, STYLES, acc, C.clean(buf.getvalue())) == ["named_style_removed"]


def test_a_library_file_setup_made_takes_a_look_edit_and_refuses_a_word(firm_brief):
    """The accepted files a seat can hold are made by "set up our ..." (3b's
    ``make``): the check is run on one, a Word save of it passes, a word does not,
    and its fixed strings pass the root writer's own check."""
    from jobs.library_setup import make as M
    from test_library_setup_accept import FIRM, _src

    made = M.make(CLS, firm_brief, _src(firm_brief), FIRM).data
    assert C.problems(CLS, MARKED, made, C.clean(word_save(made, left=None, font=FONT))) == []
    assert "words_changed" in C.problems(CLS, MARKED, made, C.clean(word_save(made, body_word="Gamma")))
    overlay = Path(__file__).resolve().parents[3] / "hermes-smd-overlay"
    import sys

    sys.path.insert(0, str(overlay))
    from establish_intake import blank_form

    kept = C.clean(word_save(made, left=None, font=FONT))
    assert blank_form.check(kept, tuple(C.fixed_of(made))).problems == ()


def test_a_prototypes_file_is_not_checkable_here():
    assert C.problems("demand_letter", "prototypes", b"", b"") == ["contract_not_checkable"]


# ---- the drafting job: the second draft has the change -------------------------------------


FONT = "Example Serif"


def _fonts(blob: bytes) -> set[str]:
    return set(re.findall(rb'w:ascii="([^"]*)"', _member(blob, "word/styles.xml")))


def test_a_saved_font_is_in_the_next_draft_with_no_release_and_no_person(spec, tmp_path, monkeypatch, tmpl, laid):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    r1, v1, log = _run(tmp_path / "one", base=tmpl.bytes, draft=_job_brief())
    assert v1.outcome == "delivered", (v1.stage, v1.reason, log[-5:])
    assert laid == [tmpl.bytes] and "firm_edit" not in lt.record(r1.data)
    assert not (r1.data / E.EDIT_JSON).exists()
    assert FONT.encode() not in _fonts(_filed(r1))
    saved = word_save(tmpl.bytes, left=None, font=FONT)
    new_version = lib.save("acc-1", saved)  # the firm's save: no release, no person at SMD
    r2, v2, log = _run(tmp_path / "two", base=tmpl.bytes, draft=_job_brief())
    assert v2.outcome == "delivered", (v2.stage, v2.reason, log[-5:])
    kept = C.clean(saved)
    assert laid[-1] == kept and _fonts(kept) == {FONT.encode()}
    assert FONT.encode() in _fonts(_filed(r2))
    rec = lt.record(r2.data)
    assert record_problems(rec) == []
    assert rec["firm_edit"]["status"] == "used" and rec["firm_edit"]["install"] == E.PENDING
    assert rec["firm_edit"]["version"] == new_version and rec["firm_edit"]["replaces_version"] == "acc-1-v1"
    f = rec["resolution"]["file"]
    assert (f["version"], f["sha256"], f["accepted_by"]) == (new_version, hashlib.sha256(kept).hexdigest(), "firm_made")
    assert rec["base"]["sha256"] == hashlib.sha256(kept).hexdigest()


def test_a_saved_margin_is_what_the_draft_is_made_in_and_the_firms_authored_layout_still_judges_it(
    spec, tmp_path, monkeypatch, tmpl, laid
):
    """The draft is made in the firm's new margin; the firm's authored pleading
    layout (``format.layout``) still checks the filed page, and a margin it does
    not author stops the job for SMD as it always has. Which one wins is piece
    5's (layout rules in the library file); this pins today's answer."""
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    lib.save("acc-1", word_save(tmpl.bytes, left="2333"))
    r, v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert _left(laid[-1]) == "2333" and lt.record(r.data)["firm_edit"]["status"] == "used"
    assert v.outcome == "failed" and "format_check" in v.reason and "margins" in v.reason


def test_the_acceptance_records_the_version_the_firm_saved_and_the_bytes_kept(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    saved = word_save(tmpl.bytes)
    v2 = lib.save("acc-1", saved)
    r, v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    doc = json.loads((r.data / E.EDIT_JSON).read_text())
    acc = doc["acceptance"]
    kept = (r.data / E.EDIT_DOCX).read_bytes()
    assert (
        acc["version"] == v2
        and acc["sha256"] == hashlib.sha256(kept).hexdigest() == hashlib.sha256(C.clean(saved)).hexdigest()
    )
    assert acc["replaces"] == {
        "file_id": "acc-1",
        "version": "acc-1-v1",
        "sha256": hashlib.sha256(tmpl.bytes).hexdigest(),
    }
    assert (acc["accepted_by"], acc["contract"], acc["attorney"], acc["kind"]) == ("firm_made", "marked", None, CLS)
    assert set(acc["fixed_strings"]) == set(C.fixed_of(tmpl.bytes))


def test_a_changed_fixed_word_makes_the_draft_in_the_accepted_copy(spec, tmp_path, monkeypatch, tmpl, laid):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    word = _header_word(tmpl.bytes)
    lib.save("acc-1", word_save(tmpl.bytes, header=(word, word + "x")))
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert laid == [tmpl.bytes]
    rec = lt.record(r.data)
    assert rec["firm_edit"]["status"] == "refused" and "words_changed" in rec["firm_edit"]["code"]
    assert (
        rec["resolution"]["file"]["version"] == "acc-1-v1" and rec["resolution"]["file"]["accepted_by"] != "firm_made"
    )
    assert not (r.data / E.EDIT_DOCX).exists()
    assert H.delivery_note(r.data) == {"used": False, "code": rec["firm_edit"]["code"]}


def test_with_nothing_accepted_no_firm_edit_is_considered_and_nothing_is_left(spec, tmp_path):
    r, v, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered"
    assert "firm_edit" not in lt.record(r.data) and not (r.data / E.EDIT_JSON).exists()
    assert H.delivery_note(r.data) is None


def test_the_firms_edit_is_read_only_on_the_case_system(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    lib.save("acc-1", word_save(tmpl.bytes))
    filed = []
    monkeypatch.setattr(lib, "file_new_version", lambda *a, **k: filed.append(a))
    _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert filed == [] and len(lib.list_versions("acc-1").versions) == 2


# ---- the root daemon, after the child ---------------------------------------------------------


class Daemon:
    LANE = "drafting"

    def __init__(self, root: Path, answers: dict[str, list[dict]]):
        self.root, self.answers, self.sent, self.paged = root, answers, [], []
        self.clock = lambda: 0.0

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _request(self, payload: dict) -> dict:
        self.sent.append(payload)
        return self.answers[payload["action"]].pop(0)


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    from jobs.proof import watch

    d = Daemon(tmp_path, {})
    monkeypatch.setattr(watch, "_client", lambda _d: d)
    monkeypatch.setattr(watch, "alert", lambda _d, job_id, code, detail="", **k: d.paged.append((job_id, code, detail)))
    monkeypatch.setattr(H, "SLEEP", lambda _s: None)
    return d


def _left_by_child(d: Daemon, tmpl_bytes: bytes, saved: bytes | None) -> None:
    """What a child leaves after considering the firm's save (``E.leave``)."""
    from operator_connector_sdk.library import Resolution, UsedFile
    from operator_connector_sdk.library.resolver import FIRM_FILE, NewerVersion

    lib = MemoryLibrary()
    lib.add("acc-1", "t", CLS, tmpl_bytes)
    if saved is not None:
        lib.save("acc-1", saved)
    sha = hashlib.sha256(tmpl_bytes).hexdigest()
    used = UsedFile("acc-1", "acc-1-v1", sha, MARKED, FIRM_FILE, "admin@firm.example", "2026-11-01T00:00:00Z")
    res = Resolution("resolved", CLS, file=used, newer=NewerVersion("acc-1-v1", "acc-1-v2"), data=tmpl_bytes)
    E.leave(d.job_dir("j1") / "data", res, E.consider(res, lib, CLS))


def test_a_used_save_is_handed_to_the_root_writer_and_nothing_pages(daemon, tmpl):
    _left_by_child(daemon, tmpl.bytes, word_save(tmpl.bytes))
    daemon.answers = {"library_edit_submit": [{"run_id": "r1"}],
                      "library_edit_status": [{"status": "pending"}, {"status": "complete", "result": {"status": "installed"}}]}  # fmt: skip
    assert H.hand_over(daemon, "j1") == "installed"
    sub = daemon.sent[0]
    assert (
        sub["action"] == "library_edit_submit"
        and sub["lane"] == "drafting"
        and sub["acceptance"]["accepted_by"] == "firm_made"
    )
    assert daemon.paged == []
    assert H.hand_over(daemon, "j1") == ""  # handed once


def test_a_lane_proof_runs_twin_hands_nothing_over_and_pages_nothing(daemon, tmpl):
    from jobs.proof_mode import PROOF_ENV

    daemon.child_env = {PROOF_ENV: "1"}
    _left_by_child(daemon, tmpl.bytes, word_save(tmpl.bytes))
    assert H.hand_over(daemon, "j1") == "proof_run" and daemon.sent == [] and daemon.paged == []


def test_a_race_lost_to_another_job_is_logged_not_paged(daemon, tmpl):
    _left_by_child(daemon, tmpl.bytes, word_save(tmpl.bytes))
    lost = {
        "status": "rejected",
        "reasons": ["the slot's accepted file is not the one this acceptance says it replaces; nothing written"],
    }
    daemon.answers = {
        "library_edit_submit": [{"run_id": "r1"}],
        "library_edit_status": [{"status": "complete", "result": lost}],
    }
    assert H.hand_over(daemon, "j1") == "already_replaced" and daemon.paged == []


def test_a_refused_save_and_a_root_refusal_page_smd_and_tell_the_firm_nothing(daemon, tmpl):
    word = _header_word(tmpl.bytes)
    _left_by_child(daemon, tmpl.bytes, word_save(tmpl.bytes, header=(word, word + "x")))
    assert H.hand_over(daemon, "j1") == "refused"
    assert daemon.paged[0][1] == H.CODE and "words_changed" in daemon.paged[0][2] and daemon.sent == []
    _left_by_child(daemon, tmpl.bytes, word_save(tmpl.bytes))
    daemon.answers = {"library_edit_submit": [{"run_id": "r2"}],
                      "library_edit_status": [{"status": "complete", "result": {"status": "rejected", "reasons": ["no-client-text check: x"]}}]}  # fmt: skip
    assert H.hand_over(daemon, "j1").startswith("rejected_by_root") and len(daemon.paged) == 2


# ---- the rehearsal's --edited step -------------------------------------------------------------


def test_the_rehearsal_runs_the_checks_on_a_given_saved_file(tmp_path, tmpl, capsys):
    import importlib.util

    tool = Path(__file__).resolve().parents[1] / "tools" / "library_setup_rehearse.py"
    spec_ = importlib.util.spec_from_file_location("rehearse_tool", tool)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    acc, saved = tmp_path / "a.docx", tmp_path / "s.docx"
    acc.write_bytes(tmpl.bytes)
    saved.write_bytes(word_save(tmpl.bytes))
    assert mod.main(["--kind", CLS, "--edited", str(saved), "--accepted", str(acc)], client=SimpleNamespace()) == 0
    out = json.loads(capsys.readouterr().out)["edited"]
    assert out["would_use"] is True and out["codes"] == [] and out["paragraphs_not_fixed"] == 0
    assert out["root_writer_check"]["problems"] == 0
    assert SAVER not in json.dumps(out)
