"""The seat's authored digest home: the one matter a memo may cite others on.

``create_memo`` refuses text that names a matter number other than the matter
it is filed on (``server._verify_matter_reference``), because that is how one
matter's facts reach another matter's record. The digest home is the exception
by authoring: ``digest.home_matter_id`` in customer.yaml names an internal
operations matter whose whole purpose is to hold the firm-wide daily digest,
which by construction lists items across every open matter. Refusing every
matter number there made the digest unwritable: on 2026-09-21 pilot-smokeball's
digest memo on 2026-OPS-001 was refused for citing 2026-PI-101, and the model
kept only a degraded copy with the numbers stripped out.

Read fresh on every call (no module-level state): a customer.yaml edit takes
effect on the next memo, and an unreadable or unauthored file means NO home, so
the ordinary mismatch check applies everywhere. Fail-closed in the direction
that matters: nothing is exempt unless the seat authored it.
"""

from __future__ import annotations

import os
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML


def digest_home_matter_id(path: str | None = None) -> str | None:
    """``digest.home_matter_id`` from the seat's live customer.yaml, or None."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable or unparseable config: no exemption
        return None
    block = data.get("digest") if isinstance(data, dict) else None
    value = block.get("home_matter_id") if isinstance(block, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def verify_unless_digest_home(verify: Any, client: Any, matter_id: str, text: str | None) -> None:
    """Run ``verify(client, matter_id, text)`` unless ``matter_id`` is the
    seat's authored digest home (see the module docstring)."""
    if matter_id and matter_id == digest_home_matter_id():
        return
    verify(client, matter_id, text)
