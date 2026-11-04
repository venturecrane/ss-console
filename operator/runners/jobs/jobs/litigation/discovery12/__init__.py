"""The 12-section discovery breakdown: a litigation report kind (``report:
"discovery12"``) that reads every litigated case's discovery papers and lays
them out in the twelve sections an attorney asked for (2026-10-29): form
interrogatories, special interrogatories, requests for production, requests
for admission, depositions, nonparty deposition subpoenas, physical and
mental examinations, expert discovery, electronically stored information,
inspection and testing, initial disclosures, and supplemental responses.

Each entry carries the item, its direction (propounded by the firm or served
on the firm's client), the other party, the date served, the response due
date, whether and when a response was served, and any extension, every date
citing the document it was read from. An empty section prints "None in file".

The reading is the lane's own (``fetch``, ``extract``, the text cache, the
three passes: read, verify, audit), on the discovery papers only, and it is
billed as it reads: the job's ``per_job_cap_usd`` and the month's budget stop
it before the call that would cross either. Nothing is written to the lane's
state: the status list's discovery tab is untouched by this report.
"""
