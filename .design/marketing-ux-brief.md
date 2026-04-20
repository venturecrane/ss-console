# Marketing — UX Brief (Modern Institutional)

_First-pass brief, authored 2026-04-19 alongside the identity sweep. Scope: the `smd.services` apex (public) surfaces — homepage, booking flow, get-started, scorecard, contact, 404, and the sign-in pages. Every surface inherits the Modern Institutional identity committed in `.design/DESIGN.md`._

## Context

Marketing is the first thing a prospective client sees. They're reading it on a laptop between jobs or on a phone in a truck cab. They're deciding whether to book a call, not whether to read about a framework. Copy + clarity first; chrome second.

SMD Services sells scope-based consulting engagements to businesses doing $750k–$5M in revenue. Positioning: collaborative guide, not diagnostic expert. Marketing must read like the firm it belongs to — calm, direct, substance over polish, evidence over reassurance.

## Scope

| #   | Path                   | Archetype      | Purpose                                                           |
| --- | ---------------------- | -------------- | ----------------------------------------------------------------- |
| 1   | `/`                    | marketing-home | Positioning, what-we-do, who-we-help, case snippets, primary CTA. |
| 2   | `/book`                | booking-wizard | Multi-step booking flow for the paid assessment call.             |
| 3   | `/book/manage/[token]` | booking-manage | Reschedule / cancel a scheduled assessment via secure link.       |
| 4   | `/get-started`         | conversion     | Intake form — name, business, rough context before a call.        |
| 5   | `/scorecard`           | lead-magnet    | Operations scorecard quiz / results flow for top-of-funnel leads. |
| 6   | `/contact`             | static         | Minimal contact page — email + direct booking link.               |
| 7   | `/404`                 | error          | Clear message, link back to home.                                 |
| 8   | `/auth/login`          | gate           | Admin sign-in (magic link).                                       |
| 9   | `/auth/portal-login`   | gate           | Client-portal sign-in (magic link on the portal subdomain).       |
| 10  | `/auth/verify`         | gate           | Magic-link verification landing.                                  |

## Visit modes

- **Cold visitor** — first touch via ad, referral, or search. Needs to answer "what does this firm do and should I talk to them?" in the first 10 seconds above the fold.
- **Returning prospect** — came back after an initial touch. Needs a clear path to book or start.
- **Book-ready prospect** — email/referral told them to book directly. Lands on `/book` from a deep link.
- **Researcher** — assistant, spouse, accountant vetting on behalf of the buyer. Needs proof of concreteness and named humans (Scott).
- **Client** — already engaged; arrived from a stale bookmark or email. Should find their way to `portal.smd.services` without confusion.

## Identity inheritance

All identity rules from `.design/DESIGN.md` apply. Summary of what marketing gets automatically from the token cutover in #455:

- Crimson Pro display, Crimson Pro body, IBM Plex Mono for any data readouts
- Warm near-white `#F9F7F1` background, graphite ink, navy primary
- 0 radii on cards, buttons, pills
- Flat — no elevation, no shadows
- Motion minimal (120ms color transitions)

## Identity chrome conventions (marketing interpretations)

- **Global nav.** `Nav.astro` is the marketing site chrome — firm name left, primary CTA right (Book assessment). Sticky on scroll. Matches portal masthead tone: restrained, not marketing-heavy.
- **Hero.** Oversized Crimson Pro headline, one-line tagline in Crimson Pro, single CTA. No rotator, no gradient text, no animated keyword reveal. The headline does the work.
- **Section labels.** Same as portal: mono-caps eyebrow above each major section (`WHAT WE DO`, `WHO WE HELP`, `HOW IT WORKS`, `PRICING`). Hairline-underlined.
- **Cards.** 0 radius, hairline border, flat. Used sparingly — marketing prefers typographic hierarchy over boxed content.
- **Booking / form flow.** Steps numbered visually (`1`, `2`, `3`), 2px rounded squares filled with the current step's primary or neutral tone. No progress bars.
- **Buttons.** Primary CTA is navy filled. Secondary CTAs are ghost buttons with `--color-border` outline. No gradient, no drop-shadow, no elevation-on-hover.
- **Footer.** Minimal — copyright, privacy, terms, contact email, portal sign-in link. Typographic, hairline-separated, no decoration.

## Anti-patterns (marketing-specific, additional to identity list)

- Hero video autoplay or background video loops.
- Parallax scroll, scroll-driven reveals, "as you scroll" animations.
- Gradient-text headlines, text shadows, text-with-outline treatments.
- Testimonial carousels with autoplay.
- Trust-badge rows ("As seen in…") unless the logos are real and earned.
- Countdown timers or urgency ticker scripts.
- Email-gated content ("enter your email to read the rest"). Marketing is the read; intake comes after the read.
- Stock photography of business owners with laptops.
- Numerical claims without specifics ("10x your operations" — no).
- Live chat widgets in the bottom-right corner.

## Copy tone

Voice is the same as everywhere else — guide persona, collaborative, evidence over reassurance, no em dashes, no AI filler. Marketing copy is a bit looser than admin but still calm and direct.

Principles (from `CLAUDE.md` §Tone & Positioning Standard):

- Objectives over problems. ("We start by understanding where you're trying to go.")
- Collaborative, not diagnostic. ("We work alongside you.")
- No fixed timeframes. ("We start with a conversation" beats "1-hour call".)
- No published dollar amounts.
- "Solution" not "systems" in positioning. ("System" is fine when literal.)
- Always "we" / "our team."

Landing-page samples (already in production, kept as tone reference):

- Hero: "Operational consulting for growing businesses. Figure out what's in the way. Build the right solution together."
- What we do: "Process design. Custom tools. Systems that talk to each other. AI when AI is the right answer — and nowhere it isn't."
- Who we help: "$750k to $5M in revenue. Past startup. Not yet ready for a dedicated operations person."
- CTA: "Book a conversation."

## Success criteria

- Every marketing page renders in Crimson Pro + Crimson Pro + IBM Plex Mono with no Inter / Plus Jakarta fallback (except via system fallback chain).
- Palette reads warm — no cool slate or indigo anywhere.
- All buttons render as 2px rectangles (navy primary, neutral ghost, error red).
- Booking wizard step indicators render as filled 2px squares with numbered labels, not circles.
- No shadows, gradients, glow effects, or motion beyond 120ms color transitions.
- Homepage above-the-fold answers "what does this firm do" without scrolling.
- Desktop and mobile renders both clean at 390px and 1280px.

## Open follow-ups

- **Email templates** (`src/lib/email/templates.ts`, `src/lib/email/follow-up-templates.ts`, `src/lib/booking/alerts.ts`) still reference `Inter,Arial,sans-serif` inline. Email clients strip custom fonts aggressively; a safe swap to `'Crimson Pro',Helvetica Neue,Arial,sans-serif` is low-risk but not yet done. Do in a dedicated email-audit pass.
- **PDF templates** (`src/lib/pdf/sow-template.tsx`, `src/lib/pdf/scorecard-template.tsx`, `src/lib/sow/service.ts`) register fonts via React-PDF; not swept. Changing requires visual regression check because SOW PDFs are client-signed contract documents. Separate PR with before/after renders before merge.
- **Nav mobile menu** currently a single-level dropdown. If more marketing sections ship, reconsider.
- **`/scorecard` results page** has a lead-gen email gate. Kept as-is for now (the gate IS the conversion mechanism) — flagged against the anti-pattern only so it's an explicit exception.
- **Marketing primitive extraction.** Shared components (`Hero`, `Pricing`, `ProblemCards`, `WhoWeHelp`, `HowItWorks`, `FinalCta`, `Footer`, `Nav`) are already componentized in `src/components/`. No new extraction needed.

## Approver

Scott Durgan. Marketing reviewed via dev server at `localhost:4321/` (or staging Vercel build after merge).

---

## Appendix: Token map

Marketing inherits every token from `.design/DESIGN.md` unchanged. No marketing-specific tokens.
