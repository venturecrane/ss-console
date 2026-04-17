# Chrome component contracts

Each chrome piece has a DOM template and a Tailwind class template. Specs reference these contracts rather than redefining them per surface class. Deviations live in surface-class appendices.

## Header band

**DOM:**

```html
<header
  role="banner"
  class="sticky top-0 z-50 bg-white border-b border-[#E2E8F0] h-14 md:h-16 px-4 md:px-6 flex items-center justify-between"
>
  <div class="flex items-center">
    <span class="text-[13px] leading-[18px] font-medium text-[#475569]"
      ><!-- client/workspace name --></span
    >
  </div>
  <div class="flex items-center gap-4">
    <!-- optional: Text Scott SMS link, settings gear, etc. -->
    <a href="sms:+..." class="text-[13px] leading-[18px] font-medium text-[#1E40AF]">Text Scott</a>
  </div>
</header>
```

**Rules:**

- `sticky top-0`, never `fixed top-0`
- `bg-white`, never translucent or `backdrop-blur-*`
- Height: `h-14` (56px) on mobile, `h-16` (64px) on desktop via `md:h-16`
- `border-b border-[#E2E8F0]` — exact hex, not `border-slate-200` (close but not exact)
- Left: client name only. No logo. No icon. No decoration.
- Right: optional single quick-action link. Never two. Never icons without labels.

## Back affordance (detail archetypes)

**DOM:**

```html
<a
  href="/portal/invoices"
  aria-label="All invoices"
  class="inline-flex items-center gap-1 w-11 h-11 text-[#475569] hover:text-[#0F172A] focus-visible:ring-2 focus-visible:ring-[#3B82F6] focus-visible:ring-offset-2 rounded-lg"
>
  <span class="material-symbols-outlined">chevron_left</span>
  <span class="text-[13px] font-medium">All invoices</span>
</a>
```

**Rules:**

- Single `<a>` or `<button>`. **Never wrap in `<nav>`.** Never use `aria-label="Breadcrumb"`.
- Target: hardcoded canonical URL string. Never `#`, `javascript:`, or `history.back()`.
- Tap target: 44×44 minimum (`w-11 h-11` = 44px).
- Positioned below the header band, within the main content area, not inside the header itself (prevents crowding).
- Label text: the destination's name ("All invoices", "Home", "Back to clients"). Not "Back" in isolation.

## Breadcrumbs (admin only)

**DOM:**

```html
<nav aria-label="Breadcrumb" class="mb-4">
  <ol class="flex items-center gap-2 text-[13px] text-[#475569]">
    <li><a href="/admin/clients" class="hover:text-[#0F172A]">Clients</a></li>
    <li aria-hidden="true">
      <span class="material-symbols-outlined text-base">chevron_right</span>
    </li>
    <li><a href="/admin/clients/123" class="hover:text-[#0F172A]">Delgado Plumbing</a></li>
    <li aria-hidden="true">
      <span class="material-symbols-outlined text-base">chevron_right</span>
    </li>
    <li><span aria-current="page" class="font-medium text-[#0F172A]">Audit log</span></li>
  </ol>
</nav>
```

**Rules:**

- **Admin only.** Never on portal. Never on token-auth. Never on public.
- 2 levels max on `list` archetype; 3 levels max on `detail` archetype.
- Last item is the current page with `aria-current="page"`; rendered as a `<span>`, not an `<a>`.
- Separator: `<chevron_right>` with `aria-hidden="true"`; never use `/` or `>` as text (screen readers will read them literally).
- Truncation: each label capped at 24ch on mobile; full label on desktop. Use `text-overflow: ellipsis` via `truncate` class with explicit `max-w-[24ch]`.

## Footer (public only)

**DOM:**

```html
<footer class="bg-white border-t border-[#E2E8F0] mt-16">
  <div
    class="max-w-7xl mx-auto px-6 py-8 flex flex-col md:flex-row justify-between gap-4 text-[13px] text-[#475569]"
  >
    <span>© 2026 SMDurgan LLC. All rights reserved.</span>
    <nav aria-label="Legal" class="flex gap-4">
      <a href="/privacy" class="hover:text-[#0F172A]">Privacy</a>
      <a href="/terms" class="hover:text-[#0F172A]">Terms</a>
      <a href="/contact" class="hover:text-[#0F172A]">Contact</a>
    </nav>
  </div>
</footer>
```

**Rules:**

- **Public surface class only.** Never rendered on authenticated or token-auth surfaces.
- Legal links in a `<nav aria-label="Legal">`. Not free-floating `<a>` elements.
- Copyright on left, legal links on right (flex-row on desktop, flex-col on mobile with copyright first).

## Skip-to-main link (all surfaces)

**DOM:**

```html
<a
  href="#main"
  class="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] bg-white px-4 py-2 rounded-lg border border-[#E2E8F0] text-[#1E40AF] font-medium"
>
  Skip to main content
</a>
```

**Rules:**

- First element inside `<body>`, before `<header>`.
- Hidden visually by default (`sr-only`); revealed on keyboard focus (`focus:not-sr-only`).
- Matching `id="main"` on the `<main>` element.

## State classes (all interactive elements)

```
Default:     text-[#475569]
Hover:       hover:text-[#0F172A]
Active link: text-[#1E40AF]   + aria-current="page"
Focus ring:  focus-visible:ring-2 focus-visible:ring-[#3B82F6] focus-visible:ring-offset-2
Disabled:    disabled:text-[#94A3B8] disabled:cursor-not-allowed
```

Tap-target padding when the rendered element is visually smaller than 44px: wrap or pad to `w-11 h-11` (44×44) minimum, or use `p-3` (12px × 4 = 48px — works for 24px icons).

## Right rail (detail, dashboard — desktop)

**DOM:**

```html
<div class="flex flex-col lg:flex-row gap-8 lg:gap-12">
  <div class="flex-1 lg:max-w-[75%]">
    <!-- primary content -->
  </div>
  <aside class="w-full lg:w-[300px] lg:sticky lg:top-[80px] lg:self-start space-y-6">
    <!-- consultant block, status, related links -->
  </aside>
</div>
```

**Rules:**

- Desktop only (collapses to stacked on mobile via `flex-col lg:flex-row`).
- Width: ~300px fixed or 25% max of container.
- `<aside>` element, not `<div>` — semantic.
- `lg:sticky lg:top-[80px]` so the rail stays visible under the sticky header (header is 64px + 16px buffer = 80px top offset).

## Consultant block (portal surfaces)

**DOM:**

```html
<section class="bg-white border border-[#E2E8F0] rounded-lg p-6 flex items-start gap-4">
  <div
    class="w-12 h-12 rounded-full bg-[#1E40AF] text-white flex items-center justify-center font-semibold"
    aria-hidden="true"
  >
    SD
  </div>
  <div class="flex-1">
    <h3 class="text-base font-semibold text-[#0F172A]">Scott Durgan</h3>
    <p class="text-sm text-[#475569]">Primary consultant</p>
    <a
      href="sms:+..."
      class="mt-2 inline-flex items-center gap-1 text-sm font-medium text-[#1E40AF]"
    >
      <span class="material-symbols-outlined text-base">chat</span>
      Text Scott
    </a>
  </div>
</section>
```

**Rules:**

- Photo placeholder: solid-color circle with white initials. Never a real photo. Never an illustration of a person.
- Color: primary hex; or per-appendix override.
- Initials: 2 letters max, capitalized.

## Mobile-desktop transform

For each chrome piece above, the mobile ↔ desktop transform is explicit via Tailwind's responsive modifiers. The convention across this skill: **`md:` is the only breakpoint used for chrome transforms**. That's 768px. Avoid `lg:` (1024px) and `sm:` (640px) for chrome — they are allowed for content layout but not for chrome structure.

Violating this creates the "breakpoint fragmentation" flagged in the ss-console drift audit.
