"""No seat may enable Clio while the image carries a clio-mcp that logs client data.

@oktopeak/clio-mcp below 2.2.0 writes contact search queries, note subjects,
matter descriptions and custom field values in plaintext to ~/.clio-mcp/audit.log
(the publisher's npm deprecation notice; fixed in v2.2.0). The image pins 2.0.0
(operator/npm/clio-mcp/package.json) and no seat uses Clio today (probed on all
six seats 2026-10-28: no ~/.clio-mcp anywhere). 2.2.0+ also changes the token
store (it adds @napi-rs/keyring), so the bump has to be proved on a real Clio
seat, not taken blind. Until then, this gate fails the build the moment any
customer.yaml authors `mcp:clio-oktopeak`, so the upgrade happens before the
first Clio client, never after.
"""

from __future__ import annotations

import json
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
SAFE = (2, 2, 0)


def _pinned() -> tuple[int, ...]:
    pkg = json.loads((OPERATOR / "npm/clio-mcp/package.json").read_text())
    version = pkg["dependencies"]["@oktopeak/clio-mcp"].lstrip("^~=")
    return tuple(int(x) for x in version.split(".")[:3])


def _clio_seats() -> list[str]:
    return sorted(
        path.parent.name
        for path in (OPERATOR / "customers").glob("*/customer.yaml")
        if path.parent.name != "_template" and "mcp:clio-oktopeak" in path.read_text()
    )


def test_no_seat_enables_clio_while_the_pinned_clio_mcp_logs_client_data() -> None:
    # FALSIFIER: author `backend: mcp:clio-oktopeak` in any seat's customer.yaml
    # with the 2.0.0 pin and this fails, naming the seat.
    seats = _clio_seats()
    if _pinned() < SAFE:
        assert seats == [], (
            f"{seats} enable Clio, but operator/npm/clio-mcp pins @oktopeak/clio-mcp "
            f"{'.'.join(map(str, _pinned()))}, which logs client data in plaintext. "
            "Upgrade to >= 2.2.0 and prove its token store on that seat first."
        )


def test_the_gate_reads_the_real_pin() -> None:
    assert len(_pinned()) == 3
