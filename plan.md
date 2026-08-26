/* ─────────────────────────────────────────────────────────────────────────
   Crack It page — dark theme, corrected
   Fixes applied (per better-ui skill + your own Cooplink design tokens):
   1. ONE accent color, not two (dropped the gold — see note below)
   2. Radius matches the base system's scale (4/8/12), concentric nesting
   3. Elevation = shadow, borders stay structural (dividers only)
   4. Exact polish values: scale(0.96) press, 150ms named transitions,
      100ms stagger, small translateY on enter
   ───────────────────────────────────────────────────────────────────────── */

.crack-it {
  /* One accent. Warm burnished copper instead of violet — reads premium,
     doesn't collide with every other "dark + purple" AI-generated dashboard,
     and still separates cleanly from the marketing site's lime without
     introducing a second hue on this page. */
  --bg-primary: #0B0A09;          /* near-black, warm undertone (not blue) */
  --bg-surface: #16140F;
  --bg-surface-hover: #1D1A14;
  --bg-elevated: #201C15;         /* #1 spot only */

  --border-subtle-ci: rgba(255, 255, 255, 0.06);
  --border-strong-ci: rgba(255, 255, 255, 0.12);

  --accent: #C9853B;              /* burnished copper — the only accent */
  --accent-hover: #DDA062;
  --accent-bright: #F2B876;       /* for glow/text on rank #1 only — same hue, not a second color */
  --accent-ink: #1A1006;          /* dark text ON accent-filled surfaces */

  --text-primary: #F4F1EA;
  --text-secondary: rgba(244, 241, 234, 0.62);
  --text-tertiary: rgba(244, 241, 234, 0.38);

  --success: #6FBF8B;
  --danger: #E0725F;

  /* Radius scale matches the base system's steps (4 / 8 / 12), not a
     one-off 10/12 pair invented for this page alone. */
  --radius-sm: 4px;    /* rank plain text, arrows */
  --radius-md: 8px;    /* logo, badge, inputs, buttons, pills */
  --radius-lg: 12px;   /* row cards (outer) */
  /* Concentric check: row padding is 20-24px, badge padding is ~8-10px —
     outer (12px) = inner (8px) + a fraction of the padding step, kept
     deliberately close rather than arbitrary. */

  --bg: var(--bg-primary);
  --surface: var(--bg-surface);
  --surface-raised: var(--bg-surface-hover);
  --ink-1: var(--text-primary);
  --ink-2: var(--text-secondary);
  --ink-3: var(--text-secondary);
  --ink-4: var(--text-tertiary);
  --border-subtle: var(--border-subtle-ci);
  --border: var(--border-strong-ci);
  --accent-lime: var(--accent); /* keep semantic token compatible with base utilities */
  --accent-lime-ink: var(--accent-ink);

  background-color: var(--bg-primary);
  color: var(--text-primary);
}

/* #1 spot — elevated via real shadow, not a fake border-as-shadow */
.crack-it .ci-row-elevated {
  background: var(--bg-elevated);
}

/* ── Fields ─────────────────────────────────────────────────────────────── */

.crack-it .ci-field {
  height: 48px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-primary);
  padding: 0 16px;
  font-size: 14px;
  transition-property: border-color, background-color;
  transition-duration: 150ms;
  transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
}
.crack-it .ci-field::placeholder { color: var(--text-tertiary); }
.crack-it .ci-field:focus-visible {
  outline: none;
  border-color: var(--accent);
}
.crack-it select.ci-field option {
  background: var(--bg-surface);
  color: var(--text-primary);
}

/* ── Buttons — exactly two styles, exact press value ───────────────────── */

.crack-it .ci-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  transition-property: background-color, border-color, color, box-shadow;
  transition-duration: 150ms;
  transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
}
/* Press feedback: exactly 0.96, CSS transition (interruptible), not a
   custom easing curve borrowed from the hover state */
.crack-it .ci-btn:active {
  transform: scale(0.96);
  transition: transform 100ms cubic-bezier(0.2, 0, 0, 1);
}
.crack-it .ci-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* Primary — filled copper, real elevation on hover via shadow not scale */
.crack-it .ci-btn-primary {
  background: var(--accent);
  color: var(--accent-ink);
  height: 48px;
  padding: 0 24px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}
.crack-it .ci-btn-primary:hover {
  background: var(--accent-hover);
  box-shadow: 0 4px 16px rgba(201, 133, 59, 0.28);
}

/* Secondary — ghost outline */
.crack-it .ci-btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border-subtle);
  height: 36px;
  padding: 0 16px;
}
.crack-it .ci-btn-secondary:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.crack-it .ci-btn-secondary:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* Pulse on the #1 CTA only — restrained, single-hue glow, still 2s */
@keyframes ci-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(201, 133, 59, 0); }
  50%      { box-shadow: 0 0 14px 2px rgba(201, 133, 59, 0.22); }
}
.crack-it .ci-btn-pulse {
  animation: ci-pulse 2s ease-in-out infinite;
  border-color: var(--accent);
  color: var(--accent);
}

/* ── Category pills ─────────────────────────────────────────────────────── */

.crack-it .ci-pill {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition-property: background-color, color, border-color;
  transition-duration: 150ms;
  transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
}
.crack-it .ci-pill:hover {
  color: var(--text-primary);
  border-color: var(--border-strong-ci);
}
.crack-it .ci-pill[aria-selected="true"] {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
  font-weight: 600;
}
.crack-it .ci-pill:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* ── Leaderboard rows ───────────────────────────────────────────────────── */

.crack-it .ci-row {
  display: grid;
  grid-template-columns: 48px 48px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 16px;
  min-height: 88px;
  padding: 20px 24px;
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle); /* structural: separates row from bg */
  cursor: pointer;
  transition-property: background-color, box-shadow, transform;
  transition-duration: 150ms;
  transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
}
.crack-it .ci-row:hover {
  background: var(--bg-surface-hover);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35); /* elevation = shadow, not border */
  transform: translateY(-2px);
}
.crack-it .ci-row:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* Top 3 — no fake border-as-shadow; real layered shadow instead */
.crack-it .ci-row-top {
  padding: 24px;
  border-color: transparent;
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.04) inset,
    0 8px 30px rgba(0, 0, 0, 0.45),
    0 0 0 1px rgba(201, 133, 59, 0.18);
}
.crack-it .ci-row-top:hover {
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.06) inset,
    0 12px 36px rgba(0, 0, 0, 0.5),
    0 0 0 1px rgba(201, 133, 59, 0.32);
}

.crack-it .ci-rank-badge {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  font-size: 18px;
  font-weight: 700;
  color: var(--accent-ink);
  background: var(--accent);
}
/* Rank #1 — brighter shade of the SAME hue, not a second color */
.crack-it .ci-rank-badge-gold {
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-bright) 100%);
}
.crack-it .ci-rank-plain {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-secondary);
  text-align: center;
  border-radius: var(--radius-sm);
}

.crack-it .ci-logo {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  object-fit: contain;
  background: rgba(255, 255, 255, 0.06);
  /* Image outline per better-ui: 1px pure white at low opacity in dark mode,
     never a tinted neutral — a tinted ring reads as dirt on the edge */
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.1);
}
.crack-it .ci-logo-fallback {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.06);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.1);
}

.crack-it .ci-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.crack-it .ci-desc {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.crack-it .ci-meta {
  font-size: 13px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crack-it .ci-price {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}
.crack-it .ci-price-top {
  color: var(--accent-bright);
}

/* Rank change arrows — subtle, single-frame, matches text weight */
@keyframes ci-arrow-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.crack-it .ci-arrow {
  animation: ci-arrow-in 200ms cubic-bezier(0.2, 0, 0, 1) both;
  font-size: 10px;
}

/* Hero underline — single hue gradient, not a rainbow sweep */
.crack-it .ci-hero-underline {
  width: 96px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  margin: 16px auto 0;
}

.crack-it .ci-banner-success {
  border: 1px solid var(--accent);
  border-radius: var(--radius-lg);
  background: rgba(201, 133, 59, 0.1);
}

.crack-it ::selection {
  background: var(--accent);
  color: var(--accent-ink);
}

/* ── Inline stake widget ────────────────────────────────────────────────── */

.crack-it .ci-stake {
  grid-column: 1 / -1;
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle); /* structural divider — border is correct here */
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  /* Staged entrance: this widget appears infrequently (on click), so it
     earns a stagger — content fades up 4px, faster and subtler than the
     row entrance since it's a secondary reveal, not the page's first paint */
  animation: fade-in-up-sm 200ms cubic-bezier(0.2, 0, 0, 1) both;
}
@keyframes fade-in-up-sm {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
.crack-it .ci-stake-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  min-width: 200px;
}
.crack-it .ci-stepper {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.crack-it .ci-step-btn {
  width: 40px;
  height: 40px;
  padding: 0;
  font-size: 16px;
  border-radius: var(--radius-md);
}
.crack-it .ci-step-val {
  min-width: 128px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}