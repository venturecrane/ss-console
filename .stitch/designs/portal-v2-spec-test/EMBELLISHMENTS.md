# Embellishment evaluation

Generated from `.stitch/designs/portal-v2-spec-test`.

Stitch-generated elements NOT present in `src/pages/portal/**`.

Review each candidate and decide: **ship** (implement in source),
**defer** (real feature but not this pass), or **reject** (drop).


## engagement-desktop.html


### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 214**: `bg-gradient-to`
  ```html
  <h3 class="font-headline text-16/24 font-bold text-[color:var(--color-text-primary)] mb-6">Need assistance with your timeline?</h3>
<button class="w-full bg-gradient-to-br from-primary to-primary-container text-white py-4 px-6 rounded-lg font-headline font-bold tracking-tight hover:opacity-90 transition-all flex items-center justify-center gap-row">
<span class="material-symbols-outlined text-[20px]">chat_bubble</span>
                        Text Scott
                    </button>
  ```

## invoice-detail-mobile.html


### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 125**: `bg-gradient-to`
  ```html
  </div>
<button class="w-full bg-gradient-to-r from-primary to-primary-container text-white py-4 rounded-lg font-bold text-body shadow-lg active:scale-[0.98] transition-transform">
                Pay invoice
            </button>
</section>
  ```

## invoice-list-desktop.html


### Aggregate stat card (Total / Count / Average / Outstanding)  `aggregate-stat`

- **Line 180**: `Total Outstanding`
  ```html
  <div class="bg-primary text-white p-card rounded-xl flex flex-col justify-between aspect-video md:aspect-auto">
<span class="uppercase text-[10px] tracking-[0.2em] font-bold opacity-80">Total Outstanding</span>
<div>
<h3 class="text-display font-extrabold font-headline">$5,500.00</h3>
<p class="text-caption opacity-70 mt-1">Across 2 active invoices</p>
  ```

### Auto-action or settings banner (Auto-pay, Auto-renew, etc.)  `auto-settings`

- **Line 189**: `AUTO-PAY`
  ```html
  <span class="material-symbols-outlined text-primary" style="font-variation-settings: 'FILL' 1;">account_balance_wallet</span>
<span class="bg-white/50 px-2 py-1 rounded text-[10px] font-bold text-primary">AUTO-PAY OFF</span>
</div>
<div>
<span class="text-body font-bold text-primary block">Payment Method</span>
  ```

### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 138**: `bg-gradient-to`
  ```html
  <span class="text-title font-bold font-headline text-[color:var(--color-text-primary)]">$4,250.00</span>
<button class="bg-gradient-to-br from-primary to-primary-container text-white px-6 py-2 rounded-lg text-body font-semibold shadow-md active:scale-95 transition-all">
                        Pay
                    </button>
</div>
  ```
- **Line 154**: `bg-gradient-to`
  ```html
  <span class="text-title font-bold font-headline text-[color:var(--color-text-primary)]">$1,250.00</span>
<button class="bg-gradient-to-br from-primary to-primary-container text-white px-6 py-2 rounded-lg text-body font-semibold shadow-md active:scale-95 transition-all">
                        Pay
                    </button>
</div>
  ```

## invoice-list-mobile.html


### Aggregate stat card (Total / Count / Average / Outstanding)  `aggregate-stat`

- **Line 177**: `Total Outstanding`
  ```html
  <div class="bg-[color:var(--color-surface)] rounded-xl p-card">
<span class="text-label font-semibold uppercase tracking-[0.08em] text-[color:var(--color-text-muted)] mb-2 block">Total Outstanding</span>
<p class="text-display font-bold leading-10 text-[color:var(--color-text-primary)] font-headline">$5,500.00</p>
<div class="mt-4 flex gap-2">
<div class="h-1.5 flex-grow bg-[color:var(--color-error)]/10 rounded-full overflow-hidden">
  ```

### Auto-action or settings banner (Auto-pay, Auto-renew, etc.)  `auto-settings`

- **Line 190**: `Auto-pay`
  ```html
  <span class="text-label font-semibold uppercase tracking-[0.08em] text-[color:var(--color-primary)] mb-2 block">Payment Methods</span>
<p class="text-body-lg font-semibold leading-7 mb-4">Auto-pay is disabled</p>
<button class="bg-white/10 hover:bg-white/20 text-white border border-white/20 px-4 py-2 rounded-lg text-caption font-medium backdrop-blur-sm transition-all">
                    Configure Auto-pay
                </button>
  ```
- **Line 192**: `Auto-pay`
  ```html
  <button class="bg-white/10 hover:bg-white/20 text-white border border-white/20 px-4 py-2 rounded-lg text-caption font-medium backdrop-blur-sm transition-all">
                    Configure Auto-pay
                </button>
</div>
</section>
  ```

## portal-home-desktop.html


### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 194**: `bg-gradient-to`
  ```html
  </div>
<button class="w-full bg-gradient-to-b from-primary to-primary-container text-white py-3.5 rounded-xl font-semibold text-body hover:opacity-90 active:scale-[0.98] transition-all">
                            Pay invoice
                        </button>
</div>
  ```

## portal-home-mobile.html


### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 129**: `blur-3xl`
  ```html
  <section class="bg-[color:var(--color-surface)] p-section rounded-lg mb-12 flex flex-col items-start shadow-[0_32px_64px_-4px_rgba(25,28,30,0.06)] relative overflow-hidden">
<div class="absolute top-0 right-0 w-32 h-32 bg-primary/5 rounded-full -mr-16 -mt-16 blur-3xl"></div>
<p class="text-caption text-[color:var(--color-text-secondary)] mb-2">Deposit invoice · Due Friday</p>
<h2 class="text-display text-[color:var(--color-text-primary)] mb-8">$2,625.00</h2>
<button class="w-full bg-[#1e40af] te
  ```

## quote-list-desktop.html


### Decorative flourish (blur, gradient circle, ornament)  `decorative-flourish`

- **Line 184**: `bg-gradient-to`
  ```html
  <div class="mt-12 opacity-40">
<div class="w-full h-32 rounded-3xl bg-gradient-to-br from-surface-container-high to-surface-container flex items-center justify-center border border-[color:var(--color-border)]-variant/10">
<span class="material-symbols-outlined text-display text-[color:var(--color-text-secondary)]" data-icon="receipt_long">receipt_long</span>
</div>
</div>
  ```

### Pictographic avatar / illustration (non-photo)  `pictographic`

- **Line 182**: `Illustration`
  ```html
  </div>
<!-- Optional Illustration/Graphic Area -->
<div class="mt-12 opacity-40">
<div class="w-full h-32 rounded-3xl bg-gradient-to-br from-surface-container-high to-surface-container flex items-center justify-center border border-[color:var(--color-border)]-variant/10">
<span class="material-symbols-outlined text-display text-[color:var(--color-text-secondary)]" data-icon="receipt_long">receipt_long</span>
  ```

## quote-list-mobile.html


### Aggregate stat card (Total / Count / Average / Outstanding)  `aggregate-stat`

- **Line 171**: `Total Value`
  ```html
  <div class="bg-[color:var(--color-surface)] p-card rounded-lg">
<p class="text-[10px] font-bold uppercase tracking-widest text-[color:var(--color-text-secondary)] mb-2">Total Value</p>
<p class="text-title font-extrabold text-[color:var(--color-text-primary)]">$15,250</p>
</div>
<div class="bg-[color:var(--color-surface)] p-card rounded-lg">
  ```
- **Line 175**: `Active Count`
  ```html
  <div class="bg-[color:var(--color-surface)] p-card rounded-lg">
<p class="text-[10px] font-bold uppercase tracking-widest text-[color:var(--color-text-secondary)] mb-2">Active Count</p>
<p class="text-title font-extrabold text-[color:var(--color-text-primary)]">3 Items</p>
</div>
</div>
  ```

## Next step

For each candidate above, add a note to the PR describing
your disposition. Future runs of this evaluator will flag the
same patterns; add matched phrases to the source corpus
(as code or tests) once accepted.