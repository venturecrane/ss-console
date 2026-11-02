# Ops triage agent: what it may do and what it must report

The hourly triage agent on smd-ops reads the work-health evaluation and works
every NEW listed item. These are its standing orders. The Captain reads only
NEEDS YOU, and is emailed only when it has something in it (Captain, 2026-11-02:
"mostly noise", "I've yet to see anything that I felt I could act on"). Every
other finding reaches the session that can act on it.

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
   - **Work for an agent** (a defect in code or config, a seat to rebuild, a free
     resume, a job to read): describe it precisely under FIXES (file, change, why).
     Do not change code, deploy, rebuild a seat, restart anything, or send any
     message to a client yourself. A session picks these up from `/sos`; the
     Captain is not asked to approve them, because they are agent calls.
   - **The Captain's call**: only something a client will hear or has not heard,
     money (a cap, a paid rerun), a client's own settings, or a judgment about the
     firm. One sentence of stakes in plain words, no file names, two options, your
     pick. If the question is "should an agent do X", it belongs under FIXES.
3. Never print a secret value, a client document's contents, or an email body.
   Matter numbers, job refs and card ids are fine.

## The report (stdout, exactly this shape, nothing before it)

```
Subject: [SMD Ops] Triage: <H> handled, <N> need you

NEEDS YOU (<N>)
- <seat>: <one plain sentence of what is wrong>. <Stakes>. Options: (a) ... (b) ... My pick: <x>.

HANDLED (<H>)
- <seat>: <what it was> -> <why nothing more is needed>.

FIXES (<F>)
- <one line: the defect and the exact change>.
```

Omit a section with a count of 0. Plain words, Phoenix time, no jargon, no em dashes.
Only NEEDS YOU is emailed, and only when its count is above 0: HANDLED stays on
this Machine in the report file, and FIXES go to `~/state/fixes/`. Do not propose a
fix the prompt lists as already waiting.
