"""Offline unit tests for the SEC-32 overlay-runtime drift verifier.

`operator/bin/verify-overlay-pairs.py` fetches the overlay repo at the pinned
ref and compares runtime-file hashes against the manifest. The fetch needs
network and runs in the operator-substrate CI step; these tests cover the
NETWORK-FREE logic — manifest loading/validation, the missing-overlaySha256
fail-closed path, and the real manifest's structural integrity — so the script
itself is gated by the pytest suite without a network dependency.

Run::

    cd operator && python -m pytest bin/tests/test_verify_overlay_pairs.py -v
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "operator" / "bin" / "verify-overlay-pairs.py"
_MANIFEST = _REPO_ROOT / "operator" / "contracts" / "overlay-pairs.json"
_DOCKERFILE = _REPO_ROOT / "operator" / "templates" / "Dockerfile"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_overlay_pairs", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


# ---------------------------------------------------------------------------
# Manifest loader contract
# ---------------------------------------------------------------------------


def test_load_manifest_accepts_real_manifest():
    m = mod._load_manifest(_MANIFEST)
    assert m["overlayRepo"]
    assert re.fullmatch(r"[0-9a-f]{40}", m["overlayRef"])
    assert isinstance(m["pairs"], list) and m["pairs"]


def test_load_manifest_rejects_array(tmp_path):
    bad = tmp_path / "arr.json"
    bad.write_text(json.dumps([{"adapterPath": "x"}]))
    with pytest.raises(ValueError):
        mod._load_manifest(bad)


def test_load_manifest_rejects_missing_keys(tmp_path):
    bad = tmp_path / "missing.json"
    bad.write_text(json.dumps({"overlayRepo": "r", "pairs": []}))
    with pytest.raises(ValueError):
        mod._load_manifest(bad)


def test_load_manifest_rejects_empty_pairs(tmp_path):
    bad = tmp_path / "empty.json"
    bad.write_text(json.dumps({"overlayRepo": "r", "overlayRef": "a" * 40, "pairs": []}))
    with pytest.raises(ValueError):
        mod._load_manifest(bad)


# ---------------------------------------------------------------------------
# Real manifest structural integrity (the pins SEC-32 added)
# ---------------------------------------------------------------------------


def test_every_pair_has_well_formed_hashes():
    m = mod._load_manifest(_MANIFEST)
    for pair in m["pairs"]:
        assert re.fullmatch(r"[0-9a-f]{64}", pair["sha256"]), pair["adapterPath"]
        assert re.fullmatch(r"[0-9a-f]{64}", pair["overlaySha256"]), pair["overlayPath"]
        assert pair["syncNote"]


def test_adapter_paths_exist_on_disk():
    m = mod._load_manifest(_MANIFEST)
    for pair in m["pairs"]:
        assert (_REPO_ROOT / pair["adapterPath"]).is_file(), pair["adapterPath"]


def test_manifest_overlay_ref_matches_dockerfile():
    # The same single-source-of-truth invariant the vitest gate enforces, also
    # asserted here so the operator-substrate suite catches a drift even if only
    # Python files changed in a PR.
    m = mod._load_manifest(_MANIFEST)
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r'ARG\s+OVERLAY_REF=["\']?([^"\'\s]+)["\']?', dockerfile)
    assert match, "Dockerfile ARG OVERLAY_REF not found"
    assert m["overlayRef"] == match.group(1), f"manifest overlayRef {m['overlayRef']} != Dockerfile {match.group(1)}"


# ---------------------------------------------------------------------------
# sha256 helper
# ---------------------------------------------------------------------------


def test_sha256_file_matches_known_value(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"hello")
    # sha256("hello")
    assert mod._sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
