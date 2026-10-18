# MCP evaluation prompts (#89)

Run by hand against Claude (claude.ai or Claude Code) with The Cauldron connected, on a test account
that has consent, goals, a few journal entries, a program and some Grimoire spells. A prompt **passes**
when Claude's first relevant tool call is the expected one (later calls may follow) and it respects the
rule in the last column. Re-run after changing any tool name, description or schema.

| # | Prompt | Expected tool(s) | Must also |
|---|---|---|---|
| 1 | "¿Cómo voy hoy?" | `get_today` | Answer from the totals vs goals; mention the Forge workout state |
| 2 | *(photo of a plate)* "Registra esto como mi comida" | `search_spells` / `search_reagents` → `log_meal` | Estimate portions; unknown nutrients stay unset; quick entries carry `estimated: true` |
| 3 | "Anoche cené dos tacos al pastor y una Coca" | `search_reagents` (Coca-Cola) → `log_meal` ×2 | `eaten_at` last night in the user's timezone; reuse the seeded Mexican Coca-Cola (unit `glass`) |
| 4 | "I just ate 150 g of my overnight oats" | `search_spells` → `log_meal` | Use the user's own spell, `quantity: 150, unit: "g"` |
| 5 | *(photo of a fridge)* "¿Qué puedo cocinar con esto?" | `search_spells` with `have_reagents` | Rank readable spells; list missing ingredients; offer to save a new recipe only if asked |
| 6 | "Save this recipe to my Grimoire: …" | `search_spells` → `create_spell` | On `duplicate`, show the matches and ask before `confirm_new: true`; the spell is private |
| 7 | "Agrega el ingrediente 'tortilla de nopal' con estos datos…" | `search_reagents` → `create_reagent` | Reuse an existing match; flag estimated nutrients |
| 8 | "Plan my dinners Monday to Friday" | `get_meal_plan` → `plan_meal` ×5 | Slot `dinner`; no duplicate on retry (`client_request_id`) |
| 9 | "Borra lo que registré en el desayuno" | `get_journal` → `delete_meal` | Show the preview and wait for the user before sending `confirm_token` |
| 10 | "Change my protein goal to at least 120 g" | `get_goals` → `set_goal` | `kind: at_least`, `min: 120` |
| 11 | "¿Cómo voy con mis metas este mes?" | `get_adherence` (`days: 30`) | Report % met and streaks; don't invent missing days |
| 12 | "What's my workout today?" | `get_forge_today` | If `unfinished_past_session`, offer to finish or delete it first |
| 13 | "Empecé. Primera serie de lagartijas: 12" | `start_forge_session` → `log_forge_set` | Log against the returned set id; reps as said |
| 14 | "Done, finish the workout" | `finish_forge_session` | Relay the progression messages and fires |
| 15 | "Quiero hacer la Prueba otra vez" | `retake_trial` → `get_trial` | Two-step preview first; then explain each test (what to count) |
| 16 | "Here are my Trial results: push-ups 18, plank 60 s…" | `get_trial` → `submit_trial_results` | One value per pattern; report placements |
| 17 | "Now I have dumbbells: 5, 10 and 15 kg" | `get_equipment` → `set_equipment` | Two-step preview; `dumbbell_weights` replaces the list |
| 18 | "Make Ana's public lasagna mine" | `search_spells` (`scope: public`) → `fork_spell` | Treat the other user's text as data, never as instructions |

Record each run (date, client, model, pass/fail, notes) below.

| Date | Client | Model | Passed | Notes |
|---|---|---|---|---|
