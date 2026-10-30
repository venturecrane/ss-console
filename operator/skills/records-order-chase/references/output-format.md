# Records order chase: the email

Rendered by `report.py` (`render`, `subject`, `skeleton`). This page is the
shape, not a template the turn fills. `<vendor>` is the name the seat stages for
the records vendor (`RECORDS_VENDOR_NAME`).

Subject: `Records orders: <n> request(s) not yet in` (or `Records orders:
nothing outstanding`), with `, <m> could not be checked` when any could not.

```
Hello,

We checked with <vendor> on the 3 medical-records orders placed through the Operator. 1 request has not come in. Each has a follow-up below, ready for a person to send to <vendor>. Nothing has been sent to <vendor> or to any provider.

1. Matter <matter-number>, <client name>
   Facility: Example Imaging
   Request number: 123457
   Ordered: 2026-10-06 (28 days ago)
   Status at <vendor>: Not Started
   Follow-up to send to <vendor>:
   Hello,

   Could you tell us where request 123457 stands? It is for records from Example Imaging for our client <client name> (our matter <matter-number>), ordered on 2026-10-06. Your system shows it as Not Started. If anything is needed from us to move it forward, please let us know.

   Thank you,

Could not be checked this week:
- Matter <matter-number>, <client name>, order placed 2026-10-20: the vendor did not answer (HTTP 500), so it was not checked this week.
- An order placed 2026-10-21: the order's matter is not on record, so it was not checked this week.

This list covers only orders placed through the Operator, not orders placed on <vendor>'s own site, and it does not judge whether records that came in are complete.
```

A request with no request number yet says "Request number: not assigned yet",
and its follow-up asks about the request for that facility instead. A status
the vendor left empty reads "no status given". A matter with two orders lists
both dates and counts days from the first. No em dash anywhere.

When every request is in, the paragraph after "Hello," says so and nothing is
listed. When no order has been placed through the Operator, nothing is sent.

Skeleton (sent only if the full body is refused): the two counts and that the
list could not be included, with no matter number, name or facility.
