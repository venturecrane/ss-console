"""The live customer.yaml as a plain mapping, for :mod:`shared.capabilities` (ADR 0100).

Gateway plugins, translate's callers and the webhook gate ask the one predicate
(:mod:`shared.capabilities`) what this seat authors. They all need the same
document to ask about: the trusted copy boot relocated off the agent-writable
volume and pointed ``SMD_CUSTOMER_YAML_PATH`` at (see
:meth:`shared.customer_config.CustomerConfig.from_volume`). This resolves that
path the same way and nothing else, so a reader cannot pick a different file.

``None`` means the document could not be read as a mapping; the caller decides
what that means for its capability (a gateway plugin registers nothing beyond
today's core, and says so in one log line).
"""

from __future__ import annotations

import os
from typing import Any

from shared import capabilities
from shared.customer_config import DEFAULT_VOLUME_PATH


def live_path() -> str:
    """The path boot points every config reader at."""
    return os.environ.get("SMD_CUSTOMER_YAML_PATH") or DEFAULT_VOLUME_PATH


def live_doc() -> dict[str, Any] | None:
    """The live customer.yaml, or None when it will not read as a mapping."""
    return capabilities.read(live_path())
