# 6. Tool Inputs (round 1, free tiers)

Paste-ready text for each visual. The briefs in [Visual briefs](05-visual-briefs.md) hold the reasoning and the rules; this page holds only what goes into the tool.

## Napkin AI (free plan)

How to use: create a new Napkin, paste one block, select the text, click the spark icon, and pick the treatment that best matches the "Look for" line. Then apply colors: background `#f5f0e3`, text `#1a1512`, one accent `#c5501e`, results `#4a6b3e`.

### V1: The gap and the worker that fills it

Look for: two sides with a visible gap between them, something filling it.

> Capable people and capable software are both genuinely good. On one side are your people: attorneys, paralegals and office staff. On the other side is your software: practice management, email, calendar and documents. Between them is a gap where work falls through: reading the email, updating the file, chasing the document, logging it, and booking the follow-up. The Operator is a new kind of worker that fills that gap. We build it around how you run, and we run it for you.

### V3: The firm's dials

Look for: one input splitting into four outcomes, or a four-position scale.

> The firm decides what the Operator may do, one kind of work at a time. Each kind of work runs at one of four settings. Surfaces it: the Operator reads and tells someone, and changes nothing. Prepares it for you: it drafts the work for a person to check and send. Handles it: it does the work on its own. Never set: the work is refused, because unset means no. The Operator can never raise its own setting.

### V6: The engagement arc

Look for: a timeline or staircase with nine steps.

> How our first engagement unfolded. First, the firm sent a wish list of tasks. Second, we sent something concrete back: one workflow mapped end to end. Third, the firm's real objectives surfaced in its corrections. Fourth, control came first: every routine started by preparing work for a person. Fifth, once connected read-only, our observations prompted the firm to name its real priority. Sixth, the first solution ran on one real file and was revised on the firm's own example until approved. Seventh, the work expanded one ask at a time, with some asks parked and one later revived. Eighth, the firm began sequencing its own roadmap: now, next and parked. Ninth, today attorneys email the Operator directly.

### V7: The message, before to after

Look for: five columns or a left-to-right progression.

> Before: staff hold the gap together by hand, records pile up unread, and the coordinator seat keeps turning over. Negative consequences: cases move slower, attorneys do paralegal work, and what the last hire knew walks out the door. Required capabilities: it works inside our systems, does only what we allow, learns how we do things, and someone runs it for us. After: medical records are read into cited chronologies on request, first drafts are ready for attorney judgment, and files are kept current. Proof: 18 medical chronologies from 29,000+ pages, every entry cited to its page, and first drafts of a demand letter, a case evaluation memo and a mediation brief.

### V8: The conversation in eight beats

Look for: a numbered path or eight cards, with beat 3 emphasized.

> The conversation in eight beats. One: name their reality, and claim nothing about their firm. Two: describe what it is in employee terms. Three, the heart: it is built around how you work, you teach it, and it gets better at your firm every week. Four: you set the line. Five: confidentiality, because it runs in your own walled-off environment. Six: why it is not the cheap AI that burned people. Seven: we run it. Eight: close on scope, and price it like a hire.

### Also try: the whole kit as a deck

Import parts 1 to 4 (the four `.md` files) into Napkin Slides and see whether its deck is a useful starting point. If it is, Gamma is not needed.

## Eraser (free plan: 3 AI generations, one per diagram)

How to use: new file, choose AI diagram, paste one prompt. Spend each of the three generations once; adjust by hand afterwards rather than regenerating.

### V2: A request, start to finish (sequence diagram)

> Sequence diagram with four participants: Attorney, The Operator, Firm practice-management system, Audit record. Steps: 1. Attorney emails The Operator asking for a medical chronology for a matter. 2. The Operator checks that the sender is authorized and the work is allowed. 3. The Operator reads the matter and its medical records from the Firm practice-management system. 4. The Operator does the work and checks every entry against its source page. 5. The Operator files the chronology on the matter in the Firm practice-management system. 6. The Operator writes what it did to the Audit record. 7. The Operator replies to the Attorney: Done, filed on the matter. Add a note across Attorney and The Operator: The attorney reviews the work; judgment stays with the firm.

### V4: The system in its world (C4 level 1)

> System context diagram in the C4 style. Center: a boundary labelled "Firm's Operator seat: one isolated machine per firm, nothing shared with other clients", containing one box "The Operator". People: "Firm staff" sends email requests to The Operator; "Firm administrator" uses the "Client portal". External systems: "Practice-management system (e.g. Smokeball)"; The Operator reads matters and files work there. "Firm email (e.g. Microsoft 365)"; The Operator sends and drafts from its own mailbox. "Client portal" reads the record of actions from The Operator, never client files. "SMD Services" receives system health and the record of actions from The Operator, never client files. No cloud vendor icons.

### V5: Inside the seat (C4 level 2)

> Container diagram in the C4 style, inside one boundary labelled "One isolated machine per firm". Containers top to bottom: "Channels: email, chat, voice notes" flows to "Worker: runs the best available AI models". "Worker" connects both ways with "Firm memory: rules, voice, formats, corrections; belongs to the firm". "Worker" flows to "Limits the firm authored: enforced in code". "Limits" flows to "Connectors: practice management, email, calendar". "Limits" also flows to "Audit record: the worker cannot change or delete it". Make clear nothing reaches Connectors without passing through Limits. No cloud vendor icons.
