# The Cauldron — The Forge

Adaptive calisthenics inside Arcano. The Forge assesses you (the **Trial**),
generates an autoregulated program, and adapts it after every session.

The single-page app (`templates/the_cauldron/forge.html` + `static/.../forge.js`)
is driven entirely by the DRF API under `/cauldron/api/`.

---

## Peer score — "fires" 🔥

After an AMRAP test (a Trial anchor, or an AMRAP working set) the result is
compared against published age × sex norms and turned into a **1–10 "fires"**
rating (percentile → decile → flames). The Exercises tab also shows the **best
fires you have ever earned** per movement.

All norm data lives in **`services/norms.py`**. The logic is pure and
unit-tested (`tests/test_norms.py`).

### Honesty policy

Peer scoring must be *fair*, so every table is sourced and carries a
`confidence`:

| confidence | meaning |
|---|---|
| `good` | real age×sex percentiles from a published scientific reference |
| `thin` | real but narrow — one study / few brackets / one sex, or crowd-sourced community data (Strength Level) rather than a peer-reviewed norm |
| `estimated` | **placeholder / derived** — no usable table was found; values are an estimate or a derivation, flagged `estimated` everywhere and listed below for replacement |

Estimated scores are flagged end-to-end: `norms.score()` sets
`PeerScore.estimated=True`, the API returns `estimated` / `best_fires_estimated`,
and the UI marks them (`est.` chip on the badge, a sub-note on the score card).

Peer scoring is applied **only to the calibrated test movement of each pattern**
(the Trial anchor). Other rungs of a ladder (e.g. *Wall Push-up* vs *Push-up*)
are intentionally **not** scored — a rep count is only comparable to a norm at
the difficulty the norm was measured at.

### Coverage

| Pattern | Scored movement | Norm key | Confidence | Source |
|---|---|---|---|---|
| Horizontal push | Push-up | `pushup` | **good** | CSEP-PATH (Payne et al., 2000). Female = modified (knee) push-up. |
| Core (anti-ext.) | Plank | `plank` | **thin** (+est. 30+) | Chase/Brigham, IJES (18–25). Ages 30+ estimated decline. |
| Vertical pull | Pull-up | `pullup` | **thin** | Topend categories, men 18–35 only; women omitted (floor effect). |
| Vertical pull (anchor) | Australian Row | `australian_row` | **thin** (+est. 40+) | Strength Level inverted-row standards (101k sets). |
| Vertical push | Pike Push-up | `pike_pushup` | **thin** (+est. 40+) | Strength Level pike-push-up standards (79k sets). |
| Hinge | Glute Bridge | `glute_bridge` | **thin** (+est. 40+) | Strength Level glute-bridge standards (163k sets). |
| Lower (unilateral) | Split Squat | `split_squat` | **estimated** | Derived from Strength Level bodyweight-squat (546k sets) ×0.5/leg. |

The three crowd-sourced norms use Strength Level's documented level→percentile
mapping: **Beginner = P5, Novice = P20, Intermediate = P50, Advanced = P80,
Elite = P95** (`"<1"` rep → recorded as 1). That population skews trained and
young-adult, so the published figures are applied to the 20–39 brackets
(confidence `thin`) and ages 40+ are an estimated decline.

### Replace / upgrade with stronger data

Nothing here is fabricated, but several entries are crowd-sourced, derived, or
extrapolated and would be **upgraded** by peer-reviewed age×sex AMRAP tables:

| Priority | Entry | What's weak | How to upgrade |
|---|---|---|---|
| High | `split_squat` | whole table is **derived** (squat ×0.5); ratio is an assumption | replace with a real per-leg split-squat rep norm; remove `"estimated": True` |
| Med | `australian_row`, `pike_pushup`, `glute_bridge` — **40+ brackets** | estimated decline of crowd-sourced prime data | drop in measured 40+ data; remove those brackets from `estimated_brackets` |
| Med | `australian_row`, `pike_pushup`, `glute_bridge` — **20–39** | crowd-sourced (trained-skewed), not peer-reviewed | swap for a scientific norm; raise `confidence` to `good` |
| Med | `plank` — **30+ brackets** | estimated decline of the 18–29 study | drop in measured data; remove from `estimated_brackets` |
| Low | `pullup` | one 18–35 bracket, men only | add age brackets + a valid women's norm |

**Mechanics:** each is a `type: "percentiles"` table in `NORMS` in
`services/norms.py`. Replacing a whole table → set real values, remove
`"estimated": True`, fix `confidence`/`source`/`url`. Replacing only older
brackets → set their values and remove those tuples from `estimated_brackets`.
The `estimated` flag then clears from the API and UI automatically; update this
table to match. Keep one `# Strength Level` / `# estimated` comment per row so
provenance stays obvious. Always update `tests/test_norms.py` alongside.

Good sources for upgrades: CSEP-PATH / ACSM fitness batteries, Topend Sports
norm tables, and peer-reviewed AMRAP/endurance studies for the specific movement.
