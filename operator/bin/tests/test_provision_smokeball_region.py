"""The provisioner stages SMOKEBALL_REGION explicitly, from customer.yaml (ss#2425).

Before this, region was never staged: the connector defaulted to "us", which is
right for every seat today and silently the wrong gateway for the first AU or
UK firm. The Smokeball API key is region-scoped (six keys on six gateways in
three AWS regions), so that firm would 403 with a valid key, the confusing
failure the credential family has already paid for once (ss#2423). Three
things are pinned, all by reading the staging block as text the way
test_deploy_ordering.py reads the provisioner: the authored key is parsed with
an explicit default, the value is refused when it is not a region the connector
knows, and it is staged in the same block as SMOKEBALL_ENVIRONMENT. The block
is lib/stage-smokeball.sh, sourced by provision-customer.sh; a fourth check
pins that the provisioner still sources it, so the block cannot be orphaned.
"""

from __future__ import annotations

import re
from pathlib import Path

_BIN = Path(__file__).resolve().parents[1]
_PROVISION = _BIN / "provision-customer.sh"
_BLOCK = _BIN / "lib" / "stage-smokeball.sh"


def _text() -> str:
    return _BLOCK.read_text()


def test_provisioner_sources_the_smokeball_block() -> None:
    assert 'source "${BIN_DIR}/lib/stage-smokeball.sh"' in _PROVISION.read_text()
    assert "authored_channel '^backend=mcp:smokeball$'" in _text()


def test_region_is_parsed_with_an_explicit_default() -> None:
    assert "print(str(sb.get('region', 'us')).strip().lower())" in _text()
    assert re.search(r'^\s*SB_REGION="\$\{SB_FIELDS\[3\]:-us\}"', _text(), re.M)


def test_unknown_region_is_refused_before_staging() -> None:
    text = _text()
    guard = re.search(r'case "\$\{SB_REGION\}" in\s*\n\s*us\|au\|uk\) ;;\s*\n\s*\*\) die ', text)
    assert guard, "no us|au|uk guard on SB_REGION"
    stage = text.index("stage_secret_from_env SMOKEBALL_REGION")
    assert guard.start() < stage, "the region guard must run before the value is staged"


def test_region_is_staged_next_to_environment() -> None:
    text = _text()
    env_idx = text.index('stage_secret_from_env SMOKEBALL_ENVIRONMENT   "${SB_ENV}"')
    region_idx = text.index('stage_secret_from_env SMOKEBALL_REGION        "${SB_REGION}"')
    assert env_idx < region_idx
    # Same authored-channel block: no `fi` closes the smokeball block between them.
    between = text[env_idx:region_idx]
    assert re.search(r"^\s*fi\s*$", between, re.M) is None
