# The Cauldron — Style Guide

> Extends `ARCANO_STYLE_GUIDE.md`. Read the global guide first; these rules take precedence within `the_cauldron/`.

---

## Identity

**App name:** The Cauldron  
**Tagline:** Recipes & the alchemy of the kitchen. The Third Arcana.  
**Persona:** Mystical, warm, ancient. The keeper of culinary secrets.

---

## Locked Decisions

| Decision | Value | Notes |
|---|---|---|
| Brand color | `#B23A1F` Paprika | Primary accent — use instead of arcano gold |
| Mark (simple) | Literal cauldron line SVG | Use everywhere small (nav, cards, favicon) |
| Mark (complex) | Ornamented cauldron with steam, sigil, flames | Hero, og-image, splash only — ≥ 96 px |
| Background | `#0c0907` deep + `#060d07` forest | Warmer than arcano's green-dark |
| Text | `#F0E8DC` cream | Slightly warmer than arcano's `#F0EDE0` |

---

## Design Tokens

All tokens live in `the_cauldron/static/the_cauldron/css/tokens.css`. **Never hardcode values.**

```css
--cauldron-paprika:         #B23A1F   /* primary brand accent */
--cauldron-paprika-light:   #D86848   /* hover, emphasis */
--cauldron-paprika-dim:     rgba(178, 58, 31, 0.22)
--cauldron-paprika-glow:    rgba(178, 58, 31, 0.15)
--cauldron-paprika-border:  rgba(178, 58, 31, 0.32)
--cauldron-paprika-subtle:  rgba(178, 58, 31, 0.14)

--cauldron-bg-deep:    #0c0907   /* deepest bg, loader screens */
--cauldron-bg-forest:  #060d07   /* default page bg */
--cauldron-bg-surface: #1a100c   /* elevated surfaces */
--cauldron-bg-card:    #130e0b   /* card backgrounds */

--cauldron-cream:       #F0E8DC
--cauldron-cream-dim:   rgba(240, 232, 220, 0.65)
--cauldron-cream-muted: rgba(240, 232, 220, 0.38)

--cauldron-shadow-card:  0 20px 50px rgba(178,58,31,0.12), 0 0 0 1px var(--cauldron-paprika-border)
--cauldron-shadow-glow:  0 0 48px rgba(178,58,31,0.28)
--cauldron-shadow-ember: 0 0 8px var(--cauldron-paprika), 0 0 20px rgba(178,58,31,0.5)

--cauldron-corner-color: var(--cauldron-paprika-border)
```

---

## Typography

Inherits all arcano font tokens. No new fonts added.

| Element | Font | Weight | Notes |
|---|---|---|---|
| Headings | Playfair Display | 700–900 | Same as global |
| Taglines, body italic | Cormorant Garamond italic | 300–400 | |
| UI labels, buttons | Inter | 400–600 | |

**Gradient heading** (hero, section titles):
```css
background: linear-gradient(135deg, var(--cauldron-cream) 0%, var(--cauldron-paprika-light) 50%, var(--cauldron-cream) 100%);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
background-clip: text;
```

---

## The Cauldron Mark

### Simple Mark (inline SVG)
```html
<svg viewBox="0 0 64 64" fill="none" stroke="currentColor"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="12" y1="24" x2="52" y2="24"/>
  <path d="M14 24 Q14 48 32 48 Q50 48 50 24"/>
  <path d="M10 22 Q10 18 14 19"/>
  <path d="M54 22 Q54 18 50 19"/>
  <path d="M20 48 L18 54"/>
  <path d="M32 48 L32 54"/>
  <path d="M44 48 L46 54"/>
</svg>
```
Use everywhere ≤ 64 px. Color via `color: var(--cauldron-paprika)` on the parent.

### Complex Mark (inline SVG)
See `the_cauldron/templates/the_cauldron/landing.html` for the full SVG.  
Use at ≥ 96 px only — hero, og-image, splash screens.  
**Never use complex mark in nav or at small sizes.**

### Mark on backgrounds
| Background | Color |
|---|---|
| Deep/forest (default) | `--cauldron-paprika` |
| On paprika | `--cauldron-bg-deep` (knockout) |
| On cream / light | `--cauldron-paprika` |
| On elevated surface | `--cauldron-paprika-light` |

---

## Cards

All cards **must** have corner ornaments. Use the four `.corner-*` divs with `--cauldron-corner-color`:

```html
<div class="cauldron-card">
  <div class="corner-tl" aria-hidden="true"></div>
  <div class="corner-tr" aria-hidden="true"></div>
  <div class="corner-bl" aria-hidden="true"></div>
  <div class="corner-br" aria-hidden="true"></div>
  <!-- content -->
</div>
```

The `.corner-*` classes are defined globally in `arcano/static/arcano/css/base.css`. The cauldron overrides their `border-color` via `--cauldron-corner-color`.

---

## Buttons

| Class | Use |
|---|---|
| `.btn-cauldron.btn-cauldron--primary` | Main CTA — paprika fill |
| `.btn-cauldron.btn-cauldron--ghost` | Secondary — ghost border |

---

## The Cauldron Loader (`CauldronLoader`)

Anatomy (matches the design spec from `cauldron-marks.jsx`):

| Element | Spec |
|---|---|
| Halo | Paprika radial glow, `cauldronPulse` 1.8s |
| Ring 1 | `4s` CW, `inset: 6%` |
| Ring 2 | `6s` CCW, `inset: 18%`, `delay: 0.2s` |
| Ring 3 | `8s` CW, `inset: 30%`, `delay: 0.4s`, `opacity: 0.7` |
| Embers | 4 orbiting, `6s` CW, paprika with ember shadow |
| Center mark | `MarkSimple`, paprika |
| Dots | 3, `cauldronBlink` 1.2s, `0.18s` stagger |
| Caption | Cormorant Garamond italic, mystical message |

All keyframes are in `landing.css`. Captions to use: `"Tending the flame…"`, `"Stirring the pot…"`, `"The magic is working…"`.

---

## Equipment cards (`.forge-equip-card`)

Equipment is chosen via **magical icon cards**, not checkboxes. Each card: an
inline line-SVG icon (`.forge-equip-icon`), a name, a hidden checkbox, a radial
`.forge-equip-glow`, and a `✦` `.forge-equip-check`. Selected (`.is-on`) lights
the icon (paprika + drop-shadow), shows the glow, pops the check, and adds
`--cauldron-shadow-glow`. Icons live in `EQUIP_ICON` in `forge.js`.

The Equipment tab also holds the optional **demographics** (`.forge-demographics`:
birth year + sex) used only for the peer score — never required.

## Peer "flames" score + results reveal (`.forge-overlay`, `.forge-flames`)

After the Trial and after each session, results are revealed in a full-screen
`.forge-overlay` whose `.forge-anvil` plays a **hammer-and-sparks forging
animation** (`.forging`) for ~1.3 s before the verdict. The peer score is shown
as `.forge-flames` — ten 🔥 glyphs, `lit` ones igniting in a staggered `ablaze`
flicker. Honesty rules carry into the UI: show the decile/percentile, link the
source norm, tag interpolated values `≈ approx`, surface the female modified-
push-up note, and show the "no peer data yet" reason rather than a fake score.
All animations respect `prefers-reduced-motion`.

### Unlock celebration (gated progression)

An earned difficulty advance is **not** automatic — it appears in the overlay as
a `.forge-unlock-burst` (🔨✨🔥) celebration with **Claim it / Not yet**. Accept
climbs the ladder; deny holds and resets the counter. Backed by
`POST /progression/<uuid>/<accept|deny>/`.

## Progress: decile lines + balance (`.forge-chart-decile`, `.forge-asym-row`)

When the Progress chart is filtered to one normed exercise on the AMRAP metric,
overlay dashed `.forge-chart-decile` peer-decile lines — only the cutoff just
below the lowest value, the one just above the highest, and those in between.
`.forge-asym-row` shows per-leg Trial results with L/R bars; `.is-warn` flags a
≥20% imbalance.

## Arcane numeral input (`.forge-trial-input`)

Result fields (the Trial and Today logging) are **not boxed**. They render as a
glowing rune-line: transparent background, no border except a paprika
`border-bottom`, large **Cormorant Garamond** numerals in `--cauldron-paprika-light`,
centred, native spinners removed. Focus brightens the underline, adds an ember
`text-shadow`, and shifts the text to cream. Never give these a filled box or
rounded border — that's the boring look this replaced.

### Unilateral (per-leg) testing (`.forge-trial-legs`, `.forge-leg`, `.forge-trial-asym`)

Single-limb movements (pattern `lower_unilateral`, exposed as
`ExerciseSerializer.is_unilateral`) are tested **per side**: two `.forge-leg`
fields labelled Left/Right. Placement uses the **weaker** leg (the min), and
`.forge-trial-asym` shows an italic Cormorant note when the sides differ ≥20%.
Each Trial move also shows its recommended rest (`.forge-trial-rest`) — "between
sides" for unilateral, "before testing" otherwise.

## Exercise catalog & blocking (`.forge-cat-*`)

The **Exercises** tab lists the full catalog grouped by equipment, each move
block/unblockable. Components (all in `forge.css`, all using cauldron tokens):

| Class | Role |
|---|---|
| `.forge-cat-group` | One equipment group; add `.is-unowned` to dim gear the user doesn't own |
| `.forge-cat-head` | Group heading (Playfair) + optional `.forge-cat-tag` pill |
| `.forge-cat-row` | One exercise row; add `.is-blocked` (paprika border + strike-through name) |
| `.forge-cat-sub` | Substitute line (`→ using X`); `.forge-cat-sub--none` when no stand-in exists |
| `.forge-cat-btn` | Block/Unblock action — reuses `.btn-cauldron.btn-cauldron--ghost` |
| `.forge-cat-tag` | Small uppercase pill ("not owned", "needs gear") |

Blocked rows always show their substitute (or an explicit "no substitute
available"). Blocking is the **deliberate server-rendered/API exception** to the
otherwise card-driven UI — it round-trips through `/cauldron/api/exercises/{uuid}/block|unblock/`.

## About / methodology (`.forge-about-*`, `.forge-refs`)

The **About** tab explains how and why the Forge works, with numbered, linked
citations. `.forge-about-h3` section headers, `.forge-about-p` body copy,
superscript `<sup><a>` reference markers, and an ordered `.forge-refs` list with
paprika external links. Every scientific claim must carry a real, linked
citation — no uncited assertions.

---

## Non-negotiables (these extend global rules)

| Rule | Detail |
|---|---|
| No hardcoded colors | Always `var(--cauldron-*)` or `var(--arcano-*)` |
| Headings | Playfair Display only |
| Corner ornaments | All cards and modals — never omit |
| Mark usage | Simple ≤ 64 px, Complex ≥ 96 px |
| Complex mark | Hero / og-image only — never nav, never small |

---

## CSS File Header

Add to every new CSS file in `the_cauldron/`:

```css
/* ═══════════════════════════════════════════════
   Arcano Design System — The Cauldron
   All values → CSS tokens in tokens.css
   Component rules → STYLE_GUIDE.md
   ═══════════════════════════════════════════════ */
```
