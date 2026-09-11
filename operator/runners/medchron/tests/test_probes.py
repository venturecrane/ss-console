"""`medchron probe <gate>`: each registered control's planted violation is
refused by the installed package, offline, and the CLI says so on one line
(the seat probe's expect_pattern is `^REFUSED <gate>`)."""

from __future__ import annotations

import pytest

from medchron import probes
from medchron.__main__ import main


@pytest.mark.parametrize("gate", sorted(probes.PROBES))
def test_each_planted_violation_is_refused(gate, capsys):
    assert main(["probe", gate]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"REFUSED {gate}")


def test_an_unknown_probe_is_a_fault_not_a_pass(capsys):
    with pytest.raises(SystemExit):
        main(["probe", "nope"])


def test_a_probe_that_finds_no_refusal_exits_one(monkeypatch, capsys):
    monkeypatch.setitem(probes.PROBES, "claim_audit", lambda tmp: (False, "nothing refused"))
    assert probes.run_probe("claim_audit") == 1
    assert capsys.readouterr().out.startswith("UNEXPECTED_PASS claim_audit")


def test_a_probe_fault_is_not_a_finding(monkeypatch, capsys):
    def boom(tmp):
        raise RuntimeError("no such fixture")

    monkeypatch.setitem(probes.PROBES, "provenance", boom)
    assert probes.run_probe("provenance") == 2
    assert capsys.readouterr().out.startswith("PROBE_FAULT provenance")
