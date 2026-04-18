# Stitch-invented feature suggestions

Features Stitch added that don't exist in source. One entry per feature — decide once, applies everywhere Stitch put it.

Styling/decoration choices (gradient CTAs, blur circles, typography drift) are NOT here — those are handled automatically by `normalize.py` and `strip.py`. What's below is product-level: real UX additions Stitch invented.

**Your decision per item:** ship (implement in source), defer (real feature, not this pass), reject (drop).

---

## 1. Aggregate outstanding-balance card

**What it is.** A prominent card on the invoices list showing the total $ outstanding across all unpaid invoices, typically with a progress-bar showing overdue share.

**Why it might ship.** Answers 'how much do I owe right now?' without scanning every invoice. Reduces the top-of-mind friction for an owner tracking cash.

**Where Stitch put it.** invoice-list-desktop, invoice-list-mobile (2 occurrence(s))

**Recommendation.** defer — real value but not MVP. Build once we have ≥3 invoices per client in real data.

**Your decision:** [ ] ship [ ] defer [ ] reject

---

## 2. Aggregate total-value / active-count stat pair

**What it is.** Two side-by-side cards on the proposals list: total $ value of all proposals + a count of active proposals.

**Why it might ship.** Gives the owner a pipeline snapshot. Useful if multi-quote is common, noise if single-quote is the norm.

**Where Stitch put it.** quote-list-mobile (2 occurrence(s))

**Recommendation.** reject — most SMD engagements are single-proposal. The stat pair would be 'Total: $5,250 / Active: 1', which is noise. Revisit if we move to multi-engagement accounts.

**Your decision:** [ ] ship [ ] defer [ ] reject

---

## 3. Auto-pay configuration banner

**What it is.** A banner (usually on the invoices list) surfacing whether auto-pay is enabled with a 'Configure' CTA.

**Why it might ship.** Would let owners set up recurring automatic payment for invoices instead of clicking Pay each time. Table-stakes for SaaS billing; unusual for project-based consulting.

**Where Stitch put it.** invoice-list-desktop, invoice-list-mobile (2 occurrence(s))

**Recommendation.** reject — SMD Services engagements are bounded projects paid per SOW, not recurring. Auto-pay doesn't match the business model.

**Your decision:** [ ] ship [ ] defer [ ] reject

---

## 4. Support / help sidebar widget

**What it is.** A sidebar card with 'Need help?' or 'Contact support' + a chat or email CTA.

**Why it might ship.** Makes support reachable without leaving the page. The ConsultantBlock already does this (name + phone); a separate 'Support' widget would be duplicative.

**Where Stitch put it.** engagement-desktop (1 occurrence(s))

**Recommendation.** reject — ConsultantBlock already covers this with the actual consultant's contact info, not generic support.

**Your decision:** [ ] ship [ ] defer [ ] reject

---
