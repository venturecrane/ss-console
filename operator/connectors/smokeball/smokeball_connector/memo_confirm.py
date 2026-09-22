"""A memo write that confirms itself.

The write posture says a write is reported as done only after a confirming read
(``_shared-write-posture.md`` rule 1). Until 2026-09-22 that read was the
model's: ``get_memos_on_matter`` after ``create_memo``. On a scheduled scan that
walks every open matter in one session, the overlay's matter-mixing fence
refuses every memo read after the first matter (one session must not hold two
matters' content), so every confirm after the first was refused, and each
refusal counted toward the seat's refusal-cascade brake. pilot-smokeball logged
16 such refusals that morning.

The confirming read belongs here instead. The connector reads back the one
memo it just wrote, by id, which is not a tool call and puts no second matter's
content in front of the model.

The POST response is returned unchanged with two keys added:

* ``confirmed``: ``True`` when the read-back's ``plainText`` matches what was
  sent; ``False`` only on a proven mismatch; ``"unknown"`` when the POST
  succeeded but no read-back could be completed. ``"unknown"`` never means
  "retry the write": the memo very likely exists, and re-creating it doubles it.
* ``confirm_detail``: a short reason.

Compared field: ``plainText``, never ``text``. Smokeball stores ``text`` as RTF
(probed live 2026-09-22, vfy_01M353GD06FZ5TCMMA8DFCM0SA), so a string compare
against it fails on every memo.
"""

from __future__ import annotations

import re
import time
from typing import Any

#: Read-back attempts and the pause before each retry (seconds).
_ATTEMPTS = 3
_BACKOFF = (0.5, 1.5)

_WS = re.compile(r"\s+")


def _norm(text: Any) -> str:
    return _WS.sub(" ", text).strip() if isinstance(text, str) else ""


def post_and_confirm(client: Any, matter_id: str, body: str, *, sleep: Any = time.sleep) -> Any:
    """POST ``body`` as a memo on ``matter_id``, then read it back by id.

    A POST failure raises exactly as before (nothing was written, so the caller
    may retry). Everything after a successful POST is reported, never raised."""
    resp = client.request("POST", f"/matters/{matter_id}/memos", json={"text": body})
    memo_id = resp.get("id") if isinstance(resp, dict) else None
    if not memo_id:
        return _with(resp, "unknown", "the write returned no memo id to read back")
    want = _norm(body)
    last = "no read-back attempted"
    for attempt in range(_ATTEMPTS):
        if attempt:
            sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
        try:
            memo = client.get(f"/matters/{matter_id}/memos/{memo_id}")
        except Exception as exc:  # noqa: BLE001 - a failed read is "unknown", never a failed write
            last = f"read-back failed: {exc.__class__.__name__}"
            continue
        if not isinstance(memo, dict) or memo.get("id") != memo_id:
            last = "read-back returned a different or malformed record"
            continue
        got = _norm(memo.get("plainText"))
        if not got:
            last = "read-back carried no plainText"
            continue
        if got == want:
            return _with(resp, True, "read back by id; text matches")
        return _with(resp, False, "read back by id; the stored text differs from what was sent")
    return _with(resp, "unknown", last)


def _with(resp: Any, confirmed: Any, detail: str) -> Any:
    if isinstance(resp, dict):
        return {**resp, "confirmed": confirmed, "confirm_detail": detail}
    return {"response": resp, "confirmed": confirmed, "confirm_detail": detail}
