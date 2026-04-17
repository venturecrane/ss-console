# Example: Prompt Enhancement

## User Input (Vague)

> "Make a landing page for my fitness app called 'Pulse'. Use red and black. It should have a place for features and a hero section."

---

## Enhanced Prompt (Stitch-Optimized)

**Overall Purpose**: A high-energy, modern landing page for 'Pulse', a fitness tracking application. The vibe is powerful, motivating, and high-performance.

**DESIGN SYSTEM (REQUIRED):**

- **Primary Color**: Racing Red (#e11d48) for CTA buttons and active states.
- **Secondary Color**: Deep Obsidian (#0f172a) for background and text containers.
- **Typography**: Bold, condensed sans-serif headings (e.g., Impact or Inter Tight) to convey speed and strength.
- **Style**: Hard edges with sharp, minimal borders; high contrast; dark mode by default.

**PAGE STRUCTURE:**

1. **Header**: Minimalist navigation with Pulse logo (left) and "Start Training" primary button (right).
2. **Hero Section**: Large, emotive fitness photography background. Headline: "Elevate Every Beat." Sub-headline: "Track, analyze, and crush your fitness goals with Pulse." Primary red CTA: "Get Started".
3. **Feature Grid**: Three-column layout highlighting:
   - **Real-time Tracking**: Live stats from your wearable.
   - **AI Coaching**: Personalized workouts based on your performance.
   - **Community Challenges**: Compete with friends and stay motivated.
4. **Social Proof Section**: Subtle slider showing "Trusted by 500,000+ athletes".
5. **Footer**: Quick links (Training, Pricing, Support), social icons, and legal.

---

💡 **Tip**: Notice how the enhanced prompt adds specific hex codes, defines the typography "vibe", and breaks the page into a logical numbered structure. This gives Stitch much clearer instructions.

---

# Example: Prompt Enhancement with NAV CONTRACT

When `.stitch/NAVIGATION.md` exists for the venture and the user provides classification tags, the prompt gains a NAV CONTRACT block between the vibe statement and the DESIGN SYSTEM.

## User Input

> "Design the invoice detail page for the client portal. surface=session-auth-client archetype=detail viewport=mobile task=pay-invoice pattern=nested-doll"

(The `task=` and `pattern=` tags are required when NAVIGATION.md spec-version >= 3. For legacy specs, only surface/archetype/viewport are needed.)

## Enhanced Prompt (with NAV CONTRACT injected)

**NAV CONTRACT (REQUIRED — do not invent beyond this block):**

Surface class: session-auth-client (portal.smd.services, authenticated).
Archetype: detail (has back button, no breadcrumbs, no bottom nav).
Viewport: mobile 390x844.

Chrome allowed (inclusive list; nothing else):

- Top band (sticky, NOT fixed): bg-white, 1px border-b #e2e8f0, h-14 on `<header>`. Left: client name (Inter 500, 13/18, #475569). Right: three-icon contact control (email/sms/tel, each 44x44 with aria-label).
- Back affordance inside `<main>`: chevron_left + "All invoices", href="/portal/invoices" (hardcoded). 44x44 tap target.
- Bottom chrome: NONE. Footer: NONE.

Chrome FORBIDDEN: global nav tabs, sidebar, hamburger, logo in header, breadcrumbs, bottom-tab nav, copyright row, marketing CTAs, real-face photos.

If any above conflicts with PAGE STRUCTURE below, THIS BLOCK WINS.

**DESIGN SYSTEM (REQUIRED):**

- Platform: Web, mobile-first, 390x844
- Palette: Primary #1E40AF, Text #475569, Bold #0F172A, Border #E2E8F0
- Typography: Inter 400/500/600
- Shape: 8px rounded

**PAGE STRUCTURE:**

1. Invoice summary: amount ($4,250), due date (Apr 18), status pill
2. Line items table
3. Pay button (primary CTA, above fold)
4. Consultant contact card (SVG silhouette, never real photo)

---

💡 **Tip**: The NAV CONTRACT separates _chrome_ from _content_. PAGE STRUCTURE no longer describes the header, footer, or back button — those are owned by the contract. If Stitch drifts on chrome, the post-generation validator (`validate.py`) catches it deterministically.
