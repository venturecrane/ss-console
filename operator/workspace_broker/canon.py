"""Canonical JSON bytes: the one encoding every broker digest and receipt uses.

Shared by the grant store (server.py), the workspace verbs (payload digests,
signed receipts) and the transmit verbs (input digests). One definition, so a
digest computed at authorize time and one computed at execute time can never
disagree on whitespace or key order.
"""

from __future__ import annotations

import json
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
