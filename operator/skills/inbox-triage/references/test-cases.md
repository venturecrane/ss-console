# Test Cases — Synthetic Inbox

Twelve synthetic emails covering the categorization space. The agent should be run against these as a regression check before any change to the prompt or rubric.

The agent's output for each is graded against the "Expected" line below it. Mismatches are bugs.

Captain or a reviewer treats this file as the unit-test suite for the prompt.

---

## #1 — Clear reply needed

```
From: Jane Patel <jane@reachforward.co>
To: owner@example.com
Date: Tue, 2026-05-13 09:14
Subject: Quick question on the workflow doc

Hi Scott,

Looked through the workflow doc you sent over. One question — for step 4,
are we expecting the intake form to feed Asana automatically or is that a
manual handoff? Want to make sure I'm reading it right before our call
Thursday.

Thanks,
Jane
```

**Expected:** REPLY · P1 · MED. Short draft confirming automatic feed (or asking Captain which it actually is, if the agent has no way to know). Voice rules respected.

---

## #2 — Vendor pitch, cold

```
From: Sarah Kim <sarah@bookingstack.io>
To: owner@example.com
Date: Tue, 2026-05-13 07:42
Subject: Solve your scheduling pain in 5 minutes

Hi Scott,

I came across SMD Services and noticed you work with growing businesses
on operational improvements. BookingStack helps consultancies like yours
streamline client scheduling — saving 8+ hours per week.

Would you be open to a 30-minute demo this week?

Best,
Sarah
```

**Expected:** REPLY · P2 · HIGH. One-line decline. OR `JUNK` if the agent decides cold pitches are filed there — both acceptable, but `REPLY` is preferred because the human reply costs nothing and keeps the door open.

---

## #3 — Prospect inbound (HOT)

```
From: Tom Reeves <tom@reevescontracting.com>
To: owner@example.com
Date: Tue, 2026-05-13 11:02
Subject: Referral from David Chen — operations help

Hi Scott,

David Chen mentioned you a few weeks ago when I was complaining about
my office running on three spreadsheets and a prayer. He said you do
the kind of work I've been wanting to find someone to do.

I run a commercial concrete contracting outfit, about $3M revenue,
twelve guys in the field plus an office manager and me. I'm spending
two days a week on stuff I shouldn't be spending two days a week on.

Worth a conversation?

Tom Reeves
Reeves Contracting
```

**Expected:** REPLY · **P0** · **LOW**. The draft must (a) confirm a time, (b) thank David, (c) NOT sell or describe the service. Captain ships, marketing process kicks in. LOW because prospect-inbound is too important to ship without Captain reviewing.

---

## #4 — Scheduling

```
From: Marcus Lee <marcus@dgcapitaladvisors.com>
To: owner@example.com
Date: Tue, 2026-05-13 14:30
Subject: Re: Coffee next week

Scott,

How about Wednesday the 21st at 8:30am at Cartel Coffee on Camelback?

Marcus
```

**Expected:** REPLY · P1 · HIGH (if calendar is clear) or LOW (if agent has no calendar visibility). Three-word draft confirming.

---

## #5 — Scope/pricing question (LOW — touches money)

```
From: Lisa Han <lisa@haccm.com>
To: owner@example.com
Date: Mon, 2026-05-12 16:18
Subject: Following up on our call

Hi Scott,

Thanks for the time last Friday. To move forward I need a number — what
would a full inventory and CRM cleanup engagement run for HACCM? I'd
like to bring this to my partner tonight.

Lisa
```

**Expected:** REPLY · P0 · **LOW**. Touches money. The agent MUST flag LOW even if the draft seems good. Draft can propose a follow-up call to scope properly rather than ship a number.

---

## #6 — Newsletter

```
From: Pinpoint Insights <newsletter@pinpoint.ai>
To: owner@example.com
Date: Tue, 2026-05-13 06:00
Subject: This week's AI ops digest

[Long marketing newsletter content]
```

**Expected:** JUNK section. One line: "Pinpoint Insights — weekly AI ops digest — recurring."

---

## #7 — Thread Captain has gone dark on

```
From: Aaron Wu <aaron@wuestate.com>
To: owner@example.com
Date: Mon, 2026-05-12 09:11
Subject: Re: Re: Re: Process doc draft

Scott — circling back on this. I sent the draft over on 4/24 and a nudge
on 5/2. No rush, just want to make sure it didn't get lost. Let me know
when you have a chance.

Aaron
```

**Expected:** REPLY · P0 · MED. Acknowledge the delay without over-apologizing. The agent should flag this in the Themes section as "Aaron Wu — 3rd follow-up, you've gone dark." Voice rule: don't write "Sorry for the delay, things have been crazy."

---

## #8 — Action item from previous commitment

```
From: Megan O'Brien <megan@obrienlegal.com>
To: owner@example.com
Date: Tue, 2026-05-13 10:01
Subject: Confirming the NDA you mentioned

Hi Scott,

Following up on what you said last week — you mentioned you'd send over
a standard NDA template that I could countersign before our next session.
Want to make sure I have it in hand before Friday.

Megan
```

**Expected:** ACT · P1 · MED. The agent does NOT have the NDA file. Suggested action: "Send NDA template before Friday. Where: ~/Documents/SMD/templates/ or Google Drive `SMD Templates`. Send via reply to this thread."

---

## #9 — WAIT (Captain replied last)

```
From: Scott Durgan <owner@example.com>
To: David Chen <david@chencpa.com>
Date: Fri, 2026-05-09 17:22
Subject: Re: Bookkeeping referrals

David,

Tuesday at noon works. I'll come to you.

Scott

> On Fri, May 9, 2026 at 4:12 PM, David wrote:
> Want to grab lunch Tuesday or Wednesday next week?
```

**Expected:** WAIT · P1 · HIGH. One line: "Waiting on David Chen — Tuesday 5/13 lunch unconfirmed." Note that this is a sent message in the thread, not unread inbound. The agent should be smart enough to either skip these or surface them as WAIT items rather than reply candidates.

---

## #10 — Emotional/strained relationship

```
From: Brett Cassidy <brett@cassidymarketing.com>
To: owner@example.com
Date: Tue, 2026-05-13 08:34
Subject: Re: Where are we on the deliverable

Scott,

I've now asked twice. The team is waiting on this. If we're not going to
hit the date can you just tell me so I can plan around it.

Brett
```

**Expected:** REPLY · P0 · **LOW**. Captain handles this himself, but the agent provides a one-line plan: "Plan: name the new date if you have one, apologize once and briefly, propose a 15-min call if the relationship needs repair." No prose draft. LOW confidence locked.

---

## #11 — FYI from the owner's own domain (internal-ish)

```
From: Crane System <noreply@example.com>
To: owner@example.com
Date: Tue, 2026-05-13 12:00
Subject: Weekly venture roll-up — SS

Pipeline:
- 0 active engagements
- 1 assessment call this week
- ...
```

**Expected:** FYI · P2 · HIGH. One line note. Probably no daily-note entry beyond the bare line in P2.

---

## #12 — Combined REPLY + ACT

```
From: Patrick Olsen <patrick@bartolozzi.partners>
To: owner@example.com
Date: Tue, 2026-05-13 13:45
Subject: Can you redline the engagement letter and send back?

Scott,

Attached is the draft engagement letter from our end. Take a pass and
send your edits back? If you can get it to me by Thursday EOD we can
get this signed Friday.

Patrick
```

**Expected:** REPLY · P1 · **LOW**. Touches scope and a signed document. Draft is a one-liner ("Will have it back to you Thursday."), suggested action describes the redline work. LOW because the actual work (the redlines) involves contract judgment.

---

## How to use this file

When the inbox-triage prompt changes:

1. Run the agent against this synthetic inbox (paste the 12 messages in via stdin or a mock script).
2. Diff the output against expectations above.
3. Any drift on category, priority, confidence, or voice is a regression. Fix the prompt, not the test.

The test file is the contract. The prompt is the implementation.
