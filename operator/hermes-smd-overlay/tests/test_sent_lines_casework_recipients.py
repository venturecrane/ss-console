"""The case-manager sends keep who they went to beside their line labels, too.

Part 1 of ss-console #3946 recorded recipient digests from the scheduled-send
path only; the two casework sends (the task review and the date brief) still
wrote label files no purge could attribute. Driven through the same casework
flows ``test_casework.py`` drives. FALSIFIER: before this change the files
carried ``labels`` (and ``kinds``) and no ``to_sha256``.
"""

from __future__ import annotations

import json

from shared import sent_lines
from tests.test_casework import (  # noqa: F401 - fixtures and flows reuse
    _ARGS,
    _brief,
    _brief_envelope,
    _clean,
    _finish,
    _labelled_review,
    _write_envelope,
    rows,
)


def _files() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(sent_lines._dir().glob("*.json"))]


def test_a_task_review_records_its_recipients(tmp_path, rows):  # noqa: F811
    envelope = _labelled_review()
    message = envelope["messages"][0]
    _write_envelope(tmp_path, "casework", envelope, "task-list-keeper")
    assert _finish(tmp_path, [], [])["status"] == "sent"
    [label_file] = _files()
    want = {
        sent_lines.recipient_digest(a) for a in [*message["recipients"], *(message.get("cc") or [])]
    }
    assert want and set(label_file["to_sha256"]) == want


def test_a_date_brief_records_its_recipients(tmp_path, rows):  # noqa: F811
    envelope = _brief_envelope()
    _write_envelope(tmp_path, "brief", envelope, "date-prep-brief")
    assert _brief(tmp_path, _ARGS, [], [])["status"] == "sent"
    [label_file] = _files()
    want = {
        sent_lines.recipient_digest(a)
        for a in [*envelope["recipients"], *(envelope.get("cc") or [])]
    }
    assert want and set(label_file["to_sha256"]) == want
