# the_cauldron — Claude Code Context

Adaptive calisthenics training app ("The Forge"). Users complete an assessment trial, get a personalized program, and log workouts. The program adapts as they progress through an exercise difficulty ladder.

URL prefix: `/cauldron/`
Auth: login required for all app views (landing is public).

---

## Conceptual flow

```
Equipment Profile (user owns) 
    ↓
Assessment Session (AMRAP trials per MovementPattern)
    ↓
Program (split, weekly volume, days)
    └── ProgramDay (day_index 0–N)
            └── PrescribedExercise (pattern → exercise → sets/reps/load)
    ↓
WorkoutSession (one completed day)
    └── SetLog (one actual set)
```

Progress mechanic: when a user hits the top of an exercise's rep range consistently (`sessions_at_top` threshold), `pending_progression` is set to the next exercise in the ladder. The program applies the progression on next session start.

### Progression rules (`services/progression.py::next_prescription`, #63)

- **Load step** — `next_load_up` returns the first buildable total ≥ `current + max(2.5% of
  current, 1 kg)` (2.5 lb floor when `profile.load_unit == "lb"`); if nothing clears the step,
  the next heavier load anyway (sparse fixed dumbbells never strand the user). `next_load_down`
  mirrors it for de-loads. Bands are level indices: always one level.
- **Actual-load basis** — `forge.apply_session_log` progresses a load-mode prescription from
  `max(target_load, heaviest actual_load logged on it this session)`, snapped with
  `nearest_available_load` (`forge._progression_base_load`). Logging a heavier day moves the plan.
- **Assisted bands** — `Exercise.is_assisted` (seeded via `seed_forge.ASSISTED`: Band-Assisted
  Row / Pull-up). The band helps, so progress *lowers* the band index, a de-load raises it, and the
  heaviest-logged rule takes the *lightest* band logged. Starting loads begin at the heaviest band
  (most help) and a stronger Trial moves toward lighter ones (`progression._easiest_first`). At band 0 + top of range the next rung is
  parked as `pending_progression`; Band-Assisted Pull-up ends its chain, so `forge._next_rung` falls
  back to the easiest harder performable rung of the pattern (Pull-up). Band Rollout is resisted
  and still raises the index.
- **Next rung** — `forge._next_rung` resolves what "harder" means for this user (walks
  `progression` past rungs needing missing gear) and passes it as `next_rung`; `None` = the top of
  *their* ladder. Without the argument the engine follows `ex.progression`.
- **Top of ladder** — difficulty rung with no performable next rung: after `SESSIONS_TO_ADVANCE`
  sessions at the top, `rmax + 1` (no cap). Once the range is extended past the rung's seeded max
  and the AMRAP is ≥ `TOP_OF_LADDER_REPS` (25), every top-of-range session re-checks: a harder rung
  that became performable (dumbbells added → Weighted Pistol Squat) is parked as pending
  immediately. Load mode at the heaviest buildable load keeps `rmax + 1` (no cap).
- **Weighted lower rungs** — Weighted Pistol Squat (12) and Weighted Dragon Squat (13), load mode,
  dumbbells OR kettlebell, linked after Dragon Squat.
- **Timed holds** (difficulty mode, every rung) — AMRAP ≥ `rmax` grows the target:
  `rmax = min(120, max(rmax + 5, round(0.9 × achieved)))`. Only at 120 s does the hold count as top
  of range for advancing (normal `SESSIONS_TO_ADVANCE`); with nowhere to go it holds at 120 with
  the counter full. Load-mode timed rungs (carries) progress load as usual.

### Unfinished past sessions (#63)

"Unfinished" = `WorkoutSession` with `status=planned`, `scheduled_for < today` and ≥ 1 SetLog with
`actual_reps`/`actual_load`. `GET sessions/unfinished/` returns the newest (`forge.unfinished_session`)
and closes older ones as `skipped` (no progression; values kept). Stale ones — on a no-longer-active
program, or dated on/before the user's latest completed session — are skipped too and never gate Today
(`forge._stale_unfinished`), so old reps are never replayed onto prescriptions that moved on. While one exists, `GET today/` and
`POST sessions/` answer **409** `{"code": "unfinished_session"}` (`forge.has_unfinished_session`).
The client shows a blocking sheet (`#forge-unfinished-modal`, no ✕/swipe/Escape) with the logged
values editable: **Save** → `POST sessions/{uuid}/log/` (progression runs, `performed_at` = noon of
its `scheduled_for` via `forge._performed_at`); **Delete** → `DELETE sessions/{uuid}/` (session +
SetLogs, prescriptions untouched; 409 for a completed session). Today's own planned session and past
sessions with no values never trigger it.

---

## Models

### Catalog (staff-seeded, shared across all users)

**MovementPattern** — the 7 training patterns
```
horizontal_push, vertical_pull, vertical_push,
lower_unilateral, core_anti_extension, hinge, grip
```
`grip` (#61) joins the normal least-trained rotation — `DAILY_PATTERN_COUNT` stays 5, so it
appears in roughly five of every seven openings. Its whole ladder is timed (seconds), on the
same scale as its anchor.
Fields: `key` (unique slug), `name`, `primary_muscles`, `is_lower_body`.

**Equipment**
Fields: `key` (unique), `name`, `is_loadable`, `load_unit` (none/kg/lb/band_level).
Examples: `bodyweight`, `pullup_bar`, `dumbbells`, `barbell`, `bands`, `rings`, `fat_grips`.
There is no `towel` — a towel is assumed universal, so towel rungs only need the bar they hang from.

**Exercise** — one rung on a difficulty ladder
| Field | Notes |
|---|---|
| `pattern` FK | which movement pattern |
| `name` | e.g. "Pike Push-up" |
| `difficulty_rank` | integer order within pattern's ladder. `lower_unilateral` is ONE ladder (`seed_forge.SINGLE_LADDER_PATTERNS`): unique ranks across both modes, one link chain; other patterns keep a chain per mode |
| `progression_mode` | `difficulty` (ladder) or `load` (weight progression) |
| `rep_range_min/max` | working rep range |
| `is_timed` | if True, reps = seconds (holds) |
| `is_assisted` | if True, the band *assists* (Band-Assisted Row / Pull-up): progression lowers the band index. See "Progression rules" |
| `is_per_side` | if True, worked one side at a time — rep targets forced even (`progression.rep_targets_for`). Workout logging records ONE value per set meaning "reps per side"; it does **not** split L/R |
| `required_equipment` | M2M Equipment — **all of** these must be owned |
| `alternative_equipment` | M2M Equipment — **at least one of** these must be owned (empty = no constraint). Lets one rung read "a bar OR rings" without duplicating it per implement; `forge.is_performable` applies both rules |
| `variant_group` | slug shared by rungs occupying the SAME ladder position (`bar_pullup` = Pull-up + Chin-up). They collapse into one ladder node, count as one chain, and `grip` is only the label telling them apart |
| `is_assessment_anchor` | the bilateral move tested per pattern in the Trial: Push-up, Australian Row, Pike Push-up, Squat, Plank, Glute Bridge — each has a peer norm. **Grip carries two**: Dead Hang, plus Towel Wring Hold as the no-equipment fallback. `forge._anchor_for` / `forge.js:renderTrial` take the highest-rank anchor the user can perform, so the fallback only shows when the hang is out of reach |
| `placement_threshold` | Trial **anchor** AMRAP score that places here. Must be non-decreasing by rank within a pattern, equal for same-rank rungs — enforced for **every** pattern by `tests/test_grip_pattern.py::TestThresholdMonotonicity` (the lower ladder alone was covered by `tests/test_lower_ladder.py`). Among same-rank rungs the user owns, placement still follows row order |
| `regression` / `progression` | FK to self, adjacent rungs |
| `name_es`, `cues_es` | Spanish display text (#87); `name` stays the English key — see the i18n notes under The Grimoire |
| `video_url`, `cues`, `rest_seconds` | coaching metadata. `rest_seconds` comes from `seed_forge.rest_for`, which is pattern-aware: a grip hold rests 90s, every other timed hold 40s |

---

### Per-user data

**UserEquipmentProfile** (OneToOne with User)
What the user has available.
Fields: `equipment` M2M, `dumbbell_weights` JSON list, `band_levels`, `barbell_min_increment`, `barbell_plates`, `load_unit`.
Birth year and sex moved to `CauldronProfile` (#105, migrations 0034–0036); the peer score reads them through
`services.forge._demographics` (never creates the row). The Forge's equipment form and MCP `set_equipment` no
longer take them — the Profile's "About you" does.

**AssessmentSession** — one trial run
Fields: `user`, `is_active` (only one active at a time), `completed_at`.
When completed: spawns a `Program`. The Trial is a **recurring** measurement — users are
nudged to retest every 30 days (`services.forge.retest_status`).

**AssessmentResult** — one pattern's result in a session
Fields: `session`, `pattern`, `tested_exercise`, `placed_exercise`, `reps_or_seconds`.
Unique: `(session, pattern)`.

The Trial takes **one value per pattern** — every anchor is bilateral, and there is no
left/right capture anywhere (removed in #50; migration 0014 summed old per-side results and
re-placed them, capped at ±1 rung).

**Program** — generated plan
Fields: `user`, `is_active`, `source_assessment` FK, `split` (full_body_3x/upper_lower_4x), `weekly_volume_target`
(sets/pattern/week, 4–40, default 16 since #109; migration 0038 set 16 on existing programs without regenerating).

**Weekly volume (#109)** — `target_sets = clamp(round(weekly_volume_target / weekly_frequency), 2, 5)`
(`forge.sets_for_volume`; half rounds up). `weekly_frequency` = the split's sessions/week (`SESSIONS_PER_WEEK`: 3 or
4) × `min(DAILY_PATTERN_COUNT, patterns on the day) / patterns on the day`. `generate_program` uses it (no more
hardcoded 3). `forge.regenerate_volume(program, pattern_ids)` runs for the touched patterns only after `set_rung`,
`set_load` and `sync_program_equipment` (patterns whose prescription was swapped or re-snapped); editing the volume
(`set_weekly_volume`) regenerates all. Today's untouched sets follow the new count (`_rewrite_open_sets`); a movement
with logged values keeps today's sets. ⇄ swaps still use `DEFAULT_TARGET_SETS` (3).

**ProgramDay**
Fields: `program` FK, `day_index`, `name`.
Unique: `(program, day_index)`.

**PrescribedExercise** — current prescription
| Field | Notes |
|---|---|
| `day` FK | |
| `pattern`, `exercise` FK | |
| `target_sets/reps_min/max/load/rest_seconds` | |
| `order` | display order on the day card |
| `sessions_at_top` | consecutive sessions at top of rep range |
| `pending_progression` | FK Exercise — earned unlock, not yet applied |

**WorkoutSession**
Fields: `user`, `program_day`, `scheduled_for`, `performed_at`, `status` (planned/completed/skipped).

**SetLog** — one actual set
Fields: `session`, `prescribed_exercise`, `exercise` (may differ if mid-session regression), `set_index`, `expected_reps/load`, `actual_reps/load`, `is_amrap`, `rir`.

Workout logging records **one input per set**. For a per-side movement that value means
*reps per side* — including per-side timed holds. Only the third (last) set is to-failure.

`SetLogSerializer.is_unilateral` is only a "per side" label hint.

Load-mode prescriptions never get a 0 load (a weightless handle/bar/shell makes 0 buildable,
but `progression.available_loads` drops it; band level 0 is kept). A load assigned from scratch
(program generation, ⇄ swap, new rung) comes from `forge._initial_load`: the user's last logged
load on that exercise; else their latest Trial score for the pattern — one buildable step up per
20% the score clears the rung's `placement_threshold`, capped at the middle of their range
(`progression.trial_seeded_load`); else the lightest prescribable load.

**BlockedExercise** — user-forbidden movements
Fields: `user`, `exercise`, `reason`. Unique `(user, exercise)`.

---

## Views

`the_cauldron/views.py`

| View | Auth | Template |
|---|---|---|
| `landing_view` | public | `the_cauldron/landing.html` |
| `forge_view` | login_required | `the_cauldron/forge.html` |

`forge.html` is a single-page app driven entirely by API calls to `/cauldron/api/` endpoints. The template itself is mostly a shell.

---

## API endpoints (`/cauldron/api/...`)

All DRF — check `the_cauldron/urls.py` for full list. Key groups:

- **Profile:** GET/PATCH `/cauldron/api/profile/` — UserEquipmentProfile
- **Catalog:** GET `/cauldron/api/patterns/`, `/cauldron/api/exercises/`, `/cauldron/api/equipment/`
- **Assessment:**
  - `GET /cauldron/api/assessment/` — the active session (404 if none)
  - `POST /cauldron/api/assessment/` — submit every row at once and forge the program.
    Body: `{"split": "full_body_3x", "results": [{"pattern_key", "tested_exercise" (uuid),
    "reps_or_seconds"}, …]}`.
    Returns `{assessment, program, peer}`. There is no `start/` or `complete/`
    endpoint — one POST does both.
  - `POST /cauldron/api/assessment/retake/` — deactivate current session + program, open a
    fresh session (full reassessment; there is no lightweight check-in mode)
  - `GET /cauldron/api/assessment/history/` — per-pattern Trial series for the Evolution
    charts: `{patterns: [{pattern_key, pattern_name, points: [...]}]}`.
    Each point: `date, exercise, reps_or_seconds, is_timed, ladder_score, delta_vs_prev, verdict`
  - `POST /cauldron/api/assessment/reminder/dismiss/` — suppress the retest nudge 3 days
- **Program:** GET `/cauldron/api/program/` (active). There is no `program/days/{id}/` route —
  days are nested inside the program payload.
- **Today:** `GET /cauldron/api/today/?day=<index>` — opens (creates) a WorkoutSession for
  that day and returns it, **plus** `retest_due`, `last_trial_at`, `days_since_last_trial`
- **Session logging:** `GET /cauldron/api/sessions/`, `POST /cauldron/api/sessions/{uuid}/log/`
  with `{"sets": {"<setlog-uuid>": {"actual_reps", "actual_load", "rir"}}}`
- **Unfinished sessions (#63):** `GET /cauldron/api/sessions/unfinished/` → `{"session": …|null}`;
  `DELETE /cauldron/api/sessions/{uuid}/` → 204 (409 if completed). `today/` and `POST sessions/`
  return 409 `{"code": "unfinished_session"}` while one is pending
- **Progression:** `POST /cauldron/api/progression/{presc_uuid}/{accept|deny}/`
- **Set my rung (#59):**
  - `GET /cauldron/api/prescription/{presc_uuid}/set-rung/` — picker options: every performable rung
    of the prescription's chain (whole mixed ladder on lower), each flagged `is_current` / `unready`
  - `POST` same URL, `{"exercise": "<uuid>", "confirm_unready": false}` → 200 `{exercise, applied_to}`;
    409 `{unready, required, your_trial, pattern, anchor, is_timed}` for a climb whose
    `placement_threshold` exceeds the latest Trial score (re-POST with `confirm_unready: true`);
    409 `{detail}` if today's open session already has logged sets for it; 400 off-chain / blocked / no gear.
    Moves **every** live prescription on the chain (`forge.set_rung`), re-derives targets/load/rest,
    resets `sessions_at_top`, clears `pending_progression`, rewrites today's untouched sets.
    Never writes `AssessmentResult` — the live prescription is the source of truth for "current rung"
    (`forge._current_rung_on_chain` prefers it over the Trial placement).
  - `GET /cauldron/api/rungs/` — `{chains: [...], by_exercise: {rung_uuid: presc_uuid}}` for the skill tree
- **Set my load (#109):** `POST /cauldron/api/prescription/{presc_uuid}/set-load/ {"load": 22.5}` → 200
  `{load, snapped_from, applied_to}` (`forge.set_load`). Program-wide for the exercise; snaps DOWN to a buildable load
  (below the lightest → the lightest; `snapped_from` = the asked value when it moved). Bands take an owned level
  index (assisted rungs keep the inverted meaning). Resets `sessions_at_top`, clears `pending_progression`, rewrites
  today's untouched sets; 409 with logged sets; 400 for difficulty mode, ≤ 0, a band level not owned. The exercise
  sheet's "Set my load" uses `load_options` / `is_band_load` from `PrescribedExerciseSerializer`.
- **Weekly volume:** `PATCH /cauldron/api/program/ {"weekly_volume_target": 16}` (4–40, else 400) → the program;
  edited in the Equipment tab ("Weekly volume").
- **PostHog:** `forge_rung_set` (replaced `rung_changed`), `forge_load_set`, `forge_volume_set` —
  `{pattern_key, direction: up|down|same, source: web|mcp}` (`pattern_key` null for volume).

---

## Serializers (key ones)

| Serializer | Fields of note |
|---|---|
| `ExerciseSerializer` | includes `is_unilateral` (computed), `required_equipment` as slug list |
| `PrescribedExerciseSerializer` | includes `pending_progression_name` (computed) |
| `ProgramSerializer` | nested days → nested prescriptions |
| `WorkoutSessionSerializer` | nested set_logs |
| `AssessmentSessionSerializer` | nested results with `placed_exercise_name` |

---

## Management commands

`the_cauldron/management/commands/seed_forge.py` — seeds all MovementPattern, Equipment, Exercise rows plus test data. Run after a clean DB: `python manage.py seed_forge`.

---

## Debugging hints

- Assessment produces wrong placement → check `placement_threshold` on the `is_assessment_anchor` exercise for that pattern. The algorithm picks the highest anchor where `reps >= threshold`.
- "No program generated" after completing assessment → confirm `is_active=True` on the assessment session before `complete/`; the view gates on active session.
- `pending_progression` not clearing → `apply-progression/` endpoint was not called; it requires an explicit POST (not automatic on session completion).
- Equipment filter excludes an exercise the user expects → `required_equipment` must be a subset of what the user owns, AND (when set) `alternative_equipment` must intersect it. Check both M2Ms, and prefetch them together or `is_performable` re-queries per row.
- `BlockedExercise` uniqueness error → user already has that exercise blocked; do a GET first.
- A prescription shows 0 kg → the user's implement weighs 0 and something bypassed
  `progression.available_loads`; migration 0017 repaired the rows written before #52.
- Retest banner won't appear → `retest_status` keys off the last **completed** assessment and is
  suppressed for 3 days after a dismissal *or a retake*. An open, incomplete session does not
  reset the 30-day clock.
- A forge overlay looks wrong on a phone → the ≤768px "Mobile sheets" block in `forge.css`; dialogs need the
  `.forge-sheet-head` / `.forge-sheet-body` wrappers. Visual baselines live in `tests/visual_baselines/`
  (`FORGE_UPDATE_BASELINES=1` rewrites them; review the PNGs before committing).
- Evolution chart reports a setback after a rung promotion → `ladder_score` (not raw reps) is the
  comparison basis; check `placed_exercise` is set on the result, since a null placement scores 0.

---

## The Grimoire — recipe book (#67)

Second section of The Cauldron, same login/session. Vocabulary: **spell** = recipe, **reagent** =
ingredient (in-world words in titles/nav; plain words in form labels). Design spec: STYLE_GUIDE.md →
"The Grimoire".

`models/` is a package: `models/forge.py` (everything above) and `models/grimoire.py`; both are
re-exported from `models/__init__.py`, so `from the_cauldron.models import X` works for either.

| Model | Notes |
|---|---|
| `Tag` | `slug`, `name`, `name_es`. Seeded (soup, salad, drink, breakfast, dessert, main, side, snack, sauce, bread, vegetarian, vegan) |
| `Reagent` | `name`, `name_es`, `search_name` (normalized, see below), `category`, `source` usda/user/staff, `fdc_id` (unique, nullable), `created_by`, `status` private/pending/approved, `grams_per_unit` JSON (`cup`/`tbsp`/`tsp`/`piece` → grams), `density_g_per_ml` |
| `ReagentNutrients` | 1:1 `reagent.nutrients`; 24 fields per 100 g (`ReagentNutrients.FIELDS`). **Null = unknown**, shown "—"; never coerced to 0. A stored 0 is a measured zero. `added_sugar_g` (#104, after `sugar_g`) stands in for WHO's free sugars minus fruit-juice sugars (sugar and honey count in full); `added_sugar_over_total` rejects it above a known `sugar_g` — reagent API (PATCH checks the stored sugars), model `clean()` (admin), quick entries, MCP |
| `Spell` | `owner`, `title` (+ `search_title`, covers `title_es` too), `description`, `servings`, `prep/cook_minutes`, `steps` JSON list, `visibility` private/public, `forked_from` (SET_NULL), `tags` M2M. `title_es` / `description_es` / `steps_es`: Spanish text for **seeded starter spells only** (read-only in the API) |
| `SpellReagent` | `spell.lines`: `reagent` (PROTECT), `quantity`, `unit`, `grams` (stored), `note` (+ `note_es`, starters only), `order` |
| `GrimoirePreferences` | 1:1 `user.grimoire_preferences`, `unit_system` metric/us. Created lazily (view / `preferences/` GET) |

**Rules** (`services/grimoire.py` — the single source):
- `readable_spells(user)` = own + public. Anything else is **404** (existence never leaks). Edit/delete/
  publish/unpublish on someone else's *public* spell is **403** (`SpellViewSet._owned`).
- `visibility` is read-only in `SpellSerializer`; it changes only via `publish/` / `unpublish/`.
- `fork_spell` deep-copies the spell + lines + tags into a private spell of the caller. Nothing is
  shared, so later edits/unpublish/delete of the source never reach the fork. The copy is written in
  the **forker's language** (`get_language()`) into the base fields — a user's spell is one-language.
- `visible_reagents(user)` = approved + the user's own. A spell line may only use a visible reagent,
  **except** reagents already on that spell (a fork may carry the source owner's private reagent).
- User reagents are created `private`, or `pending` when the user ticks "share"; staff approve in the
  admin (`ReagentAdmin` actions). `source`/`status` can't be set through the API.
- `to_grams(reagent, qty, unit)`: mass units fixed; `grams_per_unit` for cup/tbsp/tsp/piece/glass (a glass
  has no generic volume — only the reagent's own weight per glass); volume
  units through `density_g_per_ml`; otherwise `ConversionError` → 400 on the line.
- Search is case- and accent-insensitive through the stored `search_name` / `search_title`
  (`normalize_search`) — works on Postgres and the sqlite fallback, no `unaccent`. Every query word
  must match; exact → prefix → infix order. Reagent POST answers **409** `{"code": "duplicate",
  matches}` for an exact normalized-name match unless `confirm: true`.
- `has_unverified_reagents` (serializer) is true when any line's reagent isn't approved → the UI's
  "unverified nutrition" badge.

**URLs** (all login → Cauldron login; one template, `grimoire.html`, switched by `page` = list/spell/edit):
- `the_cauldron:grimoire` → `/cauldron/grimoire/` — list (spells + reagents tabs); linked from the landing and Forge navs
- `the_cauldron:grimoire-spell` → `/cauldron/grimoire/spell/<uuid>/` — one spell, **full page** (no pop-ups);
  same visibility as the API (`readable_spells`), else 404
- `the_cauldron:grimoire-spell-new` → `…/spell/new/`, `the_cauldron:grimoire-spell-edit` → `…/spell/<uuid>/edit/` —
  the editor, full page; edit is **owner-only** (404 otherwise — others fork). Save navigates to the spell page.
- `the_cauldron:grimoire-reagent` → `…/reagent/<uuid>/` — one ingredient (measures + grouped nutrients), visible like the API
- `the_cauldron:grimoire-reagent-new` → `…/reagent/new/?name=`, `the_cauldron:grimoire-reagent-edit` → `…/reagent/<uuid>/edit/` —
  full-page form; edit only while `services.grimoire.can_edit_reagent` (creator + not approved), else 404.
  A unit/density change re-derives `grams` on every line using it (`regrams_lines`). PUT replaces the whole
  nutrient profile (missing → null); PATCH changes only what it sends.
- `the_cauldron:terms` / `the_cauldron:privacy` → `/cauldron/terms/`, `/cauldron/privacy/` — The Cauldron's own legal
  pages (public, ES/EN in-template like scholomancy_console's). Every Cauldron page overrides `footer_links` with
  `_footer_links.html`. They state: shared recipes are licensed only for reading and "Make it mine" inside the
  Service; ingredients are reference data that staff may publish to the catalog without notice; aggregated usage
  statistics. **Changing what the Grimoire does with recipes/ingredients/analytics means updating these pages
  (and their version/date).**

No `<dialog>` is left in the Grimoire — every view and form is a full page.

The API: API under `/cauldron/api/grimoire/` (`SimpleRouter` — a second
`DefaultRouter` would add a duplicate `api-root` name): `reagents/` (list, `?q=`, `?mine=1`, create,
retrieve), `spells/` (CRUD, `?scope=mine|public`, `?tag=`, `?q=`), `spells/{uuid}/publish|unpublish|fork/`,
`tags/`, `preferences/` (GET/PATCH `unit_system`). Front end: `grimoire.html` +
`static/the_cauldron/js/grimoire.js`, layout in `grimoire-page.css`.

**Spells view**: one list with a scope filter — `?scope=all` (default) | `mine` | `coven` (other users'
public spells; `public` is an alias). Ordering is always **the user's own first**, then newest.
"The Coven" / "El Aquelarre" is the community name in the UI.

**i18n** (follows loom): template strings use `{% trans %}`; JS strings come from
`grimoire_i18n.grimoire_strings()` → `json_script` `#grimoire-i18n` → `t()` in `grimoire.js`. Catalog:
`the_cauldron/locale/es/` (in `LOCALE_PATHS`). The JS shows `name_es` / tag `name_es` / starter `*_es`
when the page language is Spanish, falling back to English per field. Run makemessages from the app dir
(`-w /app/the_cauldron`), ignoring `tests/*` and `seed_data/*`. Unit labels are `pgettext("unit", …)`
with `_plural` keys for spelled-out units (pieza/piezas, taza/tazas).

**The Forge, landing and login (#87)** follow the same pattern:
- Templates use `{% trans %}` / `{% blocktrans trimmed %}`. JS strings: `forge_i18n.forge_strings()` →
  `#forge-i18n` → `t(key, vars)` in `forge.js` (also read by `forge-anatomy.js`). Counted strings ship as
  `<key>_one` / `<key>_other` from `_plural(key, lambda n: ngettext("…", "…", n))` — keep the **literal**
  `ngettext` call or makemessages misses it — and `tn(key, n)` picks the form. Never name a plain key
  `…_one` / `…_other`. Dates/numbers go through `fmtDate` / `fmtNum` (`Intl`, page language).
- Loader captions: `_loader_script.html` (`data-captions`). The landing's skill-tree showcase data lives in
  `forge_i18n.showcase_tree()` → `#fshow-tree-data` (no longer hard-coded in `forge-skilltree.js`).
- Earphones mode speaks/listens in `es-MX` under Spanish; voice commands accept English words always and
  Spanish ones (`VOICE_WORDS`, accent-folded) when the page is Spanish.
- **Catalog**: `name_es` on `MovementPattern` / `Equipment` / `Muscle` / `Exercise`, `cues_es` on `Exercise`,
  seeded from `seed_forge.*_ES`. **`name` stays English** — it is the key norms, progress and `forge.js` match
  on. The page embeds `forge_i18n.forge_catalog()` (`#forge-catalog-i18n`, English name → display text) and
  `exName()` / `exCues()` / `patternName()` / `muscleName()` / `equipName()` translate at display time only.
  The catalog API adds `name_es` + `display_name` (and `cues_es` + `display_cues`) via
  `forge_i18n.localized(obj, field)` — Spanish when active and filled, else English, per field.
- `ProgramDay.name` stays the English `THE_DAY_NAME` (`gettext_noop`); `forge_i18n.day_name()` translates it at
  serialization. Norm labels are `gettext_noop` and translated per request. API `detail` messages are
  gettext'd; `code` fields and PostHog properties stay English.

**Switcher + memory** (`language.py`): arcano's base nav switcher posts to `set_language` (cookie). On every
`cauldron_page` (and the landing / English legal pages) a signed-in user's language cookie is saved to
`CauldronProfile.language` (the cookie always wins); a browser with no cookie gets the profile language
activated and the cookie set — so the choice follows the user to another browser. A page view never creates a
profile row unless there is a choice to save.
**SEO**: `/cauldron/es/`, `/cauldron/es/terms/`, `/cauldron/es/privacy/` always render Spanish
(`force_spanish`); every public page gets a self canonical + `hreflang` en/es/x-default (`_seo_head.html`,
`language.public_seo`), and `lang_switch_next` (read by arcano's `base.html`) sends the switcher to the
same page in the other language. No sitemap exists yet.

### Spanish glossary (es-MX)

| English | Español | | English | Español |
|---|---|---|---|---|
| The Cauldron | El Caldero | | set | serie |
| The Forge | La Forja | | reps | reps (prose: repeticiones) |
| The Trial | La Prueba | | hold (timed) | sostén |
| Anvil | Yunque | | flames / fires 🔥 | llamas |
| The Grimoire | El Grimorio | | Earphones mode | Modo audífonos |
| The Journal | El Diario | | Rest ping | Aviso de descanso |
| The Coven | El Aquelarre | | Claim it | Reclamarlo |
| rung | peldaño | | skill tree | árbol de habilidades |
| ladder | escalera | | AMRAP | AMRAP (kept) |
| spell / reagent | hechizo / reactivo | | band (resistance) | liga |
| dumbbell / kettlebell | mancuerna / pesa rusa | | push-up / pull-up | lagartija / dominada |

The legal pages keep "The Cauldron" — their text is versioned as a whole (bump the version to change it).

**Units**: amounts stay as authored; the per-line weight and the hover hint on non-mass units
(`≈ weight · volume`) use the user's `unit_system` (g/ml/kg/l vs oz/lb/fl oz/cups). Volume is exact for
volume units and `grams / reagent_density` for weighed/counted lines (`reagent_density` on each line).

**Scaling (#69)**: pure maths in `services/grimoire_scaling.py` (no queries, no writes):
`scale_by_factor`, `scale_to_servings`, `scale_to_total_grams`, `scale_by_available` (the smallest
available/required ratio wins and is returned as `limiting`; a reagent on two lines counts both; a 0 g line
can't limit). Zero/negative/NaN input raises `ScalingError` (→ 400); the factor is capped at `MAX_FACTOR`.
Quantities and grams stay exact; only `display_quantity` is rounded (pieces to ½, spoons/cups to ¼, g/ml to
5 above 100). `POST spells/{uuid}/scale/` (any readable spell) takes one `mode`; an "I have" amount in a unit
the reagent can't convert is listed in `unconvertible` and left out, never guessed
(`services.grimoire.grams_on_hand` prefers the line's own weight for the same unit). The spell page's scaler
re-renders the lines from the response; "Cast with these amounts" opens the cast page with `?factor=`.

**Nutrition (#70)**: `services/grimoire_nutrition.py` (no writes). `batch_totals` sums line grams × per-100 g;
an unknown value (null, or no `ReagentNutrients` row) is never 0 — it's left out and counted in `missing`
(distinct reagents), shown as "≥ x". Cooked yield, in order: the **requesting user's** latest casting with
`cooked_weight_g` (as a ratio over `casting_raw_grams` — the batch as actually cast, adjustments included) →
`Spell.cooked_yield_g` → `Spell.yield_pct` → raw weight, `source: "unknown"` (flagged in the UI). Other users'
castings never count. `GET spells/{uuid}/nutrition/?servings=|?scale=` (batch scales, per serving and per 100 g
cooked don't); `POST spells/{uuid}/portion/` → gram range of the cooked dish against what's left of the user's
**daily goals** (#80/#83, see "Daily nutrition" below), or `conflicts` (the goals setting the tightest lower and
upper bound). `reagents_missing_data` names only reagents missing the basics (`CORE`).

**Seed**: `python manage.py seed_grimoire` (idempotent; also run at container start) loads
`seed_data/grimoire_reagents.json` (66 USDA SR Legacy reagents with `fdc_id`, public domain),
`seed_data/grimoire_staff_reagents.json` (staff-entered products USDA lacks, from their labels — e.g. Mexican
Coca-Cola, 1 `glass` = 250 ml; `source=staff`, approved, keyed by English name, owned by the system user;
values not on the label stay null) and
`seed_data/grimoire_spells.json` (13 public spells owned by the inactive system user
`the-grimoire`, email `the-grimoire@system.invalid` — `auth_user` has a unique email). Rebuild the
reagent file from a USDA download with `build_grimoire_reagents <sr_legacy.json>` (see its docstring).
**Added sugars** in that build: USDA nutrient 539 where listed (SR Legacy never lists it), else **0** for a food in
`UNPROCESSED_CATEGORIES` whose description doesn't match `SWEETENED` (sweetened/syrup/honey/dressing/fruit
yogurt/peanut butter…) or listed in `NO_ADDED_SUGAR` (water, plain oats, cocoa, baking powder); Sugar and Honey
(`ALL_ADDED_SUGAR`) get added = total sugars; else unknown (Peanut butter, Vegetable broth, Vanilla extract). A nutrient added to the
seed later is listed in `seed_grimoire.BACKFILL`: every run fills it on existing rows **where still null** (no
`--refresh` needed; a staff value is never overwritten). User-created reagents: unknown unless entered.
Intake entries logged before #104 were marked `added_sugar_g` missing (in `missing` and `basis.missing`) by
migration 0033 — unknown, never 0.

**Debugging**
- A line won't save with "Can't convert…" → the reagent has no weight for that unit and no density;
  use g or add `grams_per_unit`.
- Another user can't find a reagent → it's not `approved` yet.
- `seed_grimoire` IntegrityError on `auth_user_email_unique` → the system user must keep its own email.

---

## Daily nutrition — goals, meal log, adherence, planner (#85)

Third part of The Cauldron (#80–#84). Models in `models/nutrition.py`; rules in `services/legal.py`,
`services/nutrition.py`, `services/adherence.py`; API in `nutrition_api.py`; one template
`nutrition.html` (page = `accept` | `profile` | `journal`) + `static/the_cauldron/js/nutrition.js` +
`css/nutrition.css`; JS strings in `nutrition_i18n.py` (reuses the Grimoire's `n_*`/`grp_*`/`unit_*`).

| Model | Notes |
|---|---|
| `LegalAcceptance` | `user`, `document` terms/privacy/sensitive_consent, `version`, `accepted_at`, `withdrawn_at`. **Append-only**: `save()` on an existing row raises; the only change ever made is `withdrawn_at` via `legal.withdraw_consent` (queryset update). No IP/UA. Read-only admin (ARCO) |
| `CauldronProfile` | 1:1 user, `timezone` (IANA, default `America/Mexico_City`). A day = midnight–midnight there. `language` ("" = never chosen; see "Switcher + memory", #87). Body data (#105): `birth_year`, `sex` (male/female/undisclosed), `weight_kg` (20–400), `activity_level` (sedentary/moderate/vigorous, "" = unknown); `age(today)` by birth year. Lazy (`nutrition.get_profile`) |
| `NutrientGoal` | per nutrient, `kind` at_least/at_most/between, `min_value`/`max_value`, active on `valid_from ≤ day < valid_to`. **Versioned**: a change closes the active row (`valid_to = today`) and opens a new one; no row = no goal. Unique active row per (user, nutrient). A row with `valid_to == valid_from` (changed twice in a day) was never active and is skipped |
| `IntakeEntry` | `eaten_at` (aware) + `local_date` (fixed at log time in the user's tz), `source` spell/reagent/quick, `basis` (per-100 g snapshot + `serving_g`), `nutrients` (known totals), `missing` (per-nutrient count). Changing the amount rescales `basis` — editing the spell later never rewrites history |
| `PlannedMeal` | `date`, `slot`, `spell` (SET_NULL) + `spell_title` snapshot, `quantity`/`unit` (serving/g), `intake_entry` (1:1, set by `eat/`; deleting the entry re-plans it) |

**Legal (#84)** — `legal.CURRENT_VERSIONS` is the single source (terms 1.3, privacy 1.4, consent 1.1 since #105); the
legal pages state the same versions. `views.cauldron_page` (login + gate) wraps every Cauldron page: a user
whose latest terms/privacy version isn't current goes to `the_cauldron:legal-accept` (`/cauldron/accept/?next=`,
`next` checked with `url_has_allowed_host_and_scheme`). The gate covers **pages only** — Forge/Grimoire APIs
are not gated. Goals/intake/plan/adherence APIs need `NutritionConsent` (current terms+privacy **and** a live
sensitive-data consent) → else 403 `{"code": "consent_required"}`. Bumping a version re-prompts everyone.
Tests that load Cauldron pages must `legal.accept(user, legal.GATED)` first.

**Rules** (`services/nutrition.py`)
- Unknown is never 0: totals carry `missing`; shown "≥ x".
- `judge(goal, total, final=)` → met / missed / uncertain (missing data could flip it). With `final=False`
  (today): an exceeded max is `missed`, a reached min with no max is `met`, anything else `pending`.
- Remaining-budget portion: `POST grimoire/spells/{uuid}/portion/` `{date?}` ignores any body goals and uses
  the goals active that day minus what's eaten **and** still planned (`consumed_and_planned`). A remaining
  minimum the dish can't bring is `not_covered` (not a conflict); a used-up max the dish contains is `over`.
  `{"has_goals": false}` without goals or consent → the spell page hides the section.

**Recommended goals (#105)** — `services/recommendations.py`, computed on read from `CauldronProfile`, never
stored or versioned (a profile change re-judges the whole history). `Recommendation` is shaped like a
`NutrientGoal` (`kind`/`min_value`/`max_value`) so `nutrition.bounds`/`judge` work on it, plus `source`/`note`.
Energy = FAO/WHO/UNU 2004 BMR × PAL (1.53 / 1.76 / 2.25) ±10 %; protein 0.83 g/kg; fat 20–30 %, sat < 10 %,
trans < 1 %, carbs 45–65 %, added sugars < 10 % of that energy; sodium < 2000 mg, potassium ≥ 3510 mg, fiber ≥ 25 g;
`DRI` table (RDA/AI) for calcium, iron, vitamins A/C/D/B12, ALA, LA. `NO_RECOMMENDATION` (mono/poly fat,
cholesterol, EPA, DHA, total sugars) carries a note. Unknowns: every candidate (both sexes × `CANDIDATE_AGES`)
is computed and combined — largest minimum, smallest maximum; an emptied range is left out (`unresolved`);
unknown activity = sedentary; no weight → `WEIGHT_BASED` goals left out (`needs_weight`); under 18 by the
youngest age the birth year allows (year difference − 1) → `status: "minor"`, nothing. `GET goals/` adds `recommended` (`payload`); `body/` (GET/PATCH) edits the body
data — birth year/sex need only a sign-in; adding weight/activity needs the consent (403 `consent_required`),
clearing them never does. The Journal's gauges show my goals plus `WHO_HEADLINE` recommended ones (nutrition.js);
every tracked nutrient stays in the chips.
`legal.withdraw_consent(delete_data=True)` clears weight and activity (birth year/sex stay for the Forge).
Every page showing them includes `_recommendation_notice.html` (not medical advice, adults only).
PostHog: `profile_body_data_saved {fields_count}` only.

**Goal cauldron (#107)** — clicking a Journal gauge selects it for the chart **and** opens `#nut-cauldron`
(a `<dialog>`, nutrition.js `openCauldron`) for that nutrient, `roll.span` and the tick at `roll.index`.
`GET rolling/breakdown/?span=&end=&nutrient=` (`services/breakdown.py`, consent) groups the window's entries
(rolling's rule: `end - span < eaten_at <= end`) by spell / reagent / quick-entry name (label = latest snapshot),
top `TOP` (5) by known amount + an "others" row (`pie`; amounts ÷ 7 for 7d, summing to the card's value) and
`fog` (every group with an entry missing the nutrient — never sized, never 0; a partly known spell is in both).
Each fog item says how to fill it: `fill.reagents` (the reagent, or the spell's reagents lacking it, each with
`editable`) or `fill.quick` (its entries). `POST rolling/fill/ {nutrient, value, reagent|entry}`: an own editable
reagent is saved; a catalog one gets the user's **private copy** (`Reagent.copy_of`, reused, `nutrients_estimated`)
and the user's own spells' lines switch to it (others' spells untouched); a quick entry gets its own total. Then
`refresh_entries` gap-fills **only that nutrient, only where it was missing**, in the user's entries of the last
`REFRESH_DAYS` (7) that used the reagent (directly, or via a spell still containing it, recomputed from the spell's
current recipe and the entry's grams; `basis.estimated = True`). Older entries and known values never change — a
deliberate, narrow exception to "editing the spell later never rewrites history". The pot is inline SVG (pie on the
potion, fog, embers, bubbles, steam text, status glow via `data-status`); all motion stops under reduced motion.
PostHog: `goal_breakdown_opened {span}`, `missing_value_filled {source: reagent|reagent_copy|quick}` only.

**Adherence (#82)** — computed on read, `?days=7|30`. % met = met / (met+missed+uncertain); not-logged days
never count. Streaks: missed resets; uncertain/pending/not-logged/no-goal skip. Streaks look back 90 days.

**Rolling window + time in range** (`services/rolling.py`, `GET rolling/?span=24h|7d&days=7|30|90`) — the
Journal's "How you're doing" and "How often you're in range". A tick = a window end, hourly (3-hourly at 90
days), plus "now"; its window holds entries with `end - span < eaten_at ≤ end` (7d is divided by 7 → daily
average). Each tick is judged **final** against the goals of its local date; an empty window is `not_logged`
and never counts. Response: `ticks`, `logged`, per-nutrient `values`/`missing` (all 24), `tracked` (union of both
sets) and `sets: {mine, who}` (#105) — each with per-goal-nutrient `status`/`low`/`high` arrays, `summary` (time in
range %) and `daily` (share of each day in range); `who` (the recommendations, the same at every tick) adds
`status_reason` (`adult`/`minor`). The Journal draws my band filled and the recommended one dashed, both ranges
and status dots on each gauge, and two percentages per nutrient. The page
scrubs and charts these client-side with no further requests (`nutrition.js` → `roll`). The calendar-day
`adherence/` endpoint (#82) stays but no page uses it now.

**API** (`/cauldron/api/`): `legal/`, `legal/accept/` `{documents}`, `legal/withdraw/` `{delete_data}`,
`me/` (GET/PATCH timezone), `goals/` (GET/PUT full set), `goals/history/`, `intake/` (CRUD, `?date=`),
`intake/day/?date=`, `adherence/?days=`, `plan/` (CRUD, `?from=&to=` ≤ 62 days), `plan/day/?date=`,
`plan/{uuid}/eat/`. Naive `eaten_at` is read in the user's timezone (`UserTimezoneMixin`).

**Pages**: `the_cauldron:profile` (goals, timezone, withdraw consent), `the_cauldron:journal`
(`?date=`, `?log_spell=` / `?plan_spell=` preselect a readable spell; the spell page's "I ate this" /
"Plan it" link here). The Journal's entry form is one search over spells **and** reagents (the pick sets
`source`); "type it in by hand" (`quick`) is the last search row, not an up-front choice. A picked reagent
offers its own measures first (`grams_per_unit`, e.g. `glass`) as the default unit. A floating "Log a meal"
button (`#nut-fab`) jumps to the form. `rolling/` ticks are on the user's clock (`-06:00`), since the page
reads date/hour straight off the string. The Profile and the Journal show the consent panel instead of the app until consent is given.

**PostHog**: only counts/enums (`intake_logged {source}`, `plan_meal_added {slot}`, `nutrition_goals_saved
{goal_count, changed_count}`, `legal_accepted {document, version}`…) — never values, foods or goals.

---

## MCP server — drive The Cauldron from an AI assistant (#89)

Complete: OAuth 2.1, nutrition/Grimoire/Forge read and write tools, two-step confirmation, idempotency,
`channel`, estimated nutrients, the info page, analytics and the legal update (terms 1.2 / privacy 1.3).
User docs, analytics events, the dashboard definition and how to add a tool: `README.md` → "MCP".

**Serving**: a plain Django view (`mcp_server/protocol.py::McpView`, `the_cauldron:mcp` → `/cauldron/mcp/`),
Streamable HTTP, **stateless** (no `MCP-Session-Id`), JSON responses only, **dual-era**: spec **2026-07-28**
(`MCP-Protocol-Version: 2026-07-28` — no handshake, version in `params._meta`, `Mcp-Method`/`Mcp-Name` headers must
match the body → else 400 `HeaderMismatch` -32020; `server/discover`; results carry `resultType: "complete"` and
`_meta["io.modelcontextprotocol/serverInfo"]`; `tools/list` adds `ttlMs`/`cacheScope`; unknown method → 404) and the
legacy `initialize` versions 2025-11-25 / 2025-06-18 / 2025-03-26 (batches included). An unknown version → 400
`UnsupportedProtocolVersionError` (-32022, `data.supported`) — Claude speaks 2026-07-28 first and needs this to
work out what we support. No ASGI, no MCP SDK — gunicorn/WSGI unchanged. `GET`/`DELETE` → 405,
**except** a `GET` whose `Accept` has `text/html` and not `text/event-stream` (a browser): it renders the public
info page (`oauth_views.mcp_info_view`, `mcp_info.html` + `mcp-info.css` / `mcp-info.js`), `Vary: Accept`. So the
URL users paste into Claude is also the page explaining it. Its buttons post `{target}` to
`api/mcp/connect-clicked/` (AllowAny, enum only). Nav: `_assistant_link.html` (✦ icon beside the profile icon),
footer `_footer_links.html`, landing hero, Profile → "Your AI assistants".
Present `Origin` must be ours or `CAULDRON_MCP_ALLOWED_ORIGINS` (default claude.ai/claude.com) → else 403.
Cookies are never read: bearer tokens only.

**Auth**: django-oauth-toolkit 3.4 (settings `OAUTH2_PROVIDER` in arcano): authorization code + S256 PKCE only,
public clients via DCR (`/cauldron/oauth/register/`) or CIMD, `localhost`/`127.0.0.1` loopback on any port
(Claude Code), refresh tokens rotate (reuse → `invalid_grant`), access tokens 1 h, refresh 60 days. Scopes
`cauldron:read` / `cauldron:write` (write implies read). Routes live in `oauth_urls.py` (namespace
`oauth2_provider`, included at the **site root** by arcano, guarded with `find_spec`): `/.well-known/oauth-authorization-server`
(issuer = site root), `/.well-known/oauth-protected-resource[/cauldron/mcp/]` (`resource` = the MCP URL exactly).
No token / bad token / token bound (RFC 8707) to another resource → **401** + `WWW-Authenticate: Bearer resource_metadata=…`.
Missing scope → **403** `error="insufficient_scope"`.

**Consent screen**: `oauth_views.CauldronAuthorizationView` (`oauth_authorize.html`) behind `cauldron_page`
(login + legal gate); shows the self-declared client name, the redirect host, and a warning for loopback.
**The name "Claude" is reserved** (`client_identity.py`): a client name that says or imitates Claude/Anthropic
(case, accents, full-width, Cyrillic and digit look-alikes folded) is allowed only for a CIMD client whose
`client_id` URL is on claude.ai / claude.com / anthropic.com. DCR register/rename refuses it (400
`invalid_client_metadata`, `GuardedRegistrationView`); the consent screen answers 403 for any other app using it
(before DOT can auto-approve). `McpConnection.client` is `claude`/`claude_code` only for those Anthropic clients.
Approving creates `McpConnection` (user, application, normalized `client` enum, `language`, `last_used_at`).
Profile → "Your AI assistants": `GET api/mcp/connections/`, `DELETE api/mcp/connections/{uuid}/` deletes the
client's tokens + grants for the user (next call 401).

**Tools** (`mcp_server/tools/`): thin adapters over `services/` and `nutrition_payloads.py` (shared with the REST
API — never duplicate rules in a tool). Register with `@registry.tool(...)`; every call goes through
`registry.call_tool` (scope → JSON Schema → consent → `translation.override(conn.language)` +
`timezone.override(user tz)` → result). Errors are tool results with `isError: true` and `{"code", "message", …}`:
`invalid_arguments`, `invalid_input`, `not_found` (also for other users' data), `consent_required` (+ `accept_url`:
`/cauldron/accept/` when terms are pending, else the Journal's consent panel), `internal_error` (logged, never leaked).
Another user's spell text goes in `untrusted_text` (the server `instructions` say it's data).
Read: `get_today`, `get_journal` (≤ 31 days), `get_adherence` (7/30/90), `get_goals`, `get_goal_breakdown`
(#107, same payload as `rolling/breakdown/`), `get_meal_plan`,
`search_spells` (`have_reagents` → `grimoire.spells_using`), `get_spell`, `search_reagents`, `get_reagent`.
Write (`cauldron:write`; ✱ = two-step): `log_meal`, `update_meal`✱, `delete_meal`✱, `plan_meal`,
`update_planned_meal`✱, `delete_planned_meal`✱, `mark_planned_meal_eaten`, `set_goal`, `create_reagent`,
`update_reagent`✱, `create_spell`, `update_spell`✱, `delete_spell`✱, `fork_spell`, `log_casting`.
Writes reuse the web code: `nutrition_writes.py` (`apply_entry`, `apply_plan`, `eat_plan` — moved out of
`nutrition_api.py`, shared with the REST API) and the Grimoire serializers (`request.user` = the token's user,
set by `McpView`). Errors add `invalid_input` + `fields` (serializer errors), `conflict` (plan already eaten),
`forbidden` (not the owner / approved reagent), `duplicate` (+ `matches`), `invalid_confirm_token`.

**Two-step** (`@tool(confirm=True)`, every edit and delete): the handler gets `apply=False` and returns
`{"target", "preview"}`; the caller gets the preview + a `confirm_token` (random; only its SHA-256 is stored in
`McpConfirmation`, bound to user + tool + target + arguments hash, `TTL_SECONDS` = 300). The second call with the
same arguments + token claims it atomically (single use) and runs `apply=True`; a failure there rolls the claim
back. Holds even if the user "always allows" the tool in their client.
**Idempotency**: every write tool accepts `client_request_id`; a successful result is stored in `McpIdempotency`
(user + tool + id) and a repeat within `WINDOW_SECONDS` (10 min) returns it unchanged. Errors aren't stored.
**Search before create**: `create_reagent` (`grimoire.exact_duplicates`, name and name_es) and `create_spell`
(`grimoire.exact_spell_duplicates`, over readable spells) return `duplicate` + matches and create nothing unless
`confirm_new: true`. Everything created via MCP is **private** (publishing stays web-only).
**`channel`** (`web` default / `mcp`, `models.forge.Channel`, not editable) on `IntakeEntry`, `PlannedMeal`,
`NutrientGoal`, `Spell`, `Reagent`, `Casting`, `WorkoutSession`, `AssessmentSession` — set by the MCP layer
(`channel=` on `apply_entry` / `apply_plan` / `eat_plan` / `save_goal_set`, serializer `save(channel=…)`).
**Estimated nutrients**: `Reagent.nutrients_estimated` (set only by MCP, read-only in `ReagentSerializer`) and
`IntakeEntry.basis["estimated"]` (quick entries; spell/reagent snapshots copy it from estimated reagents).
UI badges: "estimated nutrition" on spells (`has_estimated_nutrition`) and reagents, "estimated" on log entries.

**Forge tools** (`tools/forge.py`, `tools/forge_writes.py`; no nutrition consent needed):
read `get_forge_today`, `get_forge_program`, `get_forge_progress`, `get_forge_sessions` (≤ 62 days),
`get_equipment`, `get_trial`; write `set_equipment`✱, `submit_trial_results`, `retake_trial`✱,
`start_forge_session`, `log_forge_set`, `update_forge_set`✱, `finish_forge_session`, `delete_forge_session`✱;
#109: read `get_forge_rung_options`, write `set_forge_rung` (`set_rung(confirm_unready=True)`: no Trial warning),
`set_forge_load` (same contract as set-load), `set_forge_weekly_volume` — none two-step.
They call the same `services/forge.py` functions as the web, so placement, programs and progression match for the
same inputs. Logic that lived in `api.py` views moved there: `submit_assessment` (+ `TrialInputError`),
`progress_points`, `session_peer_scores`, `active_program`, `mark_equipment_configured`; new `trial_anchors` and
`pending_unfinished` (read-only, unlike `unfinished_session`). `create_session_from_plan` / `retake_assessment`
take `channel=`. `get_forge_today` states the day as `no_program` | `unfinished_past_session` | `in_progress` |
`done` | `not_started` and never writes (the plan has `id: null` sets until `start_forge_session`). The
unfinished-session rule holds: `start_forge_session` → `unfinished_session` error until that past session is
finished (`finish_forge_session`, keeps its own date) or deleted. `log_forge_set` refuses an already-logged set
(`already_logged` → `update_forge_set`); finished sessions are read-only (`conflict`). `get_today` adds a `forge`
summary.

**PostHog**: `mcp_tool_called {tool, ok, error_code, latency_ms, client, confirm_step}` (`preview`/`applied`
for two-step tools), `mcp_duplicate_prevented {kind, overridden}`, `mcp_connected {client}`,
`mcp_disconnected {client}`, `mcp_page_viewed {signed_in}`, `mcp_connect_clicked {target}` — distinct id = user pk
(`posthog.identify_context`), arcano opt-out honoured. A new event or property → update the privacy notice too.

**Legal**: terms §7 "AI assistants" and the privacy notice (assistant data, data reaching the user's AI provider,
the MCP analytics) were added in terms 1.2 / privacy 1.3 — everyone re-accepts once. Changing what the MCP server
stores or sends means another bump.

**Evals**: `tests/mcp_eval_prompts.md` — run by hand against Claude after changing tool names/descriptions/schemas.

**Debugging**
- Claude says "Couldn't reach the MCP server" → `/.well-known/oauth-protected-resource/cauldron/mcp/` must
  answer and its `resource` must equal the URL pasted (trailing slash included); behind Render the scheme
  comes from `SECURE_PROXY_SSL_HEADER`.
- Every call 401 after connecting → token `resource` doesn't match `mcp_resource_url` (host/scheme mismatch).
- `makemigrations` fails with `OAUTH2_PROVIDER_APPLICATION_MODEL` → keep that setting in arcano.
- Claude connects but says it has no tools → its requests never pass `McpView.post` (connection `last_used_at`
  stays null). Read `McpRequestLog` in the admin (every request to the endpoint, rejections and crashes
  included). `(GET) 405` rows carrying `Mcp-Method` = a redirect turned the client's POST into a tokenless GET
  (the 2026-10 case: APPEND_SLASH on `/cauldron/mcp` — keep the no-slash route). A 403 "Origin not allowed" →
  add the assistant's origin to `DEFAULT_ALLOWED_ORIGINS`. An unknown version → add the MCP revision to
  `MODERN_VERSIONS` / `LEGACY_VERSIONS` (`protocol.py`); `tests/test_mcp_modern.py` covers 2026-07-28.
- Connecting from Claude ends on "This connection can't continue / invalid_request" → DOT refused the client's CIMD
  document. Claude's lists the jwt-bearer grant; `oauth_cimd.CauldronMetadataFetcher` (arcano setting
  `CIMD_METADATA_FETCHER`) drops grant types we never issue — keep it wired.
- `/cauldron/mcp/` shows 405 in a browser → the browser didn't send `text/html` in `Accept` (or a proxy stripped it).
- Info-page tests fail with "Missing staticfiles manifest entry" → run `collectstatic` (manifest storage).
- Connecting shows "This app can't connect" → the client calls itself Claude but isn't a claude.ai/claude.com/
  anthropic.com CIMD client (`client_identity.py`); a DCR registration with that name gets 400.
