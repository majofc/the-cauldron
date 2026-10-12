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

### Per-side testing (`.forge-trial-legs`, `.forge-leg`, `.forge-trial-asym`)

Per-side movements (`Exercise.is_per_side`, exposed as
`ExerciseSerializer.is_unilateral`) are tested **per side**: two `.forge-leg`
fields labelled Left/Right. Placement uses the **weaker** side (the min), and
`.forge-trial-asym` shows an italic Cormorant note when the sides differ ≥20%.
Each Trial move also shows its recommended rest (`.forge-trial-rest`) — "between
sides" for per-side moves, "before testing" otherwise.

In a logged session only the final to-failure (AMRAP) set splits into Left/Right;
earlier working sets keep one input. Per-side **timed** holds are the exception —
every set splits, and each `.forge-leg` carries its own `.forge-timer-btn`
stopwatch. The `· per side` tag (`.forge-trial-perleg`) marks a split row.

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

## The Grimoire (recipe book)

The Grimoire is a sub-section of The Cauldron. It **extends** the Cauldron and arcano tokens and
never redefines them. If you're building a Grimoire page, start here. Visual reference:
[`grimoire_preview.html`](grimoire_preview.html). It is standalone and not served by Django. To view it, run
`python -m http.server 8765` in the arcano root and open `/the_cauldron/grimoire_preview.html`.

### Vocabulary

| In-world (titles, nav, headings) | Plain (form labels, buttons, errors) |
|---|---|
| Spell | Recipe |
| Reagent | Ingredient |
| Casting | A time you made it |
| Ritual | Meal plan |

Titles say *"Your spells"*; a form field says *"Recipe name"*. Never make a user decode a label.

### Files

Grimoire pages load these on top of the Cauldron stack (`tokens.css` → `landing.css`):

| File | Role |
|---|---|
| `static/the_cauldron/css/grimoire-tokens.css` | `--grimoire-*` tokens, dark + parchment themes |
| `static/the_cauldron/css/grimoire.css` | Components below (no page layout) |
| `static/the_cauldron/js/grimoire-loader.js` | `GrimoireLoader`. Load **after** `cauldron-loader.js` |
| `templates/the_cauldron/grimoire/_mark.html` | Grimoire mark, inline SVG include |

### Palette

The Grimoire's character comes from **gilt** (the book's gold leaf: ornaments, stars, the mark).
Paprika stays the action color, so buttons are still `.btn-cauldron--primary`. Two supporting hues
carry meaning: **herb** (sage) is reagents and **sigil** (amethyst) is tags. Every token derives from an
`--arcano-*` or `--cauldron-*` token, directly or via `color-mix()`. Literal colors are never allowed.

| Token | Dark (`:root`) | Parchment (`.grimoire-parchment`) | Use |
|---|---|---|---|
| `--grimoire-ink` | `--cauldron-bg-deep` | cream + 14% gold | page root, loader backdrop |
| `--grimoire-page` | `--cauldron-bg-card` | `--cauldron-cream` | cards, panels |
| `--grimoire-page-raised` | `--cauldron-bg-surface` | cream + 8% gold | hover / nested |
| `--grimoire-text` | cream | `--cauldron-bg-deep` | body text |
| `--grimoire-text-dim` | `--cauldron-cream-dim` | deep 72% / cream | secondary text |
| `--grimoire-text-muted` | `--cauldron-cream-muted` | deep 45% / cream | **decorative only**: fails AA for text |
| `--grimoire-accent` | `--cauldron-paprika-light` | `--cauldron-paprika` | accent **text** |
| `--grimoire-accent-fill` | `--cauldron-paprika` | `--cauldron-paprika` | fills, borders, icons, never text on dark |
| `--grimoire-gilt` (+`-dim`, `-border`) | `--arcano-gold` | gold 45% / deep | mark, kicker, stars, ornaments |
| `--grimoire-herb` (+`-dim`, `-border`) | `--arcano-sage` | `--arcano-forest` | reagent chips |
| `--grimoire-sigil` (+`-dim`, `-border`) | `--arcano-lavender` | amethyst 70% / deep | tag pills |
| `--grimoire-nutrient-{protein,carbs,fat,fiber}` | paprika-light, gold, ember, sage | paprika, gold 70%, ember 60%, forest | nutrient bars |
| `--grimoire-star-on/off`, `--grimoire-nutrient-track`, `--grimoire-border`, `--grimoire-corner-color` | derived | derived | see note |

**Derived tokens** (the last row) reference other Grimoire tokens. A custom property resolves
where it is *declared*, so they live in a final `:root, .grimoire-parchment { … }` block. If a
new token references a themed token, add it to that block and not to a theme block, or parchment
silently inherits the dark value.

**Parchment** is an opt-in class (a printed / "open book" spell view), not tied to
`prefers-color-scheme`, because the rest of The Cauldron is dark-only.

### Contrast (WCAG AA, text on `--grimoire-page`)

Measured on the preview page and recomputed from the token values.

| Text token | Dark | Parchment |
|---|---|---|
| `--grimoire-text` | 15.8 : 1 | 16.3 : 1 |
| `--grimoire-text-dim` | 7.1 : 1 | 7.6 : 1 |
| `--grimoire-accent` | 5.5 : 1 | 4.9 : 1 |
| `--grimoire-gilt` | 9.1 : 1 | 6.1 : 1 |
| `--grimoire-herb` | 9.1 : 1 | 5.3 : 1 |
| `--grimoire-sigil` | 9.2 : 1 | 6.2 : 1 |
| `--grimoire-text-muted` | 3.1 : 1 ✗ | n/a ✗ (decorative only) |
| `--grimoire-accent-fill` (paprika) | 3.2 : 1 ✗ (fills/icons only, ≥ 3 : 1 non-text) | 4.9 : 1 |

### Typography

No new fonts. Spell titles and empty-state titles use **Playfair Display 700**. Descriptions and empty-state
copy use **Cormorant Garamond italic**. Reagent quantities use Cormorant 600 so numbers read as hand-written.
Kickers ("Spell · Supper") use Inter 600, 0.68rem, 0.22em tracking, uppercase, in gilt.

### The Grimoire mark

An open book with a rising four-point sigil. It uses the same line language as the Cauldron simple mark:
64 viewBox, 2px round strokes, `currentColor`.

```django
{% include 'the_cauldron/grimoire/_mark.html' with mark_class='grimoire-nav-mark' %}
```

Its color is `--grimoire-gilt` (paprika stays the Cauldron mark's color, so the two stay distinct in
the nav). Readable down to 16px. `.grimoire-nav-mark` is 28px, matching `.cauldron-nav-mark`.

### Spell card (`.grimoire-spell-card`)

```html
<a class="grimoire-spell-card" href="…">
  <div class="corner-tl" aria-hidden="true"></div>
  <div class="corner-tr" aria-hidden="true"></div>
  <div class="corner-bl" aria-hidden="true"></div>
  <div class="corner-br" aria-hidden="true"></div>
  <span class="grimoire-spell-kicker">Spell · Supper</span>
  <h3 class="grimoire-spell-title">Smoked Paprika Lentil Stew</h3>
  <p class="grimoire-spell-desc">…</p>
  <div class="grimoire-spell-meta"><span><strong>45</strong> min</span>…</div>
  <div class="grimoire-tags">…</div>
  <div class="grimoire-spell-foot"><!-- stars + attribution --></div>
</a>
```

The corner ornaments are **mandatory**; `.grimoire-spell-card` recolors them gilt. A link card lifts 3px
and gains `--grimoire-shadow-glow` on hover and focus. `.grimoire-spell-foot` uses
`margin-top: auto`, so cards in a grid keep equal height (global equal-height rule).

### Reagent chip (`.grimoire-reagent`)

`<span class="grimoire-reagent"><span class="grimoire-reagent-qty">250 g</span>red lentils</span>`.
Herb-tinted with radius `--arcano-radius-md`. `.is-missing` (dashed, no fill) marks a reagent
the user doesn't have, or one a scaling calculation flagged as the limiting reagent.

### Tag pill (`.grimoire-tag`)

A sigil-tinted pill with a `✦` prefix and lowercase text, wrapped in `.grimoire-tags`. As `<a>` or `<button>`
it becomes a filter, and `.is-active` marks the selected filter.

### Score stars (`.grimoire-stars`)

Five `★` glyphs in `.grimoire-star` with `.is-on` and `.is-half`. Always put an `aria-label` on the
group ("4.5 out of 5"). For input, use `.grimoire-stars--input` with `role="radiogroup"` and five
`<button role="radio">` stars. Hover previews the rating: the hovered star and every star before it
light, the rest dim (`:has()`), and the hovered star scales up. All Grimoire hover motion (card lift,
star scale, mark tilt, bar fill) is disabled under `prefers-reduced-motion: reduce`.

### Nutrient bar (`.grimoire-nutrient`)

```html
<div class="grimoire-nutrient grimoire-nutrient--protein">
  <span>Protein</span>
  <span class="grimoire-nutrient-track"><span class="grimoire-nutrient-fill" style="--pct:62%"></span></span>
  <span class="grimoire-nutrient-value">24 g</span>
</div>
```

`--pct` is the share of the user's target, capped visually at 100%. The color modifiers are `--protein`,
`--carbs`, `--fat` and `--fiber`. Stack them in `.grimoire-nutrients`. The value is always shown as text,
because the bar alone is never the only carrier of the number.

### Empty state (`.grimoire-empty`)

It has a dashed border, corner ornaments (mandatory), the mark at 56px (`.grimoire-empty-mark`), a Playfair title,
a Cormorant italic line, and one `.btn-cauldron--primary` CTA. The copy speaks in-world: *"Your grimoire is blank"*,
*"No castings yet. Make this spell and log it."*

### Grimoire loader (`GrimoireLoader`)

An open book whose three leaves turn in sequence (2.4s loop, 0.8s stagger). It sits inside a gilt ring
pair (9s CW / 13s CCW), with a pulsing gilt halo, a blinking sigil above the spine, three dots and a
Cormorant italic caption. The API matches `CauldronLoader` (`build(caption, size)`, `randomCaption()`,
`showPage()`, `hidePage()`). Loaded after `cauldron-loader.js`, it takes over page transitions, so
link clicks and form posts show the book. Captions: *"Turning the pages…"*, *"Reading the
margins…"*, *"Copying out the spell…"*, *"The grimoire stirs…"*.

Under **`prefers-reduced-motion: reduce`**, every animation stops and the turning leaves are hidden. The book renders
open and still.

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
