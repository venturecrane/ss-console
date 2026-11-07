"""The sample-transform voice mechanism is gone, and cannot come back quietly
(ss ADR 0083 decision 6, "Mechanism B is removed"; CLAUDE.md "Gone means gone").

The REPO layers, one assertion each. The runtime layers (the R2 prefixes, the
volume mirror, the running plugin, each seat's loaded config, the D1 projection)
are proven by the read-only probes in the removal PR's body, run on each seat
after release; a repo test cannot see them.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from bootstrap import validate as V
from bootstrap.translate import _persona_config

OVERLAY = Path(__file__).resolve().parents[1]
OPERATOR = OVERLAY.parent
RETIRED = ("voice_library", "voice_cohorts")

#: Every file of the mechanism that existed before its removal.
GONE = (
    OVERLAY / "plugins" / "hermes-smd-voice",
    OVERLAY / "shared" / "voice_status.py",
    OPERATOR / "adapter" / "voice",
    OPERATOR / "bin" / "voice-ingest-corpus.py",
    OPERATOR / "bin" / "voice-corpus-extract.py",
)


def _authored_yaml() -> list[Path]:
    """Every customer.yaml-shaped file the repo authors: seats, presets, templates, fixtures."""
    roots = (
        OPERATOR / "customers",
        OPERATOR / "templates",
        OPERATOR / "fixtures",
        OVERLAY / "tests" / "fixtures",
    )
    out = [
        p
        for r in roots
        for p in r.rglob("*.yaml")
        if p.name.startswith(("customer", "enum-", "nesting-"))
    ]
    assert len(out) >= 10, "the sweep found too few files to mean anything"
    return out


def _seats() -> list[Path]:
    return sorted(
        p
        for p in (OPERATOR / "customers").glob("*/customer.yaml")
        if not p.parent.name.startswith("_")
    )


@pytest.mark.parametrize("path", GONE, ids=lambda p: str(p.relative_to(OPERATOR)))
def test_the_mechanisms_files_are_gone(path):
    assert not path.exists()


def test_no_plugin_manifest_loads_it():
    manifest = yaml.safe_load((OVERLAY / "plugin.yaml").read_text())
    assert "plugins/hermes-smd-voice" not in manifest["plugins"]


def test_the_boot_no_longer_syncs_a_voice_vault():
    boot = (OPERATOR / "templates" / "bootstrap.sh").read_text()
    assert "SMD_VOICE_VAULT_DIR" not in boot and "R2_VOICE_COHORT_PREFIX" not in boot


def test_no_authored_file_carries_a_retired_key():
    for path in _authored_yaml():
        lines = path.read_text().splitlines()
        hits = [line for line in lines if line.startswith(tuple(f"{k}:" for k in RETIRED))]
        assert hits == [], f"{path.relative_to(OPERATOR)}: {hits}"


@pytest.mark.parametrize("key", RETIRED)
def test_the_boot_validator_refuses_a_retired_key(key, tmp_path):
    seat = _seats()[0]
    doc = yaml.safe_load(seat.read_text())
    assert V.validate_customer_yaml(seat) == [], (
        "the real seat must validate before the key is added"
    )
    doc[key] = (
        {"samples_path": "r2://vaults/x/voice/"}
        if key == "voice_library"
        else {"cohorts": ["client"]}
    )
    planted = tmp_path / "customer.yaml"
    planted.write_text(yaml.safe_dump(doc, sort_keys=False))
    errors = V.validate_customer_yaml(planted)
    assert any(key in e and "retired" in e for e in errors), errors


@pytest.mark.parametrize("seat", _seats(), ids=lambda p: p.parent.name)
def test_every_seats_profile_config_has_no_retired_key_and_nothing_reads_one(seat):
    """Per seat: no profile config names the key, and putting the removed block
    back changes nothing in any persona's profile config (nothing reads it). The
    comparison against the configs released before the removal is in the PR."""
    doc = yaml.safe_load(seat.read_text())
    planted = copy.deepcopy(doc)
    planted["voice_library"] = {"samples_path": f"r2://vaults/{seat.parent.name}/voice/"}
    for persona, again in zip(doc["personas"], planted["personas"], strict=True):
        config = _persona_config(persona, doc, {})
        assert not set(config) & set(RETIRED)
        assert _persona_config(again, planted, {}) == config
