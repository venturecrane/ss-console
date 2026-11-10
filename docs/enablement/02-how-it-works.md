# 2. How the Operator Works

Three pictures, from the buyer's view down to the technical buyer's view. Use the first in any room; the third only when someone asks "where does my data live?"

## Picture A: a request, start to finish

How the work actually reaches the firm. The client never logs in to anything new: they email the Operator like a colleague, and the work comes back where they already work.

> **Visual V2: A request, start to finish.** Brief in [Visual briefs](05-visual-briefs.md).

## Picture B: the firm's dials

What the firm controls. Every kind of work runs at one of three levels the firm chooses, and some work never runs on its own at all.

> **Visual V3: The firm's dials.** Brief in [Visual briefs](05-visual-briefs.md).

Talking points:

- **Unset means refused.** Nothing is on by default. If nobody authorized it, the Operator won't do it.
- **It cannot raise its own limits.** Moving a level up is the firm's decision, within what the agreement allows.
- **It can be stopped.** The seat can be paused, and it stops itself if it hits a cost or safety limit.

## Picture C: the architecture, for a technical buyer

The industry-standard way to draw this is the C4 model: level 1 shows the system in its world, level 2 shows what's inside.

### Level 1: the system in its world

> **Visual V4: The system in its world (architecture, level 1).** Brief in [Visual briefs](05-visual-briefs.md).

### Level 2: inside the seat

> **Visual V5: Inside the seat (architecture, level 2).** Brief in [Visual briefs](05-visual-briefs.md).

### What a technical buyer will ask, and the answer

| Question                               | Answer                                                                                                                                                                                            |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Is my data mixed with other clients'?  | No. Each client gets its own isolated machine. There is no shared runtime, so crossing clients is impossible by design, not just denied.                                                          |
| Can SMD read my client files?          | No. We see system health and the record of actions, not the content. Any exception is with the firm's say-so and is logged.                                                                       |
| Where do my login tokens live?         | On the firm's own seat, never in SMD's console.                                                                                                                                                   |
| What does it connect to?               | Today: Smokeball (practice management), Microsoft 365 mail, the firm's website, analytics. Clio is specified. New systems connect through a standard protocol (MCP), vendor-built first.          |
| Does the AI provider train on my data? | Ask the Captain for the current written terms before answering; this is in the procurement packet, not the pitch.                                                                                 |
| Can it make things up?                 | Every chronology entry is checked against its source page before delivery, and drafts go to an attorney for judgment. The checks are built into the pipeline, not left to the model's discretion. |
| What if it does something wrong?       | It only does what was authorized, everything is on the record, and corrections are remembered so they hold.                                                                                       |

## What a prospect would see (short version)

A demo opens on a finished deliverable (a cited chronology, a first-draft demand letter), then shows the email that asked for it, then the record of what the Operator did. The detailed demo plan comes later. Available today: a walkthrough on our own pilot seat. Available by arrangement: setting up a trial seat on the prospect's own systems, so they see it in their own documents, formats and voice.
