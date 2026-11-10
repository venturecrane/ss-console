"""A drafting audit pass writes its cached system block once, then reads it.

Every section's audit call carries the same system block (the prompt, the
skeleton, the matter record and the whole digest) marked for the provider's
prompt cache. Sent all at once, each of the first ``workers`` calls found no
cache yet and paid to write its own copy. Demand fixed this on its own
(``demand/draft.py``: "four writes of the digest per audit pass on a live
job"); drafting did not (the 2026-11-10 lanes review, finding M4).

The test runs the REAL ``Drafter.audit`` through the REAL ``llm.Doorway`` and
usage ledger, at $0, against a counting client that behaves like the cache: a
block counts as written only once the call that wrote it has answered, so calls
in flight at the same moment each pay to write it. The ledger then says how
many writes the pass paid for.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from anthropic.types import Message, TextBlock, Usage
from drafting_testkit import make_inputs
from jobs.drafting import compose, firm as drafting_firm
from jobs.ledger import Ledger, read_rows
from jobs.llm import Doorway

AUDIT = "- claim | SUPPORTED | cite\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0"
SECTIONS = 6
BLOCK = 50_000


class CacheClient:
    """The provider's prompt cache, counted: each system block is written by
    the first call that FINISHES with it; a call that started before then
    paid to write it too."""

    def __init__(self, latency: float = 0.15) -> None:
        self.latency = latency
        self.written: set[str] = set()
        self.lock = threading.Lock()
        self.messages = self

    def stream(self, **params: Any) -> "CacheClient._Stream":
        return CacheClient._Stream(self, params)

    def _answer(self, params: dict[str, Any]) -> Message:
        key = repr(params.get("system"))
        with self.lock:
            hit = key in self.written
        time.sleep(self.latency)
        with self.lock:
            self.written.add(key)
        usage = Usage(
            input_tokens=200,
            output_tokens=40,
            cache_creation_input_tokens=0 if hit else BLOCK,
            cache_read_input_tokens=BLOCK if hit else 0,
        )
        return Message(
            id="msg_test",
            type="message",
            role="assistant",
            model=str(params["model"]),
            content=[TextBlock(type="text", text=AUDIT)],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=usage,
        )

    class _Stream:
        def __init__(self, client: "CacheClient", params: dict[str, Any]) -> None:
            self.client, self.params = client, params

        def __enter__(self) -> "CacheClient._Stream":
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def get_final_message(self) -> Message:
            return self.client._answer(self.params)


def _draft() -> str:
    body = "The record shows the visit on 2024-03-01 and the bill of $1,200. " * 6
    return "\n\n".join(f"# SECTION {i}\n\n{body}" for i in range(1, SECTIONS + 1)) + "\n"


def test_one_audit_pass_pays_to_write_the_cached_block_once(tmp_path: Path) -> None:
    """MUTATION: send every section at once and the first ``workers`` calls
    each write the block (four writes, not one)."""
    firm = drafting_firm.load(make_inputs(tmp_path / "inputs"))
    data = tmp_path / "data"
    data.mkdir()
    (data / "draft-v1.md").write_text(_draft(), encoding="utf-8")
    ledger_path = data / "usage-ledger.jsonl"
    door = Doorway(ledger=Ledger(ledger_path), client=CacheClient(), log=lambda _m: None)
    drafter = compose.Drafter(data, door, firm, "mediation_brief", "the request", [], 4, lambda _m: None)
    drafter.audit(1, "## DIGEST\n\nThe digest.", "the corpus")
    rows = [r for r in read_rows(ledger_path) if r["stage"] == "audit"]
    assert len(rows) == SECTIONS
    writes = [r for r in rows if r["cache_write"]]
    reads = [r for r in rows if r["cache_read"]]
    assert (len(writes), len(reads)) == (1, SECTIONS - 1)
