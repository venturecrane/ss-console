# 5. Visual Briefs

Inputs for the people and tools that make the kit's visuals. Each brief is self-contained: paste it into the tool named, or hand it to a designer as written.

## Which resource for which visual

| Resource                               | Use it for                                             | Why                                                                                                                      | Output stays editable as                        |
| -------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| **Napkin AI** (text to visual)         | Concept visuals: V1, V3, V6, V7, V8                    | Pastes in plain text and proposes several visual treatments; you pick and edit. Strongest at "explain an idea" graphics. | SVG, PNG, PDF; edit inside Napkin               |
| **Eraser** (text to technical diagram) | Architecture: V4, V5. Also V2 (sequence)               | Built for architecture and sequence diagrams from a short text description; technical buyers recognize the style.        | Editable diagram source inside Eraser; SVG, PNG |
| **Gamma** (text to presentation)       | V0 and the whole kit as a deck, once the visuals exist | Turns the four kit pages into a slide deck the rep can rework.                                                           | PowerPoint, Google Slides, PDF                  |
| **A freelance presentation designer**  | The final, client-facing versions of V1, V2 and V7     | These three carry the pitch. A person makes them consistent, on-brand and clean enough to hand a managing partner.       | Native PowerPoint or Figma files                |

Recommended sequence: make rough versions in the AI tools from these briefs, put them in front of the rep for reaction, then send only the ones that survive to a designer. Plans and prices on each vendor's site should be checked before signing up; they change often.

## Rules every visual follows

- **Audience:** a small-law-firm managing partner and office manager, plus a sales rep who has sold enterprise technology for years. Plain words, no engineering vocabulary on V1 to V3 and V6 to V8.
- **Never show:** dollar amounts, durations or timeframes, client or person names, vendor logos, robot or brain imagery, "AI-powered" badges, anything that disparages staff or software.
- **Words:** "the Operator" (never "AI employee" as a headline), "solution" not "systems", "paralegal and coordinator work" (never "legal work").
- **Look:** plainspoken, like good commercial signage. Flat, high-contrast, generous space, no gradients or stock illustration.
- **Palette (SMD brand):** background cream `#f5f0e3`, text near-black `#1a1512`, secondary text `#4a423c`, accent burnt orange `#c5501e` (use for one emphasis per visual), green `#4a6b3e` for "done" or "result".

---

## V0: The kit at a glance

- **Goal:** the rep sees the four parts and the question each answers.
- **Content:** four steps left to right. 1 What it is ("What is this, in one breath?"). 2 How it works ("What happens when a client uses it?"). 3 How an engagement goes ("How do we find what a client actually needs?"). 4 How to pitch it ("What do I say, and what do I say back?").
- **Form:** a simple four-step path. Best made as the opening slide in Gamma.

## V1: The gap and the worker that fills it

- **Goal:** in five seconds, the viewer understands the category: a worker for the space between people and software. This is the single most important visual in the kit.
- **The one message:** "Capable people. Capable software. Work falls through the gap between them. The Operator fills it, and we run it for you."
- **Content:**
  - Left: "Your people" (attorneys, paralegals, office staff).
  - Right: "Your software" (practice management, email, calendar, documents).
  - Between them, a visible gap, labelled with the work that falls into it: reading the email, updating the file, chasing the document, logging it, booking the follow-up.
  - The Operator shown filling the gap, with a small label: "Built around how you run. Run by us."
- **Must not:** suggest the Operator replaces the people or the software. Both stay, both look capable.
- **Form:** Napkin first. Then designer.

## V2: A request, start to finish

- **Goal:** show that using it feels like emailing a colleague, and that the work comes back where the firm already works.
- **The one message:** "Ask by email. The work comes back in your file."
- **Steps, in order:**
  1. An attorney emails the Operator: "Prepare a medical chronology for this matter."
  2. The Operator checks: is this person authorized, and is this kind of work allowed?
  3. It reads the matter and its medical records in the firm's own system.
  4. It does the work and checks every entry against the page it came from.
  5. It files the result on the matter.
  6. It writes down what it did in a record it cannot change.
  7. It replies: "Done. Filed on the matter."
  8. The attorney reviews it. The judgment stays with the firm.
- **Lanes (if swimlane):** the attorney, the Operator, the firm's system, the record.
- **Form:** Eraser (sequence diagram) for the rough cut, designer for the client-facing version.

## V3: The firm's dials

- **Goal:** show that the firm decides what the Operator may do, kind of work by kind of work.
- **The one message:** "You set the line. Unset means no."
- **Content:** one kind of work (example: "send a status letter") flowing into a dial with four positions:
  - **Surfaces it:** reads and tells someone, changes nothing.
  - **Prepares it for you:** drafts it for a person to check and send.
  - **Handles it:** does it on its own.
  - **Never set:** refused.
- Footer line: "It can never raise its own level."
- **Form:** Napkin.

## V4: The system in its world (architecture, level 1)

- **Goal:** a technical buyer sees what connects to what, and where the boundaries are.
- **Standard:** C4 model, level 1 (system context).
- **Elements:**
  - People: firm staff (send requests by email); firm administrator (uses the client portal).
  - The firm's Operator seat: one isolated machine for this firm only.
  - The firm's system of record (for a law firm, its practice-management system). The firm's own email.
  - The client portal: settings, the record of actions, controls.
  - SMD Services: builds, monitors and supports the seat.
- **Connections:**
  - Staff to Operator: email requests.
  - Operator to practice-management system: reads matters, files work.
  - Operator to email: sends and drafts from its own mailbox.
  - Portal to Operator: reads the record of actions, never the client files.
  - SMD's console to Operator: system health and the record of actions only. No person at SMD reads client files.
- **Emphasis:** a boundary drawn around the seat labelled "One firm. Isolated. Nothing shared with other clients."
- **Form:** Eraser.

## V5: Inside the seat (architecture, level 2)

- **Goal:** show where the firm's controls and records live inside the seat.
- **Standard:** C4 model, level 2 (containers).
- **Elements, top to bottom:** Channels (email, chat, voice notes) → Worker (runs current AI models SMD chooses for this seat) ↔ Firm memory (its rules, voice, formats and corrections; belongs to the firm) → Limits the firm authored (enforced in code, not in the prompt) → Connectors (practice management, email, calendar, any system the firm runs). From Limits, a line to the Audit record (the worker cannot change or delete it).
- **Emphasis:** the Limits box sits between the Worker and everything it can touch. Nothing reaches the connectors without passing it.
- **Form:** Eraser.

## V6: The engagement arc

- **Goal:** the rep sees how a real engagement unfolded, from a wish list to a working relationship.
- **The one message:** "We show something concrete; their reaction tells us the objective."
- **Stages, in order:**
  1. First contact: the firm sends a wish list.
  2. We send something concrete back: one workflow mapped.
  3. Objectives surface in their corrections.
  4. Control comes first: each routine starts by surfacing work or preparing it for a person.
  5. Connected read-only: our observations prompt the firm to name its real priority.
  6. First solution on a real file, revised on their own example until approved.
  7. Expansion, one ask at a time (some asks parked, one later revived).
  8. The firm sequences the roadmap itself: now, next, parked.
  9. Today: attorneys email the Operator directly.
- **Form:** Napkin (timeline or staircase).

## V7: The message, before to after

- **Goal:** the rep internalizes the value story in the shape he already knows (Command of the Message).
- **Five columns, left to right:**
  - **Before:** staff hold the gap together by hand; records pile up unread; the coordinator seat keeps turning over.
  - **Negative consequences:** cases move slower; attorneys do paralegal work; what the last hire knew walks out the door.
  - **Required capabilities:** works inside our systems; does only what we allow; learns how we do things; someone runs it for us.
  - **After:** records read into cited chronologies on request; first drafts ready for attorney judgment; files kept current.
  - **Proof:** 18 chronologies, 29,000+ pages read, every entry cited to its page; first drafts of a demand letter, a case evaluation memo and a mediation brief.
- **Emphasis:** the shift from Before to After.
- **Form:** Napkin first, then designer.

## V8: The conversation in eight beats

- **Goal:** a one-page cue card the rep can glance at before a meeting.
- **Beats:** 1 Name their reality; claim nothing about their firm. 2 What it is, in employee terms. 3 The heart: built around how you work, you teach it, it gets better the longer it works with you. 4 You set the line. 5 Confidentiality: your own walled-off environment. 6 Why it's not the cheap AI that burned people. 7 We run it. 8 Close: scope, and price it like a hire.
- **Emphasis:** beat 3, the line to land.
- **Form:** Napkin (numbered path or card).

## Later, not in v0

Short narrated walkthrough videos of parts 1 and 2 suit a visual learner well. They come after the rep has reacted to the stills, so the narration follows the visuals he actually found useful.
