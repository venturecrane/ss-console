# Case-alert routing (per-matter delivery, the law-seat rule)

How a CASE-LEVEL alert - a verification stall, a deadline flag, a draft
awaiting review, any "a person at the firm needs to see this now" raise -
chooses its recipient. Every law-seat skill that alerts a person follows this
one algorithm; skills cite this file instead of restating it. System and
technical monitoring (Machine health, connector outages, cost breakers) is out
of scope here: it runs console-side and stays with SMD on
`escalation.failure_recipients`.

Routing is authored per seat at `escalation.case_alert_routing` in
`customer.yaml` (read live from the seat's materialized config, same as this
skill's other `escalation.*` knobs). Absent block or `mode: central` = today's
behavior: deliver to `escalation.red_flag_recipients`, nothing below applies.

## `mode: matter_staff` - resolution order

For each alert item (items already carry a `matter_id` - the ledger requires
it):

1. **Read the matter's assignment fields**: `get_matter(matter_id)` returns
   `personResponsibleStaffId` (the responsible attorney) and the
   `personAssistingStaffs` list (assisting staff) on the matter. Tenant
   ground truth (staging probe, 2026-07-27): these fields are **absent
   entirely until populated** - treat absent and empty identically as
   UNPOPULATED, never as an error.
2. **Resolve each populated id through `get_staff(staff_id)`**. A staff
   record that is disabled or departed (`enabled: false` or `former: true` -
   the actual staff-record fields; there is no `isDeleted` on staff) is
   treated as UNPOPULATED - stale assignment takes the fallback path, same
   as empty. Staff records carry a top-level `email` field (verified on the
   staging tenant), which is the delivery address candidate the roster check
   in step 4 evaluates.
3. **Recipient set**: the responsible attorney always; the assisting staff
   additionally where the skill's own body says paralegal-class work routes to
   assisting staff. At least one resolved, usable person = deliver to that
   set.
4. **Roster check (hard rule)**: a resolved address may be delivered to ONLY
   if it is covered by an authored roster grant (e.g. the seat's
   `@<firm-domain>` entry). An address Smokeball returns that no authored
   grant covers is treated as UNRESOLVABLE - take the fallback path. The
   authored grant is what authorizes delivery; the API result only selects
   among already-authorized recipients.
5. **Fallback**: no usable recipient after 1–4 → deliver to the authored
   `case_alert_routing.fallback_recipients`, and ALSO flag the matter in
   place (a `create_memo` on the matter naming the alert and the unassigned
   state - memo, not task: `create_task` requires a `staff_id`, and the
   fallback case is exactly when there is none to use).
6. **Fail-closed floor**: fallback unauthored or empty → NO delivery. Flag
   the matter in place (the same memo), and surface the routing gap on the
   internal surface (the standing tracker view / needs-you digest input) so a
   person sees "this matter has no one assigned and an alert is waiting."
   Never invent a recipient; never silently drop the alert.

## Delivery mechanics

- **Tasks follow the same resolution.** A tracked item written into a matter
  carries `staff_id` (and `assignee_ids` where the skill assigns more than
  one person) from the SAME resolution above - work lands assigned to the
  person who owns the matter, the way work reaches them today.
- **Composition**: where a skill's body says "send ONE alert per run", that
  becomes one alert per RECIPIENT per run under matter_staff routing - items
  grouped by resolved recipient, each person seeing only their matters.
- **Ledger identity is routing-independent.** `item_key` stays
  `(matter_id, source_id, label, authored_date)`. Routing is a
  delivery-address decision, never an identity input - a matter whose staff
  field is populated mid-chase must NOT re-fire its backlog as new items, and
  a fallback-delivered item is the SAME item when later routed.
- **ACK codes** are per item, unchanged: whoever receives the alert can ack
  their items without silencing anyone else's.

## Security rules (non-negotiable)

- **Never grow the roster from runtime data.** No code path, no skill run,
  ever appends a resolved address to `scope.inbound_allow_from` or any other
  authored grant. The roster is human-authored only; a roster grown from
  observed data is an exfiltration path (see the recipient classifier's
  contract).
- **Taint discipline holds.** A turn that has touched a tainted surface
  (fenced document read, untrusted inbound content) cannot deliver an
  internal alert - `from_tainted` forces OUTSIDE classification and the send
  is refused. Do the resolution and delivery in a clean turn, matter-metadata
  reads only (`get_matter` / `get_staff` are metadata, not content).
- **Unauthored exposure refuses.** Internal alert email delivers under the
  persona's authored `external_send_internal`. If the seat does not author
  it, delivery is REFUSED at the gate - that is the authored state speaking,
  not a bug in this algorithm.
