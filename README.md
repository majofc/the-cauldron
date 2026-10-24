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
| Lower (unilateral) | Squat | `squat` | **estimated** | Strength Level bodyweight-squat standards (546k sets); 40+ estimated. |
| Grip / forearms | Dead Hang | `dead_hang` | **estimated** | Divulgation-grade hang-time references (Marathon Handbook, Deadhangs). Whole table estimated. |

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
| Med | `squat` | crowd-sourced (trained-skewed), 40+ extrapolated | swap for a scientific bodyweight-squat norm; remove `"estimated": True` |
| Med | `dead_hang` | shaped from divulgation articles, no published percentile table | swap for a measured age×sex hang-time table; remove `"estimated": True` |
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

## MCP — The Cauldron in your AI assistant (#89)

Users can connect The Cauldron to Claude (claude.ai, mobile, Desktop) or Claude Code and ask things like
"¿cómo voy hoy?" or "¿qué cocino con huevo, jitomate y cebolla?". Their assistant does the reasoning and
any vision; the server exposes tools over the existing services.

**Connect** — the info page is the connector URL itself: open `https://<host>/cauldron/mcp/` in a browser
(linked from the landing, the nav ✦ icon, the footer and Profile → "Your AI assistants").
- claude.ai / Claude mobile / Desktop: Settings → Connectors → *Add custom connector* →
  `https://<host>/cauldron/mcp/`. Sign in to The Cauldron and allow. (Connectors added on the web appear on mobile.)
- Claude Code: `claude mcp add --transport http cauldron https://<host>/cauldron/mcp/`, then `/mcp` to sign in.
- Render free tier: the first call after a sleep can take 30–50 s; retry if the connector times out.

**How it works**: OAuth 2.1 (django-oauth-toolkit; dynamic client registration, PKCE S256) → bearer token
→ stateless JSON-RPC at `/cauldron/mcp/`. Architecture and rules: `CLAUDE.md` → "MCP server".

**Tools**
- Read: `get_today`, `get_journal`, `get_adherence`, `get_goals`, `get_goal_breakdown`, `get_meal_plan`, `search_spells`, `get_spell`,
  `search_reagents`, `get_reagent`.
- Write: `log_meal`, `update_meal`✱, `delete_meal`✱, `plan_meal`, `update_planned_meal`✱, `delete_planned_meal`✱,
  `mark_planned_meal_eaten`, `set_goal`, `create_reagent`, `update_reagent`✱, `create_spell`, `update_spell`✱,
  `delete_spell`✱, `fork_spell`, `log_casting`.
- Forge: `get_forge_today`, `get_forge_program`, `get_forge_progress`, `get_forge_sessions`, `get_equipment`,
  `get_trial`, `set_equipment`✱, `submit_trial_results`, `retake_trial`✱, `start_forge_session`, `log_forge_set`,
  `update_forge_set`✱, `finish_forge_session`, `delete_forge_session`✱ — same placement, program and progression
  as the website for the same inputs.
- Goals (#105): `get_goals` returns two sets — `mine` (the user's own goals, plus `history`) and `recommended`
  (WHO/FAO + NASEM DRI references from the Profile's weight, age, sex and activity level, each with its `source`;
  `status: "minor"` under 18; `assumed` / `needs_weight` say what's missing). `get_adherence` adds
  `time_in_range_recommended`. Birth year and sex are set on the Profile page, not through `set_equipment`.
- ✱ Two-step: the first call changes nothing and returns a preview + `confirm_token`; the assistant shows it to
  the user and applies only with the token (single use, 5 minutes). Write tools accept `client_request_id` so a
  retry never writes twice. Everything created is private and tagged `channel="mcp"`.

**Add a tool**: write a function `handler(ctx, **args) -> dict` in `mcp_server/tools/<area>.py`, decorate it
with `@tool(name, title=…, description=…, schema=…, scope=READ|WRITE, consent=True if it touches goals/meals/plans,
confirm=True for any edit or delete)`, call services (never re-implement rules), pass `channel=Channel.MCP` on
anything created, and add tests (`tests/test_mcp_server.py` for reads, `tests/test_mcp_writes.py` for writes:
happy path, other user → `not_found`, no consent, bad arguments, and the two-step flow).
A two-step handler takes `apply`: with `apply=False` it validates and returns `{"target", "preview"}` without
changing anything; with `apply=True` it does the change.

**Analytics** (PostHog, no PII — only enums, flags, counts and the internal user id):

| Event | Properties | Where |
|---|---|---|
| `mcp_tool_called` | `tool`, `ok`, `error_code`, `latency_ms`, `client`, `confirm_step` (`preview`/`applied`, two-step tools) | `registry.call_tool` |
| `mcp_duplicate_prevented` | `kind` (spell/reagent), `overridden` | `create_spell` / `create_reagent` |
| `mcp_connected` / `mcp_disconnected` | `client` (`claude`, `claude_code`, `other`) | consent screen / Profile |
| `mcp_page_viewed` | `signed_in` | info page |
| `mcp_connect_clicked` | `target` (`claude_ai`, `desktop`, `code`) | info page buttons |

**"Cauldron MCP" dashboard** (PostHog → Dashboards; create these insights):
1. *Connections* — trend, weekly: `mcp_connected` vs `mcp_disconnected`, breakdown `client`.
2. *Funnel* — `mcp_page_viewed` → `mcp_connect_clicked` → `mcp_connected` (unique users, 7-day window).
3. *Active MCP users* — trend, weekly unique users of `mcp_tool_called`.
4. *Tool usage* — trend, total `mcp_tool_called`, breakdown `tool`.
5. *Error rate* — formula `A / B`: A = `mcp_tool_called` where `ok = false`, B = all `mcp_tool_called`; table by
   `error_code`.
6. *Latency* — `mcp_tool_called`, property `latency_ms`, p50 / p95, breakdown `tool`.
7. *Two-step follow-through* — `applied` / `preview` counts of `confirm_step` (low = users backing out of edits).
8. *Duplicates prevented* — `mcp_duplicate_prevented`, breakdown `kind` and `overridden`.
9. *Meals via MCP* — formula `A / (A + B)`: A = `mcp_tool_called` where `tool = log_meal` and `ok = true`,
   B = `intake_logged` (web only).
10. *Forge via MCP* — `mcp_tool_called` where `tool = finish_forge_session` and `ok = true`, weekly.

Effectiveness from `channel` (database, Django shell or admin; PostHog doesn't see records):
- logging days/week before vs after a user's first `McpConnection.created_at` (`IntakeEntry.local_date`, distinct);
- % of `IntakeEntry` with `channel="mcp"`;
- accuracy proxy: MCP entries edited (`updated_at - created_at > 1 min`) or deleted within 24 h — deletions show as
  `mcp_tool_called {tool: delete_meal, confirm_step: applied}`;
- adherence (`services.adherence`) of users with ≥ 1 MCP entry vs web-only users;
- `PlannedMeal` with `channel="mcp"` that got an `intake_entry`; `WorkoutSession` completed with `channel="mcp"`.

**LLM evaluation**: `tests/mcp_eval_prompts.md` — 18 prompts (ES/EN) with the tool each should pick. Run them by
hand against Claude before changing tool names, descriptions or schemas, and log the run in that file.
