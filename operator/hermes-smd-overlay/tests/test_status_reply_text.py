"""Each job lane's status tool hands the model the reply the broker rendered.

Since 2026-11-04 the broker adds ``reply_text`` (one text) or ``reply_texts``
(a held wording's choices) to a single-job status read, rendered by the same
code the bound send enforces (``workspace_broker/reply_text.py``). The overlay
renders nothing; it only must not drop the key while it projects the job, or
the DELIVER turn would have counts and no words, the shape that let the model
compose. FALSIFIER: remove ``reply_text`` from a lane's projected keys.
"""

from __future__ import annotations

import json

import pytest

JOB = "01M5ABCDEFGHJKMNPQRSTVWXYZ"
TEXT = "The ranked list is filed in the firm's Operator Library.\n\nNothing has been sent to anyone outside the firm."


class _Client:
    def __init__(self, job: dict) -> None:
        self.job = job

    def _one(self, *_a, **_k):
        return {"ok": True, "job": dict(self.job)}

    status = demand_status = drafting_status = litigation_status = _one
    casefile_status = negotiation_status = note_status = _one


LANES = [
    # (submodule or "" for the package, tool, the job the broker returned)
    ("litigation", "litigation_job_status", {"state": "delivered", "reply_text": TEXT}),
    ("casefile", "casefile_job_status", {"state": "delivered", "mode": "full", "reply_text": TEXT}),
    (
        "negotiation",
        "negotiation_job_status",
        {"kind": "notice", "state": "delivered", "message": "x", "reply_text": TEXT},
    ),
    ("note", "note_job_status", {"state": "delivered", "text": "x", "reply_text": TEXT}),
    ("demand", "demand_job_status", {"state": "delivered", "reply_text": TEXT}),
    ("drafting", "drafting_job_status", {"state": "delivered", "reply_text": TEXT}),
    ("", "_medchron_job_status", {"state": "delivered", "reply_text": TEXT}),
]


@pytest.mark.parametrize(("sub", "tool", "job"), LANES, ids=[t for _s, t, _j in LANES])
def test_the_status_tool_keeps_the_brokers_reply_text(
    sub: str, tool: str, job: dict, monkeypatch
) -> None:
    from tests.conftest import load_plugin

    pkg = load_plugin("hermes-smd-lanes")
    mod = getattr(pkg, sub) if sub else pkg
    monkeypatch.setattr(mod, "JobsBrokerClient", lambda: _Client(job))
    out = json.loads(getattr(mod, tool)({"job_id": JOB}))
    assert out["job"]["reply_text"] == TEXT


def test_a_held_wordings_choices_survive_the_projection(monkeypatch) -> None:
    from tests.conftest import load_plugin

    mod = load_plugin("hermes-smd-lanes").litigation
    texts = ["Nothing was changed: Pat ...", "Nothing was changed: Sam ..."]
    monkeypatch.setattr(
        mod, "JobsBrokerClient", lambda: _Client({"state": "held", "reply_texts": texts})
    )
    assert json.loads(mod.litigation_job_status({"job_id": JOB}))["job"]["reply_texts"] == texts
