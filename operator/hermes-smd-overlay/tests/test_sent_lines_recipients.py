"""A send's line labels record who it went to, as digests, so a person's purge
finds them (ss-console #3946; ``workspace_broker/person_purge.py``).

``shared/sent_lines.py`` keeps what each numbered line of a scheduled send said,
for 90 days, keyed by the send's ``dispatch_ref`` alone. A per-person digest's
lines carry that person's own items, so removing the person must be able to
find the files of the sends they received. The send path now passes its
recipients (To and Cc) and the file keeps only a sha256 of each address.

Shown to fail first: on origin/main the file carried ``labels`` and ``kinds``
only, and ``record`` took no recipients.
"""

from __future__ import annotations

import json

from shared import prerendered_dispatch, send_dispatch, sent_lines
from shared.send_dispatch import DispatchResult
from tests.test_prerendered_dispatch import (  # noqa: F401 - _clean is the autouse harness
    SESSION,
    _appends_recorder,
    _clean,
    _dispatch_entry,
    _routine,
    _Sender,
    _write_envelope,
)


def _file(ref: str) -> dict:
    return json.loads((sent_lines._dir() / f"{ref}.json").read_text(encoding="utf-8"))


def test_a_full_send_keeps_a_digest_of_each_recipient_and_no_address(monkeypatch, tmp_path):
    _routine(monkeypatch)
    entry = _dispatch_entry(cc=["Second <second@firm.example>"])
    entry["appends"][0]["n"] = 1
    entry["appends"][0]["kind"] = "task"
    _write_envelope(tmp_path, dispatches=[entry])
    _appends_recorder(monkeypatch)
    sender = _Sender([DispatchResult(sent=True, message_id="m1")])
    send_dispatch.set_sender(sender)
    prerendered_dispatch.dispatch_prerendered(SESSION)
    ref = sender.calls[0]["audit_extra"]["dispatch_ref"]
    raw = _file(ref)
    assert sent_lines.recipient_digest("ops@firm.example") in raw["to_sha256"]
    assert sent_lines.recipient_digest("second@firm.example") in raw["to_sha256"]
    assert "ops@firm.example" not in json.dumps(raw)
    assert "second@firm.example" not in json.dumps(raw)
    assert sent_lines.labels(ref)  # the labels themselves are unchanged


def test_record_without_recipients_writes_the_file_it_always_wrote(monkeypatch, tmp_path):
    ref = "c" * 32
    assert sent_lines.record(ref, {1: "A line"}, {1: "task"})
    assert _file(ref) == {"labels": {"1": "A line"}, "kinds": {"1": "task"}}
    assert sent_lines.record("d" * 32, {1: "A line"}, recipients=["A@X.example", " a@x.example "])
    assert _file("d" * 32)["to_sha256"] == [sent_lines.recipient_digest("a@x.example")]
