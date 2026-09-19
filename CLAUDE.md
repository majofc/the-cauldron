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
| `is_per_side` | if True, worked one side at a time — rep targets forced even (`progression.rep_targets_for`). Workout logging records ONE value per set meaning "reps per side"; it does **not** split L/R |
| `required_equipment` | M2M Equipment — **all of** these must be owned |
| `alternative_equipment` | M2M Equipment — **at least one of** these must be owned (empty = no constraint). Lets one rung read "a bar OR rings" without duplicating it per implement; `forge.is_performable` applies both rules |
| `variant_group` | slug shared by rungs occupying the SAME ladder position (`bar_pullup` = Pull-up + Chin-up). They collapse into one ladder node, count as one chain, and `grip` is only the label telling them apart |
| `is_assessment_anchor` | the bilateral move tested per pattern in the Trial: Push-up, Australian Row, Pike Push-up, Squat, Plank, Glute Bridge — each has a peer norm. **Grip carries two**: Dead Hang, plus Towel Wring Hold as the no-equipment fallback. `forge._anchor_for` / `forge.js:renderTrial` take the highest-rank anchor the user can perform, so the fallback only shows when the hang is out of reach |
| `placement_threshold` | Trial **anchor** AMRAP score that places here. Must be non-decreasing by rank within a pattern, equal for same-rank rungs — enforced for **every** pattern by `tests/test_grip_pattern.py::TestThresholdMonotonicity` (the lower ladder alone was covered by `tests/test_lower_ladder.py`). Among same-rank rungs the user owns, placement still follows row order |
| `regression` / `progression` | FK to self, adjacent rungs |
| `video_url`, `cues`, `rest_seconds` | coaching metadata. `rest_seconds` comes from `seed_forge.rest_for`, which is pattern-aware: a grip hold rests 90s, every other timed hold 40s |

---

### Per-user data

**UserEquipmentProfile** (OneToOne with User)
What the user has available + their body stats.
Fields: `equipment` M2M, `birth_year`, `sex`, `dumbbell_weights` JSON list, `band_levels`, `barbell_min_increment`, `barbell_plates`, `load_unit`.

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
Fields: `user`, `is_active`, `source_assessment` FK, `split` (full_body_3x/upper_lower_4x), `weekly_volume_target` (default 8 sets/pattern/week).

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
