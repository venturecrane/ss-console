"""A manifest key nothing reads must fail to load, not vanish (ss#2425).

The trap this pins: ``[connector.env_static]`` was a table the SDK schema
accepted and no code consumed. The smokeball manifest used it to say
``SMOKEBALL_ENVIRONMENT = "staging"`` while production seats stage
``production``; had anything ever started honoring it, a production seat would
have pointed at the staging hosts. Removing the field is half the fix; the other
half is that the next such key is rejected at load rather than ignored, which is
what ``extra="forbid"`` on the model does. Both directions are asserted: the
real manifests in this tree still load, and a manifest carrying the old table
does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from operator_connector_sdk.manifest import ConnectorManifest
from pydantic import ValidationError

CONNECTORS = Path(__file__).resolve().parents[2]


def _manifests() -> list[Path]:
    found = sorted(p for p in CONNECTORS.glob("*/manifest.toml") if p.parent.name != "_sdk")
    assert found, f"no connector manifests under {CONNECTORS}"
    return found


@pytest.mark.parametrize("path", _manifests(), ids=lambda p: p.parent.name)
def test_every_shipped_manifest_loads_under_forbid(path: Path) -> None:
    m = ConnectorManifest.from_toml(path)
    assert m.name == path.parent.name.lstrip("_") or m.name == path.parent.name


def test_env_static_table_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.toml"
    bad.write_text(
        '[connector]\nname = "x"\ncapability = "Cap"\nauth_model = "static"\n'
        '[connector.env_static]\nSMOKEBALL_ENVIRONMENT = "staging"\n'
    )
    with pytest.raises(ValidationError) as exc:
        ConnectorManifest.from_toml(bad)
    assert "env_static" in str(exc.value)


def test_any_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ConnectorManifest.model_validate(
            {"name": "x", "capability": "Cap", "auth_model": "static", "launch_env": {"A": "1"}}
        )
    assert "launch_env" in str(exc.value)
