#!/usr/bin/env python3
"""Prove the msgraph unaudited-send reconciler can actually catch one (ss#2499).

WHY A HARNESS AND NOT A UNIT TEST. Every assertion in
``operator/bin/tests/test_reconcile_sends.py`` is made against a fake Graph this
repo also wrote, so all of them together prove the matcher agrees with our model
of a mailbox. None of them can prove the model is the mailbox. The only thing
that settles that is a real message, put around the broker into a real Sent
Items folder and found by a real scheduled run. That is what this script puts
there -- by transmitting one in ``--mode send``, or by creating one in
``--mode plant`` on a seat where transmitting is not available.

WHAT IT DELIBERATELY DOES WRONG. It goes around the workspace broker entirely --
so no audit row is written and no ``X-SMD-Audit-Row`` header is stamped. That is
precisely the shape of the event this control exists for: an item in the
Operator's Sent Items with nothing in the ledger to account for it. In send mode
that item also left the mailbox; in plant mode it never did, and the reconciler
cannot tell the difference, which is what makes plant a usable falsifier at all.
The AgentMail twin of this test is on
record: ``[UNAUDITED-KILLTEST-2258]``, 2026-08-13, reported and then baselined
(``operator/bin/reconcile-sends-baseline.json``).

TWO MODES, BECAUSE THE SANDBOX HAS NO SEND APP. ``--mode send`` is the original
and needs the seat's SEND app credential. ``smd-staging`` has none: that app
registration does not exist (ss#2467, OPEN), and its READ app's token was
OBSERVED on 2026-08-21 carrying app roles ``['Mail.ReadWrite']`` and nothing
else. So the only channel that could exercise the reconciler on a sandbox was
closed, and the falsifier had nowhere to run.

``--mode plant`` opens it. ``Mail.ReadWrite`` is enough to CREATE a message
directly in Sent Items without transmitting anything:

    POST /users/{mailbox}/mailFolders/sentitems/messages

(Graph v1.0 "Create Message" in a mail folder; ``sentitems`` is a documented
well-known folder name; the application permission is ``Mail.ReadWrite``; the
answer is 201 with the created message, ``id`` included. Docs:
https://learn.microsoft.com/en-us/graph/api/user-post-messages and
https://learn.microsoft.com/en-us/graph/api/resources/mailfolder .)

WHAT PLANT PROVES, AND WHAT IT DOES NOT. To the reconciler, an item sitting in
Sent Items with no ``X-SMD-Audit-Row`` header and no ledger row is byte-for-byte
the event this control exists to catch, and it reads Sent Items through exactly
the same query either way. So plant proves THE RECONCILER: that a foreign item
in that folder is found, reported, and not silently absorbed.

It does not prove the transmit path, and it must not be read as proving it. It
does not exercise ``sendMail``, the SEND app, the two-app fence, or the broker's
header stamping -- an item that was never transmitted cannot show that a
transmitted one gets stamped. Nothing leaves the tenant, which is the point:
the sandbox gets a real artifact in a real folder and no recipient anywhere.

WHERE IT MAY RUN, AND WHY THAT IS A HARD FENCE. Only a sandbox seat named in
``SANDBOX_SEATS`` below. A client's mailbox is the client's; deliberately posting
a message into it to see whether our watchdog barks would be an unannounced
artifact in a firm's live correspondence, and the firm is the only party that can
see its own Sent Items UI. The refusal is by SEAT SLUG rather than by a flag,
because a flag is a thing an operator in a hurry passes.

RUNNING IT (a Captain act, not an agent's -- both modes leave a real artifact in
a real mailbox, and plant is no less deliberate for never having transmitted):

    infisical run --env=prod --path=/ss -- \\
        python3 operator/bin/killtest-msgraph-send.py \\
            --seat smd-staging --mode plant --confirm

    # then, after the daily run or a manual dispatch:
    infisical run --env=prod --path=/ss -- \\
        python3 operator/bin/reconcile-sends.py --channel msgraph --days 2

The second command must report the message this one prints, as a FIND. If it does
not, the control does not work and the green runs before it meant nothing. Plant
mode has an advantage here that send mode does not: Graph answers the create with
the whole message, so the id to look for is PRINTED rather than hunted for by
subject.

AFTER IT PASSES (operator/CLAUDE.md, probe-artifact contract): add the printed
message id to ``reconcile-sends-baseline.json`` in a PR with ``reported_in``
naming the issue, so this deliberate send stops being re-reported. An entry there
means REPORTED AND TRACKED, never RESOLVED.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_TOKEN_HOST = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TIMEOUT_S = 30.0

#: The subject marker, identical to the AgentMail kill test of 2026-08-13 so the
#: two read as one control in the baseline file and in any issue that quotes them.
KILLTEST_MARKER = "[UNAUDITED-KILLTEST-2258]"

#: Seats this may transmit from. A SANDBOX ONLY list, enforced below.
#:
#: ashton-price is absent and must stay absent. It is a real firm's live mailbox
#: in the firm's own tenant; SMD has no UI access to it, so the firm would be the
#: only party able to see the artifact, and it would see it as unexplained mail
#: from its own Operator. The falsifier for THIS channel runs on the sandbox and
#: the reconciler code path it exercises is the same one.
SANDBOX_SEATS: dict[str, str] = {
    "smd-staging": "SMD's own M365 sandbox seat; no client correspondence lives here",
}

#: Where the deliberate send is addressed. SMD's own operational address, never a
#: client one: a kill test that reaches a client is not a test.
KILLTEST_RECIPIENT = "team@smd.services"

#: ``send`` transmits through ``sendMail`` on the SEND app. ``plant`` creates the
#: item directly in Sent Items on the READ app and transmits nothing. Default is
#: ``send``, so the original invocation keeps its original meaning.
MODE_SEND = "send"
MODE_PLANT = "plant"
MODES = (MODE_SEND, MODE_PLANT)

#: The well-known Graph folder name. Not an id we discovered and cached: Graph
#: resolves this name per mailbox, so it cannot drift to another seat's folder.
SENT_ITEMS_FOLDER = "sentitems"


class KillTestRefused(RuntimeError):
    """This may not run here. Never retried, never overridden by a flag."""


def _seat_config(slug: str) -> dict:
    import yaml

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "customers",
        slug,
        "customer.yaml",
    )
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    email = ((data.get("connectors") or {}).get("Email")) or {}
    if email.get("adapter") != "msgraph":
        raise KillTestRefused(f"{slug} does not author adapter msgraph")
    auth = email.get("msgraph_auth") or {}
    mailbox = str(auth.get("mailbox") or "")
    tenant = str(auth.get("tenant_id") or "")
    if not (mailbox and tenant):
        raise KillTestRefused(f"{slug} authors no complete msgraph_auth")
    return {
        "mailbox": mailbox,
        "tenant_id": tenant,
        # The READ app's client id as the seat authors it. A public identifier,
        # not a credential, and the same one reconcile-sends.py authenticates
        # with (reconcile-sends.py:320) -- so plant mode cannot end up talking to
        # a different app than the control it is meant to falsify.
        "client_id": str(auth.get("client_id") or ""),
    }


def send_credential(slug: str) -> tuple[str, str]:
    """The SEND app's client id and secret for this seat.

    The SEND app on purpose, and it is the whole point: the broker's read
    credential could not transmit, and a kill test that cannot transmit proves
    nothing. Provisioning stages these under ``MSGRAPH_SEND_*__<SLUG>``
    (provision-customer.sh msgraph-two-app-fence).
    """
    key = slug.upper().replace("-", "_")
    client_id = os.environ.get(f"MSGRAPH_SEND_CLIENT_ID__{key}") or ""
    secret = os.environ.get(f"MSGRAPH_SEND_CLIENT_SECRET__{key}") or ""
    if not (client_id and secret):
        raise KillTestRefused(
            f"MSGRAPH_SEND_CLIENT_ID__{key} / MSGRAPH_SEND_CLIENT_SECRET__{key} "
            "are not in this environment; run under infisical"
        )
    return client_id, secret


def read_credential(slug: str, config: dict) -> tuple[str, str]:
    """The READ app's client id and secret for this seat, for plant mode.

    The READ app on purpose, and for the opposite reason send mode wants the SEND
    app: plant never transmits, so ``Mail.Send`` would buy nothing, and
    ``Mail.ReadWrite`` -- which the staging app OBSERVABLY has and which is the
    documented permission for creating a message in a mail folder -- is exactly
    enough. Nothing here can transmit even if it were asked to.

    Resolution order, most specific first. The suffixed pair is how provisioning
    stages a seat's own credentials; the unsuffixed pair is how the sandbox's are
    staged in Infisical today, which is the only reason the fallback exists; the
    authored ``client_id`` is the last resort for the ID ONLY. There is
    deliberately no fallback for the SECRET beyond the two env names: a shared
    secret picked up by accident would authenticate as somebody else's app
    against somebody else's mailbox, and that is a worse outcome than a refusal.
    """
    key = slug.upper().replace("-", "_")
    client_id = (
        os.environ.get(f"MSGRAPH_CLIENT_ID__{key}")
        or os.environ.get("MSGRAPH_CLIENT_ID")
        or str(config.get("client_id") or "")
    )
    secret = os.environ.get(f"MSGRAPH_CLIENT_SECRET__{key}") or os.environ.get("MSGRAPH_CLIENT_SECRET")
    if not client_id:
        raise KillTestRefused(
            f"no READ client id for {slug}: MSGRAPH_CLIENT_ID__{key}, "
            "MSGRAPH_CLIENT_ID, and customer.yaml msgraph_auth.client_id are all empty"
        )
    if not secret:
        raise KillTestRefused(
            f"MSGRAPH_CLIENT_SECRET__{key} / MSGRAPH_CLIENT_SECRET are not in this environment; run under infisical"
        )
    return client_id, secret


def mint_token(tenant_id: str, client_id: str, secret: str, *, opener=None) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": GRAPH_SCOPE,
        }
    ).encode()
    tenant = urllib.parse.quote(tenant_id, safe="")
    request = urllib.request.Request(  # noqa: S310 - GRAPH_TOKEN_HOST is an https constant; the tenant id is url-quoted, so it cannot leave its path segment
        f"{GRAPH_TOKEN_HOST}/{tenant}/oauth2/v2.0/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=TIMEOUT_S) as response:
            parsed = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Status only: the token endpoint echoes the client secret back in its
        # error bodies.
        raise KillTestRefused(f"token mint rejected with HTTP {exc.code}") from exc
    token = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not token:
        raise KillTestRefused("token response carried no access_token")
    return str(token)


def killtest_subject(now: datetime | None = None, mode: str = MODE_SEND) -> str:
    """The subject, stamped so a human reading the mailbox knows what it is.

    Three markers, all deliberate. ``KILLTEST_MARKER`` is what the reconciler
    report and the baseline entry will show. The creation stamp is the
    probe-artifact contract in operator/CLAUDE.md: firm staff reading a task or
    message list are the other consumer that can mistake a probe for real work.
    The mode is here because the two modes prove different things, and six months
    from now the subject is all the baseline file will have to say which ran.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%MZ")
    return f"{KILLTEST_MARKER} {stamp} mode={mode} deliberate unaudited send, reconciler kill test"


def killtest_message(subject: str) -> dict:
    """The Graph payload. Carries NO ``internetMessageHeaders``, which is the
    whole experiment: an unstamped message is exactly what a send that did not
    come through the broker looks like."""
    return {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": (
                    "Deliberate unaudited send. This message bypassed the workspace "
                    "broker, so no audit row exists for it and it carries no "
                    "X-SMD-Audit-Row header.\n\n"
                    "The next unaudited-send-reconcile run must report it. If it "
                    "does not, the control is not working (ss#2499).\n\n"
                    "Once it has been reported, add it to "
                    "operator/bin/reconcile-sends-baseline.json in a PR."
                ),
            },
            "toRecipients": [{"emailAddress": {"address": KILLTEST_RECIPIENT}}],
        },
        "saveToSentItems": True,
    }


def planted_message(subject: str, mailbox: str, now: datetime | None = None) -> dict:
    """The Graph payload for an item created straight into Sent Items.

    Carries NO ``internetMessageHeaders``, same as the send payload and for the
    same reason: a stamped item would be matched by the header and the run would
    come back clean, which is a kill test that always passes.

    ``toRecipients`` is the seat's own mailbox. Not SMD's operational address and
    certainly not anyone else's -- this item is never transmitted, so a recipient
    outside the tenant would be a fiction printed in a folder that firm staff
    read as a record of what was actually sent.

    ``sentDateTime`` matters more than it looks. The reconciler pages Sent Items
    ``$orderby=sentDateTime desc`` and stops at the ``--days`` boundary
    (reconcile-sends.py:446, :454), so an item with no sent time sorts to the far
    end of the mailbox and the window never reaches it -- the plant would be
    invisible and the clean run would be read as the control working. The Graph
    create-in-mailFolder samples set it explicitly, so it is set here explicitly.

    ``isDraft: false`` is asked for because an item in Sent Items is not a draft.
    The v1.0 message resource does not document it as read-only, but Exchange may
    compute it from the underlying message flags regardless, so the caller PRINTS
    what came back rather than assuming the request won. Nothing downstream
    depends on it either way: the reconciler does not filter on ``isDraft``.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": (
                "Deliberate reconciler kill test. This item was CREATED directly "
                "in Sent Items and was never transmitted -- no message left this "
                "tenant and no recipient received anything. It bypassed the "
                "workspace broker, so no audit row exists for it and it carries "
                "no X-SMD-Audit-Row header.\n\n"
                "The next unaudited-send-reconcile run must report it. If it does "
                "not, the control is not working (ss#2499).\n\n"
                "Once it has been reported, add it to "
                "operator/bin/reconcile-sends-baseline.json in a PR."
            ),
        },
        "toRecipients": [{"emailAddress": {"address": mailbox}}],
        "isDraft": False,
        "sentDateTime": stamp,
    }


def plant(mailbox: str, token: str, payload: dict, *, opener=None) -> dict:
    """Create the item in Sent Items and return the message Graph created.

    A 201 that carries no ``id`` is an ERROR, never a quiet success. Without an
    id there is nothing to print, nothing exact to baseline, and nothing to go
    and delete -- and a plant nobody can name is an unexplained item in a mailbox
    dressed up as a passing test.
    """
    request = urllib.request.Request(  # noqa: S310 - GRAPH_API_BASE is an https constant; the path is this module's literal plus the configured mailbox
        f"{GRAPH_API_BASE}/users/{mailbox}/mailFolders/{SENT_ITEMS_FOLDER}/messages",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=TIMEOUT_S) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise KillTestRefused(f"create in {SENT_ITEMS_FOLDER} failed with HTTP {exc.code}") from exc
    try:
        created = json.loads(body)
    except ValueError as exc:
        raise KillTestRefused("Graph answered the create with a non-JSON body") from exc
    if not isinstance(created, dict) or not created.get("id"):
        raise KillTestRefused(
            "Graph accepted the create but returned no message id; nothing was "
            "planted that this run can name, so it is not reported as planted"
        )
    return created


def transmit(mailbox: str, token: str, payload: dict, *, opener=None) -> None:
    request = urllib.request.Request(  # noqa: S310 - GRAPH_API_BASE is an https constant; the path is this module's literal plus the configured mailbox
        f"{GRAPH_API_BASE}/users/{mailbox}/sendMail",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=TIMEOUT_S) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise KillTestRefused(f"sendMail failed with HTTP {exc.code}") from exc


def guard(slug: str) -> str:
    """Refuse every seat that is not an authored sandbox.

    By slug rather than by flag: a flag is a thing an operator in a hurry passes,
    and the cost of getting this wrong is an unexplained message in a client's
    own correspondence that only the client can see.

    The fence is on the SEAT and applies to every mode. Plant transmits nothing,
    which makes it tempting to treat as harmless; it is not. It leaves a real
    item in a real Sent Items folder, and on a client's seat that folder is the
    firm's own record of what it sent. An entry there that the firm did not send
    is a worse artifact than a message, not a better one.
    """
    if slug not in SANDBOX_SEATS:
        raise KillTestRefused(
            f"{slug!r} is not a sandbox seat. This script puts a real message "
            "into a real mailbox and may only do so on: " + ", ".join(f"{k} ({v})" for k, v in SANDBOX_SEATS.items())
        )
    return SANDBOX_SEATS[slug]


def _next_step() -> None:
    """What the operator does next. Identical in both modes, because the run that
    matters is the reconciler's, not this one's."""
    print()
    print("  python3 operator/bin/reconcile-sends.py --channel msgraph --days 2")
    print()
    print("It must appear as a FIND. Then baseline it in a PR.")


def _run_send(seat: str, config: dict, subject: str) -> int:
    client_id, secret = send_credential(seat)
    token = mint_token(config["tenant_id"], client_id, secret)
    transmit(config["mailbox"], token, killtest_message(subject))
    print(f"sent from {config['mailbox']}")
    print(f"subject: {subject}")
    print()
    print("Graph answers sendMail with 202 and no body, so this script cannot")
    print("print the message id -- find it by subject in Sent Items, or in the")
    print("reconciler report, which is the point of the exercise:")
    _next_step()
    return 0


def _run_plant(seat: str, config: dict, subject: str) -> int:
    mailbox = config["mailbox"]
    client_id, secret = read_credential(seat, config)
    token = mint_token(config["tenant_id"], client_id, secret)
    created = plant(mailbox, token, planted_message(subject, mailbox))
    print(f"planted in {mailbox} Sent Items. Nothing was transmitted.")
    print(f"subject: {subject}")
    print(f"id: {created.get('id')}")
    print(f"internetMessageId: {created.get('internetMessageId') or '(not returned)'}")
    # Echoed rather than asserted. The reconciler's window keys on sentDateTime,
    # so an operator who sees it come back empty knows the plant will sort out of
    # every --days window before the clean run gets misread as a passing control.
    print(f"sentDateTime: {created.get('sentDateTime') or '(not returned)'}")
    print(f"isDraft: {created.get('isDraft')}")
    print()
    print("Both ids are exact, so the baseline entry needs no subject matching.")
    print("Now prove the reconciler finds an item it was never told about:")
    _next_step()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seat", required=True, help="sandbox seat slug to act on")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=MODE_SEND,
        help=(
            "send: transmit via sendMail on the SEND app (the original). "
            "plant: create the item directly in Sent Items on the READ app, "
            "transmitting nothing -- the only mode a seat with no SEND app can run"
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually do it; without it this prints what it would do",
    )
    args = parser.parse_args(argv)

    try:
        why_allowed = guard(args.seat)
        config = _seat_config(args.seat)
        subject = killtest_subject(mode=args.mode)
        if not args.confirm:
            destination = (
                f"{config['mailbox']} Sent Items, transmitting nothing"
                if args.mode == MODE_PLANT
                else f"{config['mailbox']} to {KILLTEST_RECIPIENT}"
            )
            print(f"DRY RUN. {args.seat} is allowed: {why_allowed}")
            print(f"mode {args.mode} would write into {destination}")
            print(f"subject: {subject}")
            print("re-run with --confirm to do it.")
            return 0
        if args.mode == MODE_PLANT:
            return _run_plant(args.seat, config, subject)
        return _run_send(args.seat, config, subject)
    except KillTestRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
