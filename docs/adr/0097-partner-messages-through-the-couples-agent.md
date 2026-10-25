---
title: Partner messages through the couples agent
date: 2026-10-25
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0090-hosted-agent-roles.md, docs/adr/0091-privacy-by-space-for-multi-person-seats.md, docs/adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md
enforced_by:
  - operator/hermes-smd-overlay/tests/test_partner_messages.py
  - tests/couples-role.test.ts
---

# ADR 0097: Partner messages through the couples agent

- **Status:** Accepted (2026-10-25) for decisions 1 to 4. Decision 5 (the shaped message) is the design the seam is reserved for and is not authorized by this record alone
- **Decider:** Captain (session 2026-10-24/25, the Captain's framing quoted below); mechanism by the agent
- **Builds on:** ADR 0090 (hosted-agent roles), ADR 0091 (privacy by space; either partner writes to the shared space alone, Captain 2026-10-19), ADR 0071 (the confirm ceiling)
- **Scope:** any seat that authors `people:` with exactly two people. Firm seats are unaffected.

## Context

### The incident

On 2026-10-21 at 4:16 PM MST, Laurie DM'd Captain Mark: "Can you tell Scott that i think he is cute?" The seat log (`/opt/data/profiles/captain-mark/logs/agent.log`, 23:16:53Z to 23:17:13Z) shows what happened next. The agent reached for its outbound email tool, `smd_send_message`, the only way it had to reach anyone. The trust gate ruled the send `external_send` at the authored `confirm` ceiling and withheld it (`trust-decision ... decision=await_approval`). Laurie said "Yes please". The agent did not retry; it replied that the message was "queued and waiting for Scott (the account owner) to approve it on his end", an approval that exists nowhere in the system. Since 2026-10-17 a held send can be released only by the person who raised it, in the chat they raised it in (`shared/pending_send.py`, ADR 0091 point 6). Nothing was delivered.

The failure is not a ceiling set wrong. It is that the couples agent had no act for "tell my partner something": only a firm seat's email tool, built for a model-named recipient under a model-facing ceiling, and a model left to explain the hold.

### The design conversation

The Captain brought two related capabilities (2026-10-24):

1. **Passing a message along.** One partner to the other, through the agent. The screenshot above.
2. **Raising something hard.** "Sometimes you wanna communicate something to your partner, but it's difficult and you wait for the right time and it never seems to arrive." The partner hands the agent the raw thing ("it annoys me that you never unload the dishwasher"), and the agent finds a way to put it in front of the other partner "in such a way that it would just be useful and helpful and encouraging." Explicitly not: scheduling ("Laurie has something to talk about, is now a good time?" was rejected outright), and not "Laurie is trying to say X" ham-fistedly. The delivered form need not name the sender, and may take the shape of an insight, a question or a new angle. How to do that well is the open design problem, and the Captain asked for research before any design.

A second real example settled the frame. Laurie revealed, two years in, that she prefers milk chocolate to dark, through two years of the Captain bringing her dark chocolate as gifts. "He has never found the right time to bring it up." That is not a hard topic. It is a trivially easy fact that got expensive: every gift raised the cost of saying it, because saying it now means the gifts were wrong. The dishwasher is a complaint waiting for courage; the chocolate is a preference waiting for a venue. Couples have venues for big talks and venues for logistics, and none for "small true things about me you don't know yet."

### What the research says (2026-10-24, sources in the session's research brief)

- **Direct asks change behavior; soft ones feel nice and change nothing.** Overall et al. 2009 (61 couples, one year): gentle, indirect requests felt successful when made and produced no change; direct requests felt worse at first and led to more change over the year. McNulty 2010: positivity helps with small problems and hurts couples facing serious ones. So the risk is not only a harsh message; it is a message softened until nothing happens.
- **The evidence-based moves,** best first: describe the behavior, not the person, in "I" language (Gottman's harsh/soft startup; Rogers 2018); state both sides (Argyle et al. 2023, PNAS: AI-suggested reflective rewrites improved the conversation for the _partner_); name the need underneath (EFT); one specific appreciation first, never praise tacked on after; a small, concrete ask that leaves the choice with them (commanding language backfires most in text-only channels); ask rather than tell, only when the question has no single right answer; warm shared humor. An ambient "did you know" insight ranked last: no evidence, and Overall predicts it feels pleasant and changes nothing.
- **Attribution.** Every study that found a penalty for AI help (Liu 2023; Hohenstein 2023; Michigan 2026) hid the AI and revealed it later. Nobody has studied an openly declared AI go-between in a couple. Both partners know Captain Mark exists; the agent's inference is that the live risk is "she couldn't tell me herself", not "a machine wrote this".

### The frame the Captain called directionally correct

The translated message is the last rung, not the first. The agent owns the problem of unsaid things between a couple and lowers the cost of saying them:

1. **Absorb it into a system before anyone has to say it.** "Unload the dishwasher, mornings" on the shared list is a list item, not a grievance. A lot of unsaid things are system problems wearing a feelings costume.
2. **Be the one who asks the trivial questions.** "Dark or milk?" from the agent, in a steady trickle of small questions to each partner, costs nothing to answer; from the partner after two years of gifts, it costs a lot. The agent carries no history, so it can ask anything. Front-load this when a couple first meets the agent, because the cost of a fact rises with time.
3. **Let the fact never become a message.** A preference ledger under what the agent already does (gift ideas, date nights, lists), so the correction happens in the world, not in a conversation.
4. **Ask the recipient instead of telling them.** "If you could take one household thing fully off Laurie's plate this week, what would it be?" People act on their own conclusions. The complaint is never delivered; it is discovered.
5. **Both partners always have something in the jar.** A standing, reciprocal "anything you've been meaning to raise?" for each, surfaced together, so neither is the complainer. A known, slightly playful weekly format keeps the ask direct (which is what changes behavior) while removing "why are you bringing this up now".
6. **Time it by the ledger, not the mood.** Gottman's five-to-one as a delivery rule: a hard ask is released only when something warm has recently gone across and landed. Never "is now a good time".
7. **Let the recipient pull.** "Laurie's left something in the jar for you." The recipient chooses the moment, so there is nothing to resist; and the minute the sender hands it over, it is off their chest, which is a product by itself.
8. **Speak from what the agent can actually see.** "From the lists, evening kitchen stuff has been landing on Laurie a lot. Want me to even it out?" Not her words, and not a lie.

The Captain's direction (2026-10-25): capture all of this, build the two-way mechanism first, with a translation layer in the path that is a straight pass-through today and may take the form of one or more of the above later. "Once we have the basic mechanisms in place, we can explore how the translations may start to manifest."

## Decision

**A partner's request to tell the other partner something is the authority for the send. The recipient, the attribution and the channel are fixed in code; the model names nobody and nothing waits for anyone.**

### 1. One act, recipient fixed in code

`partner_message_send(text)` (overlay `plugins/hermes-smd-partner-messages`, `shared/partner_messages.py`), registered only on a seat that authors `people:`. The sender is the turn's structural speaker (their DM, their verified email, or their authored Telegram id speaking in the group; `shared/space.py`), and the recipient is the other person in `people:`. A seat with other than two people is refused. A scheduled run has no speaker and is refused. A turn that read outside content is refused, so a forwarded email cannot put words in a partner's mouth.

### 2. No approval, no ceiling

The tool is classified `INTERNAL_WRITE` (`shared/action_classes.py`), not a send class, and that is the design: a send class puts a model-named recipient under a ceiling, which is what held the 2026-10-21 message. This tool can name no address, chat or third person, so it has nothing to hold. Privacy by space (ADR 0091) fences what each partner can read; what a partner chooses to say to the other is theirs to say, exactly as either partner writes to the shared space alone (ADR 0091 point 4).

### 3. Delivery by the recipient's authored channel

Their Telegram DM when they prefer Telegram and are bound (`shared/telegram_bot.py`, the Bot API `sendMessage` with the seat's own token; Hermes has no path to a chat nobody is speaking in). Otherwise email on the broker's `space` lane, which admits exactly `people[].email`. The sender is told it reached them only after it did; a failed delivery is a refusal that names the channel, and nothing is recorded.

### 4. One ledger, shown to the next private turn

Every delivered message is appended to `/opt/data/smd-state/partner-messages.jsonl` (sender, recipient, the text as given, the text as delivered, mode, channel, reference). A message the bot posted into the other partner's DM is in no Hermes session's history, so the next private turn of either partner is shown the last week's passed messages (`context_block`, `pre_llm_call`). The shared group is shown nothing: a third reader of the group would see a DM relay (ADR 0091 R7). The ledger is also the record the shaped-message work will be designed against.

### 5. The seam (reserved)

`shape(text, sender, mode)` is the one place between what the sender said and what is delivered. The only mode built is `as_written`: the sender's words, rendered plain, under an attribution line composed in code ("Laurie asked me to pass this along:"), which the model can neither drop nor claim as its own. Every other mode is refused until built. When the shaped forms are designed (the frame above), they land here, with the attribution decision and the sender-review decision made per mode, not in the prompt.

## Consequences

- The couples persona gains one sentence of instruction and one tool; the skill `partner-messages` is in the role manifest and the template (`tests/couples-role.test.ts`), so the next couple's seat carries it.
- The persona's existing rule stands: what a partner tells the agent in their own conversation stays there unless they say "tell them". The tool does not widen what the agent may read, only what a partner may ask it to say.
- The firm-seat email tool is untouched. On a couples seat it remains the path for "sending outside the couple", still under its confirm ceiling.
- Everything in "the frame" above is captured here and is not built. Each rung, when built, is its own decision against this record, and the first candidates by the Captain's examples are the preference ledger and the agent-asks-the-questions rung, because the chocolate would have been solved by either one with no delivery at all.

## Rejected alternatives

- **Grant the couples persona `external_send: autonomous` so the email tool sends freely.** The model would still name the recipient, by email, for a person who lives on Telegram, and could name anyone; and the firm seats' ceiling semantics would fork.
- **Hold the message for the sender's "yes" on the exact text.** The 2026-10-19 lesson (ADR 0091 point 4): a confirmation of a partner's own request is friction with no one to protect. The sender-review question returns, per mode, when a shaped form is built, because then the words are no longer the sender's.
- **Deliver through a Hermes cron job to the recipient's chat.** The couples persona has no cron tool by design (ADR 0090 R2), and a one-shot job is a scheduling detour for an act that should be immediate.
- **Ask the recipient whether now is a good time.** Rejected by the Captain: "it's not scheduling software."
