"""Every templated guide-seat send is body-verifiable: its wake row carries the hashes.

THE DEFECT (2026-11-16). The console's daily send reconcile (ss-console
``operator/bin/lib/send_verify.py``) grades each templated CONFIRM_SEND_DISPATCHED
row against the ``body_sha256`` entries on the run's EMITTED_WAKE row. The guide
seat's three templated routines (``product-guide`` through
``shared.library_notice``, ``rep-desk`` through ``shared.person_digest``,
``rep-training`` through ``shared.training_report``) built envelopes with the
hashes in them and then wrote a wake row of counts only, so the first
library-change email held as ``no_wake_hash`` and the body was never checked.

WHAT IS PINNED. For each routine, the wake row the broker is asked to write
carries one ``{body_sha256_full, body_sha256_skeleton}`` entry per dispatch in
the envelope the run wrote, each full hash equal to ``canonical_body_sha256`` of
that dispatch's body (what the trust plugin stamps as ``rendered_body_sha256``
before sending), and the printed verdict carries none of them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from shared import (
    library_notice,
    person_digest,
    prerendered_dispatch,
    training,
    training_report,
    workspace_broker,
)
from tests import test_library_notice as tln
from tests import test_rep_desk as trd
from tests import test_training_report as ttr
from tests.test_library_notice import seat  # noqa: F401 - fixture
from tests.test_rep_desk import desk  # noqa: F401 - fixture


@pytest.fixture
def broker_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every row the routines ask the broker to append, as sent."""
    rows: list[dict[str, Any]] = []

    def request(payload: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        rows.append(payload["row"])
        return {"ok": True}

    monkeypatch.setattr(workspace_broker, "request", request)
    return rows


def _emitted(rows: list[dict[str, Any]]) -> dict[str, Any]:
    [row] = [r for r in rows if r["action_type"] == "EMITTED_WAKE"]
    return json.loads(row["metadata"])


def _assert_stamped(meta: dict[str, Any], envelope: dict[str, Any]) -> None:
    dispatches = envelope["dispatches"]
    assert dispatches, "the run must have written something to send"
    assert meta["render_mode"] == "templated"
    assert meta[person_digest.WAKE_STAMPS] == [
        {
            "body_sha256_full": prerendered_dispatch.canonical_body_sha256(d["full_body"]),
            "body_sha256_skeleton": prerendered_dispatch.canonical_body_sha256(d["skeleton_body"]),
        }
        for d in dispatches
    ]


def test_the_library_notice_wake_row_carries_each_bodys_hash(
    seat,  # noqa: F811 - the fixture imported above
    broker_rows,
    monkeypatch,
) -> None:
    v1 = tln._skill(seat["tmp"] / "v1", tln.LINE_1)
    real_run = library_notice.run
    monkeypatch.setattr(
        library_notice,
        "run",
        lambda cfg, *, dry=False, wake=None: real_run(
            cfg, tln.NOW, dry=dry, skill_dir=v1, wake=wake
        ),
    )
    verdict = library_notice.pre_run()
    assert verdict["status"] == "notice_ready" and verdict["chunk"] == 2
    _assert_stamped(_emitted(broker_rows), tln._envelope(seat))
    assert person_digest.WAKE_STAMPS not in verdict and "sha256" not in json.dumps(verdict)


def test_the_rep_desk_wake_row_carries_each_bodys_hash(
    desk,  # noqa: F811 - the fixture imported above
    broker_rows,
    monkeypatch,
) -> None:
    trd._keep(trd.A, trd.TODAY, f"Call Acme {trd.A_MARK}")
    trd._keep(trd.B, trd.TODAY, f"Send Beta the deck {trd.B_MARK}")
    real_run = person_digest.run
    monkeypatch.setattr(
        person_digest,
        "run",
        lambda cfg, *, dry=False, wake=None: real_run(cfg, trd.NOW, dry=dry, wake=wake),
    )
    verdict = person_digest.pre_run()
    assert verdict["status"] == "digest_ready" and verdict["chunk"] == 2
    _assert_stamped(_emitted(broker_rows), trd._take(desk, trd.NOW))
    assert person_digest.WAKE_STAMPS not in verdict and "sha256" not in json.dumps(verdict)


def test_the_training_report_wake_row_carries_its_bodys_hash(
    tmp_path, monkeypatch, broker_rows
) -> None:
    ttr._seat(tmp_path, monkeypatch)
    ttr._enroll(tmp_path, monkeypatch, (ttr.A, None))
    training.write_progress(ttr.A, training.new_progress())
    verdict = training_report.run(clock=lambda: datetime(2026, 11, 16, 15, tzinfo=timezone.utc))
    assert verdict["status"] == "report_ready"
    _assert_stamped(_emitted(broker_rows), ttr._envelope(tmp_path))
    assert "sha256" not in json.dumps(verdict)


def test_a_dispatch_with_a_no_attachment_body_keeps_that_hash() -> None:
    envelope = {
        "dispatches": [
            {
                "body_sha256_full": "a" * 64,
                "body_sha256_skeleton": "b" * 64,
                "body_sha256_without_attachment": "c" * 64,
            },
            {"body_sha256_full": "d" * 64, "body_sha256_skeleton": "e" * 64},
        ]
    }
    assert prerendered_dispatch.wake_stamps(envelope) == [
        {
            "body_sha256_full": "a" * 64,
            "body_sha256_skeleton": "b" * 64,
            "body_sha256_without_attachment": "c" * 64,
        },
        {"body_sha256_full": "d" * 64, "body_sha256_skeleton": "e" * 64},
    ]
    assert prerendered_dispatch.wake_stamps({}) == []
