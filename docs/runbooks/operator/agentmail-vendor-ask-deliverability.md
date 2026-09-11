# AgentMail vendor ask: deliverability controls we cannot see

Status: DRAFT. The Captain sends this; agents never send vendor mail on SMD's
behalf. Send from scott@smd.services to AgentMail support.

## Why this ask exists

The 2026-08-24..31 outbound review found two deliverability gaps we cannot
observe from our side of the AgentMail API:

1. One-click unsubscribe headers appeared on operational deadline alerts. An
   Operator's case alert is not bulk mail; a one-click unsubscribe on it is a
   silent kill switch a recipient can trip without anyone at the firm or at SMD
   learning about it.
2. Lists (send/receive/reply x allow/block, org/pod/inbox scope) are the only
   documented suppression surface. If AgentMail also maintains an internal
   bounce or complaint suppression store, a rostered recipient could be
   unreachable with nothing in the Lists API showing it, and our daily check
   (`operator/bin/check-agentmail-lists.py`) would read clean while sends drop.

Probe date for the documented surface: 2026-08-31 (docs.agentmail.to; the
Lists REST paths are `GET /v0/lists/{direction}/{type}` and
`GET /v0/inboxes/{inbox_id}/lists/{direction}/{type}`).

Live-run calibration (2026-08-31, workflow run 33430061160): org-scope list
reads return HTTP 403 under our scoped per-inbox key. That is our own
credential posture (the ss#2258 fence; no org-wide key exists anywhere), so
the daily check now records it as a noted skip and measures the inbox scope
only, which overrides org per the vendor's precedence rules.

Second calibration (2026-08-31, re-dispatch after #2659): under the shared CI
key, scott@'s inbox lists read CLEAN while pilot-smokeball's own inbox 403'd.
That is exactly the shape of inbox-scoped keys reading only the inboxes they
were minted for, so the working hypothesis is now: per-inbox scoped keys DO
read their own inbox's lists, and the pilot 403 is a key-to-inbox mismatch on
our side, not a vendor entitlement gap. The checker prefers a vaulted
per-seat key (AGENTMAIL_API_KEY__<CID>) per inbox; the hypothesis is
confirmed or broken the day AGENTMAIL_API_KEY__PILOT_SMOKEBALL lands in the
workflow secrets. Question 4 below is narrowed accordingly: it now asks which
key classes may read inbox-scope lists, and only becomes a SEND if the
per-seat key test fails (a seat's own key 403ing on its own inbox lists).

## Draft email (Captain to send)

Subject: List-Unsubscribe headers and suppression visibility for transactional inboxes

Hi,

We run operational agents on AgentMail custom-domain inboxes. The mail they
send is transactional case correspondence to known recipients, not bulk mail.
Three questions:

1. Does AgentMail inject List-Unsubscribe or List-Unsubscribe-Post headers on
   outbound mail from custom-domain inboxes? If so, can they be suppressed per
   inbox or per organization for transactional inbox classes? A one-click
   unsubscribe on an operational alert is a silent kill switch for us: the
   recipient can stop case-critical mail without either side knowing.

2. Beyond the Lists API (send/receive/reply x allow/block), does AgentMail
   maintain an internal bounce or complaint suppression store that can block
   delivery to an address? If yes, is it readable per inbox via API? We
   reconcile our send lists daily and need suppression state we can observe.

3. If neither control exists today, is either on the roadmap? Header
   suppression for transactional classes and API-readable suppression state
   are the two we need.

4. Scoped API keys and list reads: our keys are deliberately scoped per
   inbox (no org-wide key exists in our posture), and org-scope list reads
   (GET /v0/lists/...) return 403 under them, which we expect and have
   designed around. We have observed a scoped key reading one inbox's lists
   cleanly (GET /v0/inboxes/{inbox_id}/lists/{direction}/{type}) while 403ing
   on another inbox's, which matches per-inbox scoping. Please confirm: is a
   per-inbox scoped key always entitled to read ITS OWN inbox's lists? Are
   there key classes or settings under which even that read is denied? Our
   daily suppression check depends on that read.

Thank you.
Scott Durgan
SMD Services

## After the reply

- If headers can be suppressed: file an issue to set the flag on every Operator
  inbox and add the setting to the provisioning path, then verify on a live
  send (fetch the raw message, assert the header absent).
- If a suppression store exists and is readable: extend
  `operator/bin/check-agentmail-lists.py` to read it in the same daily pass.
- If neither exists: record the residual in the security overview doc and keep
  this runbook's probe date current when the vendor surface changes.
