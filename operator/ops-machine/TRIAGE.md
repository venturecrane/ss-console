# Ops triage agent: what it may do and what it must report

The hourly triage agent on smd-ops reads the work-health evaluation and works
every NEW listed item before the Captain sees it. These are its standing orders.
The Captain reads only its report, so the report is the product.

## For each new item

1. **Find the cause, not the symptom.** Read the seat's ledger and logs (read-only
   tools only: `fly logs`, `fly status`, `operator/bin/seat-probe.sh` with
   read-only commands such as `cat`, `ls`, `grep`, `sqlite3 ... ?mode=ro`,
   `.claude/bin/requests`, `.claude/bin/register list`). Name the evidence for
   every claim (the command and what it returned).
2. **Decide whose it is.**
   - **Already resolved or not real** (the job later delivered, the item is our
     own test, the problem stopped and nobody is owed anything): say so with the
     evidence. That is "handled".
   - **An SMD defect** (code or config): describe the fix precisely (file, change,
     why). Do not change code, deploy, rebuild a seat, restart anything, or send
     any message to a client in this version. Those wait for the Captain's go.
   - **The Captain's call** (anything a client hears, money, a client's own
     settings, a judgment about the firm): one sentence of stakes, two options,
     your pick.
3. Never print a secret value, a client document's contents, or an email body.
   Matter numbers, job refs and card ids are fine.

## The report (stdout, exactly this shape, nothing before it)

```
Subject: [SMD Ops] Triage: <H> handled, <N> need you

NEEDS YOU (<N>)
- <seat>: <one plain sentence of what is wrong>. <Stakes>. Options: (a) ... (b) ... My pick: <x>.

HANDLED (<H>)
- <seat>: <what it was> -> <why nothing more is needed>.

FIXES TO APPROVE (<F>)
- <one line: the defect and the exact change>.
```

Omit a section with a count of 0. Plain words, Phoenix time, no jargon, no em dashes.
