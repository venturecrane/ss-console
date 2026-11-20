"""Model calls in the replay: answered by a recording, never by the vendor.

A recording is one JSON line in ``<key stem>.recordings.jsonl`` beside the key
(or ``recordings.jsonl`` in the key's folder)::

    {"sha": "<request_sha of the call>", "text": "<the reply>",
     "stop": "end_turn", "in": 1200, "out": 300, "stage": "<stage name>"}

``sha`` is :func:`request_sha` of the request the doorway would send (system,
messages and tools, canonical JSON), so a recording answers exactly the call it
was made for and nothing else. A call with no recording raises
:class:`NeedsModelCall`, which the replay reports as "needs a model call" for
that stage and stops there; the sha and stage go to ``needed`` so a recording
can be added for it.

WHY BaseException (the ``stop_request.StopRequested`` precedent). Every stage
and the doorway's retry loop catch ``Exception``; a missing recording is not a
stage defect and must not be retried for 20 seconds three times or turned into
a failed stage, so it passes through all of them to the replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...ledger import Ledger
from ...llm import Doorway


class NeedsModelCall(BaseException):
    """A model call the replay has no recording for."""

    def __init__(self, stage: str, sha: str) -> None:
        super().__init__(f"needs a model call ({stage or 'unnamed stage'}, request {sha[:12]})")
        self.stage, self.sha = stage, sha


def request_sha(params: dict[str, Any]) -> str:
    """The identity of a request: what the model would read, not the levers."""
    body = {k: params.get(k) for k in ("system", "messages", "tools")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def recordings_path(key_path: Path) -> Path | None:
    for p in (key_path.with_name(f"{key_path.stem}.recordings.jsonl"), key_path.with_name("recordings.jsonl")):
        if p.is_file():
            return p
    return None


def load_recordings(key_path: Path) -> dict[str, dict[str, Any]]:
    p = recordings_path(key_path)
    out: dict[str, dict[str, Any]] = {}
    if p is None:
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("sha"), str) and isinstance(row.get("text"), str):
            out[row["sha"]] = row
    return out


class _Stream:
    def __init__(self, msg: Any) -> None:
        self.msg = msg

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.msg


@dataclass
class RecordedClient:
    """Stands where the SDK client stands (``Doorway.client``)."""

    recordings: dict[str, dict[str, Any]]
    #: (stage, sha) of every call made, answered or not, in order.
    calls: list[tuple[str, str]] = field(default_factory=list)
    #: The calls no recording answered.
    needed: list[dict[str, Any]] = field(default_factory=list)
    stage: str = ""

    def __post_init__(self) -> None:
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)

    def _msg(self, params: dict[str, Any]) -> Any:
        sha = request_sha(params)
        self.calls.append((self.stage, sha))
        row = self.recordings.get(sha)
        if row is None:
            self.needed.append({"stage": self.stage, "sha": sha, "model": params.get("model")})
            raise NeedsModelCall(self.stage, sha)
        usage = SimpleNamespace(
            input_tokens=int(row.get("in") or 0),
            output_tokens=int(row.get("out") or 0),
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        content = [SimpleNamespace(type="text", text=row["text"])]
        return SimpleNamespace(content=content, stop_reason=str(row.get("stop") or "end_turn"), usage=usage)

    def _create(self, **params: Any) -> Any:
        return self._msg(params)

    def _stream(self, **params: Any) -> _Stream:
        return _Stream(self._msg(params))


def doorway(work_dir: Path, client: RecordedClient) -> Doorway:
    """A doorway over the recorded client; its ledger is the replay's own."""
    return Doorway(ledger=Ledger(work_dir / "replay-ledger.jsonl"), caching=False, client=client, log=lambda _m: None)
