"""Tests for overlay-ref-drift.py — the stale-overlay detector."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "overlay-ref-drift.py"


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("overlay_ref_drift", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass can resolve its own module (the
    # dataclass machinery reads sys.modules[cls.__module__] under
    # `from __future__ import annotations`).
    sys.modules["overlay_ref_drift"] = mod
    spec.loader.exec_module(mod)
    return mod


drift = _load()

DESIRED = "a5480862017c4214acce5a3f48d19b7067306ca5"


class _FakeClient:
    def __init__(self, ref):
        self._ref = ref

    def read_config(self):
        if self._ref == "__raise__":
            raise OSError("machine down")
        return {"schema": "x", "overlay_ref": {"value": self._ref, "source": "direct_url"}}


def _factory(mapping):
    def make(slug):
        val = mapping.get(slug, "__missing__")
        if val == "__missing__":
            return None  # unconfigured
        return _FakeClient(val)

    return make


# --- desired_ref_from_dockerfile -------------------------------------------


def test_desired_ref_parses(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text(f'ARG OVERLAY_REPO="x"\nARG OVERLAY_REF="{DESIRED}"\n', encoding="utf-8")
    assert drift.desired_ref_from_dockerfile(df) == DESIRED


def test_desired_ref_missing_raises(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(ValueError):
        drift.desired_ref_from_dockerfile(df)


# --- discover_slugs ---------------------------------------------------------


def test_discover_slugs_excludes_scaffolds_and_tombstones(tmp_path):
    # Every scaffold, not only `_template`: this used to count `_hosted-template`
    # and `_couples-template` as seats. The rule is bin/lib/seat_dirs.py.
    for name in (
        "smd",
        "acme",
        "_template",
        "_hosted-template",
        "_couples-template",
        ".hidden",
        "old.decommissioned.2026-10-01",
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / "customer.yaml").write_text("seat: {}\n", encoding="utf-8")
    (tmp_path / "no-yaml").mkdir()
    assert drift.discover_slugs(tmp_path) == ["acme", "smd"]


# --- refs_match -------------------------------------------------------------


def test_refs_match_exact_and_prefix():
    assert drift.refs_match(DESIRED, DESIRED)
    assert drift.refs_match(DESIRED, DESIRED[:7])  # short ref equals full
    assert drift.refs_match(DESIRED[:7], DESIRED)
    assert not drift.refs_match(DESIRED, "deadbeef")
    assert not drift.refs_match(DESIRED, None)


# --- classify ---------------------------------------------------------------


def test_classify_current_drift_unreachable_unconfigured_unknown():
    mapping = {
        "current-one": DESIRED,
        "drifted": "8d6f1a95189641366c5ff10b01e65d29b6a895fe",
        "down": "__raise__",
        # 'never-seen' omitted → unconfigured (factory returns None)
    }
    results = drift.classify(
        DESIRED,
        ["current-one", "drifted", "down", "never-seen"],
        _factory(mapping),
    )
    by = {r.slug: r.status for r in results}
    assert by == {
        "current-one": "current",
        "drifted": "drift",
        "down": "unreachable",
        "never-seen": "unconfigured",
    }


def test_classify_unknown_when_no_ref_value():
    class _NoRef:
        def read_config(self):
            return {"schema": "x", "overlay_ref": {"value": None, "source": None}}

    results = drift.classify(DESIRED, ["x"], lambda s: _NoRef())
    assert results[0].status == "unknown"


# --- main exit codes --------------------------------------------------------


def _write_dockerfile(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text(f'ARG OVERLAY_REF="{DESIRED}"\n', encoding="utf-8")
    cust = tmp_path / "customers"
    return df, cust


def test_main_exit_zero_when_all_current(tmp_path, monkeypatch):
    df, _ = _write_dockerfile(tmp_path)
    monkeypatch.setattr(drift.seam_pull, "seam_client_from_env", lambda s: _FakeClient(DESIRED))
    rc = drift.main(["smd", "acme", "--dockerfile", str(df)])
    assert rc == 0


def test_main_exit_one_on_drift(tmp_path, monkeypatch):
    df, _ = _write_dockerfile(tmp_path)
    monkeypatch.setattr(drift.seam_pull, "seam_client_from_env", lambda s: _FakeClient("deadbeef0000"))
    rc = drift.main(["smd", "--dockerfile", str(df)])
    assert rc == 1


def test_main_unreachable_zero_unless_strict(tmp_path, monkeypatch):
    df, _ = _write_dockerfile(tmp_path)
    monkeypatch.setattr(drift.seam_pull, "seam_client_from_env", lambda s: None)
    assert drift.main(["smd", "--dockerfile", str(df)]) == 0
    assert drift.main(["smd", "--dockerfile", str(df), "--strict"]) == 2
