---
name: post-intake-daily-list
description: >-
  One daily email of the scanned post that needs a person. On a schedule, reads what
  combined post intake recorded for every bundle since the last list and sends the one staff
  member the firm named a numbered list of only the letters the Operator could not place, the
  filings whose keying did not land or may be a duplicate, any scan nothing was filed from, and
  court papers to calendar, so one reply can answer it. A day with nothing to list sends
  nothing. Rendered and sent by the pre-run, not composed; the woken turn sends nothing.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Post, Intake, Digest, Internal, Cron, QuietByDesign, FailClosed]
  smd:
    vertical: law-firm
    skill_type: scheduled report (no-model pre-run renders and dispatches one internal email)
    weight: light # reads the seat's own post journal; no model composition, no Smokeball read
    action_class: read # the list goes to one authored internal recipient through the out-of-turn dispatch
    content_ceiling: connective # page ranges, senders as printed and matter numbers the intake already recorded; never what a letter says
    cron: true
    connectors: [] # reads only the journal the Smokeball connector's post intake writes on the seat
---

# Post Intake Daily List

The firm's office manager emails the day's scanned post to the Operator, often
in several scans. `combined-post-intake` files each letter it can place and
keys what it can, and records what it did with each bundle. This skill is the
one email she gets about it: once a day, only the lines that need her, numbered
so one reply answers all of them.

A firm that does not author this skill keeps the intake's reply per scan,
unchanged. Authoring it (the skill enabled with `settings.recipient` and a
`cron` row on the same persona) is what turns those replies off, because the
intake reads the same three facts before it decides whether to reply.

## What the pre-run does (all of it)

1. Reads every bundle record in the seat's post journal written since the last
   list (`state.json`'s cursor; with none, since the start of the seat's day),
   so a scan that arrives after the list, or on a weekend, is on the next one.
2. Keeps only what needs a person, decided by the journal's closed vocabulary
   and never by wording:
   - a letter not placed: no matter matched, one candidate to confirm, several
     candidates, the search could not run, or the filing was refused;
   - a letter filed whose keying did not land or may be wrong: a medical bill
     not on the Medicals tab, a figure that did not save as written, a possible
     duplicate offer or expense, an offer not on Negotiation Details, a vendor's
     bill not staged;
   - a scan nothing was filed from (unreadable, letters that could not be told
     apart, a second PDF not processed);
   - a court paper, filed, to calendar.
     A letter that filed and keyed is not listed. A figure read from a scan is
     not listed for that alone: the row or expense says so to whoever relies on
     it.
3. Nothing to list: sends nothing and records why. Otherwise renders the email
   (`references/output-format.md`), writes the numbered list to the journal
   (`lists/<reference>.json`, which `get_post_list` reads when she replies),
   hands every matter number the body prints to the send gate as read, writes
   the dispatch envelope, moves the cursor, and wakes the turn so the seat sends
   the envelope out of turn through the full gate.

The recipient is `settings.recipient` on this skill. Unset means no list is
sent and the intake keeps replying per scan. Once it is authored, a run whose
list cannot be sent (an unreadable journal, an envelope that cannot be
written) sends nothing and records why on its heartbeat row; that is SMD's to
fix, and the next run lists everything since the last list that did go.

## What you do when woken

Nothing. The list was rendered and dispatched before your first tool call;
your Script Output carries only `dispatch_expected`, a status and counts.
Compose nothing, send nothing, read nothing, and end the turn with `[SILENT]`.
If a dispatch note says delivery failed, do not write a list of your own: the
run is recorded.

**Woken without `dispatch_expected`:** do nothing and end the turn with
`[SILENT]`.

## Her reply

A reply to the list is a new rostered request, and `combined-post-intake`
answers it (its step 6): the subject carries the list's reference, and
`get_post_list` gives each numbered line's email, attachment and pages. The
matter number comes only from her reply.

## Rules that do not bend

- Internal only. One recipient, the one the firm named.
- Never lists what a letter says. Senders as printed, page ranges, matter
  numbers the intake's own resolves returned, and the reason in plain words.
- A failed run sends nothing, not a failure note and not a partial list.
- No sender, page range or matter number in the Script Output, a heartbeat
  row, or a log line.
