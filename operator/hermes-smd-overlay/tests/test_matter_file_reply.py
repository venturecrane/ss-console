"""Matter files on a staff reply (ADR 0101): the fences, and every refusal.

The capability is the attorney's request on 2026-11-01: "send me records X, Y,
Z on matter N", answered with the files attached. These tests pin that the
files reach exactly the firm colleague who asked, in the turn their email
opened, from the one matter the session read, and nobody and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from shared import inbound, matter_binding
from shared import matter_file_reply as mfr
from shared.inbound import SESSION_TAINT
from tests.conftest import load_plugin

MATTER = "11111111-2222-3333-4444-555555555555"
OTHER_MATTER = "99999999-2222-3333-4444-555555555555"
STAFF = "attorney@firm.example"
DOC = {"matter_file_delivery": {"recipients": ["@firm.example"]}}


def _pdf(pages: int = 1, *, js: bool = False) -> bytes:
    """A PDF with no text layer at all: the shape of a scanned record."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if js:
        w.add_js("app.alert('x');")  # still emits /JavaScript, the shape refused
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class FakeBroker:
    """``attachment_put`` as the broker answers it, hashing what it received."""

    def __init__(self) -> None:
        self.files: dict[str, bytearray] = {}
        self.calls = 0
        self.lie = False

    def __call__(self, envelope: dict, timeout: float = 0.0) -> dict:
        self.calls += 1
        assert envelope["action"] == "attachment_put"
        p = envelope["payload"]
        chunk = base64.b64decode(p["chunk_b64"])
        assert len(p["chunk_b64"]) <= 4 * 1024 * 1024 + 4
        handle = p.get("handle") or f"{len(self.files) + 1:032x}"
        buf = self.files.setdefault(handle, bytearray())
        assert len(buf) == p["offset"]
        buf.extend(chunk)
        if not p["final"]:
            return {"ok": True, "handle": handle, "received": len(buf)}
        sha = hashlib.sha256(bytes(buf)).hexdigest()
        return {
            "ok": True,
            "handle": handle,
            "sha256": "0" * 64 if self.lie else sha,
            "size": len(buf),
        }


def _spooled(spool: Path, data: bytes) -> dict:
    """One connector spool entry and the row its stage result carries."""
    spool.mkdir(parents=True, exist_ok=True)
    token = hashlib.md5(data).hexdigest()  # noqa: S324 - a test token, 32 hex
    (spool / f"{token}.bin").write_bytes(data)
    return {"spoolToken": token, "sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}


def _result(rows: list[dict], matter: str = MATTER) -> str:
    return json.dumps({"status": "staged", "matterId": matter, "staged": rows, "refused": []})


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    mfr._reset_for_tests()
    matter_binding._reset_for_tests()
    monkeypatch.setenv(mfr.SPOOL_DIR_ENV, str(tmp_path / "spool"))
    yield
    mfr._reset_for_tests()
    matter_binding._reset_for_tests()


def _stage(
    spool: Path,
    files: list[tuple[str, bytes]],
    *,
    refusal=None,
    broker=None,
    msg="m1",
    matter=MATTER,
    sid="s1",
):
    rows = [
        {"fileId": f"f{i}", "attachmentName": name, **_spooled(spool, data)}
        for i, (name, data) in enumerate(files)
    ]
    return mfr.stage_from_result(
        sid,
        {"matter_id": matter},
        _result(rows, matter),
        origin_message_id=msg,
        refusal=refusal,
        request=broker or FakeBroker(),
        spool=spool,
    )


def _prepare(**over):
    kw = dict(
        doc=DOC,
        message_id="m1",
        recipient=STAFF,
        internal=True,
        on_roster=True,
        redirected=False,
        bound=False,
        msgraph=True,
        read_matters={MATTER},
        cited_matters=set(),
    )
    kw.update(over)
    return mfr.prepare(mfr.staged("s1"), **kw)


# ---- who may receive ------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "address", "internal", "on_roster", "device", "fragment"),
    [
        ({}, STAFF, True, True, False, "not authored"),
        ({"matter_file_delivery": {"recipients": []}}, STAFF, True, True, False, "not authored"),
        (DOC, STAFF, True, True, True, "stand-in"),
        (DOC, "opposing@counsel.example", True, True, False, "never to anyone outside"),
        (DOC, STAFF, False, True, False, "never to anyone outside"),
        (DOC, STAFF, True, False, False, "never to anyone outside"),
        (
            {"matter_file_delivery": {"recipients": ["chris@firm.example"]}},
            STAFF,
            True,
            True,
            False,
            "outside",
        ),
    ],
)
def test_recipient_refusals(doc, address, internal, on_roster, device, fragment) -> None:
    why = mfr.recipient_refusal(doc, address, internal=internal, on_roster=on_roster, device=device)
    assert why and fragment in why


def test_firm_staff_on_the_list_may_receive() -> None:
    assert (
        mfr.recipient_refusal(
            DOC, "Attorney@Firm.example", internal=True, on_roster=True, device=False
        )
        is None
    )
    exact = {"matter_file_delivery": {"recipients": ["attorney@firm.example"]}}
    assert mfr.recipient_refusal(exact, STAFF, internal=True, on_roster=True, device=False) is None


# ---- staging --------------------------------------------------------------


def test_a_scanned_pdf_with_no_text_layer_is_staged(tmp_path: Path) -> None:
    """The scanned-PDF rule: a firm's own scan going to its own staff passes
    the structural gate; the text read the gate otherwise requires is not made."""
    broker = FakeBroker()
    told = _stage(tmp_path / "spool", [("Scan.pdf", _pdf(3))], broker=broker)
    assert "Staged for your reply: Scan.pdf" in told
    (f,) = mfr.staged("s1").files
    assert bytes(broker.files[f.handle]) == _pdf(3)


def test_the_text_gate_still_refuses_that_scan_on_every_other_send() -> None:
    from shared import outbound_attachment

    with pytest.raises(outbound_attachment.AttachmentError, match="no readable text"):
        outbound_attachment.extract_pdf_text(_pdf(1))


def test_large_files_cross_to_the_broker_in_chunks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mfr, "CHUNK_BYTES", 400)
    broker = FakeBroker()
    _stage(tmp_path / "spool", [("Big.pdf", _pdf(4))], broker=broker)
    assert broker.calls > 1 and len(mfr.staged("s1").files) == 1


def test_an_active_pdf_is_refused(tmp_path: Path) -> None:
    told = _stage(tmp_path / "spool", [("Bad.pdf", _pdf(1, js=True))])
    assert "Not staged: Bad.pdf" in told and mfr.staged("s1").files == []


def test_a_spool_file_changed_after_the_fetch_is_refused(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    row = {"fileId": "f0", "attachmentName": "A.pdf", **_spooled(spool, _pdf(1))}
    (spool / f"{row['spoolToken']}.bin").write_bytes(_pdf(2))
    told = mfr.stage_from_result(
        "s1",
        {"matter_id": MATTER},
        _result([row]),
        origin_message_id="m1",
        refusal=None,
        request=FakeBroker(),
        spool=spool,
    )
    assert "changed after the connector fetched it" in told


def test_a_symlinked_spool_entry_is_refused(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    row = {"fileId": "f0", "attachmentName": "A.pdf", **_spooled(spool, _pdf(1))}
    target = tmp_path / "elsewhere.pdf"
    target.write_bytes(_pdf(1))
    (spool / f"{row['spoolToken']}.bin").unlink()
    (spool / f"{row['spoolToken']}.bin").symlink_to(target)
    told = mfr.stage_from_result(
        "s1",
        {"matter_id": MATTER},
        _result([row]),
        origin_message_id="m1",
        refusal=None,
        request=FakeBroker(),
        spool=spool,
    )
    assert "Not staged" in told


@pytest.mark.parametrize("token", ["../../etc/passwd", "ABC", ""])
def test_a_malformed_spool_reference_is_refused(tmp_path: Path, token: str) -> None:
    with pytest.raises(mfr.FileRefused):
        mfr.read_connector_spool(token, "0" * 64, directory=tmp_path)


def test_a_broker_copy_that_does_not_hash_is_refused(tmp_path: Path) -> None:
    broker = FakeBroker()
    broker.lie = True
    assert "did not hash" in _stage(tmp_path / "spool", [("A.pdf", _pdf(1))], broker=broker)


def test_a_bad_attachment_name_is_refused(tmp_path: Path) -> None:
    assert "plain PDF name" in _stage(tmp_path / "spool", [("../x.exe", _pdf(1))])


def test_a_refused_turn_records_a_hold_and_stages_nothing(tmp_path: Path) -> None:
    broker = FakeBroker()
    told = _stage(
        tmp_path / "spool",
        [("A.pdf", _pdf(1))],
        refusal="matter files go only to staff",
        broker=broker,
    )
    assert "NOT staged" in told and broker.calls == 0
    assert mfr.staged("s1").refusal == "matter files go only to staff"


def test_no_email_this_turn_is_refused(tmp_path: Path) -> None:
    assert "no email opened this turn" in _stage(tmp_path / "spool", [("A.pdf", _pdf(1))], msg="")


def test_a_second_matter_in_one_turn_poisons_the_staging(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    _stage(spool, [("A.pdf", _pdf(1))])
    told = _stage(spool, [("B.pdf", _pdf(2))], matter=OTHER_MATTER)
    assert "second matter" in told and mfr.staged("s1").refusal


def test_a_result_for_another_matter_than_its_args_is_ignored(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    rows = [{"fileId": "f", "attachmentName": "A.pdf", **_spooled(spool, _pdf(1))}]
    told = mfr.stage_from_result(
        "s1",
        {"matter_id": MATTER},
        _result(rows, OTHER_MATTER),
        origin_message_id="m1",
        refusal=None,
        request=FakeBroker(),
    )
    assert told == "" and mfr.staged("s1") is None


# ---- at the reply ---------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"msgraph": False}, "cannot carry files"),
        ({"bound": True}, "email that asked"),
        ({"message_id": "m2"}, "email that asked"),
        ({"recipient": "client@else.example"}, "outside"),
        ({"internal": False}, "outside"),
        ({"redirected": True}, "stand-in"),
        ({"read_matters": {MATTER, OTHER_MATTER}}, "another matter's content"),
        ({"cited_matters": {OTHER_MATTER}}, "different matter"),
        ({"doc": {}}, "not authored"),
    ],
)
def test_prepare_refusals(tmp_path: Path, over, fragment) -> None:
    _stage(tmp_path / "spool", [("A.pdf", _pdf(1))])
    with pytest.raises(mfr.FileRefused, match=fragment):
        _prepare(**over)


def test_prepare_refuses_a_poisoned_or_empty_staging(tmp_path: Path) -> None:
    _stage(tmp_path / "spool", [("A.pdf", _pdf(1, js=True))])
    with pytest.raises(mfr.FileRefused, match="could be staged"):
        _prepare()
    _stage(tmp_path / "spool", [("B.pdf", _pdf(1))], refusal="no")
    with pytest.raises(mfr.FileRefused, match="no"):
        _prepare()


def test_five_records_become_two_replies_of_three_and_two(tmp_path: Path) -> None:
    files = [(f"R{i}.pdf", _pdf(i + 1)) for i in range(5)]
    _stage(tmp_path / "spool", files)
    prepared = _prepare(cited_matters={MATTER})
    assert [len(b) for b in prepared.batches] == [3, 2]
    descriptors = mfr.Prepared.descriptors(prepared.batches[0])
    assert set(descriptors[0]) == {"name", "content_type", "handle", "sha256"}
    assert mfr.continuation_text(2, 2, prepared.batches[1]) == (
        "Part 2 of 2 of the files you asked for, attached: R3.pdf, R4.pdf."
    )


def test_batches_split_on_bytes_too(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mfr, "MAX_BYTES_PER_REPLY", len(_pdf(1)) + 5)
    _stage(tmp_path / "spool", [("A.pdf", _pdf(1)), ("B.pdf", _pdf(1))])
    assert [len(b) for b in _prepare().batches] == [1, 1]


def test_more_files_than_three_replies_carry_is_refused(tmp_path: Path) -> None:
    _stage(tmp_path / "spool", [(f"R{i}.pdf", _pdf(i + 1)) for i in range(10)])
    with pytest.raises(mfr.FileRefused):
        _prepare()


def test_audit_fields_carry_ids_and_hashes_never_names(tmp_path: Path) -> None:
    _stage(tmp_path / "spool", [("Client Name MRI.pdf", _pdf(1))])
    fields = mfr.audit_fields(_prepare().batches[0])
    assert "Client" not in json.dumps(fields) and fields["attached_file_ids"] == ["f0"]


# ---- the reply lane, end to end -------------------------------------------

_SEAT_YAML = (
    "customer_id: acme\n"
    "vertical: law-firm\n"
    "connectors:\n"
    "  Email:\n"
    "    adapter: msgraph\n"
    "    enabled: true\n"
    "scope:\n"
    "  inbound_allow_from:\n"
    "    - '@firm.example'\n"
    "    - outsider@else.example\n"
    "  admins:\n"
    "    - office@firm.example\n"
    "matter_file_delivery:\n"
    "  recipients:\n"
    "    - '@firm.example'\n"
)


class _FakeD1:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, sql, *params):
        self.calls.append((sql, params))
        return 1

    def events(self) -> list[tuple[str, dict]]:
        return [(p[2], json.loads(p[-1]) if p[-1] else {}) for _s, p in self.calls]


@pytest.fixture
def lane(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-reply")
    d1 = _FakeD1()
    sent: list[dict] = []
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_SEAT_YAML)
    broker = FakeBroker()
    monkeypatch.setattr(mod, "_INFRA_READY", True, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_SLUG", "acme", raising=False)
    monkeypatch.setattr(mod, "_D1_CLIENT", d1, raising=False)
    monkeypatch.setattr(mod, "_LIMITER", mod.relay.RateLimiter(), raising=False)
    monkeypatch.setattr(mod, "_REPLIED", mod.relay.RepliedOnce(), raising=False)
    monkeypatch.setattr(mod, "_YAML_PATH", yaml_path, raising=False)
    monkeypatch.setattr(
        mod, "_HELD_STORE", mod.held_store.HeldReplyStore(str(tmp_path / "held.db")), raising=False
    )
    monkeypatch.setattr(mod, "_broker_request", broker)

    def send_reply(message_id, comment, *, html="", session_id="", matter_ref=None, **kw):
        sent.append({"message_id": message_id, "comment": comment, **kw})
        return f"<sent-{len(sent)}@firm.example>"

    monkeypatch.setattr(mod.msgraph_broker, "send_reply", send_reply)
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()
    SESSION_TAINT._tainted.clear()
    yield mod, d1, sent, tmp_path / "spool", broker
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


def _email(sender: str = STAFF, message_id: str = "m1", session: str = "s1") -> None:
    inbound.SESSION_INBOUND_ORIGIN.record(
        session,
        inbound.InboundOrigin(
            sender_address=sender, message_id=message_id, inbox_id="op@firm.example"
        ),
    )
    inbound.SESSION_INBOUND_ORIGIN.note_turn_prompt(session, message_id)


def _stage_call(mod, spool: Path, files, *, call="st1", session="s1", matter=MATTER) -> str | None:
    rows = [
        {"fileId": f"f{i}", "attachmentName": n, **_spooled(spool, d)}
        for i, (n, d) in enumerate(files)
    ]
    matter_binding.membership_for(session).note_content_read(matter)
    return mod.on_transform_tool_result(
        tool_name=mfr.STAGE_TOOL,
        args={"matter_id": matter, "files": []},
        result=_result(rows, matter),
        session_id=session,
        tool_call_id=call,
    )


def _draft(
    mod, to=STAFF, body="Here are the records you asked for.", call="d1", session="s1"
) -> str | None:
    return mod.on_transform_tool_result(
        tool_name="mcp_msgraph_mail_create_draft",
        args={"to": [to], "subject": "Re: records", "body_text": body},
        result='{"id": "draft-1"}',
        session_id=session,
        tool_call_id=call,
    )


def test_lane_staff_request_gets_its_files_in_two_replies(lane) -> None:
    mod, d1, sent, spool, broker = lane
    _email()
    told = _stage_call(mod, spool, [(f"R{i}.pdf", _pdf(i + 1)) for i in range(5)])
    assert "Staged for your reply" in told
    assert _draft(mod) is None  # relayed, nothing held
    assert [len(s.get("attachments") or []) for s in sent] == [3, 2]
    assert sent[1]["comment"].startswith("Part 2 of 2 of the files you asked for")
    assert all(s["message_id"] == "m1" for s in sent)
    for s in sent:
        for a in s["attachments"]:
            assert hashlib.sha256(bytes(broker.files[a["handle"]])).hexdigest() == a["sha256"]
    rows = [m for kind, m in d1.events() if kind == "REPLY_SENT"]
    assert [r["file_reply_part"] for r in rows] == ["1/2", "2/2"]
    assert mfr.staged("s1") is None


def test_lane_a_reply_with_nothing_staged_carries_no_files(lane) -> None:
    mod, _d1, sent, _spool, _b = lane
    _email()
    _draft(mod)
    assert sent and "attachments" not in sent[0]


def test_lane_an_outsider_on_the_reply_roster_gets_no_files(lane) -> None:
    mod, d1, sent, spool, broker = lane
    _email(sender="outsider@else.example")
    told = _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    assert "NOT staged" in told and broker.calls == 0
    notice = _draft(mod, to="outsider@else.example")
    assert sent == [] and "matter_files_refused" in notice
    # The hold cleared the staging: the redraft goes, without any file.
    _draft(mod, to="outsider@else.example", body="I could not attach the records.", call="d2")
    assert len(sent) == 1 and "attachments" not in sent[0]


def test_lane_no_email_this_turn_stages_nothing(lane) -> None:
    mod, _d1, _sent, spool, broker = lane
    told = _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    assert "no email opened this turn" in told and broker.calls == 0


def test_lane_files_never_ride_a_reply_to_a_later_email(lane) -> None:
    mod, _d1, sent, spool, _b = lane
    _email(message_id="m1")
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    # The same session's origin stays the email that asked; a later email is
    # a new session, which holds no staging at all.
    _email(message_id="m2", session="s2")
    _draft(mod, session="s2", call="d2")
    assert len(sent) == 1 and sent[0]["message_id"] == "m2" and "attachments" not in sent[0]
    # And a staging recorded for m1 never matches m2 at the reply.
    staging = mfr.staged("s1")
    with pytest.raises(mfr.FileRefused, match="email that asked"):
        mfr.prepare(
            staging,
            doc=DOC,
            message_id="m2",
            recipient=STAFF,
            internal=True,
            on_roster=True,
            redirected=False,
            bound=False,
            msgraph=True,
            read_matters={MATTER},
            cited_matters=set(),
        )


def test_lane_a_reply_naming_another_matter_carries_no_file(lane) -> None:
    mod, _d1, sent, spool, _b = lane
    _email()
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    matter_binding.membership_for("s1").add_alias("2026-PI-999", OTHER_MATTER)
    notice = _draft(mod, body="Attached are the records for 2026-PI-999.")
    assert sent == [] and "different matter" in notice


def test_lane_a_reply_with_files_is_never_queued_for_later(lane, monkeypatch) -> None:
    mod, _d1, sent, spool, _b = lane
    _email()
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    monkeypatch.setattr(
        mod._LIMITER, "check", lambda *a, **k: mod.relay.RateDecision(False, "rate_limited")
    )
    notice = _draft(mod)
    assert sent == [] and "matter_files_rate_limited" in notice
    assert mod._HELD_STORE.has_pending_for_message("m1") is False


def test_lane_a_tool_argument_can_never_carry_a_file(lane) -> None:
    """The model's own draft args naming attachments put nothing on the wire."""
    mod, _d1, sent, _spool, _b = lane
    _email()
    mod.on_transform_tool_result(
        tool_name="mcp_msgraph_mail_create_draft",
        args={
            "to": [STAFF],
            "subject": "Re",
            "body_text": "Here.",
            "attachments": [{"handle": "a" * 32}],
        },
        result='{"id": "draft-1"}',
        session_id="s1",
        tool_call_id="d9",
    )
    assert sent and "attachments" not in sent[0]
