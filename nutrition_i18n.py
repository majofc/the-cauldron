"""Strings for nutrition.js (#80–#84), translated server-side like grimoire_i18n.

Reuses the Grimoire's nutrient, group and unit labels so a nutrient reads the
same everywhere. Placeholders use ``%(name)s`` and are filled by ``t()`` in JS.
"""

from django.utils.translation import gettext as _

from .grimoire_i18n import grimoire_strings

SHARED_PREFIXES = ("n_", "grp_", "unit_")
SHARED_KEYS = ("error_generic", "act_edit", "act_delete", "act_delete_confirm", "goal_at_least",
               "goal_at_most", "goal_between")


def nutrition_strings():
    grimoire = grimoire_strings()
    shared = {k: v for k, v in grimoire.items() if k.startswith(SHARED_PREFIXES) or k in SHARED_KEYS}
    return {
        **shared,
        "act_remove": _("Remove"),
        "change": _("Change"),
        "today": _("Today"),
        # Goals (#80)
        "goal_none": _("no goal"),
        "goal_kind_for": _("Goal for %(nutrient)s"),
        "goal_min_for": _("Minimum %(nutrient)s"),
        "goal_max_for": _("Maximum %(nutrient)s"),
        "target_at_least": _("goal ≥ %(value)s %(unit)s"),
        "target_at_most": _("goal ≤ %(value)s %(unit)s"),
        "target_between": _("goal %(min)s–%(max)s %(unit)s"),
        "toast_goals_saved": _("Goals saved."),
        "history_show": _("Show goal history"),
        "history_hide": _("Hide goal history"),
        "history_empty": _("No goals yet."),
        "history_since": _("since %(from)s"),
        "history_range": _("%(from)s – %(to)s"),
        "no_goals_yet": _("You haven't set daily goals yet."),
        "set_goals": _("Set your goals"),
        # Status of a day × goal (#81, #82)
        "status_met": _("met"),
        "status_missed": _("missed"),
        "status_uncertain": _("uncertain"),
        "status_pending": _("not yet"),
        "status_not_logged": _("not logged"),
        "totals_incomplete": _("“≥” means some of what you ate has no data for that nutrient, so the real amount may be higher."),
        # How you're doing: the moving window and time in range
        "adh_no_goals": _("Set a daily goal to see how often you meet it."),
        "span_24h": _("24 hours"),
        "span_7d": _("7 days"),
        "window_range": _("%(from)s → %(to)s"),
        "window_live": _("Live: the window ends now and moves with the clock. Drag the timeline to see an earlier moment."),
        "window_past": _("An earlier moment. Drag the timeline, or go back to now."),
        "today_now": _("now"),
        "avg_note": _("Daily average over the last 7 days, against your daily goals."),
        "gauge_nothing": _("nothing logged in this window"),
        "gauge_over": _("%(n)s %(unit)s over"),
        "gauge_short": _("%(n)s %(unit)s to go"),
        "gauge_short_maybe": _("up to %(n)s %(unit)s to go"),
        "gauge_in": _("in range"),
        "all_show": _("Show every nutrient"),
        "all_hide": _("Hide the other nutrients"),
        "tir_of_time": _("of the time in range"),
        "tir_breakdown": _("Out of range %(out)s · uncertain %(unc)s · %(hours)s h with nothing logged (not counted)"),
        "tir_none": _("Nothing logged in this period yet."),
        "chart_label": _("%(nutrient)s over a moving %(span)s window, last %(days)s days, against the goal range"),
        "strip_label": _("Share of each day in range"),
        "strip_day": _("%(date)s: %(pct)s in range (%(hours)s h judged)"),
        "strip_empty": _("%(date)s: nothing logged"),
        "table_show": _("Show as a table"),
        "th_day": _("Day"),
        "th_pct": _("In range"),
        "th_hours": _("Hours judged"),
        # Settings + consent
        "toast_tz_saved": _("Timezone saved."),
        "consent_given_on": _("You gave your consent on %(date)s (version %(version)s)."),
        "withdraw_confirm": _("Really withdraw?"),
        # Journal (#81)
        "no_matches": _("Nothing found."),
        "pick_spell_first": _("Choose a spell first."),
        "pick_food_first": _("Search for what you had, or type it in by hand."),
        "kind_spell": _("dish"),
        "kind_reagent": _("ingredient"),
        "type_it_in": _("Not here? Type “%(q)s” in by hand"),
        "mcp_unnamed": _("An assistant"),
        "badge_estimated_short": _("estimated"),
        "mcp_connected_on": _("connected %(date)s"),
        "mcp_last_used": _("last used %(date)s"),
        "mcp_disconnect": _("Disconnect"),
        "mcp_disconnect_confirm": _("Disconnect? Click again"),
        "mcp_none": _("No assistant is connected."),
        "toast_mcp_disconnected": _("Disconnected. It can't use your account anymore."),
        "form_log": _("Log something"),
        "form_log_btn": _("Log it"),
        "form_edit": _("Edit entry"),
        "form_save_btn": _("Save entry"),
        "entries_empty": _("Nothing logged on this day."),
        "entries_empty_today": _("Nothing logged yet today."),
        "from_plan": _("planned"),
        "toast_entry_logged": _("Logged."),
        "toast_entry_saved": _("Entry saved."),
        "unit_serving": _("serving"),
        "unit_servings": _("servings"),
        # Ritual planner (#83)
        "slot_breakfast": _("Breakfast"),
        "slot_lunch": _("Lunch"),
        "slot_dinner": _("Dinner"),
        "slot_snack": _("Snack"),
        "slot_empty": _("Nothing planned."),
        "plan_eat": _("I ate this"),
        "plan_eaten": _("eaten"),
        "plan_unavailable": _("This spell is no longer available."),
        "projected_title": _("If you eat what's planned"),
        "toast_planned": _("Added to the ritual."),
        "toast_eaten": _("Logged as eaten."),
    }
