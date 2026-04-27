# Target User Contribution - Design Brief Round 2

**Author:** Marcus (HVAC owner, Chandler AZ)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Status:** Revised after cross-role review

---

## Changes from Round 1

A few things shifted after reading what the other three roles wrote.

The biggest one: I said in Round 1 that the invoice needs to look professional and the signing experience can't feel like I left the portal. But I didn't say it forcefully enough. Reading the Interaction Designer's actual spec made me realize how close the system already is to getting those two things right — and how badly it would hurt if they slip even slightly. So I'm being more direct this time about what "professional" actually means to me, and what "scam" looks like.

The second thing: I completely missed the parking lot. I mentioned it in Round 1 but I didn't really explain why it makes me nervous. After reading the Interaction Designer's spec on how it's supposed to work — the dispositions, the admin review before I see anything — I have more to say about it. The question I keep coming back to is whether the parking lot feels like collaboration or like homework. That answer matters more than anything visual about it.

The third thing: I don't log into this portal every day. I didn't say that clearly in Round 1. The other roles are designing for a user who's in here often. I'm not. And the design needs to account for that.

The inspiration board the Brand Strategist put together — Stripe, Linear, HEY, Stack Overflow for Teams, Letterform Archive — I read all of those references and I had a reaction. It's not that they're wrong. It's that I don't use any of those things. I needed to say that.

---

## Who I Am

My name's Marcus. I run a 14-person HVAC company out of Chandler — been doing it for twelve years. Started with one truck and me doing every call myself. Now I've got six techs, two install crews, a dispatcher position that's currently held together with duct tape and a 23-year-old who's trying her hardest, and a bookkeeper named Pat who handles QuickBooks on Tuesdays and Thursdays. I am not a software guy. I am not anti-technology. But I do not have time to learn new software just so some consultant can feel organized.

The reason I agreed to the assessment call — and I want to be honest about this — is that my scheduler quit in January and we've been bleeding since then. Jobs are getting dropped. Follow-ups aren't happening. I had a service call last month where the tech showed up and the customer had called to cancel two days earlier. Nobody caught it. I'm losing money and I know it, and I don't know how to fix the people problem, so maybe the answer is a process problem. That's what I'm hoping. I paid $250 to have that conversation, and I'm not sure yet if that was smart.

---

## My Environment

I check email on my phone. I check it between service calls, in parking lots, sometimes at red lights when I shouldn't. I read things on my phone but I sign things on my laptop — anything that requires me to actually read and commit, I'm doing that at the kitchen table after 7pm. My wife is usually watching something on the TV in the background. One of my kids will come in at least once. I'm tired. I don't want to puzzle anything out.

My phone is an iPhone 15. Cold mornings in November, I'm sometimes still wearing a glove when I check messages. Not the winter gloves — just the thin work gloves I wear on installs in the morning before it gets warm. Small targets on a screen with a glove on are a problem.

The apps I actually use: QuickBooks, my bank's app, Apple Calendar, Square. I've seen references in what these designers wrote to things like Linear and Stripe Dashboard. I've never opened Linear. That's a software developer tool. Stripe is for businesses with online payment flows — mine runs on invoicing and checks, with some Venmo from smaller customers. The inspiration references those designers chose are all for people who live in software all day. I don't. My reference points are QuickBooks, Apple Calendar, my bank's app, and Square. Those are the things that feel right to me and I trust. The portal should feel like those things, not like something designed for a startup's founding team.

---

## First Impressions

Cream background, dark ink text, burnt orange button, no rounded corners. Heavy weight on the headings. No gradients, no shadows, no purple.

**This is right.** The Brand Strategist wrote about a "no-shadows, no-rounded-corners, no-gradient-purple" commitment. I don't know what they called it — Plainspoken something — but I know what it looks like and I trust it. The cream paper and ink feel like a letterhead. Something you'd receive from a law firm or a commercial account. That's the right register. I would file that invoice. I would not file a Slack-purple receipt.

One reaction I did not have in Round 1: if I had to compare this to something, the closest thing in my life is my bank's app on the iPad. Not the website — the iPad app. Big numbers, clean layout, nothing extra, the balance is the first thing I see. The portal feels like it's aiming for something in that register and I think it can get there.

---

## Emotional Reactions

### The portal invitation email arrives

I said this in Round 1 but I want to be more specific. The Interaction Designer has an entire email touchpoint inventory. That's the right instinct. **The email matters more than the portal on day one.** If the email looks like spam — wrong sender name, vague subject line, no context from our conversation — I don't click it. I might even report it as phishing. The email has to pass the "did Marcus actually hire these people" test in two seconds. Subject line with my company name. Sender address that matches what I remember. One button.

The Interaction Designer's subject line suggestion — "{Business name} — your proposal is signed. Invoice enclosed." — that's exactly right. It's specific, it names my company, it tells me what's in it. I would click that.

### Magic-link login

The flow has to be invisible. If it works, I don't notice it. If it doesn't work — expired link, link already used — I feel stupid even though it's not my fault. The spec handles this with a friendly recovery form and I'm glad it's in there. Just make sure the error message is plain. "That link has expired. Enter your email to get a new one." That I understand. "Token validation error EC-003" would make me close the tab.

### Landing on the portal for the first time

**Business name. Price. Sign button. In that order. Without scrolling.** That's the whole thing. Everything else is secondary.

One thing I want to add: the portal needs to make sense if I haven't looked at it in four days. I'm not logging in every day. I'm logging in when an email comes in saying "you have something to do," or maybe Sunday night when I'm planning the week. If I log in on a Sunday after four days and I have to figure out where I am and what I was doing, something is wrong. The home screen has to immediately answer "what do I need to do next." Not "here is everything about your engagement." Just: what do I do next.

### The SignWell embed

The Interaction Designer specified that the signing surface keeps the portal's header and breadcrumbs visible when I click "Review and sign." **This is the right call and I want to be very clear about why.**

If I click that button and suddenly I'm on a page that looks completely different — different colors, different typeface, different layout — my gut reaction is that I've been redirected somewhere I didn't intend to go. That is the exact visual experience of a phishing scam. I know that sounds extreme but it's true. I've gotten fake DocuSign emails before. The way you spot them is that the signing page doesn't match the email that sent you there. If your portal sends me to a signing surface that doesn't look like the rest of the portal, I'm going to hesitate. And hesitation when you're asking someone to commit $6,000 is a problem.

The portal's cream and ink and burnt orange need to stay visible around the signing frame. That's the framing that tells me I know where I am.

### Seeing the "description" tab icon

I read that the Interaction Designer specified tabs with Material Symbols icons — home, work, description, receipt_long, folder_open. I had to stop on "description." What is the "description" tab? Is that the scope? Is that some kind of document? I had to keep reading to figure out that it's probably the proposal detail. That's too much guessing. If the tab label says "Proposals" and the icon next to it looks like a document, I understand immediately. If the icon is "description" — which is a word that means nothing in a business context — and there's no label, I'm going to tap it not knowing what I'll find.

**Tabs need words. Real words.** "Home," "Proposals," "Invoices," "Progress." The icons can be there too for scanning, but the word has to be there. Don't make me read an icon and guess.

### The deposit invoice

The Interaction Designer specified the invoice should have a firm name, address, real invoice number, amount, due date, and downloadable PDF. **This is exactly correct and I want to confirm it out loud.**

I keep invoices. I have a Dropbox folder — "Vendors and Contractors 2026" — and every business expense that matters goes in there. Paper copies, PDFs, whatever. If I pay $3,500 to a consulting firm, there is going to be a document in that folder. If the portal gives me a PDF that looks like a real invoice — firm name, invoice number, amount, due date, itemized — it goes in the folder and I feel like I hired a professional firm. If the portal doesn't have a PDF, or if the PDF looks like a Canva template someone made in twenty minutes, I take a screenshot instead. A screenshot means I don't fully trust the portal. It's a small thing but it's the signal.

The monospaced font for the amounts is right too. When numbers line up in a column, they read like a real ledger. When they're in a proportional font all over the place, they look like a receipt from a lunch app.

### The "deposit overdue" warning

I read that the Brand Strategist proposed a warning state for things like "quote near expiry" and "deposit overdue." I have a reaction to this.

**Warnings are how you get me defensive.** If I open the portal and the first thing I see is "DEPOSIT OVERDUE" in some alarming color, I'm not thinking "oh I should pay that." I'm thinking "why is this firm calling me out before they've even called me." Or I'm thinking I missed something I should have known about and now I feel behind.

The question I need answered before any warning shows me anything is: does it tell me what to do? If "deposit overdue" has a "Pay now" button right there, fine. The warning and the action are in the same place, I can deal with it. If "quote expires in 3 days" is just a notice with no button and no explanation of what happens if it expires, that's just stress. I don't need more stress. I need to know what to do.

Same with "quote near expiry." If I see that three days before a deadline I didn't know existed, I feel rushed. If the firm had told me the expiry date when they sent the quote, that would have been fine. But surfacing it as an urgent warning at the last minute — even if it's technically accurate — feels like a gotcha.

**Any warning has to be paired with a clear next action.** Otherwise it's just yelling at me.

### The parking lot

This is the thing I shortchanged in Round 1.

The parking lot is supposed to be where the firm puts things they found during the engagement but couldn't fit in the original scope. And the question they're asking me is: what do you want to do with this? Address it now, defer it, or drop it?

Here's my real-world version of what the parking lot is for. My scheduler quit. The firm and I scoped out a scheduling system fix. But partway through, they find out that the way my techs are tracking job completion is a mess too — nobody writes down when they finished a job, so billing is always a day late. That's not in the scope. So it goes in the parking lot.

When I see it there, I need to feel like the firm is saying "we noticed this, we're not ignoring it, here's what we think it means, what do you want to do." That's collaboration. What I don't want to feel is that the parking lot is a to-do list the firm left for me to complete. Or worse, a hint list of things they'd like to sell me next.

The Interaction Designer's spec says parking lot items only show up in the portal after the admin has reviewed and dispositioned them at handoff. That's the right call. I don't need to see every random note they took during the engagement. I need to see the things they actually thought were worth surfacing, after they've already decided whether to fold it in, follow on, or drop it.

What I want to feel when I look at the parking lot: "these people were paying attention, they found things I hadn't noticed, and they're being straight with me about what's worth doing and what isn't." What I don't want to feel: "here's your homework."

The UI question that matters: does each item explain clearly what it is, why it matters, and what I decided? If I come back four months later and look at the parking lot and there's an item that says "disposition: dropped" with no note explaining why it was dropped, I won't remember what that was. **The disposition needs a human explanation, not just a status tag.**

### When I come back after four days

I need to stress this. The other roles are writing as if I'm going to be checking this portal regularly. I won't. I check it when the email tells me to. Maybe Sunday night.

If I come back after four days, I need the home screen to answer one question in under ten seconds: "what do I need to do right now?" If the answer is "nothing, the work is in progress," the home screen should tell me that. Not with a progress bar. Not with a percentage. With a plain sentence: "The scheduling system work is underway. We'll be in touch when it's ready for your review." That's it. That's all I need.

If there IS something I need to do — sign something, pay something, review something — it needs to be the first thing on the screen. Not buried. Not in a tab I have to think about. On the home screen.

---

## What Feels Right

**QuickBooks.** My bank's app. Apple Calendar. Square. These are the references that matter to me. They're boring, they work, and every time I open them I know where I am. The portal should feel like these things.

The cream paper and dark ink register feels right. It looks like something from a professional firm, not a startup. The burnt orange button stands out without trying to be exciting. The flat edges — no rounded corners anywhere — make it feel like there are rules and the rules are being followed. That's reassuring.

The invoice spec the Interaction Designer wrote is right. The tab labels need words — that's what I need to add from Round 1.

---

## What Would Turn Me Off

**Tabs with icons but no labels.** I said this above. If I have to guess what a tab does by looking at an icon, you've lost me.

**Warnings without actions.** "Deposit overdue" with no Pay button is just an accusation. Give me the action or don't show me the warning.

**A portal that looks different when I try to sign something.** This is the visual whiplash problem. The signing surface has to live inside the same visual context as the rest of the portal. If it doesn't, I'm going to hesitate. Hesitation is the last thing you want when someone is about to commit money.

**Progress bars with percentages.** I read that this is already on the anti-pattern list. Good. I don't want a progress bar. I want to know what's being worked on and what's next. That's it.

**Gray text I can't read in my truck.** The Design Technologist wrote about WCAG 2.1 AA compliance and contrast ratios. I don't know what WCAG is. What I know is that last year I got an invoice from a vendor that had light gray text on a white background and I could not read it sitting in my truck in the Phoenix sun in August. I had to go inside to read the due date. That's a real failure. It doesn't matter what the contrast ratio technically is — if I can't read it in a bright parking lot on my phone, it failed.

Dark ink on cream should solve this. But if any of the secondary text — the muted color, the captions, the metadata — is light enough that it washes out in direct sunlight, I'm going to miss things that matter. Keep it dark enough that I can read it with the sun on the screen.

**Tiny buttons.** The Design Technologist specified 44 by 44 pixels for all touch targets. That number means nothing to me, but the reason behind it does. Cold mornings in November I'm still wearing my work gloves. I mistap all the time on things that are too small. And here's the thing about mistapping on a financial portal: if I accidentally tap "Pay now" when I meant to tap somewhere else, I'm terrified. Did I just pay something? Did I submit something? Was that binding? The touch targets need to be big enough that I don't wonder.

---

## Navigation Expectations

Same as Round 1, but I want to be specific about the tabs.

**Home** — this is the screen that answers "what do I do right now." Always the default when I log in.

**Proposals** — where I find my quote and the signing history. The word "Proposals" is clear. The icon for this tab should be something that obviously means "document I need to read and sign." Not "description."

**Invoices** — where I find what I owe and what I've paid. "Invoices" is clear. Don't call it "Billing" or "Payments" or anything else.

**Progress** — where I can check what the firm is working on and what's in the parking lot after handoff. "Progress" is clear.

Four tabs. That's enough. I don't need a fifth tab for documents in MVP. I already said I'll look for documents when the email tells me to look for them.

Billing information should never be buried in a settings screen. I said this in Round 1. I'll say it again. **Billing is not a setting.** If I have to navigate through any kind of "Account" or "Settings" menu to find what I owe, something went wrong.

The champion access thing — the Interaction Designer flagged this as deferred to Phase 4, meaning I could eventually give my operations manager limited access to the portal. That deferral is the right call for now. I'm the owner, I'm the decision-maker, I'll handle the portal at MVP. But I'll say this: if we ever get to the point where Maria can log in and see what's going on without bothering me, that would actually be useful. So defer it, but don't forget it.

---

## Make-or-Break Moments

**The email that arrives after the assessment.** If it looks like spam, nothing else matters. The portal never gets opened.

**The first authenticated screen.** Business name. Price. Sign button. In under ten seconds. No scrolling. This is still the most important screen in the system.

**The signing surface.** It has to feel continuous with the portal. Cream and ink have to be visible around the signing frame. If it looks like I went somewhere else, I hesitate. Hesitation kills the signing moment.

**The deposit invoice PDF.** If the portal can give me a real PDF with a real invoice number, I feel like I hired a professional firm. If it can't, I feel like I hired an app. That gap matters more than it should.

**Coming back after four days.** The home screen has to tell me immediately whether I have something to do or whether things are just moving. If I have to click around to figure out what's going on, the portal is adding friction instead of removing it.

Those five moments. Get them right and everything else is detail. Get any one of them wrong and I'm spending the rest of the engagement at low-grade skeptical.
