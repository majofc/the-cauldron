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
| `is_per_side` | if True, worked one side at a time — rep targets forced even (`progression.rep_targets_for`), to-failure set logged L/R |
| `required_equipment` | M2M Equipment |
| `is_assessment_anchor` | used during trial |
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
When completed: spawns a `Program`.

**AssessmentResult** — one pattern's result in a session
Fields: `session`, `pattern`, `tested_exercise`, `placed_exercise`, `reps_or_seconds`, `left_reps`, `right_reps` (unilateral).
Unique: `(session, pattern)`.

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
For per-side moves the AMRAP set is logged per side: `left_reps`/`right_reps` are stored and `actual_reps` holds the weaker side (the min), mirroring `AssessmentResult`. `SetLogSerializer.is_unilateral` (read from `Exercise.is_per_side`) tells the client to render the split. Only the AMRAP set splits — except per-side *timed* holds, where every set does.

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
- **Assessment:** POST `/cauldron/api/assessment/start/`, GET/POST per result, POST `/cauldron/api/assessment/complete/`
- **Program:** GET `/cauldron/api/program/` (active), `/cauldron/api/program/days/{id}/`
- **Session logging:** POST `/cauldron/api/sessions/`, PATCH `/cauldron/api/sessions/{id}/`, POST `/cauldron/api/sessions/{id}/sets/`
- **Progression:** POST `/cauldron/api/prescribed/{id}/apply-progression/`

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
