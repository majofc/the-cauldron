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

**MovementPattern** — the 6 training patterns
```
horizontal_push, vertical_pull, vertical_push,
lower_unilateral, core_anti_extension, hinge
```
Fields: `key` (unique slug), `name`, `primary_muscles`, `is_lower_body`.

**Equipment**
Fields: `key` (unique), `name`, `is_loadable`, `load_unit` (none/kg/lb/band_level).
Examples: `bodyweight`, `pullup_bar`, `dumbbells`, `barbell`, `bands`, `rings`.

**Exercise** — one rung on a difficulty ladder
| Field | Notes |
|---|---|
| `pattern` FK | which movement pattern |
| `name` | e.g. "Pike Push-up" |
| `difficulty_rank` | integer order within pattern's ladder |
| `progression_mode` | `difficulty` (ladder) or `load` (weight progression) |
| `rep_range_min/max` | working rep range |
| `is_timed` | if True, reps = seconds (holds) |
| `is_per_side` | if True, worked one side at a time — rep targets forced even (`progression.rep_targets_for`). Workout logging records ONE value per set meaning "reps per side"; it does **not** split L/R |
| `required_equipment` | M2M Equipment |
| `is_assessment_anchor` | used during trial |
| `measures_asymmetry` | Trial captures L/R separately for this move and tracks signed asymmetry. True for exactly 3 anchors (see below). Independent of `is_per_side` |
| `placement_threshold` | AMRAP reps that place here |
| `regression` / `progression` | FK to self, adjacent rungs |
| `video_url`, `cues`, `rest_seconds` | coaching metadata |

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
Fields: `session`, `pattern`, `tested_exercise`, `placed_exercise`, `reps_or_seconds`,
`left_reps`, `right_reps`, `asymmetry_pct`.
Unique: `(session, pattern)`.

`asymmetry_pct` is signed, **right-stronger positive**:
`round((right - left) / max(left, right) * 100)`, null when a side is missing or both are 0.
Stored, not computed on read. Use `AssessmentResult.compute_asymmetry_pct(left, right)` —
`forge.js` mirrors it exactly in `asymmetryPct()`.

### Limb measurement — where it lives

Per-limb data is captured in the **Trial only**, on the three `measures_asymmetry` anchors:

| Pattern | Anchor | Covers |
|---|---|---|
| `horizontal_push` | Incline Archer Push-up | arms |
| `vertical_pull` | Single-Arm Australian Row | arms |
| `hinge` | Single-Leg Glute Bridge | legs |

Placement still uses the **weaker** side (`min(left, right)`) — the signed percentage is a
tracked metric, never an input to placement.

The `lower_unilateral` anchor (Split Squat) is `is_per_side` but **not** an asymmetry anchor:
it takes a single "reps per side" box. That is why the Trial's L/R split is driven by
`measures_asymmetry` and not by `is_unilateral` — don't conflate them.

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
Fields: `session`, `prescribed_exercise`, `exercise` (may differ if mid-session regression), `set_index`, `expected_reps/load`, `actual_reps/load`, `left_reps`, `right_reps`, `is_amrap`, `rir`.

Workout logging records **one input per set**. For a per-side movement that value means
*reps per side* — including per-side timed holds. Only the third (last) set is to-failure.

`left_reps` / `right_reps` are **read-only historical fields**: nothing writes them any more
(limb measurement moved to the Trial), but rows written before that change keep their values
and must still render. There was no backfill. `apply_session_log` still *accepts* them so an
older cached client doesn't break; `SetLogSerializer.is_unilateral` is now only a "per side"
label hint, not a render-the-split instruction.

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
    "reps_or_seconds", "left_reps"?, "right_reps"?}, …]}`.
    Returns `{assessment, program, peer, asymmetry}`. There is no `start/` or `complete/`
    endpoint — one POST does both.
  - `POST /cauldron/api/assessment/retake/` — deactivate current session + program, open a
    fresh session (full reassessment; there is no lightweight check-in mode)
  - `GET /cauldron/api/assessment/history/` — per-pattern Trial series for the Evolution
    charts: `{patterns: [{pattern_key, pattern_name, measures_asymmetry, points: [...]}]}`.
    Each point: `date, exercise, reps_or_seconds, is_timed, left_reps, right_reps,
    asymmetry_pct, ladder_score, delta_vs_prev, verdict`
  - `POST /cauldron/api/assessment/reminder/dismiss/` — suppress the retest nudge 3 days
- **Program:** GET `/cauldron/api/program/` (active). There is no `program/days/{id}/` route —
  days are nested inside the program payload.
- **Today:** `GET /cauldron/api/today/?day=<index>` — opens (creates) a WorkoutSession for
  that day and returns it, **plus** `retest_due`, `last_trial_at`, `days_since_last_trial`
- **Session logging:** `GET /cauldron/api/sessions/`, `POST /cauldron/api/sessions/{uuid}/log/`
  with `{"sets": {"<setlog-uuid>": {"actual_reps", "actual_load", "rir"}}}`
- **Progression:** `POST /cauldron/api/progression/{presc_uuid}/{accept|deny}/`

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
- Equipment filter excludes an exercise the user expects → `required_equipment` M2M must contain only equipment the user has; any mismatch excludes the exercise.
- `BlockedExercise` uniqueness error → user already has that exercise blocked; do a GET first.
- Trial row shows one box when you expected Left/Right (or vice versa) → the split is driven by
  `Exercise.measures_asymmetry`, **not** `is_unilateral`/`is_per_side`. Check the seed's
  `ASYMMETRY_ANCHORS`.
- Retest banner won't appear → `retest_status` keys off the last **completed** assessment and is
  suppressed for 3 days after a dismissal *or a retake*. An open, incomplete session does not
  reset the 30-day clock.
- Evolution chart reports a setback after a rung promotion → `ladder_score` (not raw reps) is the
  comparison basis; check `placed_exercise` is set on the result, since a null placement scores 0.
