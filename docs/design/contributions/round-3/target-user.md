# Target User Contribution - Design Brief Round 3 (Final)

**Author:** Marcus (HVAC owner, Chandler AZ)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Status:** Final after 3 rounds

---

## Changes from Round 2

One thing I said more clearly this round that I'd only implied before: the portal needs to work for someone who shows up infrequently. Round 2 I said "I check this when an email tells me to." Round 3 I'm getting specific about what that means for the home screen, which is where the infrequent-visitor problem actually shows up.

The Brand Strategist locked in some design choices in Round 2 and I reacted to most of them positively but I'm pushing back on the no-photography rule as it applies to the marketing site. That's new.

The Interaction Designer added the stale-visit header and a pre-signing summary screen. Both of those I reacted to. The stale-visit header I want to extend. The pre-signing summary I'm raising a real concern about that they need to address before this ships.

The warning color question — the Brand Strategist proposed `#7a5800`, a deep golden-brown — I have an honest reaction to that I haven't shared yet.

The eight emails. I went through them one by one this round and said which I actually need.

---

## The Brand Strategist's Final Design Choices

**No rounded corners — confirmed.** I said this in Rounds 1 and 2 and I'll say it again for the last time: flat edges feel like rules are being followed. Rounded corners feel like the software is trying to be friendly. I don't need the software to be my friend. I need it to be correct.

**Plainspoken canonical — confirmed.** The "plain sentence that says what it means and stops" approach is right. The invoice spec, the tab labels, the status messages — all of that sounds right to me. Don't congratulate me. Don't soften things. Just say what's happening.

**Single warning token — I have a reaction, see below.**

**No photography in the portal — confirmed.** The portal is not a lifestyle product. I'm not looking at photos of consultants while I'm trying to sign something. Keep it clean.

**No photography on the marketing site — I'm pushing back on this.**

Here's the thing. At some point before I hired this firm, I went to the website. And the thing that a website with no faces says to me is: I don't know who I'm dealing with. Every HVAC vendor I trust — every parts supplier, every manufacturer rep — I know what they look like. Even if I've only met them once. There's a face that goes with the name. When I go to a website for a consulting firm and there are no photos of the people who will actually be in my business, I think: either they're hiding something, or there's only one person and they don't want me to know that, or they use stock photography and they're embarrassed about it.

I understand why you'd want to keep photography out of the product. That makes sense. But the marketing site is a different surface. I need to see who I'm going to be dealing with before I call you. Not a stock photo. A real photo. Someone who looks like they've met a business owner before. If the rule is "no photography anywhere," then the marketing site is going to feel anonymous in a way that's going to cost you first calls.

So: no photography in the portal, yes. No photography in the admin, yes. But someone needs to reconsider the marketing site rule before the site goes live. That's my pushback.

---

## The Stale-Visit Header

The Interaction Designer specified a "Last activity: 4 days ago — [last event description]" header that shows up when I return to the portal after a long gap.

**Yes. This is right.** This is exactly the problem I described in Round 2. I come back after four days and I have to figure out where I am. The stale-visit header solves that. "Last activity: 4 days ago — Deposit payment received." Three seconds and I know where I am.

**But I want something added: if there's a milestone happening this week, I want to see that too.**

Not a progress bar. Not a percentage. Just a sentence. Something like: "Kickoff review coming up Thursday." Or: "Milestone review scheduled for this week." If there's nothing coming up, don't say anything. But if I'm about to have a meeting and I forgot, the stale-visit header is the one place that could catch me before I walk in blind.

My Apple Calendar comparison: when I open the app on a Monday morning, I can see immediately what's on this week. I don't want to navigate anywhere. I just want the answer to "do I have anything I need to be ready for." The stale-visit header is the right place to surface that for one-line.

**I don't want to be surprised by a meeting tomorrow because I forgot to check the portal.** If there's anything scheduled and the system knows about it, tell me on that header.

---

## The Pre-Signing Summary Screen

The Interaction Designer specified a pre-signing summary screen before the SignWell embed loads. Scope reminder, total price, payment structure. Then the iframe.

**I'm glad this is in there. But I have a real concern and I need someone to answer it.**

What if the summary doesn't match the document?

Here's what I mean. The portal shows me: "$6,000 total — $3,000 at signing, $3,000 at completion." I read that. I think, okay, that's what we agreed. I click through to the signing iframe. The SignWell document says $6,200. Maybe there was a line item added after the portal summary was generated. Maybe someone updated the document but didn't update the portal record.

**I'm going to notice. And when I notice, I'm not going to sign. I'm going to close the tab and call someone.**

This isn't a hypothetical. I've had this exact experience with a roofing contractor. The estimate said one number. The contract said a higher number. They explained it away as "materials pricing update" but I'd already lost trust. I didn't sign that day. I called around and found a different contractor.

The pre-signing summary has to come from the same data source as the document. Not from a different field, not from a cached value, not from whatever the portal record happened to say at invitation time. If the SignWell document is generated from certain fields in the database, the summary has to display those same fields. If there's any chance the summary can get out of sync with the actual document, that's a system design problem that needs to be fixed before this feature ships.

I'm marking this as a serious concern. Not a preference. A concern. If Marcus shows up to sign and the summary doesn't match the document, the engagement might not survive it.

---

## The Technical Work I Don't Care About (and What I Actually Care About)

The Design Technologist talked a lot about CI gates, ESLint rules, ARIA labels, focus rings, token architecture, lint checks.

I don't know what any of that is. That's fine — it's their job, not mine.

**What I actually care about is whether the portal works when I load it on truck Wi-Fi.**

Not great Wi-Fi. Not the office Wi-Fi I'm using to test it. Truck Wi-Fi. Sitting in a parking lot in Peoria between service calls. Patchy LTE. The kind of connection where some apps load fine and others spin for thirty seconds and then give you a blank screen.

I've watched apps fail on that connection. The thing that tells me a company takes their software seriously is not what it looks like — it's whether it loads. If the portal takes more than four seconds to show me anything on a slow connection, I'm going to close it and find what I need some other way. I might text someone at the firm. I might wait until I get back to my office. I'm not going to sit in a parking lot watching a spinner.

Whatever caching, prefetching, progressive loading — whatever the technical term is for "it loads fast on bad connections" — that is where I want the technical effort to go. The ESLint rules and CI gates are presumably already doing their job. The slow-network experience is what fails end users and I've never once seen a designer spec talk about it from the user's perspective rather than the engineer's perspective.

If the portal is 0KB client JavaScript on portal routes, as the Design Technologist specified, great. That should mean it loads fast. But someone should actually test it on a throttled connection before they ship.

---

## The Warning Color

The Brand Strategist revised the warning color from their Round 1 amber to `#7a5800` — described as a "deep golden brown" or "deep ochre."

My honest reaction: **I don't know if I want warnings to be loud or quiet and I don't think you do either.**

Here's the problem. The burnt orange — the main button color — is already somewhat serious-feeling to me. Not alarming, but it commands attention. It's the right color for "do this thing." Now the warning is going to be a different warm brown. Deep golden-brown.

I had to try to picture that on a cream background. My instinct is that deep golden-brown on cream is going to read as... subtle? It's in the same warm family as everything else on the screen. It's not going to jump out. If I'm tired and I open the portal and there's an overdue invoice and the only signal is some warm brownish text in a callout I wasn't looking for, I might miss it.

But the alternative — something bright and alarming — would feel accusatory. I said this in Round 2. "DEPOSIT OVERDUE" in bright red on the first screen I see when I log in is going to make me feel like I'm being yelled at. Even if it's technically correct. Especially at 10pm when I'm tired.

Where does warning sit? In my gut, when a parts vendor shows me an item as "low stock, order soon," it's usually in yellow. Not the yellow of caution tape — a softer yellow, more like mustard. It communicates "pay attention to this" without communicating "disaster." That feels closer to right than either bright red or deep golden-brown.

**My vote is: warning should be visible enough that I notice it unprompted, but calm enough that I don't feel attacked.** I can't tell from a hex code whether `#7a5800` gets that balance right. I'd want to see it on a real screen.

I'm not overriding the Brand Strategist's call — they know more about this than I do. But I want them to know that "calm" warning has a real risk: I might scroll past it at 10pm without registering it. Someone on the design team should put the warning color on a phone screen in a dim room and ask whether it reads as "pay attention" or "fine to ignore."

---

## Phone-First Reality Check

Going through all three rounds, I kept checking whether the proposals actually assumed a phone user. Here's my honest assessment.

**The above-fold spec from the Interaction Designer — that got it right.** The pixel budget, the order of elements (business name, engagement title, scope summary, price, sign button), the explicit constraint that the CTA has to be visible without scrolling on a 375px viewport. That's phone-first thinking. That's not someone designing on a big monitor and then saying "it'll be fine on mobile." That's someone who actually thought through what a tired business owner sees when they open an email on their phone between service calls.

**The signing flow on mobile — mostly right.** The collapsible scope accordion, the full-width iframe, the tab bar staying visible. All of that is correct. The one thing I'd flag: the accordion is "collapsed by default" because the assumption is I already read the scope on the quote detail page. That assumption is often right. But if I come back to sign a week after I first read the proposal — and I might, because signing happens when I'm at the kitchen table after dinner, not while I'm scrolling on my phone — I may not remember the details. The accordion should be easy to open. The label "Review scope" is clear enough. Just make sure it doesn't take two taps to find it.

**The invoice detail page — I'm less sure.** The Interaction Designer specified that the invoice renders in a "constrained column, max-width 720px" on desktop to give it a "paper document feel." That's right for desktop. But on my phone, a constrained invoice with full invoice structure is going to require scrolling. That's fine — invoices are documents, I expect to scroll to read a document. But the "Pay Now — $3,500" button needs to be reachable without excessive scrolling. If the invoice is long and the pay button is at the very bottom, I'm going to scroll past it, wonder where it is, scroll back down. Put the pay button near the top too, or make it sticky.

**The admin surface — I don't use it, so I don't have an opinion about whether it's phone-first or desktop-first.** That's Scott's surface, not mine.

The one thing across all three rounds that felt slightly desktop-first-masquerading-as-responsive was the two-column signing layout. On desktop: SignWell iframe on the left, scope summary sidebar on the right. That's a good desktop layout. But the mobile experience of collapsing the scope summary into a closed accordion above the iframe — I want to make sure that accordion doesn't add so much visual noise that the iframe feels buried. The whole point of the signing screen on mobile is that the signing action is the primary thing. The scope reminder is secondary. Make sure the collapsed accordion doesn't eat up so much vertical space that the iframe doesn't have room to breathe.

---

## The Eight Emails

Eight is a lot. Let me be honest about which ones I actually need versus which ones I'd mute after two weeks.

**Email 1 — Portal invitation (Quote sent):** Yes. This is the one that has to work perfectly. It gets my company name right, it has the dollar amount, one button. If this one is wrong, nothing else matters.

**Email 2 — Proposal signed, deposit invoice enclosed:** Yes. I just signed something. I need a confirmation that tells me what happens next. And putting the deposit invoice summary in the same email is the right call — I don't want to have to go find it. One thing I'd note: keep it short. "Your proposal is signed. Here's your deposit invoice: $3,500, due by [date]. Pay at [link]." That's the email. Don't add paragraphs.

**Email 3 — Firm acknowledgment (Countersigned):** This is the one I'm least sure about. I just signed. I got Email 2. Now I'm getting a third email saying the firm received my signed document? If Email 2 already confirms the signing and gives me the invoice, what is Email 3 adding? If the answer is "it's the one that tells me what happens next," then keep it. But only if it actually has the "here's what happens next" content from the authored field. If that field is empty and the email is just "we received your signed document" with nothing else — I don't need that. That's an email I'd mute after the first time.

**Email 4 — Invoice issued:** Yes. Every time an invoice is created, I want to know. I'm not logging into the portal to check for invoices. I'll pay when the email tells me to.

**Email 5 — Payment confirmed:** Yes. But make it one line. "We received your payment of $3,500 for invoice INV-2026-001." That's the whole email. Maybe a link to the portal if I want the receipt. Don't write me a paragraph about it.

**Email 6 — Parking lot item needs decision:** Yes, but carefully. The framing in the Interaction Designer's spec is right: "we found this and you should know about it, here's what we think, what do you want to do." That framing I trust. What I don't want is a parking lot email that sounds like an upsell. If every parking lot item email sounds like "there's more work you could buy," I'm going to start skimming them. **The daily digest approach the Design Technologist mentioned is right: if there are multiple parking lot items at once, don't send me five emails. Send me one.** "Three things came up during your engagement that we want to flag. Review them in the portal."

**Email 7 — Engagement complete summary:** Yes. I want this one. It's the record of what we did together. The spec requires it to list completed milestones with their plain-language names, and I want exactly that. No generic "we streamlined your operations." What specifically did we build? Name it. That email goes in my Dropbox folder along with the invoice.

**Email 8 — Magic link re-auth:** Yes. This is functional infrastructure, not a communication. It has to exist and it has to be clear and that's it.

**My overall take:** Keep all eight. But Email 3 is the one that could easily become noise. Make sure it only goes out when there's actual "here's what happens next" content. If the `next_step_text` field is empty, don't send Email 3.

---

## The Focus Ring Question

The Design Technologist specified a focus ring — the visual indicator that shows which element has keyboard focus — as a 2px burnt orange outline. That's for people who use keyboards to navigate instead of touch or mouse.

**Does this matter to me directly?** No. I tap on a touchscreen. I'm never using keyboard navigation on my phone.

**But.** My sister-in-law has had vision problems since she was a kid. Not fully blind, but bad enough that she uses accessibility features on her phone. She navigates things differently from how I do. If she needed to use this portal — let's say she's helping me with something administrative — the focus ring would matter to her.

The honest answer is: I don't know enough about what accessibility features she actually uses to know whether a visible focus ring specifically would help her. But I do know that when I've watched her use software that doesn't accommodate her, she gets frustrated and quits faster than I do. **The focus ring is the kind of thing I'd never notice if it's there and doing its job. I'd only notice if I needed it and it wasn't there.**

Keep it. It costs nothing for the people who don't need it. It matters for the people who do.

---

## Make-or-Break List — Final

Rounds 1 and 2 had the same three moments: the first authenticated screen, the signing moment, and the deposit invoice. Round 2 added "coming back after four days." Here's my final version with one addition.

**1. The email that earns the click.** If the portal invitation email looks wrong — generic subject, wrong sender, no dollar amount — I don't click it. The portal never opens. Everything downstream depends on this email being right.

**2. The first authenticated screen.** Business name, total, sign button, no scrolling. This is still the highest-stakes moment in the system. I need to know immediately that I'm in the right place and I know what to do.

**3. The signing moment.** Cream and ink have to be visible around the signing frame. If the visual register changes when I click "Review and sign," I hesitate. Hesitation at the signing moment is how a deal falls apart at the last minute.

**4. The deposit invoice as a real document.** Not a receipt. Not a screenshot. A PDF with an invoice number, a firm name, an amount in a format I can file. This is the moment that tells me whether I hired a firm or hired an app.

**5. Coming back after four days.** The home screen has to orient me in under ten seconds. What do I need to do? Is anything coming up this week? If the answer is "nothing to do right now, here's where things stand" — fine, tell me that in a sentence. Don't make me click around to figure out I don't have anything to do.

**6. The parking lot at handoff.** This one I'm adding from Round 2. If the parking lot is handled right — specific items, human explanations for each disposition, no homework, no hint-list for future upsells — I leave the engagement feeling like the firm was paying attention and being straight with me. If the parking lot is a wall of tags and status codes with no explanations, I feel like I got a form letter instead of a debrief. **This is a make-or-break moment for whether I refer this firm to anyone.**

---

## Trust Signals

What tells me a firm is professional has almost nothing to do with their website's visual design.

Here's what actually makes me trust an HVAC parts vendor. Someone I know recommended them. They answered the phone when I called. The person who answered knew what I was talking about without me explaining the part number three times. They showed up when they said they would. They didn't try to sell me an upgraded version of what I asked for when the original was fine.

Apply that to the portal:

**Someone I know recommended them.** I can't do anything about this. If I came to this firm through a referral, I already trust them more than I would if I found them through a Google ad. The portal can't manufacture that trust. It can only preserve or destroy it.

**They answered the phone.** The digital equivalent is: the portal loaded. It didn't spin for thirty seconds. When I clicked "Review and sign," it worked. When I requested a new magic link, I got it in under a minute. Reliability is trust. An unreliable portal is a firm that doesn't pick up.

**The person knew what they were talking about.** The digital equivalent is: the portal contents are specific to me. My company name. My scope in plain language. Real invoice numbers, not placeholder text. When I log in and everything is specific to my engagement, I feel like I'm dealing with people who are on top of it. When I log in and something looks generic — a fallback phrase, a missing field showing "TBD," a date that doesn't match what we discussed — I wonder if they're paying attention.

**They showed up on time.** The digital equivalent is: emails arrive when they're supposed to. If I sign and the deposit invoice email arrives 45 minutes later, that's not "on time." The technical work to make webhooks and email delivery fast and reliable is the equivalent of showing up on time. I'll notice if it's slow even if I can't articulate why.

**They didn't try to sell me on stuff I didn't need.** The digital equivalent is: the parking lot doesn't feel like a sales tool. If every parking lot item is framed as "here's another thing we could build," I'm going to feel like the engagement was bait and the real money is in the follow-ons. The disposition framing in the Interaction Designer's spec — "Added to project / Deferred to follow-on / Not addressed" — is neutral enough. But the explanations for each have to feel like genuine judgment, not an invitation to buy more.

That's what makes me trust this firm. The visual design is the packaging. Packaging matters for the first impression. But it's the operational stuff — speed, specificity, reliability, no upselling — that I'll actually remember six months later when my buddy in the trades asks if I know a good operations consultant.

---

## Open Design Decisions

Things I haven't decided and someone needs to ask me about.

**The marketing site photography rule.** I pushed back on this above. Someone needs to ask me: what would make you trust a consulting firm's website before you ever talked to them? And then show me two versions — one with photos of the actual team, one without. My guess is I pick the one with real faces. But this is my gut talking. Confirm it.

**The stale-visit "upcoming this week" preview.** I said I want a preview of any milestone or scheduled touchpoint coming up this week if one exists. I don't know what data the system has access to. If milestones are entered by the admin and have scheduled dates, the system should know. But does it? Does someone need to author that date before it can show up? If the milestone has no date attached, what does the header say? Ask me what I'd want to see when the system has that data versus when it doesn't.

**The warning color in real conditions.** I flagged this above. Someone needs to put `#7a5800` on a real phone screen in a dim room and ask me if I'd notice an overdue invoice flagged in that color. I genuinely don't know. The answer matters.

**How I pay the invoice.** The spec has a "Pay Now" button that goes to a Stripe hosted URL. That's fine for me — I use credit cards for business expenses. But what if I want to pay by ACH? The Interaction Designer noted an "ACH note per BR-036." I don't know what that note says. If ACH is available, where does it say so? Does the "Pay Now" button go to a Stripe page where I can pick ACH, or is ACH a separate flow? I might actually pay by ACH for a $6,000 invoice to avoid the credit card fee. Ask me.

**Email 3 conditional send.** I said: only send Email 3 if there's authored "what happens next" content. But someone needs to decide whether to implement that conditional or just always send it. If you always send it with no content, it's a dead-end email that creates noise. If you conditionally suppress it, is the logic to suppress it simple enough to implement cleanly? Ask the engineers.

**Whether I'd actually pay the invoice from inside the portal or just forward it to my bookkeeper Pat.** Pat handles QuickBooks on Tuesdays and Thursdays. If a deposit invoice arrives on a Monday, I might just forward it to Pat and let her handle the payment. That means the "Pay Now" button might not be for me — it might be for Pat. Does Pat get her own portal access? Does the email forward correctly? Does Pat clicking the link cause any confusion about who's authenticated? I haven't decided how I'd actually handle this. Ask me.
