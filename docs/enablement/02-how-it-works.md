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

| Question                               | Answer                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Is my data mixed with other clients'?  | No. Each client gets its own isolated machine and storage volume, and a seat refuses to start if it is pointed at another client's storage. The services SMD runs around the seats keep each client's records apart.                                                                                                                                                                |
| Can SMD read my client files?          | No person at SMD reads client files. Only AI agents handle them: the firm's own Operator, and SMD's agents in a workspace sealed to that firm. SMD's console shows health, settings and the record of actions, never business content. SMD's operations mailbox gets a notice per request, with the requester's words and the reply.                                                |
| Where do my login tokens live?         | On the firm's own seat, never in SMD's console.                                                                                                                                                                                                                                                                                                                                     |
| What does it connect to?               | The systems the firm already runs: its practice-management system, its email, its website and analytics, and anything else it uses. Each connects through the standard protocol for AI tools (MCP): the vendor's own server first, then a vetted community server, then one we write. If asked what a client runs today: a legal practice-management system and Microsoft 365 mail. |
| Does the AI provider train on my data? | No. Task content goes to a major model provider's commercial service for the task, under terms that bar training on it.                                                                                                                                                                                                                                                             |
| Can it make things up?                 | Every chronology entry is checked against its source page before delivery, and drafts go to an attorney for judgment. The checks are built into the pipeline, not left to the model's discretion.                                                                                                                                                                                   |
| What if it does something wrong?       | It only does what was authorized, everything is on the record, and corrections are remembered so they hold.                                                                                                                                                                                                                                                                         |

## What a prospect would see (short version)

A demo opens on a finished deliverable (a cited chronology, a first-draft demand letter), then shows the email that asked for it, then the record of what the Operator did. The detailed demo plan comes later. Available today: a walkthrough on our own pilot seat. Available by arrangement: setting up a trial seat on the prospect's own systems, so they see it in their own documents, formats and voice.
