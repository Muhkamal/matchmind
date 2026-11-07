# DESIGN.md

> MatchMind looks like an instrument panel for honest probabilities: dense, calm, numbers first, every claim checkable.

## 1. Visual Theme & Atmosphere

**Style**: Data-dense dark (trading-terminal discipline, sports-analytics clarity)
**Keywords**: dense, precise, calm, tabular, auditable, restrained, high-contrast numbers, no hype
**Tone**: Credible and sober, like a lab report with good typography. NOT flashy, NOT betting-site neon, NOT gamified.
**Feel**: A cockpit gauge cluster: you can read every value at a glance and nothing is decorative.

**Interaction Tier**: L1 refined static
**Dependencies**: CSS only (inline SVG for charts; no JS libraries)

## 2. Color Palette & Roles

```css
:root {
  /* Backgrounds */
  --bg: #0a0d12;                 /* page */
  --surface: #11151c;            /* cards, tables */
  --surface-alt: #151a23;        /* table header, zebra rows */
  --surface-hover: #1a202b;      /* row / card hover */

  /* Borders */
  --border: #232a36;
  --border-hover: #34405a;

  /* Text */
  --text: #e8edf5;               /* headings, key numbers */
  --text-secondary: #a3adbd;     /* body, table cells */
  --text-tertiary: #6f7a8c;      /* labels, captions */

  /* Accent (one accent only: interactive + focus) */
  --accent: #5aa9ff;
  --accent-hover: #82bfff;

  /* Outcome encoding (used ONLY for H / D / A, never decoration) */
  --home: #3ecf8e;
  --draw: #8a94a6;
  --away: #b794f6;

  /* RGB variants for rgba() */
  --bg-rgb: 10, 13, 18;
  --accent-rgb: 90, 169, 255;
  --home-rgb: 62, 207, 142;
  --away-rgb: 183, 148, 246;

  /* Semantic */
  --success: #3ecf8e;
  --error: #f5656b;
  --warning: #e8b04a;
}
```

**Color Rules:**
- Every colour is a CSS variable. No hard-coded hex in rules or inline styles.
- Green/grey/purple mean Home/Draw/Away everywhere and only there. Hit/miss use `--success` / `--error` with an icon or text too (never colour alone).
- One accent (`--accent`) for links, focus rings and the active tab. No gradients on text or panels.
- Body text on `--surface` must keep contrast >= 4.5:1; `--text-tertiary` is for labels >= 12px only.

## 3. Typography Rules

**Font Stack:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root {
  --font-sans: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}
```

| Role | Font | Size | Weight | Line Height | Letter Spacing |
|------|------|------|--------|-------------|----------------|
| Page title (H1) | Inter | 24px | 700 | 1.2 | -0.01em |
| Section H2 | Inter | 13px uppercase | 600 | 1.3 | 0.08em |
| H3 / card title | Inter | 15px | 600 | 1.35 | — |
| Body | Inter | 14px | 400 | 1.55 | — |
| Label / caption | Inter | 12px | 500 | 1.4 | 0.02em |
| KPI number | JetBrains Mono | 28px | 600 | 1.1 | -0.02em |
| Table numbers | JetBrains Mono | 13px | 500 | 1.4 | — |

**Typography Rules:**
- All numbers (probabilities, Brier, counts) use `--font-mono` with `font-variant-numeric: tabular-nums` so columns align.
- Headings weight >= 600. Section H2 is a small uppercase label, not a big title.
- **NEVER use**: Arial/Times as a primary face, decorative or script fonts, font sizes below 12px.

**Text Decoration:**
- No text gradients or text shadows anywhere (restrained style; numbers must stay crisp).

## 4. Component Stylings

### Buttons
```css
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  min-height: 36px; padding: 0 14px;
  font: 600 13px/1 var(--font-sans);
  color: var(--text); background: var(--surface-alt);
  border: 1px solid var(--border); border-radius: 8px; cursor: pointer;
  transition: background .15s, border-color .15s, transform .15s;
}
.btn:hover  { background: var(--surface-hover); border-color: var(--border-hover); }
.btn:active { transform: translateY(1px); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn:disabled, .btn[aria-disabled="true"] { opacity: .45; cursor: not-allowed; }
.btn--primary { background: var(--accent); color: var(--bg); border-color: transparent; }
.btn--primary:hover { background: var(--accent-hover); }
```

### Cards
```css
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px;
  transition: border-color .15s, background .15s;
}
.card:hover { border-color: var(--border-hover); }
.card:focus-within { border-color: var(--accent); }
.kpi .v { font: 600 28px/1.1 var(--font-mono); color: var(--text); letter-spacing: -0.02em; }
.kpi .l { font: 500 12px/1.4 var(--font-sans); color: var(--text-tertiary); margin-top: 6px; }
```

### Navigation (sticky top bar)
```css
.topbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 16px; padding: 12px 20px;
  background: rgba(var(--bg-rgb), .92); border-bottom: 1px solid var(--border);
}
.topbar a { color: var(--text-secondary); text-decoration: none; font: 500 13px var(--font-sans);
  padding: 6px 10px; border-radius: 6px; }
.topbar a:hover { color: var(--text); background: var(--surface-hover); }
.topbar a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.topbar a[aria-current="true"] { color: var(--accent); }
```

### Links
```css
a { color: var(--accent); text-decoration: underline; text-decoration-color: transparent;
    text-underline-offset: 3px; transition: text-decoration-color .15s, color .15s; }
a:hover { color: var(--accent-hover); text-decoration-color: currentColor; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
```

### Tables (the core component)
```css
table { width: 100%; border-collapse: collapse; background: var(--surface);
        border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
th { background: var(--surface-alt); color: var(--text-tertiary);
     font: 600 11.5px/1.3 var(--font-sans); text-transform: uppercase; letter-spacing: .06em;
     padding: 10px 12px; text-align: left; position: sticky; top: 0; }
td { padding: 9px 12px; border-top: 1px solid var(--border); color: var(--text-secondary);
     font-size: 13.5px; }
td.num, th.num { text-align: right; font: 500 13px var(--font-mono); font-variant-numeric: tabular-nums; }
tbody tr:hover td { background: var(--surface-hover); }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
```

### Probability bar (H/D/A)
```css
.probs { display: flex; height: 10px; border-radius: 3px; overflow: hidden; min-width: 120px;
         background: var(--surface-alt); }
.probs > .h { background: var(--home); } .probs > .d { background: var(--draw); } .probs > .a { background: var(--away); }
/* always paired with the numeric % in adjacent cells */
```

### Tags / Badges
```css
.badge { display: inline-flex; align-items: center; height: 20px; padding: 0 8px;
  font: 600 11px var(--font-sans); border-radius: 999px; border: 1px solid var(--border);
  color: var(--text-secondary); background: var(--surface-alt); }
.badge--high   { color: var(--success); border-color: rgba(var(--home-rgb), .4); }
.badge--medium { color: var(--warning); border-color: rgba(232, 176, 74, .4); }
.badge--low    { color: var(--text-tertiary); }
```

### Segmented control (calibration market switch)
```css
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button { min-height: 32px; padding: 0 14px; background: var(--surface); color: var(--text-secondary);
  border: 0; font: 600 12.5px var(--font-sans); cursor: pointer; }
.seg button:hover { background: var(--surface-hover); color: var(--text); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--bg); }
.seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
```

### Empty / error state
```css
.empty { padding: 20px; border: 1px dashed var(--border); border-radius: 12px;
         color: var(--text-tertiary); font-size: 13px; }
.err { color: var(--error); }
```

## 5. Layout Principles

**Container:**
- Max width: 1180px; side padding 20px (16px on mobile)
- Narrow variant (footer / methodology text): 720px

**Spacing Scale (4px base):** 4, 8, 12, 16, 24, 32, 48
- Section gap: 32px (24px mobile)
- Component gap: 12-16px
- Card internal padding: 16px

**Grid:**
```css
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.two-col { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }   /* e.g. upcoming + calibration */
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
```
Order on the page: header/status, KPIs, upcoming picks, recent results, record + goal markets, calibration, arena, biggest misses, methodology. Information a visitor needs to judge honesty (record vs baseline, misses) is never hidden below decoration.

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Flat | 1px `--border`, no shadow | Cards, tables (default) |
| Raised | `--surface-hover` + `--border-hover` | Hover on rows/cards |
| Overlay | `0 8px 24px rgba(0,0,0,.45)` + 1px border | Tooltips only |

Depth comes from borders and surface steps, not shadows.

## 7. Animation & Interaction

**Motion Philosophy**: Quiet and functional: 150ms colour/transform feedback and one short fade-in on load. Nothing moves on scroll.
**Tier**: L1

### Dependencies
None (CSS only).

### Entrance Animation
```css
@keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.reveal { animation: fadeUp .35s ease-out both; }
.reveal:nth-child(2) { animation-delay: .04s; } .reveal:nth-child(3) { animation-delay: .08s; }
.reveal:nth-child(4) { animation-delay: .12s; }
```

### Scroll Behavior
Native scrolling. `html { scroll-behavior: smooth; }`. Sticky topbar and sticky table headers only.

### Hover & Focus States
Every interactive element has `:hover` and `:focus-visible` (2px `--accent` outline, 2px offset). Table rows highlight with `--surface-hover`. Chart points show a native `<title>` tooltip with predicted / actual / n.

### Special Effects
None. No parallax, cursor effects, or canvas/WebGL.

### Reduced Motion
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
```

## 8. Do's and Don'ts

### Do
- Put the numbers first: KPI row and tables above any prose.
- Show sample size (`n`) next to every rate, and a baseline next to every Brier score.
- Use tabular monospace numerals and right-align numeric columns.
- Pair colour with text/icon for hit/miss and H/D/A so it works for colour-blind users.
- Keep empty states honest ("No graded picks yet") instead of fake placeholder data.
- Escape every string that came from the API before inserting it into HTML.
- Label experimental markets (Over 2.5 / BTTS) as experimental on the page.

### Don't
- ❌ No gradients on text, buttons or panels.
- ❌ No neon, glow, or bookmaker-style odds banners.
- ❌ No "guaranteed", "sure win", or tipster language anywhere in copy.
- ❌ No decorative animation, parallax, or auto-playing motion.
- ❌ No emoji as icons (use inline SVG).
- ❌ No hard-coded hex values outside `:root`.
- ❌ No font size below 12px and no light-grey-on-dark text below 4.5:1 contrast.
- ❌ No horizontally scrolling page; wide tables scroll inside `.table-scroll` only.
- ❌ No colour as the only signal of correct/incorrect.

## 9. Responsive Behavior

**Breakpoints:**
| Name | Width | Key Changes |
|------|-------|-------------|
| Desktop | > 900px | Two-column zones, full tables |
| Tablet | 601-900px | Single column, KPIs in 2-4 columns |
| Mobile | <= 600px | Single column, tables scroll inside their card, secondary columns hidden (`.hide-sm`) |

**Touch Targets:** minimum 44x44px for tappable controls on mobile (segmented buttons and nav links get extra padding at <= 600px).
**Collapsing Strategy:** On mobile, upcoming picks render the probability bar plus a compact "H 52 · D 27 · A 21" line; Over 2.5/BTTS columns and model metadata move behind `.hide-sm`. Page never overflows horizontally.

---

## 10. Landing page extension (L2, added for `/`)

The dashboard stays L1. The public landing page (`api/static/landing.html`) reuses sections 2-6 and 8-9 unchanged and raises motion to **L2 fluid**, CSS and vanilla JS only (no libraries).

**Page order:** Hero (H1 word reveal, live "ledger entry" card, mouse spotlight) → live stats strip → manifesto marquee → How it works (3 steps) → Bento guarantees (unequal sizes, spotlight hover) → "What we do / don't do" → Verify-it-yourself (copyable API call) → final CTA → footer with risk notice.

**Signature moments (performance-safe):**
1. Hero: H1 word-by-word reveal, ambient grid + mouse-follow spotlight (CSS vars, rAF-throttled, `hover: hover` only), ledger card rows "locking" in sequence.
2. First scroll: manifesto marquee band (pure CSS `transform`).
3. Guarantees: unequal bento with per-card spotlight glow.
4. Clever detail: the "Verify it yourself" block copies a real API call; the button then reads "Copied. Now go check us."

**Rules:** no `filter: blur`, no `backdrop-filter`, no scroll-jacking, no parallax; reveal-on-scroll uses opacity + translateY once; every effect disabled under `prefers-reduced-motion`; mouse effects only when `(hover: hover)`.
**Copy rules:** never imply guaranteed wins; every claim is something the database enforces or the dashboard shows; example data on the page is labelled "Example entry".
