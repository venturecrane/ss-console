"""`medchron.litigation_close`: SMD ends a held litigation job it finished by hand,
through the broker's root-only `litigation_job_close`, and nothing else.

WHY (2026-10-14). A held litigation job SMD filed by hand had no way off `held`
on the seat. Each test names the failure it stands against."""

from __future__ import annotations

import json

import pytest

from medchron import litigation_close as lc
from medchron.broker_client import BrokerError

JOB = "01M4AAAAAAAAAAAAAAAAAAAAAA"
VFY = "vfy_01K7ABCDEFGHJKMNPQRSTVWXYZ"


class _Client:
    def __init__(self, refuse: str | None = None) -> None:
        self.requests: list[dict] = []
        self.refuse = refuse

    def _request(self, req):
        self.requests.append(req)
        if self.refuse:
            raise BrokerError(f"broker refused: invalid_request: {self.refuse}")
        return {"ok": True, "dry_run": req["dry_run"], "job_id": req["job_id"]}


def test_it_sends_exactly_one_close_request_and_nothing_else(capsys) -> None:
    """FALSIFIER: any second verb (a record, a resume, a reply) would be a
    second write the close does not own."""
    client = _Client()
    code = lc.main([JOB, "--reason", "filed by hand; requester told", "--source-ref", VFY], client=client)
    assert code == 0
    assert client.requests == [
        {
            "action": "litigation_job_close",
            "job_id": JOB,
            "reason": "filed by hand; requester told",
            "source_ref": VFY,
            "dry_run": False,
        }
    ]
    assert "nothing was sent" in capsys.readouterr().out


def test_dry_run_is_passed_to_the_broker(capsys) -> None:
    client = _Client()
    assert lc.main([JOB, "--reason", "x", "--source-ref", VFY, "--dry-run"], client=client) == 0
    assert client.requests[0]["dry_run"] is True
    out = capsys.readouterr().out
    assert json.loads(out)["dry_run"] is True and "closed" not in out.split("}")[-1]


def test_a_broker_refusal_exits_2_and_says_refused(capsys) -> None:
    client = _Client(refuse=f"job {JOB} is failed; only a held job is closed by SMD")
    assert lc.main([JOB, "--reason", "x", "--source-ref", VFY], client=client) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_reason_and_source_ref_are_required() -> None:
    with pytest.raises(SystemExit):
        lc.main([JOB, "--source-ref", VFY], client=_Client())
    with pytest.raises(SystemExit):
        lc.main([JOB, "--reason", "x"], client=_Client())


def test_without_the_broker_socket_it_refuses(monkeypatch, capsys) -> None:
    monkeypatch.delenv(lc.SOCKET_ENV, raising=False)
    assert lc.main([JOB, "--reason", "x", "--source-ref", VFY]) == 2
    assert lc.SOCKET_ENV in capsys.readouterr().err
