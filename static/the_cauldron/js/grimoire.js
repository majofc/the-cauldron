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
  const IS_ES = String(CFG.lang || "").toLowerCase().startsWith("es");
  const I18N = JSON.parse(document.getElementById("grimoire-i18n").textContent);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function t(key, vars) {
    let s = I18N[key] || key;
    if (vars) s = s.replace(/%\((\w+)\)s/g, (_, name) => (name in vars ? vars[name] : ""));
    return s;
  }

  const UNITS = ["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "oz", "lb", "piece"];
  const MASS_G = { g: 1, kg: 1000, oz: 28.349523125, lb: 453.59237 };
  const VOLUME_ML = { ml: 1, l: 1000, tsp: 4.92892, tbsp: 14.7868, cup: 236.588 }; // US spoons/cup
  const CATEGORIES = ["vegetable", "fruit", "grain", "legume", "protein", "dairy", "fat",
    "nut_seed", "herb_spice", "sweetener", "condiment", "beverage", "other"];
  // Mirrors ReagentNutrients.FIELDS — field → display unit
  const NUTRIENTS = [
    ["kcal", "kcal"], ["protein_g", "g"], ["carbs_g", "g"], ["sugar_g", "g"], ["fiber_g", "g"],
    ["fat_g", "g"], ["sat_fat_g", "g"], ["mono_fat_g", "g"], ["poly_fat_g", "g"],
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
    $('[data-panel="spells"]').classList.toggle("is-active", tab === "spells");
    $('[data-panel="reagents"]').classList.toggle("is-active", tab === "reagents");
    if (push) history.replaceState(null, "", `#${tab === "spells" ? state.scope : "reagents"}`);
    if (tab === "spells") loadSpells();
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
    try {
      renderSpellDetail(await api(`spells/${encodeURIComponent(uuid)}/`));
    } catch (err) {
      body.innerHTML = `<p class="grim-error">${esc(err.status === 404 ? t("spell_missing") : errorText(err))}</p>`;
    }
  }

  function lineChip(l) {
    const tip = approxText(l);
    const tipAttrs = tip ? ` tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(`${fmtNum(l.quantity)} ${unitLabel(l.unit, l.quantity)} ${lineName(l)}, ${tip}`)}"` : "";
    return `<li class="grimoire-reagent${l.reagent_status === "approved" ? "" : " is-unverified"}${tip ? " grim-tip" : ""}"${tipAttrs}>
        <span class="grimoire-reagent-qty">${esc(fmtNum(l.quantity))} ${esc(unitLabel(l.unit, l.quantity))}</span><a class="grim-reagent-link" href="${esc(reagentUrl(l.reagent))}">${esc(lineName(l))}</a>
        <span class="grim-grams">${esc(fmtWeight(l.grams))}</span>${lineNote(l) ? `<span class="grim-note">${esc(lineNote(l))}</span>` : ""}</li>`;
  }

  function renderSpellDetail(s) {
    const body = spellBody();
    const lines = s.lines.map(lineChip).join("");
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
      ${forked}
      <h3 class="grim-h3">${esc(t("h_reagents"))}</h3>
      ${lines ? `<ul class="grim-detail-lines">${lines}</ul>` : `<p class="grim-help">${esc(t("no_lines"))}</p>`}
      <h3 class="grim-h3">${esc(t("h_method"))}</h3>
      ${steps ? `<ol class="grim-steps">${steps}</ol>` : `<p class="grim-help">${esc(t("no_steps"))}</p>`}
      <div class="grim-actions">${actions}</div>`;
    body._spell = s;
    document.title = `${spellTitle(s)} — ${document.title.split(" — ").slice(-1)[0]}`;
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
    const status = r.status === "approved" ? "" :
      `<span class="grim-badge">${esc(r.status === "pending" ? t("status_pending") : t("status_private"))}</span>`;
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
    ["cup", "tbsp", "piece"].forEach((u) => { R[`unit_${u}`].value = (r.grams_per_unit || {})[u] ?? ""; });
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
    const status = r.status === "approved" ? "" :
      `<span class="grim-badge">${esc(r.status === "pending" ? t("status_pending") : t("status_private"))}</span>`;
    const g = r.grams_per_unit || {};
    const measures = ["cup", "tbsp", "tsp", "piece"].filter((u) => g[u]).map((u) =>
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
    ["cup", "tbsp", "piece"].forEach((u) => {
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
    ["grp_macros", ["kcal", "protein_g", "carbs_g", "sugar_g", "fiber_g"]],
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
    if (start === "reagents") {
      setTab("reagents", { push: false });
    } else {
      if (["all", "mine", "coven"].includes(start)) setScope(start, { load: false });
      setTab("spells", { push: false });
    }
  }

  init();
})();
