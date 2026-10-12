"""Grading an HTML channel body against the broker's ``html_body_sha256`` stamp.

THE GAP THIS CLOSES (ss#3030 follow-up). On msgraph the broker hands Graph
ONLY the html part of a report send, so Sent Items stores HTML, while every
other stamp hashes markdown (``rendered_body_sha256``) or the text/plain
down-render (``plain_body_sha256``). Until this module the channel check graded
such a body ``channel_unstamped_representation``: honest, never a false
finding, and it meant the independent mailbox observation verified NO templated
send on an all-msgraph seat. The broker now stamps ``html_body_sha256`` on
CONFIRM_SEND_DISPATCHED (``workspace_broker/msgraph_ops._html_stamp``) over the
exact html it put on the wire, and this module grades the stored body against
it with the SAME canonical hash (``workspace_broker.html_canon``), which undoes
the rewrite Exchange applies on storage (calibration fixture:
``operator/contracts/fixtures/html-canon-calibration.json``).

THE DEPLOY EDGE, PER ROW -- the plain stamp's rule, mirrored
(``send_attribution.plain_stamp_edge`` has the incident that made it per row).
The html stamp exists only on rows written by a broker that carries it, which
lands on a seat at reprovision. So:

* a row with the stamp grades MATCH on equality; a mismatch on a row that a
  dispatch IDENTIFIED is a FINDING, on one claimed only by window a hold;
* a row without it, before the inbox's first html-stamped row (or on an inbox
  with none yet), keeps ``channel_unstamped_representation`` -- no action by
  anyone will ever stamp it, so it must not be a hold, and nothing stamped what
  the mailbox stores, so it must not be a finding;
* a row without it AFTER the edge is also unstamped-representation, with its
  own detail: the broker stamps sendMail bodies only, and a reply's or a
  send-as's stored html (Exchange composes the quoted thread itself) has no
  stamp by construction. Never a finding: nothing hashed what is stored.

LEAK SAFETY. :class:`HtmlBody` is the fetched body; it lives in the grader's
local variable, is hashed, and goes nowhere else. Verdicts carry hashes only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from workspace_broker.html_canon import html_body_sha256


class HtmlBody(str):
    """A channel body whose stored representation is HTML (msgraph Sent Items).

    A ``str`` so every existing consumer of a fetched body keeps working; the
    type is the representation tag the grader dispatches on."""


def html_stamp_edge(dispatches: list) -> Optional[datetime]:
    """The earliest dispatch row on the inbox carrying ``html_body_sha256``."""
    stamped = [stamp.ts for stamp in dispatches if getattr(stamp, "html_body_sha256", "")]
    return min(stamped) if stamped else None


def grade_html_body(body: HtmlBody, wake, message, dispatches, window_s, html_edge, identified, common):
    """The verdict for one HTML channel body (module docstring has the rules)."""
    from . import send_verify as sv  # lazy: send_verify imports this module
    from .send_attribution import claim_dispatch_stamp

    digest = html_body_sha256(body)
    stamp = claim_dispatch_stamp(dispatches, wake, window_s, message)
    if stamp is not None:
        common["dispatch_ts"] = stamp.ts.isoformat()
    if stamp is not None and stamp.html_body_sha256:
        common["expected_sha256"] = stamp.html_body_sha256
        return sv._graded(digest == stamp.html_body_sha256, digest, "the dispatch html_body_sha256", common, identified)
    if stamp is None and html_edge is not None:
        detail = (
            "html channel body and no stamped dispatch row in window is left to compare against (hold, not finding)"
        )
        return sv.BodyVerdict(verdict=sv.VERDICT_CHANNEL_MISMATCH, actual_sha256=digest, detail=detail, **common)
    if stamp is not None and html_edge is not None and stamp.ts >= html_edge:
        detail = "msgraph stores html for this send but its dispatch row carries no html_body_sha256 (reply or send-as path; the broker stamps sendMail bodies only)"
    elif stamp is not None:
        detail = "msgraph stores this send's html part and its dispatch row predates the inbox's html_body_sha256 stamp (ss#3030)"
    else:
        detail = "msgraph stores this send's html part and no row on this inbox carries html_body_sha256 yet (ss#3030)"
    return sv.BodyVerdict(verdict=sv.VERDICT_UNSTAMPED_REPR, actual_sha256=digest, detail=detail, **common)


__all__ = ["HtmlBody", "grade_html_body", "html_stamp_edge"]
