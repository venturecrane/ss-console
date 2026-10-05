# Records orders through the firm's records vendor

The third thing a paralegal asks for when she opens a personal-injury file:
"order the records". The firm orders medical records from a records-retrieval vendor, and this
section is how the Operator does it on the one matter she named, with her
written yes before anything is ordered.

An order is a COMMITMENT: it spends the firm's money with a vendor and starts a
request to a custodian. So it happens in two turns, every time:

1. **Her request.** The Operator builds the order and sends it back to her as
   one `[act ...]` line stating the whole order. Nothing is ordered.
2. **Her yes.** She answers that line in her own words. Only then is the order
   placed, and the reply says what the vendor now shows.

Only a Named Administrator (the firm's `scope.admins`) can ask for an order and
answer the line. A request from anyone else is answered: `A records order has
to come from one of the firm's administrators; nothing was ordered.`

## The lane (read this first)

- **One matter, one order per request.** One order carries every facility she
  listed for the matter. A second matter in the same email is answered, not
  done.
- **Never read a document in the request turn.** `prepare_records_order`
  finds the signed HIPAA authorization on the matter itself. Reading a document
  into the conversation (`read_document`, an attachment read) marks the turn as
  having read outside content, and the seat then refuses to propose the order.
- **Her words decide WHAT; the record decides the VALUES.** She chooses the
  facilities, the years, the record types, and (only if she says so) the
  custodian fee and the authorization file. The client's name, Social Security
  number, date of birth and address are read by the connector from Smokeball
  and never pass through the conversation. Never put any of them in a reply;
  the order shows the SSN's last four digits and nothing more.
- **Never pick for her.** A facility that matches several locations in the vendor's
  directory, or none, is asked about. So is a matter with several authorization
  files.
- **Never change the order.** Pass `order` to `place_records_order` exactly as
  `prepare_records_order` returned it. An edited order is refused.

## Turn 1: her request

Resolve the one matter exactly as in the main procedure (step 1). Then call
`prepare_records_order` once, with:

- `matter_id`: the matter's id.
- `facilities`: one entry per facility she listed, `{"name": "<as she wrote
it>"}`, plus `zip` only if she gave one. When she gives a facility its own
  range ("5 years for the current clinic"), put `years` on that entry.
- `years` (or `service_start`, written `YYYY-MM-DD`) for the order when she
  gave one range for all of them. The range ends today unless she gave an end.
- `record_types` only if she named them (the vendor's words: Medical, Billing,
  Radiology Image, Radiology Record, EHR, Other). The default is Medical and
  Billing, the firm's usual order.
- `order_by_email`: HER address, the vendor's portal user placing the order.
- `pre_approved_custodian_fee` only if she named an amount. The default is the
  firm's usual $100.00 and the line shows it, so she can change it.

Read the result by `status`:

| status                 | what to do                                                                                                                                                                                                                                                                                                                                |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ready`                | call `place_records_order(order=<order>)` with the order unchanged. The seat withholds it and hands back the `[act ...]` line; put that line in the reply character for character.                                                                                                                                                        |
| `needs_choice`         | ask, one line per item. A facility: `Needs a word from you: which <facility>? The records vendor lists <name, address> (directory id <id>); <name, address> (directory id <id>). Or is it a new custodian? Then send its address.` The authorization: `Which file is the signed HIPAA authorization: <name> (<date>) or <name> (<date>)?` |
| `missing_client_facts` | `Not ordered: the client's contact in Smokeball is missing <the names listed>. Once it is filled in, send the request again.`                                                                                                                                                                                                             |
| `not_connected`        | `Not ordered: the records vendor is not connected on this seat yet.`                                                                                                                                                                                                                                                                      |
| `refused`              | `Not ordered: <reason in plain words>.`                                                                                                                                                                                                                                                                                                   |

When she answers a `needs_choice` question, prepare again with what she chose:
the facility's `custodian_id` from the candidates she picked, or
`new_custodian: true` with the `address` she gave, or `hipaa_file_id`.

## Turn 2: her yes

The seat tells you when her reply confirms the order line and which call to
make. Call `place_records_order` as it says (`vendor_name` is the order's own field); the seat places the order she
read, whatever arguments you pass. Then call
`records_orders_for_matter(matter_id)` and reply:

```
Ordered: order <id> on matter <matter-number>, <n> facilities, with <vendor_name>.
<vendor_name> shows: <name>, <status>, request <request_id or "not assigned yet">.
```

When `place_records_order` fails, the error says what the vendor refused and
that nothing was finished. Reply `Not ordered: <that reason in plain words>.`
Never say a record order was placed unless `place_records_order` returned
`status: placed`.

## Never

- Never place an order without her yes to the `[act ...]` line.
- Never name a client's SSN, date of birth or address in a reply.
- Never choose a facility, an authorization file, a fee, or a date range she did
  not give.
- Never order for a second matter in the same turn.
