"""Strings for forge.js and the Forge's catalog names, translated server-side
(the Grimoire's pattern, see grimoire_i18n.py).

``forge_strings()`` runs inside the request, after LocaleMiddleware has
activated the user's language, and reaches the page through ``json_script``.
Placeholders use ``%(name)s`` and are filled by ``t()`` in forge.js. Plurals
ship as ``<key>_one`` / ``<key>_other`` (ngettext at n=1 and n=2), picked by
``tn()``. Glossary: the_cauldron/CLAUDE.md → "Spanish glossary".

``forge_catalog()`` maps the catalog's English names — the identifiers the API
and forge.js key on — to their display name in the active language
(``name_es`` / ``cues_es``, falling back to English per field).
"""

from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.utils.translation import ngettext, pgettext

from .models import Equipment, Exercise, MovementPattern, Muscle
from .services.forge import THE_DAY_NAME


def _plural(key, forms):
    """Both forms of a counted string, for ``tn()`` in forge.js. ``forms`` is
    ``lambda n: ngettext("…", "…", n)`` — the literal call keeps makemessages
    able to see the strings."""
    return {f"{key}_one": forms(1), f"{key}_other": forms(2)}


def forge_strings():
    strings = {
        # Errors / notices
        "err_generic": _("Something went wrong."),
        "err_progression": _("Couldn't update progression."),
        "err_load_equipment": _("Couldn't load equipment."),
        "err_parse_weights": _("Check your weights — something didn't parse."),
        "err_save_equipment": _("Couldn't save equipment."),
        "err_load_trial": _("Couldn't load the Trial."),
        "equipment_saved": _("Equipment saved. Now take the Trial."),
        # Rest ping
        "chime_cauldron": _("Cauldron Chime"),
        "chime_ember": _("Ember Bell"),
        "chime_gong": _("Deep Gong"),
        "chime_triad": _("Arcane Triad"),
        # Timers
        "sw_start": _("▶ Start"),
        "sw_stop": _("⏸ Stop"),
        "rested": _("✓ Rested"),
        "rest_m_s": pgettext("rest time", "%(m)sm %(s)ss"),
        "rest_m": pgettext("rest time", "%(m)s min"),
        "rest_s": pgettext("rest time", "%(s)ss"),
        # Peer score ("fires")
        "flames_aria": _("%(n)s out of 10 flames"),
        "no_peer_data": _("No peer data yet."),
        "score_est_title": _("Estimated benchmark — not yet from published data"),
        "score_est": _("≈ est."),
        "score_approx_title": _("Interpolated from published percentiles"),
        "score_approx": _("≈ approx"),
        "score_decile": _("top %(pct)s · decile %(decile)s/10"),
        "score_vs_peers": _("%(n)s/10 🔥 vs peers your age & sex"),
        "score_norms": _("%(label)s norms"),
        "verdict": _("The verdict"),
        "other_movements": _("Other movements (%(n)s)"),
        "peer_hint_rating": _("Add your birth year & sex in Equipment to unlock your 🔥 peer rating."),
        "peer_hint_flames": _("Add birth year & sex in Equipment to unlock your peer flames."),
        "continue": _("Continue"),
        # Unlocks
        "unlock_title": _("New power unlocked!"),
        "unlock_text": _("You've mastered %(from)s. The path to %(to)s is open — take it?"),
        "claim_it": _("Claim it 🔥"),
        "not_yet": _("Not yet"),
        "advanced_to": _("Advanced to %(name)s!"),
        "holding_level": _("Holding at your current level."),
        # Session report (the anvil)
        "report_unlocks": _("New paths unlocked"),
        "unlocked_from": _("unlocked from %(name)s"),
        "report_flames": _("Your flames"),
        "session_forged": _("Session forged"),
        "anvil_eyebrow": _("Day complete"),
        "anvil_title": _("Forged."),
        "anvil_sub": _("Every rep on today's list is struck and tempered. Rest — the anvil keeps its heat."),
        "anvil_cta": _("Bank the day"),
        # Equipment
        "plates_format": _('%(label)s: write "%(chunk)s" as weight x how many, e.g. 2.5x8.'),
        "plates_weight": _('%(label)s: "%(chunk)s" needs a weight above zero.'),
        "plates_count": _('%(label)s: "%(chunk)s" needs a count above zero.'),
        "weights_nan": _('%(label)s: "%(chunk)s" isn\'t a number.'),
        "weights_positive": _('%(label)s: "%(chunk)s" must be above zero.'),
        "unit_switch_confirm": _(
            "Switching to %(unit)s clears the weights and plates you've entered — "
            "they'd no longer match anything you own. Continue?"
        ),
        "orphans": _("Unused — not enough to make a set: %(list)s."),
        "recipe_bare": _("just the bare bar / empty handle"),
        "recipe_in_bell": _("%(plates)s in the bell"),
        "recipe_per_side": _("%(plates)s per side"),
        "lbl_dumbbell_weights": _("Dumbbell weights"),
        "lbl_dumbbell_plates": _("Dumbbell plates"),
        "lbl_barbell_plates": _("Barbell plates"),
        "lbl_kettlebell_weights": _("Kettlebell weights"),
        "lbl_kettlebell_plates": _("Kettlebell plates"),
        # Units
        "unit_reps": _("reps"),
        "unit_seconds": _("seconds"),
        "unit_s": pgettext("seconds, abbreviated", "s"),
        "unit_per_side": _("· %(unit)s per side"),
        "per_side_dot": _("· per side"),
        "load": _("Load"),
        # The Trial
        "watch_how": _("▶ Watch how"),
        "trial_rest": _("⏱ Rest %(rest)s before testing"),
        "result_for": _("result for %(name)s"),
        "timer_title": _("Tap to start; tap again when done"),
        "verdict_progress": _("▲ progress"),
        "verdict_same": _("= no change"),
        "verdict_setback": _("▼ setback"),
        "verdict_first": _("· first Trial"),
        "evolution": _("Evolution"),
        "no_history": _("no history"),
        "evolution_empty": _("No Trials logged yet — your first result starts the line."),
        "evolution_latest": _("Latest: %(value)s on %(name)s"),
        "evolution_perf": _("Performance (ladder-normalised)"),
        "trial_history": _("Trial history"),
        "set_equipment_first": _("Set your equipment first."),
        "trial_verdict": _("Your Trial verdict"),
        "err_forge_program": _("Couldn't forge the program."),
        "retake_confirm": _("Retake the Trial? Your current program will be replaced (history is kept)."),
        "retake_opened": _("Fresh Trial opened. Enter your new results."),
        "err_retake": _("Couldn't start a retake."),
        # Today / unfinished session
        "no_program": _("No program yet — take the Trial."),
        "err_load_today": _("Couldn't load today."),
        "an_earlier_day": _("an earlier day"),
        "unfinished_help": _(
            "You logged sets on %(day)s but never saved the session. Check the numbers, "
            "then save it to count toward your progress — or delete it."
        ),
        "weight_for_set": _("weight used for set %(n)s of %(name)s"),
        "unit_for_set": _("%(unit)s for set %(n)s of %(name)s"),
        "set_n": _("Set %(n)s"),
        "target_n": _("target %(n)s %(unit)s"),
        "delete_it": _("Delete it"),
        "tap_again_delete": _("Tap again to delete"),
        "err_save_that_session": _("Couldn't save that session."),
        "session_deleted": _("Session deleted."),
        "err_delete_session": _("Couldn't delete that session."),
        "retest_title": _("Time to retest."),
        "take_trial": _("Take the Trial"),
        "dismiss_reminder": _("Dismiss this reminder"),
        "rung_btn": _("⇅ Rung"),
        "rung_btn_title": _("Move to an easier or harder rung of this ladder"),
        "swap_btn": _("⇄ Change"),
        "swap_btn_title": _("Swap this for a movement you've trained less"),
        "skip_btn": _("✕ Skip exercise"),
        "skip_btn_title": _("Remove this exercise from the session"),
        "load_btn_title": _("How to build this load"),
        "start_rest": _("⏱ Start rest (%(rest)s)"),
        "err_save_values": _("Couldn't save that just yet — your reps are still on screen."),
        "already_logged": _("You've already logged sets for this one."),
        "no_swap_left": _("No other movement left to swap in."),
        "swap_help": _("Pick a movement you've trained less. Counts are sessions in the last two weeks."),
        "least_trained": _("Least trained"),
        "load_value": _("Load: %(load)s"),
        "rest_value": _("Rest: %(rest)s"),
        "sets_x_reps": _("%(sets)s × %(reps)s %(unit)s"),
        "sets_x_reps_side": _("%(sets)s × %(reps)s %(unit)s per side"),
        "back": _("Back"),
        "use_instead": _("Use this instead"),
        "err_change_exercise": _("Couldn't change that exercise."),
        "log_one_set": _("Log at least one set before saving."),
        "err_save_session": _("Couldn't save the session."),
        # Exercises (catalog)
        "err_load_exercises": _("Couldn't load exercises."),
        "no_exercises": _("No exercises to show."),
        "no_exercises_match": _("No exercises match your filters."),
        "not_owned": _("not owned"),
        "not_owned_title": _("You haven't marked this equipment as owned"),
        "using_sub": _("→ using %(name)s"),
        "no_sub": _("→ no substitute available"),
        "block": _("Block"),
        "unblock": _("Unblock"),
        "blocked": _("Blocked."),
        "unblocked": _("Unblocked."),
        "substitute_is": _("Substitute: %(name)s."),
        "no_substitute": _("No substitute available."),
        "err_update_exercise": _("Couldn't update that exercise."),
        "best_fires_title": _("Your best peer score on %(name)s: %(n)s/10 fires (%(value)s)"),
        "best_fires_title_est": _(
            "Your best peer score on %(name)s: %(n)s/10 fires (%(value)s) — "
            "estimated benchmark, not yet from published data"
        ),
        "est": _("est."),
        "needs_gear": _("needs gear"),
        # Skill tree
        "pm_horizontal_push": _("Chest · Front Delts · Triceps"),
        "pm_vertical_pull": _("Lats · Biceps · Mid-back"),
        "pm_vertical_push": _("Shoulders · Triceps"),
        "pm_lower_unilateral": _("Quads · Glutes"),
        "pm_core_anti_extension": _("Abs · Deep Core"),
        "pm_hinge": _("Hamstrings · Glutes · Low Back"),
        "pm_grip": _("Forearms · Hands"),
        "state_done": _("Cleared"),
        "state_current": _("Active"),
        "state_next": _("Next up"),
        "state_locked": _("Needs gear"),
        "state_blocked": pgettext("exercise state", "Blocked"),
        "err_load_tree": _("Couldn't load the skill tree."),
        "range_hold": _("%(range)ss hold"),
        "range_reps": _("%(range)s reps"),
        "best_score_title": _("Best peer score: %(n)s/10 flames"),
        "tree_bodyweight": _("Bodyweight"),
        "tree_with_equipment": _("With equipment"),
        # Exercise sheet
        "sec_target": _("Target"),
        "sec_prescribed_load": _("Prescribed load"),
        "sec_muscles": _("Muscles worked"),
        "sec_cues": _("Cues"),
        "quoted": _("“%(text)s”"),
        "sec_video": _("Video"),
        "sec_equipment": _("Equipment"),
        "or": _(" or "),
        "sec_peer_score": _("Peer score"),
        "sec_substitute": _("Substitute"),
        "change_my_rung": _("⇅ Change my rung"),
        # Set my load + weekly volume (#109)
        "sec_set_load": _("Set my load"),
        "set_load_save": _("Use this load"),
        "set_load_help": _("Applies everywhere this movement is in your plan. A load you can't build rounds down to the nearest one you can."),
        "band_level": _("Band %(n)s"),
        "load_set": _("Load set to %(load)s."),
        "load_snapped": _("%(asked)s can't be built — set to %(load)s."),
        "err_set_load": _("Couldn't set that load."),
        "volume_saved": _("Weekly volume set to %(n)s sets per pattern."),
        "err_volume": _("Couldn't save the weekly volume."),
        "make_my_rung": _("Make this my rung"),
        "block_exercise": _("Block this exercise"),
        # Load recipe sheet
        "sec_how_build": _("How to build it"),
        "bare_no_plates": _("Bare bar / empty handle — no plates."),
        "sec_load_bell": _("Load into the bell"),
        "sec_load_side": _("Load per side"),
        "sec_leftover": _("Still in the box"),
        # Set my rung
        "err_load_ladder": _("Couldn't load your ladder."),
        "change_your_rung": _("Change your rung"),
        "rung_help": _(
            "Jump to any rung you can do, up or down. Every day that trains this ladder "
            "moves with it, and progress carries on from there."
        ),
        "rung_trial_note": _("Rungs marked %(mark)s ask you to confirm first."),
        "above_trial_lc": _("above your Trial"),
        "above_trial": _("Above your Trial"),
        "current": _("Current"),
        "rung_warning_anchor": _(
            "This rung is placed at %(required)s on the %(anchor)s; your last Trial was "
            "%(yours)s. You can still take it — if the sets don't land, the Forge will ease you back down."
        ),
        "rung_warning": _(
            "This rung is placed at %(required)s; your last Trial was %(yours)s. You can still "
            "take it — if the sets don't land, the Forge will ease you back down."
        ),
        "cancel": _("Cancel"),
        "move_anyway": _("Move anyway"),
        "err_change_rung": _("Couldn't change your rung."),
        "now_training": _("Now training %(name)s."),
        # History + progress chart
        "no_saved_sessions": _("No saved sessions yet."),
        "session": _("Session"),
        "history_set": _("%(name)s · set %(n)s"),
        "history_reps": _("%(actual)s / %(expected)s %(unit)s"),
        "err_load_history": _("Couldn't load history."),
        "chart_empty": _("Log a session to see your progression."),
        "chart_deciles": _("Dashed lines = peer deciles for %(name)s (your age & sex)."),
        "decile_short": pgettext("decile, chart label", "D%(d)s"),
        "chart_aria": _("Progression chart"),
        "week_label": pgettext("chart bucket", "Wk %(n)s"),
        "err_start_session": _("Couldn't start the session."),
        # Earphones mode — on-screen and spoken
        "v_mode": _("Earphones mode"),
        "v_getting_ready": _("Getting ready…"),
        "v_on": _("Earphones mode on. Let's forge."),
        "v_log": _("Log"),
        "v_skip": _("Skip"),
        "v_exit": _("Exit"),
        "v_stop_btn": _("■ Stop"),
        "v_skip_rest": _("⏭ Skip rest"),
        "v_count_1": _("one"),
        "v_count_2": _("two"),
        "v_count_3": _("three"),
        "v_go": _("Go."),
        "v_mic_blocked": _("Microphone blocked — use the on-screen buttons (allow the mic and reopen for voice)."),
        "v_mic_denied": _("Mic permission denied — voice off. Use the buttons."),
        "v_set_of": _("Set %(n)s of %(total)s"),
        "v_at": _("at %(load)s"),
        "v_hold_max": _("Hold as long as you can"),
        "v_target_hold": _("Target hold %(n)s seconds"),
        "v_target_hold_say": _("Target hold, %(n)s seconds."),
        "v_amrap_target": _("As many reps as possible (target %(n)s+)"),
        "v_amrap_say": _("As many reps as you can"),
        "v_target_reps_say": _("Target %(n)s reps"),
        "v_say_start": _("Say start when you're ready."),
        "v_tap_start": _("Tap start when you're ready."),
        "v_say_reps": _("Say your reps when you're done."),
        "v_tap_reps": _("Tap your reps when you're done."),
        "v_i_say_start": _("Say “start” to begin the hold."),
        "v_i_tap_start": _("Tap Start to begin the hold."),
        "v_i_say_stop": _("Holding… say “stop” when you're done."),
        "v_i_tap_stop": _("Holding… tap Stop when you're done."),
        "v_i_say_reps": _("Do your set, then say your reps (a number)."),
        "v_i_tap_reps": _("Do your set, then tap your reps below."),
        "v_i_say_skip": _("Resting — say “skip” to go now."),
        "v_i_tap_skip": _("Resting — tap Skip to go now."),
        "v_still_resting": _("Still resting. Say skip to go now."),
        "v_next": _("Next: %(name)s, %(detail)s."),
        "v_next_hold_max": _("hold as long as you can"),
        "v_next_hold": _("hold %(n)s seconds"),
        "v_next_amrap": _("as many reps as you can"),
        "v_next_target": _("target %(n)s reps"),
        "v_recover": _("Recover"),
        "v_rest": _("Rest"),
        "v_rest_say_skip": _("Rest %(rest)s — say “skip” to go now."),
        "v_rest_tap_skip": _("Rest %(rest)s — tap Skip to go now."),
        "v_rest_over": _("Rest over."),
        "v_done": _("Done"),
        "v_complete": _("Workout complete"),
        "v_no_sets": _("No sets logged."),
        "v_no_sets_say": _("No sets were logged. Exiting earphones mode."),
        "v_saving": _("Saving your session…"),
        "v_complete_say": _("Workout complete. Saving your session."),
        "v_save_failed": _("Couldn't save — your reps are still on the Today screen."),
        "v_exiting": _("Exiting earphones mode."),
        "v_closed": _(
            "Earphones mode closed. Your logged reps are on the Today screen — tap Save session to keep them."
        ),
        "v_unsupported": _("Earphones mode needs Chrome or Edge."),
        "v_open_day": _("Open a day first."),
        "v_nothing_today": _("Nothing scheduled today."),
        "v_all_filled": _("All sets are already filled — nothing left for earphones mode."),
        "v_input_on": _("Voice input on — tap to mute (stops the listening beep)"),
        "v_input_off": _("Voice input off — tap to enable hands-free commands (your browser beeps while listening)"),
        # forge-anatomy.js
        "anat_front_map": _("Front muscle map"),
        "anat_back_map": _("Back muscle map"),
        "anat_front": pgettext("body side", "Front"),
        "anat_back": pgettext("body side", "Back"),
    }
    strings.update(_plural("v_holding_so_far", lambda n: ngettext(
        "Holding. %(n)s second so far. Say stop when done.",
        "Holding. %(n)s seconds so far. Say stop when done.",
        n,
    )))
    strings.update(_plural("v_recorded_secs", lambda n: ngettext(
        "Recorded %(n)s second.",
        "Recorded %(n)s seconds.",
        n,
    )))
    strings.update(_plural("v_logged_reps", lambda n: ngettext(
        "Logged %(n)s rep.",
        "Logged %(n)s reps.",
        n,
    )))
    strings.update(_plural("v_rest_for", lambda n: ngettext(
        "Rest for %(n)s second.",
        "Rest for %(n)s seconds.",
        n,
    )))
    strings.update(_plural("swapped_n", lambda n: ngettext(
        "Swapped in %(n)s live prescription.",
        "Swapped in %(n)s live prescriptions.",
        n,
    )))
    strings.update(_plural("retest_text", lambda n: ngettext(
        "Your last Trial was %(n)s day ago — retake it to re-place yourself and see how your strength has moved.",
        "Your last Trial was %(n)s days ago — retake it to re-place yourself and see how your strength has moved.",
        n,
    )))
    strings.update(_plural("n_sessions", lambda n: ngettext(
        "%(n)s session",
        "%(n)s sessions",
        n,
    )))
    strings.update(_plural("n_sets", lambda n: ngettext(
        "%(n)s set",
        "%(n)s sets",
        n,
    )))
    strings.update(_plural("n_flames", lambda n: ngettext(
        "%(n)s flame",
        "%(n)s flames",
        n,
    )))
    strings.update(_plural("n_trials", lambda n: ngettext(
        "%(n)s Trial",
        "%(n)s Trials",
        n,
    )))
    strings.update(_plural("n_reps", lambda n: ngettext(
        "%(n)s rep",
        "%(n)s reps",
        n,
    )))
    strings.update(_plural("n_seconds", lambda n: ngettext(
        "%(n)s second",
        "%(n)s seconds",
        n,
    )))
    return strings


def _is_es():
    return (get_language() or "").startswith("es")


def localized(obj, field):
    """``obj.<field>_es`` when Spanish is active and it is filled, else the
    English ``obj.<field>`` — the per-field fallback the whole Forge uses."""
    if _is_es():
        value = getattr(obj, f"{field}_es", "")
        if value:
            return value
    return getattr(obj, field)


def forge_catalog():
    """English name → display text, for every catalog row the Forge shows.

    forge.js keeps the English name as the key (norms, progress and the icon
    map look things up by it) and swaps in these only for display.
    """
    return {
        "exercises": {
            ex.name: {"name": localized(ex, "name"), "cues": localized(ex, "cues")}
            for ex in Exercise.objects.only("name", "name_es", "cues", "cues_es")
        },
        "patterns": {p.key: localized(p, "name") for p in MovementPattern.objects.only("key", "name", "name_es")},
        "muscles": {m.key: localized(m, "name") for m in Muscle.objects.only("key", "name", "name_es")},
        "equipment": {e.key: localized(e, "name") for e in Equipment.objects.only("key", "name", "name_es")},
    }


def day_name(name):
    """A ProgramDay name for display. The one generated day is stored as the
    English ``THE_DAY_NAME`` and translated here, so existing programs show it in
    the active language without a data migration."""
    return _(THE_DAY_NAME) if name == THE_DAY_NAME else name


def _rung(icon, name, state, cue, loaded=None, master=False):
    rung = {"icon": icon, "name": name, "state": state, "cue": cue}
    if loaded:
        rung["loaded"] = loaded
    if master:
        rung["master"] = True
    return rung


def _loaded(icon, name, equip, cue):
    return {"icon": icon, "name": name, "equip": equip, "cue": cue}


def showcase_tree():
    """The landing page's illustrative skill tree (forge-skilltree.js).

    A static showcase, not the live catalog — the landing is public and the
    catalog API is login-gated. Keep it roughly in step with seed_forge.LADDERS.
    """
    return {
        "strings": {
            "evolve": _("Evolve"),
            "watch_evolve": _("Watch %(name)s evolve"),
            "status_branch": _("Loaded variant"),
            "status_done": _("Cleared"),
            "status_current": _("Your rung"),
            "status_next": _("Unlocked — ready"),
            "status_locked": _("Locked"),
            "requires": _("Requires"),
            "hint": _("Hover or tap any node to read its cue."),
        },
        "patterns": [
            {"id": "hpush", "name": _("Horizontal Push"), "icon": "pushup", "rungs": [
                _rung("wallpush", _("Wall Push-up"), "done", _(
                    "Stand an arm’s length from a wall, body in one line. Bend the elbows to bring your "
                    "chest near, then press away.")),
                _rung("inclinepush", _("Incline Push-up"), "done", _(
                    "Hands on a raised edge — the higher the surface, the lighter the load. Lower the "
                    "surface as you get stronger.")),
                _rung("kneepush", _("Knee Push-up"), "current", _(
                    "From the knees, keep a straight line hip-to-head. A clean half-step before the full push-up."),
                    loaded=_loaded("band", _("Banded Knee Push-up"), _("Resistance band"), _(
                        "Loop a band across the upper back for accommodating resistance — hardest at lockout."))),
                _rung("pushup", _("Full Push-up"), "next", _(
                    "Plank-tight, elbows tracking ~45°, chest to the floor and back. The benchmark of "
                    "horizontal pressing."),
                    loaded=_loaded("plate", _("Weighted Push-up"), _("Plate / pack"), _(
                        "Rest a plate or loaded pack on the upper back once bodyweight reps feel easy."))),
                _rung("diamondpush", _("Diamond Push-up"), "locked", _(
                    "Hands together under the chest forming a diamond. Shifts the load onto the triceps "
                    "and inner chest.")),
                _rung("archerpush", _("Archer Push-up"), "locked", _(
                    "Shift your weight over one arm while the other stays straight. The doorway to the "
                    "one-arm push-up."), master=True),
            ]},
            {"id": "vpull", "name": _("Vertical Pull"), "icon": "pullup", "rungs": [
                _rung("row", _("Australian Row"), "done", _(
                    "Hang under a waist-high bar, body straight, and row your chest to it. Walk the feet "
                    "forward to ease it.")),
                _rung("negpull", _("Negative Pull-up"), "current", _(
                    "Jump or step to the top, then lower yourself as slowly as you can. Builds the "
                    "strength to pull up."),
                    loaded=_loaded("band", _("Banded Pull-up"), _("Resistance band"), _(
                        "Stand in a band looped over the bar — it gives the most help at the bottom where "
                        "you’re weakest."))),
                _rung("pullup", _("Pull-up"), "next", _(
                    "Dead hang to chin-over-bar, no kip. The gold standard of vertical pulling strength."),
                    loaded=_loaded("plate", _("Weighted Pull-up"), _("Belt / pack"), _(
                        "Add load on a dip belt once you clear clean reps. The fastest route to raw "
                        "pulling power."))),
                _rung("archerpull", _("Archer Pull-up"), "locked", _(
                    "Pull to one hand while the other arm stays straight along the bar. The bridge to a "
                    "one-arm pull-up."), master=True),
            ]},
            {"id": "vpush", "name": _("Vertical Push"), "icon": "pikepush", "rungs": [
                _rung("pikepush", _("Incline Pike Push-up"), "done", _(
                    "Feet raised, hips high in a pike. Press the crown of your head toward the floor and back up.")),
                _rung("deeppike", _("Pike Push-up"), "current", _(
                    "Floor pike, hips stacked over the shoulders. Deepen the range until the head taps the ground.")),
                _rung("wallhspu", _("Wall Handstand Push-up"), "next", _(
                    "Kick to the wall and press. Chest-to-wall is the honest version; lower under control.")),
                _rung("hspu", _("Handstand Push-up"), "locked", _(
                    "Free-balanced, full range. Overhead pressing strength meets total-body control."), master=True),
            ]},
            {"id": "lower", "name": _("Lower Unilateral"), "icon": "pistol", "rungs": [
                _rung("assistsplit", _("Assisted Split Squat"), "done", _(
                    "Split stance, one hand on a rail for balance. Drop the back knee toward the floor and drive up.")),
                _rung("splitsquat", _("Split Squat"), "current", _(
                    "Unsupported split stance. Vertical torso, front heel planted, full depth each rep."),
                    loaded=_loaded("dumbbell", _("Goblet Split Squat"), _("Dumbbell / KB"), _(
                        "Hold a weight at the chest. The cleanest way to overload the legs without a barbell."))),
                _rung("bulgarian", _("Bulgarian Split Squat"), "next", _(
                    "Rear foot on a bench. Brutal single-leg range and balance — the legs’ true test."),
                    loaded=_loaded("dumbbell", _("Loaded Bulgarian"), _("Dumbbells"), _(
                        "A dumbbell in each hand. Among the highest-yield lower-body movements there is."))),
                _rung("pistol", _("Pistol Squat"), "locked", _(
                    "Full one-leg squat, other leg extended. Strength, mobility and control in a single feat."),
                    master=True),
            ]},
            {"id": "core", "name": _("Core · Anti-Extension"), "icon": "plank", "rungs": [
                _rung("kneeplank", _("Knee Plank"), "done", _(
                    "Forearms down, knees on the floor, hips tucked. Brace as if bracing for a punch.")),
                _rung("plank", _("Forearm Plank"), "current", _(
                    "Full plank, straight line heel-to-head. Squeeze glutes and ribs-down — no sagging hips.")),
                _rung("rkcplank", _("RKC Plank"), "next", _(
                    "A plank turned to maximum tension — everything clenched. Ten brutal seconds beats a lazy minute.")),
                _rung("hollow", _("Hollow Body Hold"), "locked", _(
                    "On your back, lower back pressed flat, arms and legs lifted. The gymnastic core foundation."),
                    master=True),
            ]},
            {"id": "hinge", "name": _("Hinge"), "icon": "glutebridge", "rungs": [
                _rung("glutebridge", _("Glute Bridge"), "done", _(
                    "On your back, knees bent, drive the hips up and squeeze. Learn to fire the glutes, not the back.")),
                _rung("slbridge", _("Single-leg Glute Bridge"), "current", _(
                    "One leg extended, bridge with the other. Exposes and fixes side-to-side imbalances.")),
                _rung("hipthrust", _("Hip Thrust"), "next", _(
                    "Shoulders on a bench for a deeper range. The most direct glute builder you can do."),
                    loaded=_loaded("barbell", _("Barbell Hip Thrust"), _("Barbell"), _(
                        "Load the lap across the hips. Scales near-endlessly — the answer to overloading the hips."))),
                _rung("nordic", _("Nordic Curl"), "locked", _(
                    "Ankles anchored, lower your straight body under control. The ultimate hamstring strength feat."),
                    master=True),
            ]},
        ],
    }
