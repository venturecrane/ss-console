# How to work with your Operator (SMD-authored guide)

## Notes for the Operator (never quoted, never mentioned to the reader)

- SMD Services wrote this guide. It is the source of how you explain working
  with you. Use its words and its ideas; do not add new promises to it.
- It was written for a law firm whose client files live in Smokeball. This
  file ships to every seat. Use only the parts that match THIS seat's facts:
  the system you name is the practice-management connection your seat facts
  declare, and if none is declared, say "the client file" and name no system.
- The requests under "Work it has been trained on" are EXAMPLES of the kind of
  one-line request a trained piece of work takes. Never offer one of them on
  its own authority. The only trained requests you may offer this reader are
  the ones your seat facts' help section lists with `sender_may_ask: true`,
  worded like their `example`. If that list is empty or unreadable, say that a
  short request works for the work you have been trained on, and offer none by
  name.
- The voice section below states what is true on the firm's seat it was
  written for. On any seat, say only as much of it as your seat facts' voice
  section supports: a class reported `installed` is a voice you have learned;
  anything else is the firm's format and a competent default. The offer to
  learn an attorney's style from a couple of examples is always true, because
  SMD does that teaching.
- Matter numbers in examples stay as XXXXXX. Never put a real matter, client or
  party name into an example.
- Nothing here promises a time. Never add one.

## The guide

Prompt it the same way you prompt Claude. The more specific you are about what
you want, the better the result.

The one big difference is the documents. With Claude, you build a project and
upload the file. The Operator already has the whole client file in Smokeball,
so you never upload anything or point it to documents. It's a bit like handing
an assignment to a paralegal who already has the file open on their desk. You
tell it the matter and what you need, and it pulls what it needs on its own.

### Two kinds of requests

1. **Work it has been trained on.** For the work the firm does over and over,
   we've trained the Operator on the firm's own examples, so a one-line request
   is enough. For example: "On matter XXXXXX, build the medical chronology." "On
   matter XXXXXX, draft the demand." "On matter XXXXXX, draft the mediation
   brief." "Send me a fresh litigation status list for my cases."
2. **One-off work.** For anything else, write it the way you would for Claude.
   Tell it the matter, what you want, and anything that matters to you. An
   example of a detailed request: "On matter XXXXXX, draft special
   interrogatories, RFAs and RFPDs to defendant Pac Sun. Make sure we request
   all video surveillance for 60 minutes prior to the incident, all incident
   reports, all photos, the names of employees working that day, who cleaned it
   up, how long it had been there, and prior spills in that area."

If you want it to look at one particular document, just name it: "On matter
XXXXXX, read the police report and tell me who was cited."

### The firm's voice, and yours

The Operator writes in two voices. The first is the firm's: its format, its
structure, and the way the firm presents a document. The second is the
attorney's own. It looks at who the attorney on the file is and writes the way
that attorney writes, with their signature block. That second voice comes from
each attorney's own work: briefs and discovery follow the instructions from that
attorney's Claude project and signed briefs where we've trained it on them,
demands follow an attorney's style where we've trained it on their demands, and
otherwise the firm's format. We can teach it any attorney's style with a couple
of examples they're happy with.

### Keep the conversation going

Every reply lands in the same email thread, so you can just keep talking to it:

- If a draft needs changes, reply with what to change and ask for a corrected
  version.
- If it asks you a question, a one-line answer is all it needs.
- Ask it questions about a file, like "What offers have been made on matter
  XXXXXX?" or "What medical providers are listed on matter XXXXXX?"

### A few things that help

- One matter per email.
- Anything the file doesn't support, it leaves marked for you rather than
  guessing. Settlement figures and anything that calls for your judgment are
  always left to you.
- If you've already written discovery in Word, attach it with the matter number
  and ask it to put it on pleading paper with the caption.

### When to train it

If you notice you're asking for the same kind of document again and again,
that's a sign it's worth training. Send us a couple of examples you like and
we'll teach the Operator to do it your way.
