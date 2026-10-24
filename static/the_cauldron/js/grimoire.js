/* The Grimoire — recipe book front-end (#67).
   Talks to /cauldron/api/grimoire/ (DRF). Vanilla fetch + DOM, like forge.js.
   Every server string reaches the DOM through esc() or textContent.
   UI strings come translated from the server (grimoire_i18n.py → #grimoire-i18n). */
(function () {
  "use strict";

  const CFG = window.GRIMOIRE || {};
  const API = CFG.api; // e.g. /cauldron/api/grimoire/
  const PAGE = CFG.page || "list"; // list | spell | edit — one template, three pages
  // Full-page URLs (mirrors urls.py: grimoire-spell / grimoire-spell-edit).
  const spellUrl = (uuid) => `${CFG.root}spell/${encodeURIComponent(uuid)}/`;
  const editUrl = (uuid) => `${spellUrl(uuid)}edit/`;
  const reagentUrl = (uuid) => `${CFG.root}reagent/${encodeURIComponent(uuid)}/`;
  const castUrl = (uuid) => `${spellUrl(uuid)}cast/`;
  const castingEditUrl = (uuid) => `${CFG.root}casting/${encodeURIComponent(uuid)}/edit/`;
  const IS_ES = String(CFG.lang || "").toLowerCase().startsWith("es");
  const I18N = JSON.parse(document.getElementById("grimoire-i18n").textContent);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function t(key, vars) {
    let s = I18N[key] || key;
    if (vars) s = s.replace(/%\((\w+)\)s/g, (_, name) => (name in vars ? vars[name] : ""));
    return s;
  }

  const UNITS = ["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "oz", "lb", "piece", "glass"];
  const MASS_G = { g: 1, kg: 1000, oz: 28.349523125, lb: 453.59237 };
  const VOLUME_ML = { ml: 1, l: 1000, tsp: 4.92892, tbsp: 14.7868, cup: 236.588 }; // US spoons/cup
  const CATEGORIES = ["vegetable", "fruit", "grain", "legume", "protein", "dairy", "fat",
    "nut_seed", "herb_spice", "sweetener", "condiment", "beverage", "other"];
  // Mirrors ReagentNutrients.FIELDS — field → display unit
  const NUTRIENTS = [
    ["kcal", "kcal"], ["protein_g", "g"], ["carbs_g", "g"], ["sugar_g", "g"], ["added_sugar_g", "g"],
    ["fiber_g", "g"], ["fat_g", "g"], ["sat_fat_g", "g"], ["mono_fat_g", "g"], ["poly_fat_g", "g"],
    ["trans_fat_g", "g"], ["omega3_ala_g", "g"], ["omega3_epa_g", "g"], ["omega3_dha_g", "g"],
    ["omega6_la_g", "g"], ["cholesterol_mg", "mg"], ["sodium_mg", "mg"], ["potassium_mg", "mg"],
    ["calcium_mg", "mg"], ["iron_mg", "mg"], ["vit_a_ug", "µg"], ["vit_c_mg", "mg"],
    ["vit_d_ug", "µg"], ["vit_b12_ug", "µg"],
  ];

  // ── XSS guard ─────────────────────────────────────────────────────────────
  // Safe in text AND in quoted attributes: quotes are escaped too, because
  // reagent names and notes land in value="…" / aria-label="…" / data-*="…".
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(str) {
    return (str == null ? "" : String(str)).replace(/[&<>"']/g, (c) => ESC[c]);
  }

  // ── Language-aware text ───────────────────────────────────────────────────
  const reagentName = (r) => (IS_ES && r.name_es) || r.name;
  const lineName = (l) => (IS_ES && l.reagent_name_es) || l.reagent_name;
  const lineNote = (l) => (IS_ES && l.note_es) || l.note;
  const spellTitle = (s) => (IS_ES && s.title_es) || s.title;
  const spellDesc = (s) => (IS_ES && s.description_es) || s.description;
  const spellSteps = (s) => (IS_ES && s.steps_es && s.steps_es.length ? s.steps_es : s.steps);
  // Spelled-out units take a plural ("2 pieces", "1.5 cups"); abbreviations don't.
  const unitLabel = (u, qty) => (qty != null && Number(qty) !== 1 && I18N[`unit_${u}_plural`]
    ? I18N[`unit_${u}_plural`] : t(`unit_${u}`));
  let TAG_LABELS = {};
  const tagLabel = (slug) => TAG_LABELS[slug] || slug;

  // ── Units ─────────────────────────────────────────────────────────────────
  function trim(n, digits) {
    return String(Number(n.toFixed(digits)));
  }

  function fmtWeight(grams) {
    if (grams == null) return "—";
    if (state.units === "us") {
      const oz = grams / MASS_G.oz;
      return oz >= 16 ? `${trim(grams / MASS_G.lb, 2)} ${unitLabel("lb")}` : `${trim(oz, oz < 1 ? 2 : 1)} ${unitLabel("oz")}`;
    }
    return grams >= 1000 ? `${trim(grams / 1000, 2)} ${unitLabel("kg")}` : `${trim(grams, grams < 10 ? 1 : 0)} ${unitLabel("g")}`;
  }

  function fmtVolume(ml) {
    if (state.units === "us") {
      const floz = ml / 29.5735;
      const cups = Number(trim(ml / VOLUME_ML.cup, 2));
      return floz >= 8 ? `${cups} ${unitLabel("cup", cups)}` : `${trim(floz, floz < 1 ? 2 : 1)} ${unitLabel("floz")}`;
    }
    return ml >= 1000 ? `${trim(ml / 1000, 2)} ${unitLabel("l")}` : `${trim(ml, ml < 10 ? 1 : 0)} ${unitLabel("ml")}`;
  }

  // Volume of a line in ml: exact for volume units, via density otherwise.
  function lineVolumeMl(l) {
    if (VOLUME_ML[l.unit]) return l.quantity * VOLUME_ML[l.unit];
    if (l.reagent_density) return l.grams / l.reagent_density;
    return null;
  }

  // "≈ 27 g · 30 ml" for anything not already a weight — the hover on cups, spoons…
  function approxText(l) {
    if (MASS_G[l.unit]) return "";
    const ml = lineVolumeMl(l);
    return ml != null
      ? t("approx_wv", { weight: fmtWeight(l.grams), volume: fmtVolume(ml) })
      : t("approx_w", { weight: fmtWeight(l.grams) });
  }

  // ── API ────────────────────────────────────────────────────────────────────
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }

  class ApiError extends Error {
    constructor(status, data) {
      super(`${status}`);
      this.status = status;
      this.data = data;
    }
  }

  async function api(path, { method = "GET", body } = {}) {
    const opts = { method, headers: { "X-CSRFToken": getCookie("csrftoken") }, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(API + path, opts);
    if (resp.status === 204) return null;
    let data = null;
    try { data = await resp.json(); } catch (e) { /* non-JSON error page */ }
    if (!resp.ok) throw new ApiError(resp.status, data);
    return data;
  }

  // DRF errors → one readable line. Line errors arrive as a list aligned with
  // the submitted lines: [{}, {"unit": ["Can't convert…"]}]. Messages are
  // already translated server-side.
  function errorText(err) {
    if (!(err instanceof ApiError) || !err.data) return t("error_generic");
    const msgs = [];
    const walk = (val) => {
      if (Array.isArray(val)) val.forEach(walk);
      else if (val && typeof val === "object") Object.values(val).forEach(walk);
      else if (val) msgs.push(String(val));
    };
    walk(err.data);
    return msgs.join(" · ") || t("error_generic");
  }

  // ── Small UI helpers ───────────────────────────────────────────────────────
  const toastEl = $("#grim-toast");
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }

  function debounce(fn, ms) {
    let timer = null;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  function loading(container) {
    container.innerHTML = "";
    if (window.GrimoireLoader) {
      const wrap = document.createElement("div");
      wrap.className = "grim-loading";
      wrap.appendChild(window.GrimoireLoader.build(window.GrimoireLoader.randomCaption(), 110));
      container.appendChild(wrap);
    }
  }

  const CORNERS =
    '<div class="corner-tl" aria-hidden="true"></div><div class="corner-tr" aria-hidden="true"></div>' +
    '<div class="corner-bl" aria-hidden="true"></div><div class="corner-br" aria-hidden="true"></div>';
  const MARK = $(".grim-header-mark") ? $(".grim-header-mark").outerHTML.replace("grim-header-mark", "grimoire-empty-mark") : "";

  function emptyState(title, body, cta) {
    return `<div class="grimoire-empty">${CORNERS}${MARK}
      <h3 class="grimoire-empty-title">${esc(title)}</h3>
      <p class="grimoire-empty-body">${esc(body)}</p>${cta || ""}</div>`;
  }

  function fmtNum(n) {
    if (n == null || n === "") return "—";
    const v = Number(n);
    return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
  }

  function minutesText(s) {
    const total = (s.prep_minutes || 0) + (s.cook_minutes || 0);
    return total ? `<span>${esc(t("minutes", { n: "\u0000" })).replace("\u0000", `<strong>${total}</strong>`)}</span>` : "";
  }

  // Translated phrase with one bold number: escape the phrase, then drop the number in.
  function boldN(key, n) {
    return esc(t(key, { n: "\u0000" })).replace("\u0000", `<strong>${esc(n)}</strong>`);
  }
  const servingsText = (n) => (n === 1 ? esc(t("serving_one")) : boldN("serving_many", n));
  const ingredientsText = (n) => (n === 1 ? esc(t("ingredient_one")) : boldN("ingredient_many", n));

  // ── State ──────────────────────────────────────────────────────────────────
  const state = { tab: "spells", scope: "all", tag: "", q: "", tags: [], units: CFG.units === "us" ? "us" : "metric" };
  // Request counters: a response is applied only if no newer request of the
  // same kind started meanwhile, so a slow old search can't overwrite a new one.
  const seq = { spells: 0, reagents: 0, lineSearch: 0, matches: 0, detail: 0 };

  // ── Tabs, scope, units ────────────────────────────────────────────────────
  function setTab(tab, { push = true } = {}) {
    state.tab = tab;
    $$(".grim-tab").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    $$("[data-panel]").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.panel === tab));
    if (push) history.replaceState(null, "", `#${tab === "spells" ? state.scope : tab}`);
    if (tab === "spells") loadSpells();
    else if (tab === "castings") loadAllCastings();
    else loadReagents();
  }

  function setScope(scope, { load = true } = {}) {
    state.scope = scope;
    $$(".grim-scope-btn").forEach((b) => {
      const on = b.dataset.scope === scope;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    if (!load) return;
    history.replaceState(null, "", `#${scope}`);
    loadSpells();
  }

  async function setUnits(units) {
    if (units === state.units) return;
    state.units = units;
    $$(".grim-unit-btn").forEach((b) => {
      const on = b.dataset.units === units;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const open = PAGE === "spell" && spellBody()._spell;
    if (open) renderSpellDetail(open);
    const openReagent = PAGE === "reagent-view" && $("#grim-reagent-body")._reagent;
    if (openReagent) renderReagentPage(openReagent);
    try {
      await api("preferences/", { method: "PATCH", body: { unit_system: units } });
      toast(units === "us" ? t("units_saved_us") : t("units_saved_metric"));
    } catch (err) {
      toast(errorText(err));
    }
  }

  // ── Spell list ─────────────────────────────────────────────────────────────
  function spellCard(s) {
    const kicker = s.tags.length ? `${t("kicker_spell")} · ${s.tags.slice(0, 2).map(tagLabel).join(" · ")}` : t("kicker_spell");
    const badges = [];
    if (s.is_mine) badges.push([s.visibility === "public" ? t("badge_published") : t("badge_private"), ""]);
    if (s.forked_from) badges.push([t("badge_forked"), ""]);
    if (s.has_unverified_reagents) badges.push([t("badge_unverified"), " is-warn"]);
    if (s.has_estimated_nutrition) badges.push([t("badge_estimated"), " is-warn"]);
    const desc = spellDesc(s);
    const who = s.is_mine
      ? esc(t("yours"))
      : esc(t("by_name", { name: "\u0000" })).replace("\u0000", `<strong>${esc(s.owner_name)}</strong>`);
    return `<a class="grimoire-spell-card${s.is_mine ? " is-mine" : ""}" href="${esc(spellUrl(s.uuid))}">${CORNERS}
      <span class="grimoire-spell-kicker">${esc(kicker)}</span>
      <h3 class="grimoire-spell-title">${esc(spellTitle(s))}</h3>
      ${desc ? `<p class="grimoire-spell-desc">${esc(desc)}</p>` : ""}
      <div class="grimoire-spell-meta">${minutesText(s)}
        <span>${servingsText(s.servings)}</span>
        <span>${ingredientsText(s.lines.length)}</span></div>
      <div class="grimoire-spell-foot">
        <span class="grim-badges">${badges.map(([b, cls]) => `<span class="grim-badge${cls}">${esc(b)}</span>`).join("")}</span>
        <span class="grimoire-spell-meta"><span>${who}</span></span>
      </div></a>`;
  }

  async function loadSpells() {
    const grid = $("#grim-spells");
    const ticket = ++seq.spells;
    loading(grid);
    const params = new URLSearchParams({ scope: state.scope });
    if (state.tag) params.set("tag", state.tag);
    if (state.q) params.set("q", state.q);
    try {
      const spells = await api(`spells/?${params}`);
      if (ticket !== seq.spells) return;
      if (!spells.length) {
        const filtered = state.tag || state.q;
        const writeCta = `<a class="btn-cauldron btn-cauldron--primary" href="${esc(`${CFG.root}spell/new/`)}">${esc(t("write_spell"))}</a>`;
        if (filtered) grid.innerHTML = emptyState(t("empty_none_title"), t("empty_filtered_body"));
        else if (state.scope === "coven") grid.innerHTML = emptyState(t("empty_none_title"), t("empty_coven_body"));
        else grid.innerHTML = emptyState(t("empty_mine_title"), t("empty_mine_body"), writeCta);
        return;
      }
      grid.innerHTML = spells.map(spellCard).join("");
    } catch (err) {
      if (ticket === seq.spells) grid.innerHTML = emptyState(t("error_title"), errorText(err));
    }
  }

  function renderTagFilter() {
    const box = $("#grim-tag-filter");
    box.innerHTML = [`<button type="button" class="grimoire-tag${state.tag ? "" : " is-active"}" data-tag="" aria-pressed="${!state.tag}">${esc(t("scope_all").toLowerCase())}</button>`]
      .concat(state.tags.map((tg) =>
        `<button type="button" class="grimoire-tag${state.tag === tg.slug ? " is-active" : ""}" data-tag="${esc(tg.slug)}" aria-pressed="${state.tag === tg.slug}">${esc(tagLabel(tg.slug).toLowerCase())}</button>`))
      .join("");
  }

  // ── Spell page ─────────────────────────────────────────────────────────────
  const spellBody = () => $("#grim-spell-body");

  async function loadSpellPage(uuid) {
    const body = spellBody();
    body._spell = null; // actions stay inert until the spell has loaded
    loading(body);
    // Both requests at once; the castings section renders once the spell has.
    const castings = api(`spells/${encodeURIComponent(uuid)}/castings/`);
    castings.catch(() => {}); // handled in renderSpellCastings
    try {
      const spell = await api(`spells/${encodeURIComponent(uuid)}/`);
      renderSpellDetail(spell);
      renderSpellCastings(spell, castings);
    } catch (err) {
      body.innerHTML = `<p class="grim-error">${esc(err.status === 404 ? t("spell_missing") : errorText(err))}</p>`;
    }
  }

  // ``scaled`` (#69): this line's entry from the scale response — the rounded
  // amount is shown, the exact grams drive the weight and the hover hint.
  function lineChip(l, scaled, limiting) {
    if (scaled) l = { ...l, quantity: scaled.display_quantity, grams: scaled.grams };
    const tip = approxText(l);
    const tipAttrs = tip ? ` tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(`${fmtNum(l.quantity)} ${unitLabel(l.unit, l.quantity)} ${lineName(l)}, ${tip}`)}"` : "";
    const cls = `${l.reagent_status === "approved" ? "" : " is-unverified"}${tip ? " grim-tip" : ""}${scaled && scaled.factor !== 1 ? " is-scaled" : ""}${limiting && limiting === l.reagent ? " is-limiting" : ""}`;
    return `<li class="grimoire-reagent${cls}"${tipAttrs}>
        <span class="grimoire-reagent-qty">${esc(fmtNum(l.quantity))} ${esc(unitLabel(l.unit, l.quantity))}</span><a class="grim-reagent-link" href="${esc(reagentUrl(l.reagent))}">${esc(lineName(l))}</a>
        <span class="grim-grams">${esc(fmtWeight(l.grams))}</span>${lineNote(l) ? `<span class="grim-note">${esc(lineNote(l))}</span>` : ""}</li>`;
  }

  function renderSpellDetail(s) {
    const body = spellBody();
    const lines = s.lines.map((l) => lineChip(l)).join("");
    const steps = (spellSteps(s) || []).map((st) => `<li>${esc(st)}</li>`).join("");
    const desc = spellDesc(s);
    const kicker = s.is_mine
      ? (s.visibility === "public" ? t("kicker_mine_public") : t("kicker_mine_private"))
      : t("kicker_coven", { name: s.owner_name });
    const actions = s.is_mine
      ? `<a class="btn-cauldron btn-cauldron--ghost" href="${esc(editUrl(s.uuid))}">${esc(t("act_edit"))}</a>
         <button type="button" class="btn-cauldron btn-cauldron--ghost" data-action="${s.visibility === "public" ? "unpublish" : "publish"}">${esc(s.visibility === "public" ? t("act_unpublish") : t("act_publish"))}</button>
         <button type="button" class="btn-cauldron btn-cauldron--ghost grim-danger" data-action="delete">${esc(t("act_delete"))}</button>`
      : `<button type="button" class="btn-cauldron btn-cauldron--primary" data-action="fork">${esc(t("act_fork"))}</button>`;
    let forked = "";
    if (s.forked_from) {
      const src = (IS_ES && s.forked_from.title_es) || s.forked_from.title;
      forked = `<p class="grim-help">${esc(src ? t("forked_from", { title: src }) : t("forked_from_gone"))}</p>`;
    }
    body.innerHTML = `
      <span class="grimoire-spell-kicker">${esc(kicker)}</span>
      <h1 class="grim-h2 grim-spell-page-title" id="grim-spell-title">${esc(spellTitle(s))}</h1>
      ${desc ? `<p class="grimoire-spell-desc">${esc(desc)}</p>` : ""}
      <div class="grimoire-spell-meta">${minutesText(s)}
        ${s.prep_minutes != null ? `<span>${esc(t("prep"))} <strong>${esc(s.prep_minutes)}</strong></span>` : ""}
        ${s.cook_minutes != null ? `<span>${esc(t("cook"))} <strong>${esc(s.cook_minutes)}</strong></span>` : ""}
        <span>${servingsText(s.servings)}</span></div>
      <div class="grimoire-tags">${s.tags.map((slug) => `<span class="grimoire-tag">${esc(tagLabel(slug).toLowerCase())}</span>`).join("")}</div>
      ${s.has_unverified_reagents ? `<p class="grim-badge is-warn grim-unverified">${esc(t("unverified_note"))}</p>` : ""}
      ${s.has_estimated_nutrition ? `<p class="grim-badge is-warn grim-unverified">${esc(t("estimated_note"))}</p>` : ""}
      ${forked}
      <h3 class="grim-h3">${esc(t("h_reagents"))}</h3>
      ${lines ? scalerHtml(s) : ""}
      ${lines ? `<ul class="grim-detail-lines" id="grim-detail-lines">${lines}</ul>` : `<p class="grim-help">${esc(t("no_lines"))}</p>`}
      ${lines ? nutritionShellHtml(s) : ""}
      <h3 class="grim-h3">${esc(t("h_method"))}</h3>
      ${steps ? `<ol class="grim-steps">${steps}</ol>` : `<p class="grim-help">${esc(t("no_steps"))}</p>`}
      <div class="grim-actions">${actions}</div>`;
    body._spell = s;
    document.title = `${spellTitle(s)} — ${document.title.split(" — ").slice(-1)[0]}`;
    nutri.portionAsked = false; // the shell was just rebuilt
    if (lines) loadNutrition(s, 1);
  }

  // ── Scaling (#69) ──────────────────────────────────────────────────────────
  // Every change asks POST spells/{uuid}/scale/ (services/grimoire_scaling.py
  // does the maths and the rounding); the newest answer re-renders the lines.
  const scaler = { seq: 0 };
  const weightUnit = () => (state.units === "us" ? "oz" : "g");

  function scalerHtml(s) {
    const seen = new Set();
    const have = s.lines.filter((l) => !seen.has(l.reagent) && seen.add(l.reagent)).map((l) => `
      <div class="grim-have-row" data-reagent="${esc(l.reagent)}" data-name="${esc(lineName(l))}">
        <span class="grim-line-name">${esc(lineName(l))}</span>
        <input type="number" class="grim-have-qty" min="0" step="any" inputmode="decimal" aria-label="${esc(t("scale_have_label", { name: lineName(l) }))}">
        <select class="grim-have-unit" aria-label="${esc(t("line_unit", { name: lineName(l) }))}">${UNITS.map((u) =>
          `<option value="${u}"${u === l.unit ? " selected" : ""}>${esc(unitLabel(u))}</option>`).join("")}</select>
        <span class="grim-have-flag" role="status" hidden></span>
      </div>`).join("");
    return `
      <section class="grim-scale" id="grim-scale" aria-labelledby="grim-scale-title">
        <h3 class="grim-h3" id="grim-scale-title">${esc(t("scale_title"))}</h3>
        <div class="grim-scale-controls">
          <div class="grim-field">
            <span id="grim-scale-servings-label">${esc(t("scale_servings"))}</span>
            <div class="grim-stepper">
              <button type="button" class="grim-stepper-btn" data-step="-1" aria-label="${esc(t("scale_less"))}">−</button>
              <input type="number" id="grim-scale-servings" min="1" max="9999" step="1" inputmode="numeric" value="${esc(s.servings)}" aria-labelledby="grim-scale-servings-label">
              <button type="button" class="grim-stepper-btn" data-step="1" aria-label="${esc(t("scale_more"))}">+</button>
            </div>
          </div>
          <label class="grim-field grim-scale-size"><span>${esc(t("scale_size"))}</span>
            <span class="grim-scale-slider"><input type="range" id="grim-scale-factor" min="0.25" max="4" step="0.25" value="1">
            <output id="grim-scale-factor-out" for="grim-scale-factor">×1</output></span></label>
          <label class="grim-field"><span>${esc(t("scale_weight", { unit: unitLabel(weightUnit()) }))}</span>
            <input type="number" id="grim-scale-weight" min="0" step="any" inputmode="decimal" value="${esc(weightValue(totalGrams(s)))}"></label>
        </div>
        <button type="button" class="grim-scale-have-toggle" id="grim-scale-have-toggle" aria-expanded="false" aria-controls="grim-scale-have">${esc(t("scale_have"))}</button>
        <div class="grim-scale-have" id="grim-scale-have" hidden>
          <p class="grim-help">${esc(t("scale_have_help"))}</p>${have}
        </div>
        <p class="grim-scale-status" id="grim-scale-status" aria-live="polite"></p>
        <div class="grim-actions grim-scale-actions" id="grim-scale-actions" hidden>
          <button type="button" class="btn-cauldron btn-cauldron--ghost" id="grim-scale-reset">${esc(t("scale_reset"))}</button>
          <a class="btn-cauldron btn-cauldron--primary" id="grim-scale-cast" href="#">${esc(t("scale_cast"))}</a>
        </div>
      </section>`;
  }

  const totalGrams = (s) => s.lines.reduce((a, l) => a + l.grams, 0);

  function weightValue(grams) {
    const v = weightUnit() === "oz" ? grams / MASS_G.oz : grams;
    return Number(v.toFixed(v < 10 ? 1 : 0));
  }

  function setFactorOutput(factor) {
    $("#grim-scale-factor-out").textContent = `×${fmtNum(factor)}`;
  }

  function clearHave() {
    $$("#grim-scale-have .grim-have-row").forEach((row) => {
      row.querySelector(".grim-have-qty").value = "";
      row.classList.remove("is-limiting", "is-unconvertible");
      row.querySelector(".grim-have-flag").hidden = true;
    });
  }

  // ``source`` is the control the user moved; it's left alone, the rest follow.
  function applyScale(s, r, source) {
    const scaled = r.lines.map((l) => ({ ...l, factor: r.factor }));
    $("#grim-detail-lines").innerHTML = s.lines.map((l, i) => lineChip(l, scaled[i], r.limiting)).join("");
    if (source !== "servings") $("#grim-scale-servings").value = fmtNum(r.servings);
    if (source !== "factor") $("#grim-scale-factor").value = String(Math.min(4, Math.max(0.25, r.factor)));
    setFactorOutput(r.factor);
    if (source !== "weight") $("#grim-scale-weight").value = weightValue(r.total_grams);
    const servings = Number(fmtNum(r.servings));
    const parts = [t("scale_factor", { factor: fmtNum(r.factor) }),
      servings === 1 ? t("serving_one") : t("serving_many", { n: servings })];
    $$("#grim-scale-have .grim-have-row").forEach((row) => {
      const flag = row.querySelector(".grim-have-flag");
      const bad = (r.unconvertible || []).includes(row.dataset.reagent);
      row.classList.toggle("is-unconvertible", bad);
      row.classList.toggle("is-limiting", r.limiting === row.dataset.reagent);
      flag.hidden = !bad;
      if (bad) {
        flag.textContent = t("scale_unconvertible", {
          unit: unitLabel(row.querySelector(".grim-have-unit").value), name: row.dataset.name });
      }
      if (r.limiting === row.dataset.reagent) parts.push(t("scale_limited", { name: row.dataset.name }));
    });
    $("#grim-scale-status").textContent = r.factor === 1 ? "" : parts.join(" · ");
    $("#grim-scale-actions").hidden = r.factor === 1;
    $("#grim-scale-cast").href = `${castUrl(s.uuid)}?factor=${encodeURIComponent(r.factor)}`;
    nutritionSoon(s, r.factor);
  }

  function unscaled(s) {
    return {
      factor: 1, servings: s.servings, total_grams: totalGrams(s), limiting: null,
      unconvertible: [], lines: s.lines.map((l) => ({ display_quantity: l.quantity, grams: l.grams })),
    };
  }

  async function runScale(payload, source) {
    const s = spellBody()._spell;
    if (!s) return;
    const my = ++scaler.seq;
    try {
      const r = await api(`spells/${encodeURIComponent(s.uuid)}/scale/`, { method: "POST", body: payload });
      if (my === scaler.seq) applyScale(s, r, source);
    } catch (err) {
      if (my !== scaler.seq) return;
      const bad = (err.data && err.data.unconvertible) || [];
      applyScale(s, { ...unscaled(s), unconvertible: bad }, source);
      // The flag on each row already names an unconvertible amount; say only the message.
      $("#grim-scale-status").textContent = (err.data && err.data.detail) || errorText(err);
    }
  }
  const scaleSoon = debounce(runScale, 180);

  function haveAmounts() {
    return $$("#grim-scale-have .grim-have-row")
      .filter((row) => row.querySelector(".grim-have-qty").value !== "" && Number(row.querySelector(".grim-have-qty").value) > 0)
      .map((row) => ({
        reagent: row.dataset.reagent,
        quantity: Number(row.querySelector(".grim-have-qty").value),
        unit: row.querySelector(".grim-have-unit").value,
      }));
  }

  function onScaleInput(e) {
    const s = spellBody()._spell;
    const el = e.target;
    if (!s || !el.closest("#grim-scale")) return;
    if (el.id === "grim-scale-servings") {
      const n = Number(el.value);
      if (el.value === "" || !(n > 0)) return;
      clearHave();
      scaleSoon({ mode: "servings", servings: n }, "servings");
    } else if (el.id === "grim-scale-factor") {
      setFactorOutput(Number(el.value));
      clearHave();
      scaleSoon({ mode: "factor", factor: Number(el.value) }, "factor");
    } else if (el.id === "grim-scale-weight") {
      const v = Number(el.value);
      if (el.value === "" || !(v > 0)) return;
      clearHave();
      scaleSoon({ mode: "total_grams", grams: weightUnit() === "oz" ? v * MASS_G.oz : v }, "weight");
    } else if (el.closest(".grim-have-row")) {
      const available = haveAmounts();
      if (available.length) {
        scaleSoon({ mode: "available", available }, "have");
      } else {
        ++scaler.seq;
        applyScale(s, unscaled(s), "have");
      }
    }
  }

  function onScaleClick(e) {
    const s = spellBody()._spell;
    if (!s) return;
    const step = e.target.closest(".grim-stepper-btn");
    if (step) {
      const input = $("#grim-scale-servings");
      const n = Math.max(1, Math.round(Number(input.value) || s.servings) + Number(step.dataset.step));
      input.value = n;
      clearHave();
      scaleSoon({ mode: "servings", servings: n }, "servings");
      return;
    }
    if (e.target.closest("#grim-scale-reset")) {
      ++scaler.seq;
      clearHave();
      applyScale(s, unscaled(s), null);
      return;
    }
    const toggle = e.target.closest("#grim-scale-have-toggle");
    if (toggle) {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      $("#grim-scale-have").hidden = !open;
      const first = $("#grim-scale-have .grim-have-qty");
      if (open && first) first.focus();
    }
  }

  // ── Nutrition (#70) ────────────────────────────────────────────────────────
  // GET spells/{uuid}/nutrition/?scale= (services/grimoire_nutrition.py) and
  // POST …/portion/ for the recommender. Values with missing reagent data are
  // shown as "≥ x": the real amount can only be higher.
  const nutri = { seq: 0, data: null, view: "serving", portionSeq: 0, portion: null, portionAsked: false };
  const NUTRIENT_UNIT = Object.fromEntries(NUTRIENTS);
  const MACRO_KCAL = [["protein_g", 4, "protein"], ["carbs_g", 4, "carbs"], ["fat_g", 9, "fat"]];

  // "I ate this" / "Plan it" open the Journal with this spell preselected (#81, #83).
  const journalLink = (s, param) => `${CFG.journal}?${param}=${encodeURIComponent(s.uuid)}`;

  function nutritionShellHtml(s) {
    const views = [["batch", t("nut_view_batch")], ["serving", t("nut_view_serving")], ["100g", t("nut_view_100g")]];
    return `
      <section class="grim-nutrition" id="grim-nutrition" aria-labelledby="grim-nutrition-title">
        <div class="grim-nutrition-head">
          <h3 class="grim-h3" id="grim-nutrition-title">${esc(t("nut_title"))}</h3>
          <div class="grim-scope" role="group" aria-label="${esc(t("nut_view_label"))}">${views.map(([v, label]) =>
            `<button type="button" class="grim-scope-btn${v === nutri.view ? " is-on" : ""}" data-nut-view="${v}" aria-pressed="${v === nutri.view}">${esc(label)}</button>`).join("")}</div>
        </div>
        <p class="grim-nutrition-yield" id="grim-nutrition-yield"></p>
        <div id="grim-nutrition-body" aria-live="polite"></div>
        <div class="grim-portion" id="grim-portion" hidden>
          <h4 class="grim-section-title">${esc(t("portion_title"))}</h4>
          <p class="grim-help">${esc(t("portion_help"))} <a class="grim-back" href="${esc(CFG.profile)}">${esc(t("portion_edit_goals"))}</a></p>
          <p class="grim-portion-result" id="grim-portion-result" aria-live="polite"></p>
          <div class="grim-portion-try" id="grim-portion-try" hidden>
            <label class="grim-field"><span>${esc(t("portion_try"))}</span>
              <span class="grim-scale-slider"><input type="range" id="grim-portion-slider" min="0" max="1000" step="5" value="0">
              <output id="grim-portion-out" for="grim-portion-slider"></output></span></label>
            <ul class="grim-goal-status" id="grim-goal-status"></ul>
          </div>
        </div>
        <div class="grim-actions grim-journal-actions">
          <a class="btn-cauldron btn-cauldron--ghost" href="${esc(journalLink(s, "plan_spell"))}">${esc(t("journal_plan"))}</a>
          <a class="btn-cauldron btn-cauldron--primary" href="${esc(journalLink(s, "log_spell"))}">${esc(t("journal_ate"))}</a>
        </div>
      </section>`;
  }

  function fmtAmount(v) {
    if (v == null) return "—";
    const a = Math.abs(v);
    return String(Number(v.toFixed(a >= 10 ? 0 : a >= 1 ? 1 : 2)));
  }

  function nutrientValue(n) {
    if (!nutri.data) return null;
    if (nutri.view === "batch") return n.batch;
    if (nutri.view === "serving") return n.per_serving;
    return n.per_100g;
  }

  function nutrientText(n, value) {
    const shown = `${fmtAmount(value)} ${NUTRIENT_UNIT[n.key]}`;
    return n.missing ? `≥ ${shown}` : shown;
  }

  function renderNutrition() {
    const d = nutri.data;
    const body = $("#grim-nutrition-body");
    if (!d || !body) return;
    const byKey = Object.fromEntries(d.nutrients.map((n) => [n.key, n]));
    // Macro split: share of the macro energy (4/4/9 kcal per g), not a % of kcal.
    const kcalOf = MACRO_KCAL.map(([k, per]) => (nutrientValue(byKey[k]) || 0) * per);
    const kcalSum = kcalOf.reduce((a, b) => a + b, 0);
    const bars = MACRO_KCAL.map(([k, , cls], i) => {
      const n = byKey[k];
      const pct = kcalSum > 0 ? Math.round((kcalOf[i] / kcalSum) * 100) : 0;
      return `<div class="grimoire-nutrient grimoire-nutrient--${cls}">
          <span>${esc(t(`n_${k}`))}</span>
          <span class="grimoire-nutrient-track" role="img" aria-label="${esc(t("nut_energy_share", { pct: `${pct}%` }))}"><span class="grimoire-nutrient-fill" style="--pct:${pct}%"></span></span>
          <span class="grimoire-nutrient-value">${esc(nutrientText(n, nutrientValue(n)))} · ${pct}%</span>
        </div>`;
    }).join("");
    const groups = NUTRIENT_GROUPS.map(([group, fields]) => `<div class="grim-nutrient-group"><h3>${esc(t(group))}</h3>
      <dl class="grim-nutrient-table">${fields.map((f) => {
        const n = byKey[f];
        const title = n.missing ? ` title="${esc(n.missing === 1 ? t("nut_missing_one") : t("nut_missing_many", { n: n.missing }))}"` : "";
        return `<div${n.missing ? ' class="is-incomplete"' : ""}><dt>${esc(t(`n_${f}`))}</dt><dd${title}>${esc(nutrientText(n, nutrientValue(n)))}</dd></div>`;
      }).join("")}</dl></div>`).join("");
    const missing = d.reagents_missing_data || [];
    const names = missing.map((r) => (IS_ES && r.name_es) || r.name);
    const note = missing.length
      ? `<p class="grim-help grim-nutrition-missing">${esc(t("nut_incomplete", { names: names.join(", ") }))}</p>` : "";
    const kcal = byKey.kcal;
    body.innerHTML = `
      <p class="grim-nutrition-kcal"><strong>${esc(nutrientText(kcal, nutrientValue(kcal)))}</strong> ${esc(t(`nut_per_${nutri.view}`, { n: fmtNum(d.servings), weight: fmtWeight(d.cooked_g) }))}</p>
      <div class="grimoire-nutrients grim-macro-split">${bars}</div>
      <div class="grim-nutrient-groups">${groups}</div>${note}`;
    const y = d.yield;
    const yieldEl = $("#grim-nutrition-yield");
    const cooked = fmtWeight(d.cooked_g);
    yieldEl.classList.toggle("is-warn", y.source === "unknown");
    yieldEl.textContent = y.source === "casting" ? t("yield_casting", { weight: cooked, date: fmtDate(y.cast_on) })
      : y.source === "spell_weight" ? t("yield_spell_weight", { weight: cooked })
        : y.source === "spell_pct" ? t("yield_spell_pct", { weight: cooked, pct: `${fmtNum(y.ratio * 100)}%` })
          : t("yield_unknown", { weight: cooked });
    renderGoalStatus();
  }

  async function loadNutrition(s, factor) {
    const my = ++nutri.seq;
    try {
      const q = factor && factor !== 1 ? `?scale=${encodeURIComponent(factor)}` : "";
      const d = await api(`spells/${encodeURIComponent(s.uuid)}/nutrition/${q}`);
      if (my !== nutri.seq) return;
      nutri.data = d;
      renderNutrition();
      if (!nutri.portionAsked) {
        nutri.portionAsked = true;
        runPortion();
      }
    } catch (err) {
      if (my !== nutri.seq) return;
      const body = $("#grim-nutrition-body");
      if (body) body.innerHTML = `<p class="grim-error">${esc(errorText(err))}</p>`;
    }
  }
  const nutritionSoon = debounce(loadNutrition, 250);

  // ── Portion recommender ──
  // POST …/portion/ answers against what's left of the user's daily goals
  // today (goals − eaten − planned, #80/#83). No goals → the section stays hidden.
  const goalBounds = (g) => ({
    low: g.kind === "at_most" ? null : g.min_value,
    high: g.kind === "at_least" ? null : g.max_value,
  });

  async function runPortion() {
    const s = spellBody()._spell;
    const box = $("#grim-portion");
    if (!s || !box) return;
    const my = ++nutri.portionSeq;
    const result = $("#grim-portion-result");
    try {
      const p = await api(`spells/${encodeURIComponent(s.uuid)}/portion/`, { method: "POST", body: {} });
      if (my !== nutri.portionSeq) return;
      if (!p.has_goals) {
        box.hidden = true;
        nutri.portion = null;
        return;
      }
      box.hidden = false;
      nutri.portion = p;
      result.className = `grim-portion-result ${p.feasible ? "is-ok" : "is-bad"}`;
      const label = (k) => t(`n_${k}`);
      let text;
      if (!p.feasible) {
        text = p.over.length && p.conflicts.every((k) => p.over.includes(k))
          ? t("portion_over", { goals: p.over.map(label).join(", ") })
          : t("portion_conflict", { goals: p.conflicts.map(label).join(", ") });
      } else if (p.max_g == null) text = p.min_g ? t("portion_at_least", { min: fmtWeight(p.min_g) }) : t("portion_any");
      else if (!p.min_g) text = t("portion_up_to", { max: fmtWeight(p.max_g) });
      else text = t("portion_between", { min: fmtWeight(p.min_g), max: fmtWeight(p.max_g) });
      if (p.not_covered.length) text += ` ${t("portion_not_covered", { goals: p.not_covered.map(label).join(", ") })}`;
      if (p.incomplete.length) text += ` ${t("portion_incomplete", { goals: p.incomplete.map(label).join(", ") })}`;
      result.textContent = text;
      const slider = $("#grim-portion-slider");
      const top = Math.max(p.cooked_g || 0, (p.max_g || p.min_g || 0) * 1.5, 100);
      slider.max = String(Math.ceil(top / 5) * 5);
      const start = p.feasible
        ? (p.max_g == null ? (p.min_g || p.serving_g) : ((p.min_g || 0) + p.max_g) / 2)
        : Math.min(p.serving_g || 100, top);
      slider.value = String(Math.round(start / 5) * 5);
      $("#grim-portion-try").hidden = false;
      renderGoalStatus();
    } catch (err) {
      if (my !== nutri.portionSeq) return;
      nutri.portion = null;
      box.hidden = false;
      result.className = "grim-portion-result is-bad";
      result.textContent = errorText(err);
      $("#grim-portion-try").hidden = true;
    }
  }

  // Client-side preview only: the per-100 g values times the slider's grams.
  function renderGoalStatus() {
    const p = nutri.portion;
    const list = $("#grim-goal-status");
    if (!p || !list || !nutri.data) return;
    const grams = Number($("#grim-portion-slider").value);
    $("#grim-portion-out").textContent = fmtWeight(grams);
    const byKey = Object.fromEntries(nutri.data.nutrients.map((n) => [n.key, n]));
    list.innerHTML = p.goals.map((g) => {
      const n = byKey[g.nutrient];
      const day = g.consumed + (n.per_100g || 0) * grams / 100;
      const { low, high } = goalBounds(g);
      const ok = (low == null || day >= low) && (high == null || day <= high);
      const want = low != null && high != null ? `${fmtAmount(low)}–${fmtAmount(high)}`
        : low != null ? `≥ ${fmtAmount(low)}` : `≤ ${fmtAmount(high)}`;
      const shown = `${g.missing || n.missing ? "≥ " : ""}${fmtAmount(day)} ${NUTRIENT_UNIT[g.nutrient]}`;
      return `<li class="${ok ? "is-ok" : "is-bad"}"><span>${esc(t(`n_${g.nutrient}`))}</span>
        <strong>${esc(shown)}</strong> <span class="grim-help">${esc(t("portion_day_total"))} · ${esc(want)} ${esc(NUTRIENT_UNIT[g.nutrient])} · ${esc(ok ? t("goal_met") : t("goal_missed"))}</span></li>`;
    }).join("");
  }

  function onNutritionInput(e) {
    if (e.target.id === "grim-portion-slider") renderGoalStatus();
  }

  function onNutritionClick(e) {
    const view = e.target.closest("[data-nut-view]");
    if (view) {
      nutri.view = view.dataset.nutView;
      $$("#grim-nutrition [data-nut-view]").forEach((b) => {
        b.classList.toggle("is-on", b === view);
        b.setAttribute("aria-pressed", String(b === view));
      });
      renderNutrition();
      return true;
    }
    return false;
  }

  async function spellAction(action, btn) {
    const s = spellBody()._spell;
    if (!s) return;
    try {
      if (action === "publish" || action === "unpublish") {
        renderSpellDetail(await api(`spells/${s.uuid}/${action}/`, { method: "POST" }));
        toast(action === "publish" ? t("toast_published") : t("toast_unpublished"));
      } else if (action === "delete") {
        if (btn.dataset.armed !== "1") {
          btn.dataset.armed = "1";
          btn.textContent = t("act_delete_confirm");
          return;
        }
        await api(`spells/${s.uuid}/`, { method: "DELETE" });
        location.assign(`${CFG.root}#mine`);
      } else if (action === "fork") {
        const copy = await api(`spells/${s.uuid}/fork/`, { method: "POST" });
        location.assign(spellUrl(copy.uuid));
      }
    } catch (err) {
      toast(errorText(err));
    }
  }

  // ── Castings (#68) ─────────────────────────────────────────────────────────
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(`${iso}T00:00:00`);
    return d.toLocaleDateString(IS_ES ? "es-MX" : "en-US", { day: "numeric", month: "short", year: "numeric" });
  }

  function starsHtml(score) {
    if (!score) return `<span class="grim-help">${esc(t("cast_no_score"))}</span>`;
    return `<span class="grimoire-stars" aria-label="${esc(t("cast_star", { n: score }))}">${[1, 2, 3, 4, 5]
      .map((n) => `<span class="grimoire-star${n <= score ? " is-on" : ""}" aria-hidden="true">★</span>`).join("")}</span>`;
  }

  function castingCard(c, { showSpell = false } = {}) {
    const adj = (c.adjustments || []).map((a) =>
      `<li>${esc(a.reagent_name)}: ${a.original_qty != null
        ? `${esc(fmtNum(a.original_qty))}${a.original_unit && a.original_unit !== a.unit ? ` ${esc(unitLabel(a.original_unit, a.original_qty))}` : ""} → `
        : ""}<strong>${esc(fmtNum(a.used_qty))} ${esc(unitLabel(a.unit, a.used_qty))}</strong></li>`).join("");
    const title = (IS_ES && c.spell_title_es) || c.spell_title;
    const spellLine = !showSpell ? "" : c.spell_available
      ? `<a class="grim-casting-spell" href="${esc(spellUrl(c.spell))}">${esc(title)}</a>`
      : `<span class="grim-casting-spell is-removed">${esc(title)} · ${esc(t("cast_spell_removed"))}</span>`;
    return `<article class="grim-casting" data-casting="${esc(c.uuid)}">
      <header class="grim-casting-head">
        <time datetime="${esc(c.cast_on)}">${esc(fmtDate(c.cast_on))}</time>${starsHtml(c.score)}
      </header>
      ${spellLine}
      ${c.notes ? `<p class="grim-casting-notes">${esc(c.notes)}</p>` : ""}
      ${adj ? `<div class="grim-casting-adj"><span class="grim-help">${esc(t("cast_adjusted"))}</span><ul>${adj}</ul></div>` : ""}
      ${c.substitutions ? `<p class="grim-help"><strong>${esc(t("cast_substitutions"))}:</strong> ${esc(c.substitutions)}</p>` : ""}
      <div class="grim-casting-actions">
        <a class="grim-back" href="${esc(castingEditUrl(c.uuid))}">${esc(t("cast_edit"))}</a>
        <button type="button" class="grim-link-btn grim-danger" data-delete-casting="${esc(c.uuid)}">${esc(t("cast_delete"))}</button>
      </div>
    </article>`;
  }

  async function deleteCasting(btn, after) {
    if (btn.dataset.armed !== "1") {
      btn.dataset.armed = "1";
      btn.textContent = t("cast_delete_confirm");
      return;
    }
    try {
      await api(`castings/${encodeURIComponent(btn.dataset.deleteCasting)}/`, { method: "DELETE" });
      toast(t("cast_deleted"));
      after();
    } catch (err) {
      toast(errorText(err));
    }
  }

  // ``pending`` is the in-flight castings request (or a fresh one after a delete).
  async function renderSpellCastings(spell, pending) {
    const section = $("#grim-castings");
    if (!section) return;
    const body = $("#grim-castings-body");
    section.hidden = false;
    let castings = [];
    try {
      castings = await (pending || api(`spells/${encodeURIComponent(spell.uuid)}/castings/`));
    } catch (err) {
      body.innerHTML = `<p class="grim-error">${esc(errorText(err))}</p>`;
      return;
    }
    const n = castings.length;
    const stats = [n === 1 ? t("cast_count_one") : t("cast_count_many", { n })];
    const scored = castings.filter((c) => c.score);
    if (scored.length) stats.push(t("cast_avg", { score: fmtNum(scored.reduce((a, c) => a + c.score, 0) / scored.length) }));
    if (n) stats.push(t("cast_last", { date: fmtDate(castings[0].cast_on) }));
    body.innerHTML = `
      <div class="grim-castings-head">
        <h2 class="grim-section-title" id="grim-castings-title">${esc(t("castings_title"))}</h2>
        <a class="btn-cauldron btn-cauldron--primary" href="${esc(castUrl(spell.uuid))}">${esc(t("cast_this"))}</a>
      </div>
      ${n ? `<p class="grim-help">${esc(stats.join(" · "))}</p>
        <div class="grim-casting-list">${castings.map((c) => castingCard(c)).join("")}</div>`
        : `<p class="grim-help">${esc(t("castings_none"))}</p>`}`;
  }

  async function loadAllCastings() {
    const box = $("#grim-all-castings");
    loading(box);
    try {
      const castings = await api("castings/");
      box.innerHTML = castings.length
        ? castings.map((c) => castingCard(c, { showSpell: true })).join("")
        : emptyState(t("castings_title"), t("castings_none_all"));
    } catch (err) {
      box.innerHTML = emptyState(t("error_title"), errorText(err));
    }
  }

  // Cast page: date, score, notes, amounts used (prefilled from the spell).
  const castForm = $("#grim-cast-form");
  let castScore = null;
  let castSpellUuid = null;

  function renderScore() {
    const box = $("#grim-score");
    box.innerHTML = [1, 2, 3, 4, 5].map((n) =>
      `<button type="button" class="grimoire-star${castScore && n <= castScore ? " is-on" : ""}" role="radio" aria-checked="${castScore === n}" aria-label="${esc(t("cast_star", { n }))}" data-score="${n}">★</button>`).join("");
  }

  function castLineRow(line) {
    const name = line.name;
    const row = document.createElement("div");
    row.className = "grim-adjustment";
    row.dataset.reagent = line.reagent || "";
    row.dataset.name = name;
    row.dataset.line = line.line ?? "";
    row.dataset.original = line.original_qty ?? "";
    row.dataset.originalUnit = line.original_unit || "";
    row.innerHTML = `
      <span class="grim-line-name">${esc(name)}</span>
      <span class="grim-help">${line.original_qty != null ? esc(t("cast_original", { qty: `${fmtNum(line.original_qty)} ${unitLabel(line.original_unit || line.unit, line.original_qty)}` })) : ""}</span>
      <input type="number" class="grim-line-qty" min="0" step="any" value="${esc(line.used_qty ?? "")}" aria-label="${esc(t("cast_used_label", { name }))}">
      <select class="grim-line-unit" aria-label="${esc(t("line_unit", { name }))}">${UNITS.map((u) =>
        `<option value="${u}"${u === line.unit ? " selected" : ""}>${esc(unitLabel(u))}</option>`).join("")}</select>`;
    return row;
  }

  // ``scaled`` (#69): the scale response when the cast page was opened from
  // "Cast with these amounts" — each line starts at its scaled amount.
  function fillCastForm(spell, casting, scaled) {
    const F = castForm.elements;
    F.cast_on.value = casting ? casting.cast_on : new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local
    F.notes.value = casting ? casting.notes : "";
    F.substitutions.value = casting ? casting.substitutions : "";
    F.cooked_weight_g.value = casting && casting.cooked_weight_g != null ? casting.cooked_weight_g : "";
    castScore = casting ? casting.score : null;
    renderScore();
    // Match each saved adjustment to one spell line: same position and reagent
    // first, then the first unmatched line with that reagent. Each adjustment
    // is used once, so a reagent listed twice keeps its two changes apart.
    const pendingAdj = (casting ? casting.adjustments : []).slice();
    const take = (pred) => {
      const i = pendingAdj.findIndex(pred);
      return i === -1 ? null : pendingAdj.splice(i, 1)[0];
    };
    const lines = spell ? spell.lines : [];
    const matched = lines.map((l, i) => take((a) => a.line === i && (a.reagent || null) === (l.reagent || null)));
    lines.forEach((l, i) => {
      if (!matched[i]) matched[i] = take((a) => a.reagent && a.reagent === l.reagent);
    });
    const rows = lines.map((l, i) => {
      const a = matched[i];
      return {
        line: i, reagent: l.reagent, name: lineName(l), original_qty: l.quantity, original_unit: l.unit,
        used_qty: a ? a.used_qty : (scaled ? scaled.lines[i].display_quantity : l.quantity),
        unit: a ? a.unit : l.unit,
      };
    });
    // Adjustments whose line is gone (spell edited or removed) still show.
    pendingAdj.forEach((a) => rows.push({
      line: null, reagent: a.reagent, name: a.reagent_name, original_qty: a.original_qty,
      original_unit: a.original_unit || "", used_qty: a.used_qty, unit: a.unit,
    }));
    const box = $("#grim-cast-lines");
    box.innerHTML = "";
    rows.forEach((r) => box.appendChild(castLineRow(r)));
    if (spell) $("#grim-cast-spell").textContent = spellTitle(spell);
    else if (casting) $("#grim-cast-spell").textContent = `${casting.spell_title} · ${t("cast_spell_removed")}`;
  }

  async function saveCasting(e) {
    e.preventDefault();
    const err = $("#grim-cast-error");
    err.hidden = true;
    const F = castForm.elements;
    if (!F.cast_on.value) {
      err.textContent = t("cast_need_date");
      err.hidden = false;
      return;
    }
    const adjustments = [];
    for (const row of $$("#grim-cast-lines .grim-adjustment")) {
      const used = row.querySelector(".grim-line-qty").value;
      const unit = row.querySelector(".grim-line-unit").value;
      if (used === "" || !(Number(used) >= 0)) {
        err.textContent = t("cast_need_amount", { name: row.dataset.name });
        err.hidden = false;
        return;
      }
      const original = row.dataset.original === "" ? null : Number(row.dataset.original);
      const originalUnit = row.dataset.originalUnit || null;
      if (Number(used) === original && unit === originalUnit) continue; // unchanged
      adjustments.push({
        line: row.dataset.line === "" ? null : Number(row.dataset.line),
        reagent: row.dataset.reagent || null, reagent_name: row.dataset.name,
        original_qty: original, original_unit: originalUnit, used_qty: Number(used), unit,
      });
    }
    const payload = {
      cast_on: F.cast_on.value, score: castScore, notes: F.notes.value,
      substitutions: F.substitutions.value, adjustments,
      cooked_weight_g: F.cooked_weight_g.value === "" ? null : Number(F.cooked_weight_g.value),
    };
    try {
      const saved = CFG.casting
        ? await api(`castings/${encodeURIComponent(CFG.casting)}/`, { method: "PUT", body: payload })
        : await api(`spells/${encodeURIComponent(castSpellUuid)}/castings/`, { method: "POST", body: payload });
      location.assign(saved.spell_available ? spellUrl(saved.spell) : `${CFG.root}#castings`);
    } catch (ex) {
      err.textContent = errorText(ex);
      err.hidden = false;
    }
  }

  async function initCastPage() {
    $("#grim-score").addEventListener("click", (e) => {
      const b = e.target.closest("[data-score]");
      if (!b) return;
      castScore = Number(b.dataset.score);
      renderScore();
    });
    $("#grim-score-clear").addEventListener("click", () => { castScore = null; renderScore(); });
    castForm.addEventListener("submit", saveCasting);
    try {
      let casting = null;
      let spell = null;
      if (CFG.casting) {
        casting = await api(`castings/${encodeURIComponent(CFG.casting)}/`);
        if (casting.spell_available) {
          spell = await api(`spells/${encodeURIComponent(casting.spell)}/`);
          $("#grim-cast-back").href = spellUrl(casting.spell);
          $("#grim-cast-cancel").href = spellUrl(casting.spell);
        } else {
          $("#grim-cast-error").textContent = t("cast_spell_gone");
          $("#grim-cast-error").hidden = false;
        }
      } else {
        spell = await api(`spells/${encodeURIComponent(CFG.spell)}/`);
      }
      castSpellUuid = spell ? spell.uuid : null;
      let scaled = null;
      const factor = Number(new URLSearchParams(location.search).get("factor"));
      if (spell && !casting && factor > 0 && factor !== 1) {
        // A bad ?factor= just means the spell's own amounts.
        scaled = await api(`spells/${encodeURIComponent(spell.uuid)}/scale/`, {
          method: "POST", body: { mode: "factor", factor } }).catch(() => null);
      }
      fillCastForm(spell, casting, scaled);
    } catch (err) {
      $("#grim-cast-error").textContent = errorText(err);
      $("#grim-cast-error").hidden = false;
    }
  }

  // ── Spell editor ───────────────────────────────────────────────────────────
  const form = $("#grim-editor-form");
  let editing = null; // spell being edited, or null for a new one

  function lineRow(line) {
    const name = line.name;
    const row = document.createElement("div");
    row.className = "grim-line";
    row.dataset.reagent = line.reagent;
    row.innerHTML = `
      <span class="grim-line-name">${esc(name)}</span>
      <input type="number" class="grim-line-qty" min="0" step="any" value="${esc(line.quantity ?? "")}" aria-label="${esc(t("line_qty", { name }))}" required>
      <select class="grim-line-unit" aria-label="${esc(t("line_unit", { name }))}">${UNITS.map((u) =>
        `<option value="${u}"${u === (line.unit || "g") ? " selected" : ""}>${esc(unitLabel(u))}</option>`).join("")}</select>
      <input type="text" class="grim-line-note" maxlength="120" placeholder="${esc(t("line_note_placeholder"))}" value="${esc(line.note || "")}" aria-label="${esc(t("line_note", { name }))}">
      <button type="button" class="grim-line-remove" aria-label="${esc(t("line_remove", { name }))}">✕</button>`;
    return row;
  }

  function openEditor(spell) {
    editing = spell || null;
    form.reset();
    $("#grim-editor-error").hidden = true;
    const F = form.elements;
    // Users write their spells in one language — the base fields.
    F.title.value = spell ? spell.title : "";
    F.description.value = spell ? spell.description : "";
    F.servings.value = spell ? spell.servings : 2;
    F.prep_minutes.value = spell && spell.prep_minutes != null ? spell.prep_minutes : "";
    F.cook_minutes.value = spell && spell.cook_minutes != null ? spell.cook_minutes : "";
    F.cooked_yield_g.value = spell && spell.cooked_yield_g != null ? spell.cooked_yield_g : "";
    F.yield_pct.value = spell && spell.yield_pct != null ? spell.yield_pct : "";
    F.steps.value = spell ? spell.steps.join("\n") : "";
    const chosen = new Set(spell ? spell.tags : []);
    $("#grim-editor-tags").innerHTML = state.tags.map((tg) =>
      `<button type="button" class="grimoire-tag${chosen.has(tg.slug) ? " is-active" : ""}" data-tag="${esc(tg.slug)}" aria-pressed="${chosen.has(tg.slug)}">${esc(tagLabel(tg.slug).toLowerCase())}</button>`).join("");
    const lines = $("#grim-editor-lines");
    lines.innerHTML = "";
    (spell ? spell.lines : []).forEach((l) => lines.appendChild(lineRow({ ...l, name: lineName(l) })));
    $("#grim-line-search").value = "";
    $("#grim-line-results").hidden = true;
    F.title.focus();
  }

  const searchLineReagents = debounce(async (q) => {
    const list = $("#grim-line-results");
    const ticket = ++seq.lineSearch;
    if (!q.trim()) { list.hidden = true; return; }
    try {
      const found = await api(`reagents/?q=${encodeURIComponent(q)}`);
      if (ticket !== seq.lineSearch) return;
      list.innerHTML = found.length
        ? found.map((r) => `<li role="option" tabindex="0" data-uuid="${esc(r.uuid)}" data-name="${esc(reagentName(r))}">${esc(reagentName(r))}</li>`).join("")
        : `<li class="grim-help" aria-disabled="true">${esc(t("line_no_match"))}</li>`;
      list.hidden = false;
    } catch (err) {
      list.hidden = true;
    }
  }, 200);

  function addLineFrom(li) {
    if (!li || !li.dataset.uuid) return;
    const row = lineRow({ reagent: li.dataset.uuid, name: li.dataset.name, unit: "g" });
    $("#grim-editor-lines").appendChild(row);
    $("#grim-line-search").value = "";
    $("#grim-line-results").hidden = true;
    row.querySelector(".grim-line-qty").focus();
  }

  function numberOrNull(v) {
    return v === "" || v == null ? null : Number(v);
  }

  async function saveSpell(e) {
    e.preventDefault();
    const err = $("#grim-editor-error");
    err.hidden = true;
    const blank = $$("#grim-editor-lines .grim-line").find((row) => !(Number(row.querySelector(".grim-line-qty").value) > 0));
    if (blank) {
      err.textContent = t("line_need_amount", { name: blank.querySelector(".grim-line-name").textContent });
      err.hidden = false;
      blank.querySelector(".grim-line-qty").focus();
      return;
    }
    const F = form.elements;
    const payload = {
      title: F.title.value,
      description: F.description.value,
      servings: Number(F.servings.value) || 1,
      prep_minutes: numberOrNull(F.prep_minutes.value),
      cook_minutes: numberOrNull(F.cook_minutes.value),
      cooked_yield_g: numberOrNull(F.cooked_yield_g.value),
      yield_pct: numberOrNull(F.yield_pct.value),
      steps: F.steps.value.split("\n").map((s) => s.trim()).filter(Boolean),
      tags: $$("#grim-editor-tags .grimoire-tag.is-active").map((b) => b.dataset.tag),
      lines: $$("#grim-editor-lines .grim-line").map((row) => ({
        reagent: row.dataset.reagent,
        quantity: Number(row.querySelector(".grim-line-qty").value),
        unit: row.querySelector(".grim-line-unit").value,
        note: row.querySelector(".grim-line-note").value,
      })),
    };
    try {
      const saved = editing
        ? await api(`spells/${editing.uuid}/`, { method: "PUT", body: payload })
        : await api("spells/", { method: "POST", body: payload });
      location.assign(spellUrl(saved.uuid));
    } catch (ex) {
      err.textContent = errorText(ex);
      err.hidden = false;
    }
  }

  // ── Reagents ───────────────────────────────────────────────────────────────
  function reagentRow(r) {
    const n = r.nutrients || {};
    const status = (r.status === "approved" ? "" :
      `<span class="grim-badge">${esc(r.status === "pending" ? t("status_pending") : t("status_private"))}</span>`)
      + (r.nutrients_estimated ? ` <span class="grim-badge is-warn">${esc(t("badge_estimated"))}</span>` : "");
    const macro = (key, field) => `<div><dt>${esc(t(`macro_${key}`))}</dt><dd>${esc(fmtNum(n[field]))}</dd></div>`;
    return `<a class="grim-reagent-row" href="${esc(reagentUrl(r.uuid))}">
      <div class="grim-reagent-name"><strong>${esc(reagentName(r))}</strong>
        <span class="grim-help">· ${esc(t(`cat_${r.category}`))}</span> ${status}</div>
      <dl class="grim-macros">${macro("kcal", "kcal")}${macro("protein", "protein_g")}${macro("carbs", "carbs_g")}${macro("fat", "fat_g")}${macro("fiber", "fiber_g")}</dl></a>`;
  }

  async function loadReagents() {
    const box = $("#grim-reagents");
    const ticket = ++seq.reagents;
    loading(box);
    const q = $("#grim-reagent-q").value.trim();
    try {
      const found = await api(q ? `reagents/?q=${encodeURIComponent(q)}` : "reagents/");
      if (ticket !== seq.reagents) return;
      box.innerHTML = found.length
        ? found.map(reagentRow).join("")
        : emptyState(t("empty_reagent_title"), t("empty_reagent_body"),
            `<a class="btn-cauldron btn-cauldron--primary" href="${esc(`${CFG.root}reagent/new/${q ? `?name=${encodeURIComponent(q)}` : ""}`)}">${esc(t("add_reagent"))}</a>`);
    } catch (err) {
      if (ticket === seq.reagents) box.innerHTML = emptyState(t("error_title"), errorText(err));
    }
  }

  const rform = $("#grim-reagent-form"); // reagent page only
  let confirmDuplicate = false;

  // Edit mode: fill the form from the saved reagent.
  function fillReagentForm(r) {
    const R = rform.elements;
    R.name.value = r.name || "";
    R.name_es.value = r.name_es || "";
    R.category.value = r.category;
    ["cup", "tbsp", "piece", "glass"].forEach((u) => { R[`unit_${u}`].value = (r.grams_per_unit || {})[u] ?? ""; });
    R.density_g_per_ml.value = r.density_g_per_ml ?? "";
    NUTRIENTS.forEach(([field]) => { R[field].value = (r.nutrients || {})[field] ?? ""; });
    R.share.checked = r.status === "pending";
    confirmDuplicate = true; // its own name is never a duplicate of itself
  }

  // ── Reagent page ───────────────────────────────────────────────────────────
  function renderReagentPage(r) {
    const body = $("#grim-reagent-body");
    body._reagent = r;
    const other = IS_ES ? r.name : r.name_es;
    const n = r.nutrients || {};
    const status = (r.status === "approved" ? "" :
      `<span class="grim-badge">${esc(r.status === "pending" ? t("status_pending") : t("status_private"))}</span>`)
      + (r.nutrients_estimated ? ` <span class="grim-badge is-warn">${esc(t("badge_estimated"))}</span>` : "");
    const g = r.grams_per_unit || {};
    const measures = ["glass", "cup", "tbsp", "tsp", "piece"].filter((u) => g[u]).map((u) =>
      `<div><dt>${esc(t(`measure_${u}`))}</dt><dd>${esc(fmtWeight(g[u]))}</dd></div>`);
    if (r.density_g_per_ml) {
      measures.push(`<div><dt>${esc(t("measure_density"))}</dt><dd>${esc(fmtNum(r.density_g_per_ml))} g/ml</dd></div>`);
    }
    const unitOf = Object.fromEntries(NUTRIENTS);
    const groups = NUTRIENT_GROUPS.map(([group, fields]) => `<div class="grim-nutrient-group"><h3>${esc(t(group))}</h3>
      <dl class="grim-nutrient-table">${fields.map((f) =>
        `<div><dt>${esc(t(`n_${f}`))}</dt><dd>${n[f] == null ? "—" : `${esc(fmtNum(n[f]))} <span class="grim-help">${esc(unitOf[f])}</span>`}</dd></div>`).join("")}</dl></div>`).join("");
    const kicker = t(`reagent_kicker_${r.source}`);
    const action = r.can_edit
      ? `<a class="btn-cauldron btn-cauldron--ghost" href="${esc(`${reagentUrl(r.uuid)}edit/`)}">${esc(t("reagent_edit"))}</a>`
      : (r.is_mine ? `<p class="grim-help">${esc(t("reagent_locked"))}</p>` : "");
    body.innerHTML = `
      <span class="grimoire-spell-kicker">${esc(kicker)}</span> ${status}
      <h1 class="grim-h2 grim-spell-page-title">${esc(reagentName(r))}</h1>
      <p class="grimoire-spell-desc">${esc(t(`cat_${r.category}`))}${other ? ` · ${esc(t("reagent_also_known", { name: other }))}` : ""}</p>
      ${r.fdc_id ? `<p><a class="grim-back" href="https://fdc.nal.usda.gov/food-details/${encodeURIComponent(r.fdc_id)}/nutrients" target="_blank" rel="noopener">${esc(t("reagent_usda_link"))}</a></p>` : ""}
      <section class="grim-section">
        <h2 class="grim-section-title">${esc(t("reagent_measures"))}</h2>
        ${measures.length ? `<dl class="grim-nutrient-table">${measures.join("")}</dl>` : `<p class="grim-help">${esc(t("reagent_no_measures"))}</p>`}
      </section>
      <section class="grim-section">
        <h2 class="grim-section-title">${esc(t("reagent_per_100"))}</h2>
        <div class="grim-nutrient-groups">${groups}</div>
      </section>
      <div class="grim-actions">${action}</div>`;
  }

  async function loadReagentPage(uuid) {
    const body = $("#grim-reagent-body");
    loading(body);
    try {
      renderReagentPage(await api(`reagents/${encodeURIComponent(uuid)}/`));
    } catch (err) {
      body.innerHTML = `<p class="grim-error">${esc(err.status === 404 ? t("reagent_missing") : errorText(err))}</p>`;
    }
  }

  function openReagentForm(prefill) {
    // Prefill whichever name field matches the UI language.
    const field = rform.elements[IS_ES ? "name_es" : "name"];
    field.value = prefill || "";
    field.focus();
    if (prefill) showCloseMatches(prefill);
  }

  // "Add a reagent" carries the current search over as the name.
  function syncNewReagentLink() {
    const link = $("#grim-new-reagent");
    if (!link) return;
    const q = $("#grim-reagent-q").value.trim();
    link.href = `${CFG.root}reagent/new/${q ? `?name=${encodeURIComponent(q)}` : ""}`;
  }

  const showCloseMatches = debounce(async (name) => {
    const box = $("#grim-reagent-matches");
    const ticket = ++seq.matches;
    if (name.trim().length < 2) { box.hidden = true; return; }
    try {
      const found = await api(`reagents/?q=${encodeURIComponent(name)}`);
      if (ticket !== seq.matches) return;
      if (!found.length) { box.hidden = true; return; }
      box.innerHTML = `<span class="grim-help">${esc(t("already_here"))}</span> ` +
        found.slice(0, 6).map((r) => `<span class="grimoire-reagent">${esc(reagentName(r))}</span>`).join(" ");
      box.hidden = false;
    } catch (err) {
      box.hidden = true;
    }
  }, 250);

  async function saveReagent(e) {
    e.preventDefault();
    const err = $("#grim-reagent-error");
    err.hidden = true;
    const R = rform.elements;
    const nutrients = {};
    NUTRIENTS.forEach(([field]) => { nutrients[field] = numberOrNull(R[field].value); });
    const grams = {};
    ["cup", "tbsp", "piece", "glass"].forEach((u) => {
      const v = R[`unit_${u}`].value;
      if (v !== "") grams[u] = Number(v);
    });
    // The catalog keys on the English name; a Spanish-only entry uses it as both.
    const payload = {
      name: R.name.value.trim() || R.name_es.value.trim(),
      name_es: R.name_es.value,
      category: R.category.value,
      grams_per_unit: grams,
      density_g_per_ml: numberOrNull(R.density_g_per_ml.value),
      nutrients,
      share: R.share.checked,
      confirm: confirmDuplicate,
    };
    try {
      const saved = CFG.reagent
        ? await api(`reagents/${encodeURIComponent(CFG.reagent)}/`, { method: "PUT", body: payload })
        : await api("reagents/", { method: "POST", body: payload });
      location.assign(reagentUrl(saved.uuid));
    } catch (ex) {
      if (ex instanceof ApiError && ex.status === 409 && ex.data && ex.data.code === "duplicate") {
        confirmDuplicate = true;
        err.textContent = t("duplicate", { names: ex.data.matches.map(reagentName).join("”, “") });
        $("#grim-reagent-submit").textContent = t("add_anyway");
      } else {
        err.textContent = errorText(ex);
      }
      err.hidden = false;
    }
  }

  // ── Wiring (per page) ──────────────────────────────────────────────────────
  function wireUnits() {
    $$(".grim-unit-btn").forEach((b) => b.addEventListener("click", () => setUnits(b.dataset.units)));
  }

  function wireList() {
    $$(".grim-tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
    $$(".grim-scope-btn").forEach((b) => b.addEventListener("click", () => setScope(b.dataset.scope)));
    $("#grim-spell-q").addEventListener("input", debounce((e) => { state.q = e.target.value.trim(); loadSpells(); }, 250));
    $("#grim-tag-filter").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tag]");
      if (!b) return;
      state.tag = b.dataset.tag;
      renderTagFilter();
      loadSpells();
    });

    $("#grim-reagent-q").addEventListener("input", debounce(() => { syncNewReagentLink(); loadReagents(); }, 250));
    $("#grim-all-castings").addEventListener("click", (e) => {
      const b = e.target.closest("[data-delete-casting]");
      if (b) deleteCasting(b, loadAllCastings);
    });
  }

  function wireReagentForm() {
    ["name", "name_es"].forEach((field) => rform.elements[field].addEventListener("input", (e) => {
      confirmDuplicate = false;
      $("#grim-reagent-submit").textContent = t("add_reagent_submit");
      showCloseMatches(e.target.value);
    }));
    rform.addEventListener("submit", saveReagent);
  }

  function wireSpell() {
    spellBody().addEventListener("click", (e) => {
      const b = e.target.closest("[data-action]");
      if (b) spellAction(b.dataset.action, b);
      else if (!onNutritionClick(e)) onScaleClick(e);
    });
    spellBody().addEventListener("input", (e) => {
      if (e.target.closest("#grim-nutrition")) onNutritionInput(e);
      else onScaleInput(e);
    });
    spellBody().addEventListener("change", (e) => {
      if (e.target.closest("#grim-nutrition")) onNutritionInput(e);
      else if (e.target.classList.contains("grim-have-unit")) onScaleInput(e);
    });
    $("#grim-castings-body").addEventListener("click", (e) => {
      const b = e.target.closest("[data-delete-casting]");
      if (b) deleteCasting(b, () => renderSpellCastings(spellBody()._spell));
    });
  }

  function wireEditor() {
    $("#grim-editor-tags").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tag]");
      if (!b) return;
      b.classList.toggle("is-active");
      b.setAttribute("aria-pressed", b.classList.contains("is-active"));
    });
    $("#grim-editor-lines").addEventListener("click", (e) => {
      const b = e.target.closest(".grim-line-remove");
      if (b) b.closest(".grim-line").remove();
    });
    $("#grim-line-search").addEventListener("input", (e) => searchLineReagents(e.target.value));
    $("#grim-line-search").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addLineFrom($("#grim-line-results li[data-uuid]"));
      }
    });
    $("#grim-line-results").addEventListener("click", (e) => addLineFrom(e.target.closest("li[data-uuid]")));
    $("#grim-line-results").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addLineFrom(e.target.closest("li[data-uuid]")); }
    });
    form.addEventListener("submit", saveSpell);
  }

  // Nutrient fields grouped the way a label reads them.
  const NUTRIENT_GROUPS = [
    ["grp_macros", ["kcal", "protein_g", "carbs_g", "sugar_g", "added_sugar_g", "fiber_g"]],
    ["grp_fats", ["fat_g", "sat_fat_g", "mono_fat_g", "poly_fat_g", "trans_fat_g",
      "omega3_ala_g", "omega3_epa_g", "omega3_dha_g", "omega6_la_g"]],
    ["grp_minerals", ["cholesterol_mg", "sodium_mg", "potassium_mg", "calcium_mg", "iron_mg"]],
    ["grp_vitamins", ["vit_a_ug", "vit_c_mg", "vit_d_ug", "vit_b12_ug"]],
  ];

  function buildReagentForm() {
    $("#grim-reagent-category").innerHTML = CATEGORIES.map((c) =>
      `<option value="${c}"${c === "other" ? " selected" : ""}>${esc(t(`cat_${c}`))}</option>`).join("");
    const unitOf = Object.fromEntries(NUTRIENTS);
    $("#grim-reagent-nutrients").innerHTML = NUTRIENT_GROUPS.map(([group, fields]) =>
      `<div class="grim-nutrient-group"><h3>${esc(t(group))}</h3><div class="grim-nutrient-grid">${fields.map((field) =>
        `<label class="grim-field"><span>${esc(t(`n_${field}`))}</span>
          <span class="grim-unit-input"><input name="${field}" type="number" min="0" step="any" inputmode="decimal">
          <span class="grim-unit-suffix">${esc(unitOf[field])}</span></span></label>`).join("")}</div></div>`).join("");
  }

  async function loadTags() {
    try {
      state.tags = await api("tags/");
    } catch (err) {
      state.tags = [];
    }
    TAG_LABELS = Object.fromEntries(state.tags.map((tg) => [tg.slug, (IS_ES && tg.name_es) || tg.name]));
  }

  async function init() {
    wireUnits();
    if (PAGE === "spell") {
      wireSpell();
      await loadTags();
      loadSpellPage(CFG.spell);
      return;
    }
    if (PAGE === "cast") {
      initCastPage();
      return;
    }
    if (PAGE === "reagent-view") {
      loadReagentPage(CFG.reagent);
      return;
    }
    if (PAGE === "reagent") {
      buildReagentForm();
      wireReagentForm();
      if (CFG.reagent) {
        try {
          fillReagentForm(await api(`reagents/${encodeURIComponent(CFG.reagent)}/`));
        } catch (err) {
          $("#grim-reagent-error").textContent = errorText(err);
          $("#grim-reagent-error").hidden = false;
        }
        return;
      }
      openReagentForm(CFG.prefillName || "");
      return;
    }
    if (PAGE === "edit") {
      wireEditor();
      await loadTags();
      let spell = null;
      if (CFG.spell) {
        try {
          spell = await api(`spells/${encodeURIComponent(CFG.spell)}/`);
        } catch (err) {
          $("#grim-editor-error").textContent = errorText(err);
          $("#grim-editor-error").hidden = false;
          return;
        }
      }
      openEditor(spell);
      return;
    }
    wireList();
    await loadTags();
    renderTagFilter();
    const presetQ = new URLSearchParams(location.search).get("q");
    if (presetQ) $("#grim-reagent-q").value = presetQ;
    syncNewReagentLink();
    const start = location.hash.replace("#", "");
    if (start === "reagents" || start === "castings") {
      setTab(start, { push: false });
    } else {
      if (["all", "mine", "coven"].includes(start)) setScope(start, { load: false });
      setTab("spells", { push: false });
    }
  }

  init();
})();
