"""The in-package connector manifest — how a connector self-describes.

A connector ships a ``manifest.toml`` declaring the facts the platform needs to
wire it: the capability it serves, the NAMES of the secrets it requires at
runtime, its auth model, and ``tool_classes``. It does not carry launch env
values: the overlay registry (``bootstrap/mcp_registry.py``) is the one wiring
spec, and the provisioner stages every per-seat value. A manifest key nothing
reads is a declaration that looks binding and is not (ss#2425: an
``env_static`` table once said ``SMOKEBALL_ENVIRONMENT = "staging"`` for a
connector whose production seats stage ``production``), so unknown keys are
rejected at load rather than ignored.

``tool_classes`` is the **conformance oracle, not a runtime input.** The enforced
tool->ActionClass mapping is the hand-authored literal in the overlay
(``shared/action_classes.py::_RAW_TOOL_ACTION_CLASS_MAP``), reviewed in the trust
repo. A conformance test asserts the manifest agrees with that map (manifest is
checked *against* the map; the map is authority). The manifest carries the
classes here so the connector author declares intent in one place and the test
has something to check — never so the connector can self-certify its own trust.
"""

from __future__ import annotations

import enum
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The action-class vocabulary, mirrored from the overlay's ActionClass enum
# (shared/action_classes.py). REFUSED is deliberately excluded — it is the
# fail-closed sentinel for an *un*classified tool, never a class a connector may
# declare. A PR-2 conformance test asserts this set equals the overlay enum
# (minus REFUSED), the same way capability_contract.py mirrors the TS union.
ACTION_CLASSES: frozenset[str] = frozenset(
    {
        "read",
        "internal_write",
        "external_send",
        "commitment",
        "destructive",
        "code_execution",
    }
)


class AuthModel(str, enum.Enum):
    """How the platform provisions this connector's credentials.

    - ``static``: a long-lived secret (e.g. API key) injected as an env var.
    - ``client_credentials``: OAuth2 client-credentials grant; the server mints
      its own bearer from client id/secret env vars.
    - ``authorization_code``: OAuth2 auth-code + refresh; the platform supplies
      the credential via the existing per-customer OAuth custody path (portal
      consent -> staged secret -> on-volume refresh). No per-connector
      consent/seeding code.
    """

    STATIC = "static"
    CLIENT_CREDENTIALS = "client_credentials"
    AUTHORIZATION_CODE = "authorization_code"


class SecretSpec(BaseModel):
    """One secret the connector needs at runtime. The connector declares the
    runtime env-var NAME and why; the *source* (where the value comes from) and
    any var remap are bound by the overlay registry per customer, not here — so
    the manifest never becomes a second, contradictory wiring spec."""

    runtime_env: str = Field(..., min_length=1)
    purpose: str = ""

    @field_validator("runtime_env")
    @classmethod
    def _env_shape(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or not v[0].isalpha():
            raise ValueError(f"runtime_env {v!r} must be an env-var-shaped identifier")
        return v


class ConnectorManifest(BaseModel):
    """Self-description loaded from a connector's ``manifest.toml``.

    ``extra="forbid"``: a key this model does not know is a load error, never a
    silently dropped table (see the module docstring)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    capability: str = Field(..., min_length=1)
    auth_model: AuthModel
    required_secrets: list[SecretSpec] = Field(default_factory=list)
    # Bare-tool-name -> ActionClass string. Oracle only (see module docstring).
    # A tool the connector intends to leave UNCLASSIFIED (to prove fail-closed
    # refusal) is simply omitted here.
    tool_classes: dict[str, str] = Field(default_factory=dict)
    description: str = ""

    @field_validator("required_secrets")
    @classmethod
    def _unique_envs(cls, v: list[SecretSpec]) -> list[SecretSpec]:
        envs = [s.runtime_env for s in v]
        if len(envs) != len(set(envs)):
            raise ValueError("duplicate runtime_env names in required_secrets")
        return v

    @field_validator("tool_classes")
    @classmethod
    def _known_classes(cls, v: dict[str, str]) -> dict[str, str]:
        for tool, cls_name in v.items():
            if cls_name not in ACTION_CLASSES:
                raise ValueError(
                    f"tool_classes[{tool!r}] = {cls_name!r} is not a known ActionClass "
                    f"(allowed: {sorted(ACTION_CLASSES)}; 'refused' is never declarable)"
                )
        return v

    @classmethod
    def from_toml(cls, path: str | Path) -> ConnectorManifest:
        data = tomllib.loads(Path(path).read_text())
        # Accept either a top-level table or a [connector] table.
        return cls.model_validate(data.get("connector", data))
