/* Daily nutrition front-end (#80–#84): legal gate, sensitive-data consent,
   daily goals (profile) and the Journal: the moving 24 h / 7-day window, time in
   range, the meal log and the ritual planner.
   Talks to /cauldron/api/ (DRF). Vanilla fetch + DOM, like grimoire.js.
   Every server string reaches the DOM through esc() or textContent.
   UI strings come translated from the server (nutrition_i18n.py → #nutrition-i18n). */
(function () {
  "use strict";

  const CFG = window.NUTRITION || {};
  const API = CFG.api; // /cauldron/api/
  const GAPI = CFG.grimoireApi; // /cauldron/api/grimoire/
  const PAGE = CFG.page;
  const IS_ES = String(CFG.lang || "").toLowerCase().startsWith("es");
  const I18N = JSON.parse(document.getElementById("nutrition-i18n").textContent);
  const PREFILL = JSON.parse(document.getElementById("nutrition-prefill").textContent);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function t(key, vars) {
    let s = I18N[key] || key;
    if (vars) s = s.replace(/%\((\w+)\)s/g, (_, name) => (name in vars ? vars[name] : ""));
    return s;
  }

  // Mirrors ReagentNutrients.FIELDS (field → display unit) and grimoire.js's groups.
  const NUTRIENTS = [
    ["kcal", "kcal"], ["protein_g", "g"], ["carbs_g", "g"], ["sugar_g", "g"], ["fiber_g", "g"],
    ["fat_g", "g"], ["sat_fat_g", "g"], ["mono_fat_g", "g"], ["poly_fat_g", "g"],
    ["trans_fat_g", "g"], ["omega3_ala_g", "g"], ["omega3_epa_g", "g"], ["omega3_dha_g", "g"],
    ["omega6_la_g", "g"], ["cholesterol_mg", "mg"], ["sodium_mg", "mg"], ["potassium_mg", "mg"],
    ["calcium_mg", "mg"], ["iron_mg", "mg"], ["vit_a_ug", "µg"], ["vit_c_mg", "mg"],
    ["vit_d_ug", "µg"], ["vit_b12_ug", "µg"],
  ];
  const UNIT_OF = Object.fromEntries(NUTRIENTS);
  const NUTRIENT_GROUPS = [
    ["grp_macros", ["kcal", "protein_g", "carbs_g", "sugar_g", "fiber_g"]],
    ["grp_fats", ["fat_g", "sat_fat_g", "mono_fat_g", "poly_fat_g", "trans_fat_g",
      "omega3_ala_g", "omega3_epa_g", "omega3_dha_g", "omega6_la_g"]],
    ["grp_minerals", ["cholesterol_mg", "sodium_mg", "potassium_mg", "calcium_mg", "iron_mg"]],
    ["grp_vitamins", ["vit_a_ug", "vit_c_mg", "vit_d_ug", "vit_b12_ug"]],
  ];
  const KEY_TOTALS = ["kcal", "protein_g", "carbs_g", "fat_g"];
  const GOAL_KINDS = ["none", "at_least", "at_most", "between"];
  const SLOTS = ["breakfast", "lunch", "dinner", "snack"];
  const REAGENT_UNITS = ["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "oz", "lb", "piece"];

  // ── XSS guard ─────────────────────────────────────────────────────────────
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(str) {
    return (str == null ? "" : String(str)).replace(/[&<>"']/g, (c) => ESC[c]);
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

  async function request(base, path, { method = "GET", body } = {}) {
    const opts = { method, headers: { "X-CSRFToken": getCookie("csrftoken") }, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(base + path, opts);
    if (resp.status === 204) return null;
    let data = null;
    try { data = await resp.json(); } catch (e) { /* non-JSON error page */ }
    if (!resp.ok) throw new ApiError(resp.status, data);
    return data;
  }
  const api = (path, opts) => request(API, path, opts);
  const gapi = (path, opts) => request(GAPI, path, opts);

  // DRF errors → one readable line (messages are already translated server-side).
  function errorText(err) {
    if (!(err instanceof ApiError) || !err.data) return t("error_generic");
    if (err.data.detail) return String(err.data.detail);
    const msgs = [];
    const walk = (val) => {
      if (Array.isArray(val)) val.forEach(walk);
      else if (val && typeof val === "object") Object.values(val).forEach(walk);
      else if (val) msgs.push(String(val));
    };
    walk(err.data);
    return msgs.join(" · ") || t("error_generic");
  }

  function showError(el, err) {
    if (!el) return;
    el.textContent = err ? (typeof err === "string" ? err : errorText(err)) : "";
    el.hidden = !err;
  }

  const toastEl = $("#grim-toast");
  let toastTimer = null;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }

  function debounce(fn, ms) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  // ── Formatting ─────────────────────────────────────────────────────────────
  function fmtAmount(v) {
    if (v == null) return "—";
    const a = Math.abs(v);
    return String(Number(v.toFixed(a >= 10 ? 0 : a >= 1 ? 1 : 2)));
  }

  // "≥ 12 g" when some entries lacked the value: the real amount can only be higher.
  function valueText(key, value, missing) {
    return `${missing ? "≥ " : ""}${fmtAmount(value)} ${UNIT_OF[key]}`;
  }

  function targetText(g) {
    const unit = UNIT_OF[g.nutrient];
    if (g.kind === "at_least") return t("target_at_least", { value: fmtAmount(g.min_value), unit });
    if (g.kind === "at_most") return t("target_at_most", { value: fmtAmount(g.max_value), unit });
    return t("target_between", { min: fmtAmount(g.min_value), max: fmtAmount(g.max_value), unit });
  }

  function fmtDate(iso, opts = { day: "numeric", month: "short", year: "numeric" }) {
    if (!iso) return "";
    return new Date(`${iso}T00:00:00`).toLocaleDateString(IS_ES ? "es-MX" : "en-US", opts);
  }

  function fmtNum(n) {
    return String(Number(Number(n).toFixed(2)));
  }

  const statusBadge = (status) =>
    `<span class="nut-status nut-status--${esc(status)}">${esc(t(`status_${status}`))}</span>`;

  // ── Legal gate (#84) ───────────────────────────────────────────────────────
  function initAccept() {
    const form = $("#nut-accept-form");
    const btn = $("#nut-accept-btn");
    const boxes = $$('input[name="documents"]', form);
    const sync = () => { btn.disabled = !boxes.every((b) => b.checked); };
    boxes.forEach((b) => b.addEventListener("change", sync));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await api("legal/accept/", { method: "POST", body: { documents: boxes.map((b) => b.value) } });
        location.assign(CFG.next || "/cauldron/");
      } catch (err) {
        showError($("#nut-accept-error"), err);
        sync();
      }
    });
  }

  // ── Sensitive-data consent ─────────────────────────────────────────────────
  function initConsent() {
    const check = $("#nut-consent-check");
    const btn = $("#nut-consent-btn");
    if (!check || !btn) return;
    check.addEventListener("change", () => { btn.disabled = !check.checked; });
    btn.addEventListener("click", async () => {
      if (!check.checked) return;
      btn.disabled = true;
      try {
        await api("legal/accept/", { method: "POST", body: { documents: ["sensitive_consent"] } });
        location.reload();
      } catch (err) {
        showError($("#nut-consent-error"), err);
        btn.disabled = false;
      }
    });
  }

  const hasConsent = () => !$("#nut-app").hidden;

  // ════════════════════════════════════════════════════════════════════════
  // Profile: goals (#80), settings
  // ════════════════════════════════════════════════════════════════════════

  function goalRowHtml(key, goal) {
    const kind = goal ? goal.kind : "none";
    const low = goal && goal.min_value != null ? goal.min_value : "";
    const high = goal && goal.max_value != null ? goal.max_value : "";
    return `<div class="nut-goal" data-nutrient="${key}" data-kind="${kind}">
      <span class="nut-goal-name">${esc(t(`n_${key}`))}</span>
      <select class="nut-goal-kind" aria-label="${esc(t("goal_kind_for", { nutrient: t(`n_${key}`) }))}">${GOAL_KINDS.map((k) =>
        `<option value="${k}"${k === kind ? " selected" : ""}>${esc(t(`goal_${k}`))}</option>`).join("")}</select>
      <input type="number" class="nut-goal-min" min="0" step="any" inputmode="decimal" value="${esc(low)}"
             aria-label="${esc(t("goal_min_for", { nutrient: t(`n_${key}`) }))}">
      <input type="number" class="nut-goal-max" min="0" step="any" inputmode="decimal" value="${esc(high)}"
             aria-label="${esc(t("goal_max_for", { nutrient: t(`n_${key}`) }))}">
      <span class="nut-goal-unit">${esc(UNIT_OF[key])}</span>
    </div>`;
  }

  // Which inputs a goal kind uses: at least → min, at most → max, between → both.
  function syncGoalRow(row) {
    const kind = $(".nut-goal-kind", row).value;
    row.dataset.kind = kind;
    $(".nut-goal-min", row).hidden = !(kind === "at_least" || kind === "between");
    $(".nut-goal-max", row).hidden = !(kind === "at_most" || kind === "between");
  }

  async function loadGoals() {
    const box = $("#nut-goal-rows");
    try {
      const d = await api("goals/");
      const byKey = Object.fromEntries(d.goals.map((g) => [g.nutrient, g]));
      box.innerHTML = NUTRIENT_GROUPS.map(([group, keys]) => `<fieldset class="nut-goal-group">
          <legend>${esc(t(group))}</legend>${keys.map((k) => goalRowHtml(k, byKey[k])).join("")}</fieldset>`).join("");
      $$(".nut-goal", box).forEach(syncGoalRow);
    } catch (err) {
      box.innerHTML = `<p class="grim-error">${esc(errorText(err))}</p>`;
    }
  }

  function readGoalForm() {
    return $$("#nut-goal-rows .nut-goal").map((row) => ({
      nutrient: row.dataset.nutrient,
      kind: $(".nut-goal-kind", row).value,
      min_value: $(".nut-goal-min", row).hidden ? null : ($(".nut-goal-min", row).value || null),
      max_value: $(".nut-goal-max", row).hidden ? null : ($(".nut-goal-max", row).value || null),
    })).filter((g) => g.kind !== "none");
  }

  async function saveGoals(e) {
    e.preventDefault();
    const errEl = $("#nut-goal-error");
    showError(errEl, null);
    try {
      await api("goals/", { method: "PUT", body: { goals: readGoalForm() } });
      toast(t("toast_goals_saved"));
      loadGoals();
      if (!$("#nut-history").hidden) loadHistory();
    } catch (err) {
      showError(errEl, err);
    }
  }

  async function loadHistory() {
    const box = $("#nut-history");
    try {
      const d = await api("goals/history/");
      if (!d.history.length) {
        box.innerHTML = `<p class="grim-help">${esc(t("history_empty"))}</p>`;
        return;
      }
      box.innerHTML = `<ul class="nut-history-list">${d.history.map((g) => {
        const range = g.valid_to
          ? t("history_range", { from: fmtDate(g.valid_from), to: fmtDate(g.valid_to) })
          : t("history_since", { from: fmtDate(g.valid_from) });
        return `<li${g.valid_to ? "" : ' class="is-active"'}><strong>${esc(t(`n_${g.nutrient}`))}</strong>
          ${esc(targetText(g))} <span class="grim-help">${esc(range)}</span></li>`;
      }).join("")}</ul>`;
    } catch (err) {
      box.innerHTML = `<p class="grim-error">${esc(errorText(err))}</p>`;
    }
  }

  async function loadSettings() {
    try {
      const me = await api("me/");
      $("#nut-tz").value = me.timezone;
    } catch (err) { /* the field just stays empty */ }
    const list = $("#nut-tz-list");
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    list.innerHTML = zones.map((z) => `<option value="${esc(z)}"></option>`).join("");
    try {
      const legal = await api("legal/");
      const c = legal.accepted.sensitive_consent;
      $("#nut-consent-state").textContent = c && !c.withdrawn_at
        ? t("consent_given_on", {
          date: new Date(c.accepted_at).toLocaleDateString(IS_ES ? "es-MX" : "en-US", { day: "numeric", month: "short", year: "numeric" }),
          version: c.version,
        })
        : "";
    } catch (err) { /* informational only */ }
  }

  function initProfile() {
    initConsent();
    if (!hasConsent()) return;
    loadGoals();
    loadSettings();
    $("#nut-goal-form").addEventListener("submit", saveGoals);
    $("#nut-goal-rows").addEventListener("change", (e) => {
      const row = e.target.closest(".nut-goal");
      if (row && e.target.classList.contains("nut-goal-kind")) syncGoalRow(row);
    });
    $("#nut-history-toggle").addEventListener("click", (e) => {
      const box = $("#nut-history");
      box.hidden = !box.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!box.hidden));
      e.currentTarget.textContent = box.hidden ? t("history_show") : t("history_hide");
      if (!box.hidden) loadHistory();
    });
    $("#nut-tz-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("me/", { method: "PATCH", body: { timezone: $("#nut-tz").value.trim() } });
        toast(t("toast_tz_saved"));
      } catch (err) {
        toast(errorText(err));
      }
    });
    const withdraw = $("#nut-withdraw-btn");
    withdraw.addEventListener("click", async () => {
      if (withdraw.dataset.armed !== "1") {
        withdraw.dataset.armed = "1";
        withdraw.textContent = t("withdraw_confirm");
        return;
      }
      try {
        await api("legal/withdraw/", { method: "POST", body: { delete_data: $("#nut-withdraw-delete").checked } });
        location.reload();
      } catch (err) {
        toast(errorText(err));
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════
  // Journal: meal log (#81) + ritual planner (#83)
  // ════════════════════════════════════════════════════════════════════════

  const journal = { entries: [], editing: null, source: "spell", picked: null, planPicked: null, seq: 0 };
  const isToday = () => CFG.day === CFG.today;
  const spellTitle = (s) => (IS_ES && s.title_es) || s.title;
  const reagentName = (r) => (IS_ES && r.name_es) || r.name;
  const unitText = (u, qty) => {
    if (u === "serving") return Number(qty) === 1 ? t("unit_serving") : t("unit_servings");
    if (u === "portion") return "";
    return I18N[`unit_${u}`] || u;
  };
  const amountText = (e) => (e.unit === "portion" ? "" : `${fmtNum(e.quantity)} ${unitText(e.unit, e.quantity)}`);
  const spellLink = (uuid) => `${CFG.grimoireRoot}spell/${encodeURIComponent(uuid)}/`;

  function nowTime() {
    const d = new Date();
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  async function loadDay() {
    const my = ++journal.seq;
    try {
      const [day, plan] = await Promise.all([
        api(`intake/day/?date=${encodeURIComponent(CFG.day)}`),
        api(`plan/day/?date=${encodeURIComponent(CFG.day)}`),
      ]);
      if (my !== journal.seq) return;
      journal.entries = day.entries;
      renderEntries(day.entries);
      renderPlan(plan);
    } catch (err) {
      $("#nut-entries").innerHTML = `<li class="grim-error">${esc(errorText(err))}</li>`;
    }
  }

  // Something was logged, edited, eaten or removed: the day and the window both move.
  function refreshAll() {
    loadDay();
    loadRolling();
  }

  function goalListHtml(goals) {
    return `<ul class="nut-goal-status">${goals.map((g) => `<li class="nut-goal-line nut-goal-line--${esc(g.status)}">
        <span class="nut-goal-line-name">${esc(t(`n_${g.nutrient}`))}</span>
        <strong>${esc(valueText(g.nutrient, g.value, g.missing))}</strong>
        <span class="grim-help">${esc(targetText(g))}</span>
        ${statusBadge(g.status)}</li>`).join("")}</ul>`;
  }

  function renderEntries(entries) {
    const list = $("#nut-entries");
    if (!entries.length) {
      list.innerHTML = `<li class="grim-help nut-empty">${esc(isToday() ? t("entries_empty_today") : t("entries_empty"))}</li>`;
      return;
    }
    list.innerHTML = entries.map((e) => {
      const kcal = e.nutrients.kcal != null || e.missing.kcal
        ? valueText("kcal", e.nutrients.kcal || 0, e.missing.kcal) : "";
      const name = e.source === "spell" && e.spell
        ? `<a class="grim-casting-spell" href="${esc(spellLink(e.spell))}">${esc(e.label)}</a>`
        : `<span class="grim-casting-spell">${esc(e.label)}</span>`;
      const grams = e.grams != null && e.unit !== "g" ? ` <span class="grim-help">(${esc(fmtAmount(e.grams))} g)</span>` : "";
      return `<li class="nut-entry" data-uuid="${esc(e.uuid)}">
        <time class="nut-entry-time">${esc(e.local_time)}</time>
        <div class="nut-entry-body">${name}
          <span class="nut-entry-amount">${esc(amountText(e))}${grams}</span>
          ${e.planned_meal ? `<span class="grim-badge">${esc(t("from_plan"))}</span>` : ""}
        </div>
        <span class="nut-entry-kcal">${esc(kcal)}</span>
        <div class="grim-casting-actions">
          <button type="button" class="grim-link-btn" data-entry-edit>${esc(t("act_edit"))}</button>
          <button type="button" class="grim-link-btn grim-danger" data-entry-delete>${esc(t("act_delete"))}</button>
        </div></li>`;
    }).join("");
  }

  // ── How you're doing: the moving window + time in range (services/rolling.py) ──
  // One GET rolling/ brings every hourly tick of the history: values, goal
  // bands and statuses, all judged on the server. Dragging the scrubber or the
  // chart only re-renders; it never asks the server again.
  const SVG = "http://www.w3.org/2000/svg";
  const roll = { span: "24h", days: 7, data: null, index: 0, follow: true, nutrient: null, seq: 0, geom: null };
  const tickDay = (iso) => String(iso).slice(0, 10);
  const tickClock = (iso) => String(iso).slice(11, 16);
  const fmtTick = (iso) => `${fmtDate(tickDay(iso), { weekday: "short", day: "numeric", month: "short" })}, ${tickClock(iso)}`;
  const lastIndex = () => (roll.data ? roll.data.ticks.length - 1 : 0);
  const spanHours = () => (roll.span === "24h" ? 24 : 7 * 24);

  // A tick moved back by some hours, still on the user's own clock: ticks come
  // with their UTC offset ("…T18:00:00-06:00"), so the browser's zone never matters.
  function shiftIso(iso, hoursBack) {
    const m = String(iso).match(/([+-])(\d\d):(\d\d)$/);
    const offset = m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
    const d = new Date(Date.parse(iso) - hoursBack * 3600e3 + offset * 60e3);
    const two = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())}T${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;
  }

  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function goalOf(n, i) {
    const d = roll.data;
    const low = d.low[n] ? d.low[n][i] : null;
    const high = d.high[n] ? d.high[n][i] : null;
    if (low == null && high == null) return null;
    return { nutrient: n, kind: low != null && high != null ? "between" : low != null ? "at_least" : "at_most", min_value: low, max_value: high };
  }

  function initialIndex(d) {
    const last = d.ticks.length - 1;
    if (CFG.day === CFG.today) return last;
    for (let i = last; i >= 0; i -= 1) if (tickDay(d.ticks[i]) === CFG.day) return i;
    return last;
  }

  async function loadRolling() {
    const my = ++roll.seq;
    try {
      const d = await api(`rolling/?span=${roll.span}&days=${roll.days}`);
      if (my !== roll.seq) return;
      const kept = roll.data && !roll.follow ? roll.data.ticks[roll.index] : null;
      roll.data = d;
      const found = kept ? d.ticks.indexOf(kept) : -1;
      roll.index = found >= 0 ? found : (roll.follow ? lastIndex() : initialIndex(d));
      roll.follow = roll.index === lastIndex();
      const tracked = d.summary.map((s) => s.nutrient);
      if (!tracked.includes(roll.nutrient)) roll.nutrient = tracked[0] || null;
      const scrub = $("#nut-scrub");
      scrub.max = String(lastIndex());
      scrub.value = String(roll.index);
      scrub.disabled = lastIndex() < 1;
      renderTimelineDays();
      renderChips();
      renderHistory();
      renderNow();
    } catch (err) {
      $("#nut-gauges").innerHTML = `<p class="grim-error">${esc(errorText(err))}</p>`;
    }
  }

  function setIndex(i) {
    roll.index = Math.max(0, Math.min(lastIndex(), i));
    roll.follow = roll.index === lastIndex();
    $("#nut-scrub").value = String(roll.index);
    renderNow();
    moveCursor();
  }

  function renderNow() {
    const d = roll.data;
    if (!d) return;
    const i = roll.index;
    const iso = d.ticks[i];
    const range = t("window_range", { from: fmtTick(shiftIso(iso, spanHours())), to: fmtTick(iso) });
    $("#nut-window-range").textContent = range;
    $("#nut-window-label").textContent = roll.follow ? t("window_live") : t("window_past");
    $("#nut-scrub").setAttribute("aria-valuetext", range);
    placeWindow();
    $("#nut-now-btn").hidden = roll.follow;
    const goals = d.summary.map((s) => s.nutrient).filter((n) => d.status[n][i] != null);
    const note = roll.span === "7d" ? `<p class="grim-help">${esc(t("avg_note"))}</p>` : "";
    $("#nut-gauges").innerHTML = goals.length
      ? `${note}${goals.map((n) => gaugeHtml(n, i)).join("")}`
      : `${note}<p class="grim-help">${esc(t("no_goals_yet"))} <a class="grim-back" href="${esc(CFG.profile)}">${esc(t("set_goals"))}</a></p>
         <dl class="grim-macros nut-key-totals">${KEY_TOTALS.map((k) =>
          `<div><dt>${esc(t(`n_${k}`))}</dt><dd>${esc(valueText(k, d.values[k][i], d.missing[k][i]))}</dd></div>`).join("")}</dl>`;
    if (!$("#nut-all").hidden) renderAll();
  }

  // The highlighted stretch of the timeline: exactly the hours the window counts.
  function placeWindow() {
    const d = roll.data;
    const span = Math.max(1, d.ticks.length - 1);
    const end = roll.index / span;
    const width = Math.min(end, spanHours() / (span * d.step_hours));
    const el = $("#nut-timeline-window");
    el.style.left = `${(end - width) * 100}%`;
    el.style.width = `${Math.max(width * 100, 0.6)}%`;
  }

  function renderTimelineDays() {
    const d = roll.data;
    const span = Math.max(1, d.ticks.length - 1);
    const midnights = d.ticks.map((iso, i) => [iso, i]).filter(([iso]) => tickClock(iso) === "00:00");
    // As many day labels as fit (~64px each, leaving room for "now"), never overlapping.
    const width = $("#nut-timeline-days").clientWidth || 600;
    const fit = Math.max(1, Math.floor(width / 64) - 1);
    const every = Math.max(1, Math.ceil(midnights.length / fit));
    // Labels near the right edge would collide with "now".
    $("#nut-timeline-days").innerHTML = midnights.filter(([, i], k) => k % every === 0 && i / span < 1 - 72 / width).map(([iso, i]) =>
      `<span style="left:${(i / span) * 100}%">${esc(fmtDate(tickDay(iso), { weekday: "short", day: "numeric" }))}</span>`).join("")
      + `<span class="is-now" style="left:100%">${esc(t("today_now"))}</span>`;
  }

  function gaugeHtml(n, i) {
    const d = roll.data;
    const value = d.values[n][i];
    const missing = d.missing[n][i];
    const status = d.status[n][i];
    const goal = goalOf(n, i);
    const low = goal.min_value;
    const high = goal.max_value;
    const unit = UNIT_OF[n];
    const top = Math.max(value, high || 0, (low || 0) * 1.25, 1) * 1.1;
    const at = (x) => Math.min(100, (x / top) * 100);
    const from = low != null ? at(low) : 0;
    const to = high != null ? at(high) : 100;
    let delta;
    if (status === "not_logged") delta = t("gauge_nothing");
    else if (high != null && value > high) delta = t("gauge_over", { n: fmtAmount(value - high), unit });
    else if (low != null && value < low) delta = t(missing ? "gauge_short_maybe" : "gauge_short", { n: fmtAmount(low - value), unit });
    else delta = t("gauge_in");
    const name = t(`n_${n}`);
    const label = `${name}: ${valueText(n, value, missing)}, ${targetText(goal)}, ${t(`status_${status}`)}`;
    return `<button type="button" class="nut-gauge nut-gauge--${esc(status)}${n === roll.nutrient ? " is-selected" : ""}"
        data-nutrient="${n}" aria-pressed="${n === roll.nutrient}" aria-label="${esc(label)}">
      <span class="nut-gauge-head"><span class="nut-gauge-name">${esc(name)}</span>
        <span class="nut-gauge-value">${esc(valueText(n, value, missing))}</span></span>
      <span class="nut-gauge-track" aria-hidden="true">
        <span class="nut-gauge-band" style="left:${from}%;width:${Math.max(0, to - from)}%"></span>
        <span class="nut-gauge-fill" style="width:${at(value)}%"></span>
      </span>
      <span class="nut-gauge-foot"><span class="grim-help">${esc(targetText(goal))}</span>
        <span class="nut-gauge-delta">${esc(delta)}</span>${statusBadge(status)}</span>
    </button>`;
  }

  function renderAll() {
    const d = roll.data;
    const i = roll.index;
    $("#nut-all").innerHTML = NUTRIENT_GROUPS.map(([group, keys]) => `<div class="grim-nutrient-group"><h3>${esc(t(group))}</h3>
      <dl class="grim-nutrient-table">${keys.map((k) => `<div${d.missing[k][i] ? ' class="is-incomplete"' : ""}>
        <dt>${esc(t(`n_${k}`))}</dt><dd>${esc(valueText(k, d.values[k][i], d.missing[k][i]))}</dd></div>`).join("")}</dl></div>`).join("");
  }

  function renderChips() {
    const d = roll.data;
    $("#nut-hist-chips").innerHTML = d.summary.map((s) => `<button type="button" class="grim-scope-btn${s.nutrient === roll.nutrient ? " is-on" : ""}"
        data-chip="${s.nutrient}" aria-pressed="${s.nutrient === roll.nutrient}">${esc(t(`n_${s.nutrient}`))}
        <span class="nut-chip-pct">${s.pct == null ? "—" : `${s.pct}%`}</span></button>`).join("");
  }

  function selectNutrient(n) {
    roll.nutrient = n;
    renderChips();
    renderHistory();
    renderNow();
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG, name);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  function renderHistory() {
    const d = roll.data;
    const n = roll.nutrient;
    const box = $("#nut-hist");
    if (!n) {
      box.innerHTML = `<p class="grim-help">${esc(t("adh_no_goals"))}</p>`;
      roll.geom = null;
      return;
    }
    const s = d.summary.find((x) => x.nutrient === n);
    const share = (k) => (s.judged ? `${Math.round((k / s.judged) * 100)}%` : "—");
    const stat = s.judged
      ? `<p class="nut-tir"><strong class="nut-tir-pct">${s.pct}%</strong> <span>${esc(t("tir_of_time"))}</span></p>
         <p class="grim-help">${esc(t("tir_breakdown", { out: share(s.out_of_range), unc: share(s.uncertain), hours: s.not_logged * d.step_hours }))}</p>`
      : `<p class="grim-help">${esc(t("tir_none"))}</p>`;
    box.innerHTML = `${stat}
      <div class="nut-chart" id="nut-chart"><div class="nut-tip" id="nut-tip" hidden></div></div>
      <ol class="nut-strip" id="nut-strip" aria-label="${esc(t("strip_label"))}"></ol>
      <details class="nut-table"><summary>${esc(t("table_show"))}</summary><div id="nut-table"></div></details>`;
    drawChart(n);
    drawStrip(n);
  }

  function drawChart(n) {
    const d = roll.data;
    const wrap = $("#nut-chart");
    const W = Math.max(300, Math.round(wrap.clientWidth || 640));
    const H = 220;
    const m = { l: 52, r: 12, t: 24, b: 30 };
    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    const N = d.ticks.length;
    const vals = d.values[n];
    let peak = 0;
    for (let i = 0; i < N; i += 1) {
      if (d.logged[i]) peak = Math.max(peak, vals[i]);
      peak = Math.max(peak, d.high[n][i] || 0, (d.low[n][i] || 0) * 1.25);
    }
    const yMax = niceMax(peak * 1.05);
    const x = (i) => m.l + (N > 1 ? (i / (N - 1)) * pw : pw);
    const y = (v) => m.t + ph - (Math.min(v, yMax) / yMax) * ph;
    roll.geom = { x, y, m, pw, N, W, H };

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": t("chart_label", { nutrient: t(`n_${n}`), span: t(roll.span === "24h" ? "span_24h" : "span_7d"), days: roll.days }) });
    // Recessive grid: three hairlines with clean values.
    for (const v of [0, yMax / 2, yMax]) {
      svg.appendChild(svgEl("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "nut-grid" }));
      const label = svgEl("text", { x: m.l - 8, y: y(v) + 4, class: "nut-axis", "text-anchor": "end" });
      label.textContent = `${fmtAmount(v)}`;
      svg.appendChild(label);
    }
    const unitLabel = svgEl("text", { x: m.l, y: m.t - 12, class: "nut-axis", "text-anchor": "start" });
    unitLabel.textContent = UNIT_OF[n];
    // Day labels at local midnights, thinned to about six.
    const midnights = d.ticks.map((iso, i) => [iso, i]).filter(([iso]) => tickClock(iso) === "00:00");
    const every = Math.max(1, Math.ceil(midnights.length / 6));
    midnights.forEach(([iso, i], k) => {
      if (k % every) return;
      const label = svgEl("text", { x: x(i), y: H - 8, class: "nut-axis", "text-anchor": "middle" });
      label.textContent = fmtDate(tickDay(iso), { day: "numeric", month: "short" });
      svg.appendChild(label);
    });
    // Goal band: the range that counts as met, segment by segment as goals change.
    let band = [];
    const flushBand = () => {
      if (band.length > 1) {
        const top = band.map(([i, lo, hi]) => `${x(i)},${y(hi == null ? yMax : hi)}`);
        const bottom = band.slice().reverse().map(([i, lo]) => `${x(i)},${y(lo == null ? 0 : lo)}`);
        svg.appendChild(svgEl("polygon", { points: [...top, ...bottom].join(" "), class: "nut-band" }));
      }
      band = [];
    };
    for (let i = 0; i < N; i += 1) {
      const lo = d.low[n][i];
      const hi = d.high[n][i];
      if (lo == null && hi == null) flushBand();
      else band.push([i, lo, hi]);
    }
    flushBand();
    // The line: the window's value, broken where nothing was logged.
    let path = "";
    let pen = false;
    for (let i = 0; i < N; i += 1) {
      if (!d.logged[i]) { pen = false; continue; }
      path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(vals[i]).toFixed(1)}`;
      pen = true;
    }
    if (path) svg.appendChild(svgEl("path", { d: path, class: "nut-line" }));
    svg.appendChild(svgEl("line", { id: "nut-cursor-line", class: "nut-cursor", y1: m.t, y2: m.t + ph }));
    svg.appendChild(svgEl("circle", { id: "nut-cursor-dot", class: "nut-cursor-dot", r: 5 }));
    svg.appendChild(svgEl("line", { id: "nut-hover-line", class: "nut-hover", y1: m.t, y2: m.t + ph, visibility: "hidden" }));
    const hit = svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph, class: "nut-hit" });
    svg.appendChild(hit);
    svg.appendChild(unitLabel);
    wrap.appendChild(svg);

    const indexAt = (evt) => {
      const r = svg.getBoundingClientRect();
      const px = ((evt.clientX - r.left) / r.width) * W;
      return Math.round(((px - m.l) / pw) * (N - 1));
    };
    let dragging = false;
    hit.addEventListener("pointermove", (evt) => {
      const i = Math.max(0, Math.min(N - 1, indexAt(evt)));
      showTip(n, i);
      if (dragging) setIndex(i);
    });
    hit.addEventListener("pointerleave", () => { hideTip(); dragging = false; });
    hit.addEventListener("pointerdown", (evt) => {
      dragging = true;
      hit.setPointerCapture(evt.pointerId);
      setIndex(indexAt(evt));
    });
    hit.addEventListener("pointerup", () => { dragging = false; });
    moveCursor();
  }

  function moveCursor() {
    const g = roll.geom;
    const line = document.getElementById("nut-cursor-line");
    const dot = document.getElementById("nut-cursor-dot");
    if (!g || !line) return;
    const i = roll.index;
    line.setAttribute("x1", g.x(i));
    line.setAttribute("x2", g.x(i));
    const logged = roll.data.logged[i];
    dot.setAttribute("visibility", logged ? "visible" : "hidden");
    if (logged) {
      dot.setAttribute("cx", g.x(i));
      dot.setAttribute("cy", g.y(roll.data.values[roll.nutrient][i]));
    }
  }

  function showTip(n, i) {
    const g = roll.geom;
    const d = roll.data;
    const tip = $("#nut-tip");
    const hover = document.getElementById("nut-hover-line");
    hover.setAttribute("x1", g.x(i));
    hover.setAttribute("x2", g.x(i));
    hover.setAttribute("visibility", "visible");
    const status = d.status[n][i];
    const goal = goalOf(n, i);
    tip.innerHTML = `<strong>${esc(fmtTick(d.ticks[i]))}</strong>
      <span>${esc(d.logged[i] ? valueText(n, d.values[n][i], d.missing[n][i]) : t("status_not_logged"))}</span>
      ${goal ? `<span class="grim-help">${esc(targetText(goal))}</span>` : ""}
      ${status ? statusBadge(status) : ""}`;
    tip.hidden = false;
    const wrapW = $("#nut-chart").clientWidth;
    const px = (g.x(i) / g.W) * wrapW;
    tip.style.left = `${Math.min(Math.max(px, 70), wrapW - 70)}px`;
  }

  function hideTip() {
    const tip = $("#nut-tip");
    const hover = document.getElementById("nut-hover-line");
    if (tip) tip.hidden = true;
    if (hover) hover.setAttribute("visibility", "hidden");
  }

  function drawStrip(n) {
    const d = roll.data;
    const byDay = Object.fromEntries((d.daily[n] || []).map((x) => [x.date, x]));
    const days = [...new Set(d.ticks.map(tickDay))];
    $("#nut-strip").innerHTML = days.map((day) => {
      const cell = byDay[day];
      const label = cell
        ? t("strip_day", { date: fmtDate(day, { weekday: "long", day: "numeric", month: "long" }), pct: `${cell.pct}%`, hours: cell.judged * d.step_hours })
        : t("strip_empty", { date: fmtDate(day, { weekday: "long", day: "numeric", month: "long" }) });
      const style = cell ? ` style="--in:${(0.12 + (cell.pct / 100) * 0.88).toFixed(2)}"` : "";
      return `<li><button type="button" class="nut-strip-day${cell ? "" : " is-empty"}"${style} data-day="${day}"
        title="${esc(label)}" aria-label="${esc(label)}"></button></li>`;
    }).join("");
    $("#nut-table").innerHTML = `<table class="nut-tir-table"><thead><tr><th scope="col">${esc(t("th_day"))}</th>
      <th scope="col">${esc(t("th_pct"))}</th><th scope="col">${esc(t("th_hours"))}</th></tr></thead><tbody>${days.map((day) => {
      const cell = byDay[day];
      return `<tr><td>${esc(fmtDate(day))}</td><td>${cell ? `${cell.pct}%` : "—"}</td><td>${cell ? cell.judged * d.step_hours : 0}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function onRollingClick(e) {
    const span = e.target.closest("[data-span]");
    if (span) {
      roll.span = span.dataset.span;
      $$("[data-span]").forEach((b) => { b.classList.toggle("is-on", b === span); b.setAttribute("aria-pressed", String(b === span)); });
      loadRolling();
      return true;
    }
    const days = e.target.closest("[data-days]");
    if (days) {
      roll.days = Number(days.dataset.days);
      $$("[data-days]").forEach((b) => { b.classList.toggle("is-on", b === days); b.setAttribute("aria-pressed", String(b === days)); });
      loadRolling();
      return true;
    }
    const chip = e.target.closest("[data-chip]");
    if (chip) { selectNutrient(chip.dataset.chip); return true; }
    const gauge = e.target.closest(".nut-gauge");
    if (gauge) { selectNutrient(gauge.dataset.nutrient); return true; }
    const day = e.target.closest("[data-day]");
    if (day) {
      const ticks = roll.data.ticks;
      for (let i = ticks.length - 1; i >= 0; i -= 1) if (tickDay(ticks[i]) === day.dataset.day) { setIndex(i); break; }
      return true;
    }
    if (e.target.closest("#nut-now-btn")) { setIndex(lastIndex()); return true; }
    if (e.target.closest("#nut-all-toggle")) {
      const box = $("#nut-all");
      box.hidden = !box.hidden;
      e.target.setAttribute("aria-expanded", String(!box.hidden));
      e.target.textContent = box.hidden ? t("all_show") : t("all_hide");
      if (!box.hidden) renderAll();
      return true;
    }
    return false;
  }

  function initRolling() {
    $("#nut-scrub").addEventListener("input", (e) => setIndex(Number(e.target.value)));
    window.addEventListener("resize", debounce(() => { if (roll.data) { renderTimelineDays(); renderHistory(); } }, 200));
    // The window moves with the clock: refresh while it's following "now".
    setInterval(() => { if (roll.follow && !document.hidden) loadRolling(); }, 5 * 60 * 1000);
    loadRolling();
  }

  // ── Entry form ──
  // "spell" / "reagent" follow whatever was picked from the one search box;
  // "quick" is typing it by hand when the search doesn't have it.
  function setSource(source, reagent = null) {
    journal.source = source;
    $$("#nut-entry-form [data-for]").forEach((el) => {
      el.hidden = !el.dataset.for.split(" ").includes(source);
    });
    const unit = $("#nut-entry-unit");
    const units = source === "spell" ? ["serving", "g"] : reagentUnits(reagent);
    unit.innerHTML = units.map((u) => `<option value="${u}">${esc(u === "serving" ? t("unit_servings") : (I18N[`unit_${u}`] || u))}</option>`).join("");
  }

  // An ingredient's own measures first (its standard one, e.g. a glass of
  // Coca-Cola, is the default), then weight, then volume if it has a density.
  // Unknown (an entry being edited): every unit.
  function reagentUnits(r) {
    if (!r || !r.grams_per_unit) return REAGENT_UNITS;
    const own = ["glass", "piece", "cup", "tbsp", "tsp"].filter((u) => r.grams_per_unit[u]);
    const volume = r.density_g_per_ml ? ["ml", "l", "cup", "tbsp", "tsp"] : [];
    return [...new Set([...own, "g", "kg", "oz", "lb", ...volume])];
  }

  function pick(target, item) {
    const isPlan = target === "plan";
    if (isPlan) journal.planPicked = item; else journal.picked = item;
    const el = $(isPlan ? "#nut-plan-picked" : "#nut-entry-picked");
    el.hidden = !item;
    el.innerHTML = item
      ? `<span class="grimoire-reagent">${esc(item.title)}</span> <button type="button" class="grim-link-btn" data-unpick="${target}">${esc(t("change"))}</button>`
      : "";
    $(isPlan ? "#nut-plan-search" : "#nut-entry-search").hidden = !!item;
    $(isPlan ? "#nut-plan-results" : "#nut-entry-results").hidden = true;
    if (!isPlan && item && item.kind) {
      setSource(item.kind, item);
      $("#nut-entry-form").elements.quantity.value = "1";
    }
  }

  const fold = (x) => String(x).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

  async function search(target, q) {
    const results = $(target === "plan" ? "#nut-plan-results" : "#nut-entry-results");
    if (!q.trim()) {
      results.hidden = true;
      return;
    }
    try {
      const qs = encodeURIComponent(q);
      const asSpell = (s) => ({ kind: "spell", uuid: s.uuid, title: spellTitle(s), servings: s.servings });
      let rows;
      if (target === "plan") {
        rows = (await gapi(`spells/?q=${qs}`)).slice(0, 8).map(asSpell);
      } else {
        // Dishes and ingredients in one list: exact name, then starts-with, then the rest.
        const [spells, reagents] = await Promise.all([gapi(`spells/?q=${qs}`), gapi(`reagents/?q=${qs}`)]);
        const f = fold(q);
        const rank = (r) => (fold(r.title) === f ? 0 : fold(r.title).startsWith(f) ? 1 : 2);
        rows = [
          ...spells.slice(0, 6).map(asSpell),
          ...reagents.slice(0, 6).map((r) => ({
            kind: "reagent", uuid: r.uuid, title: reagentName(r),
            grams_per_unit: r.grams_per_unit, density_g_per_ml: r.density_g_per_ml,
          })),
        ].filter((r) => !journal.editing || r.kind === journal.editing.source) // a logged entry keeps its kind
          .map((r, i) => [rank(r), i, r]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).slice(0, 8).map((x) => x[2]);
      }
      const kindTag = (r) => (target === "plan" ? "" : ` <span class="nut-kind">${esc(t(r.kind === "spell" ? "kind_spell" : "kind_reagent"))}</span>`);
      results.innerHTML = (rows.length
        ? rows.map((r, i) => `<li role="option" tabindex="-1" data-i="${i}">${esc(r.title)}${kindTag(r)}</li>`).join("")
        : `<li class="grim-help">${esc(t("no_matches"))}</li>`)
        + (target === "plan" || journal.editing ? "" : `<li role="option" tabindex="-1" class="nut-manual" data-manual>${esc(t("type_it_in", { q: q.trim() }))}</li>`);
      results._rows = rows;
      results.hidden = false;
    } catch (err) {
      results.innerHTML = `<li class="grim-error">${esc(errorText(err))}</li>`;
      results.hidden = false;
    }
  }
  const searchEntrySoon = debounce((q) => search("entry", q), 250);
  const searchPlanSoon = debounce((q) => search("plan", q), 250);

  function resetEntryForm() {
    const form = $("#nut-entry-form");
    form.reset();
    journal.editing = null;
    pick("entry", null);
    $("#nut-entry-search").value = "";
    setSource("spell");
    form.elements.time.value = isToday() ? nowTime() : "12:00";
    form.elements.quantity.value = "1";
    $("#nut-entry-form-title").textContent = t("form_log");
    $("#nut-entry-submit").textContent = t("form_log_btn");
    $("#nut-entry-cancel").hidden = true;
    showError($("#nut-entry-error"), null);
  }

  function editEntry(entry) {
    const form = $("#nut-entry-form");
    journal.editing = entry;
    setSource(entry.source);
    form.elements.time.value = entry.local_time;
    if (entry.source === "quick") {
      form.elements.label.value = entry.label;
      for (const k of KEY_TOTALS) form.elements[k].value = entry.nutrients[k] != null ? entry.nutrients[k] : "";
      pick("entry", null);
    } else {
      pick("entry", { uuid: entry.source === "spell" ? entry.spell : entry.reagent, title: entry.label, keep: true });
      form.elements.quantity.value = entry.quantity;
      form.elements.unit.value = entry.unit;
    }
    $("#nut-entry-form-title").textContent = t("form_edit");
    $("#nut-entry-submit").textContent = t("form_save_btn");
    $("#nut-entry-cancel").hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Floating "Log a meal": jumps to the form (a fresh one unless an edit is open)
  // and hides itself while the form is on screen.
  function initFab() {
    const fab = $("#nut-fab");
    const form = $("#nut-entry-form");
    fab.addEventListener("click", () => {
      if (!journal.editing) resetEntryForm();
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      form.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
      const target = journal.source === "quick" ? form.elements.label : $("#nut-entry-search");
      (target.hidden ? form.elements.quantity : target).focus({ preventScroll: true });
    });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(([e]) => fab.classList.toggle("is-away", e.isIntersecting), { threshold: 0.25 })
        .observe(form);
    }
  }

  async function submitEntry(e) {
    e.preventDefault();
    const form = $("#nut-entry-form");
    const errEl = $("#nut-entry-error");
    showError(errEl, null);
    const time = form.elements.time.value || (isToday() ? nowTime() : "12:00");
    const body = { source: journal.source, eaten_at: `${CFG.day}T${time}` };
    if (journal.source === "quick") {
      body.label = form.elements.label.value.trim();
      // Keep values the form doesn't show (an entry may carry more than the key totals).
      body.values = {};
      if (journal.editing && journal.editing.source === "quick") {
        Object.assign(body.values, journal.editing.nutrients);
        for (const k of KEY_TOTALS) delete body.values[k];
      }
      for (const k of KEY_TOTALS) if (form.elements[k].value !== "") body.values[k] = Number(form.elements[k].value);
    } else {
      const picked = journal.picked;
      if (!picked) {
        showError(errEl, t("pick_food_first"));
        return;
      }
      // Editing keeps the logged snapshot unless another spell/ingredient was picked.
      if (!picked.keep) body[journal.source] = picked.uuid;
      body.quantity = Number(form.elements.quantity.value);
      body.unit = form.elements.unit.value;
    }
    try {
      if (journal.editing) {
        await api(`intake/${encodeURIComponent(journal.editing.uuid)}/`, { method: "PATCH", body });
        toast(t("toast_entry_saved"));
      } else {
        await api("intake/", { method: "POST", body });
        toast(t("toast_entry_logged"));
      }
      resetEntryForm();
      refreshAll();
    } catch (err) {
      showError(errEl, err);
    }
  }

  // ── Plan ──
  function renderPlan(plan) {
    const box = $("#nut-plan");
    box.innerHTML = SLOTS.map((slot) => {
      const meals = plan.meals.filter((m) => m.slot === slot);
      const items = meals.map((m) => {
        const kcal = m.nutrients ? valueText("kcal", m.nutrients.kcal || 0, m.missing && m.missing.kcal) : "";
        const title = m.spell && m.available
          ? `<a class="grim-casting-spell" href="${esc(spellLink(m.spell))}">${esc(m.spell_title)}</a>`
          : `<span class="grim-casting-spell is-removed">${esc(m.spell_title)}</span>`;
        const actions = m.eaten
          ? `<span class="nut-status nut-status--met">${esc(t("plan_eaten"))}</span>`
          : `${m.available ? `<button type="button" class="grim-link-btn" data-plan-eat>${esc(t("plan_eat"))}</button>` : `<span class="grim-help">${esc(t("plan_unavailable"))}</span>`}
             <button type="button" class="grim-link-btn grim-danger" data-plan-remove>${esc(t("act_remove"))}</button>`;
        return `<li class="nut-plan-item${m.eaten ? " is-eaten" : ""}" data-uuid="${esc(m.uuid)}">
          <div class="nut-entry-body">${title}<span class="nut-entry-amount">${esc(`${fmtNum(m.quantity)} ${unitText(m.unit, m.quantity)}`)}${m.grams != null && m.unit !== "g" ? ` (${esc(fmtAmount(m.grams))} g)` : ""}</span></div>
          <span class="nut-entry-kcal">${esc(kcal)}</span>
          <div class="grim-casting-actions">${actions}</div></li>`;
      }).join("");
      return `<div class="nut-slot"><h3 class="grim-section-title">${esc(t(`slot_${slot}`))}</h3>
        ${items ? `<ul class="nut-plan-list">${items}</ul>` : `<p class="grim-help nut-empty">${esc(t("slot_empty"))}</p>`}</div>`;
    }).join("");
    const pending = plan.meals.some((m) => !m.eaten && m.available);
    const projected = $("#nut-projected");
    if (!pending) {
      projected.innerHTML = "";
      return;
    }
    const byKey = Object.fromEntries(plan.projected.map((n) => [n.key, n]));
    projected.innerHTML = `<h3 class="grim-section-title">${esc(t("projected_title"))}</h3>
      <dl class="grim-macros nut-key-totals">${KEY_TOTALS.map((k) =>
        `<div><dt>${esc(t(`n_${k}`))}</dt><dd>${esc(valueText(k, byKey[k].value, byKey[k].missing))}</dd></div>`).join("")}</dl>
      ${plan.goals.length ? goalListHtml(plan.goals) : ""}`;
  }

  async function submitPlan(e) {
    e.preventDefault();
    const form = $("#nut-plan-form");
    const errEl = $("#nut-plan-error");
    showError(errEl, null);
    if (!journal.planPicked) {
      showError(errEl, t("pick_spell_first"));
      return;
    }
    try {
      await api("plan/", {
        method: "POST",
        body: {
          date: CFG.day,
          slot: form.elements.slot.value,
          spell: journal.planPicked.uuid,
          quantity: Number(form.elements.quantity.value),
          unit: form.elements.unit.value,
        },
      });
      toast(t("toast_planned"));
      pick("plan", null);
      $("#nut-plan-search").value = "";
      refreshAll();
    } catch (err) {
      showError(errEl, err);
    }
  }

  async function onJournalClick(e) {
    if (onRollingClick(e)) return;
    // Not in the search: type it by hand, starting from what was searched.
    if (e.target.closest("[data-manual]")) {
      const form = $("#nut-entry-form");
      const q = $("#nut-entry-search").value.trim();
      pick("entry", null);
      setSource("quick");
      form.elements.label.value = q;
      form.elements.kcal.focus();
      return;
    }
    if (e.target.closest("[data-back-search]")) {
      if (journal.editing) return; // a logged entry keeps its kind while editing
      setSource("spell");
      $("#nut-entry-search").focus();
      return;
    }
    const option = e.target.closest(".grim-suggest li[data-i]");
    if (option) {
      const list = option.parentElement;
      const row = list._rows[Number(option.dataset.i)];
      pick(list.id === "nut-plan-results" ? "plan" : "entry", row);
      return;
    }
    const unpick = e.target.closest("[data-unpick]");
    if (unpick) {
      pick(unpick.dataset.unpick, null);
      $(unpick.dataset.unpick === "plan" ? "#nut-plan-search" : "#nut-entry-search").focus();
      return;
    }
    const entryEl = e.target.closest(".nut-entry");
    if (entryEl && e.target.closest("[data-entry-edit]")) {
      const entry = journal.entries.find((x) => x.uuid === entryEl.dataset.uuid);
      if (entry) editEntry(entry);
      return;
    }
    const del = e.target.closest("[data-entry-delete]");
    if (entryEl && del) {
      if (del.dataset.armed !== "1") {
        del.dataset.armed = "1";
        del.textContent = t("act_delete_confirm");
        return;
      }
      try {
        await api(`intake/${encodeURIComponent(entryEl.dataset.uuid)}/`, { method: "DELETE" });
        if (journal.editing && journal.editing.uuid === entryEl.dataset.uuid) resetEntryForm();
        refreshAll();
      } catch (err) { toast(errorText(err)); }
      return;
    }
    const planEl = e.target.closest(".nut-plan-item");
    if (planEl && e.target.closest("[data-plan-eat]")) {
      try {
        const body = isToday() ? { eaten_at: `${CFG.day}T${nowTime()}` } : {};
        await api(`plan/${encodeURIComponent(planEl.dataset.uuid)}/eat/`, { method: "POST", body });
        toast(t("toast_eaten"));
        refreshAll();
      } catch (err) { toast(errorText(err)); }
      return;
    }
    if (planEl && e.target.closest("[data-plan-remove]")) {
      try {
        await api(`plan/${encodeURIComponent(planEl.dataset.uuid)}/`, { method: "DELETE" });
        loadDay();
      } catch (err) { toast(errorText(err)); }
    }
  }

  function initJournal() {
    initConsent();
    $("#nut-day-title").textContent = isToday()
      ? `${t("today")} · ${fmtDate(CFG.day, { weekday: "long", day: "numeric", month: "long" })}`
      : fmtDate(CFG.day, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    if (!hasConsent()) return;
    resetEntryForm();
    roll.follow = isToday(); // a past day opens the window at that day's end
    loadDay();
    initRolling();
    initFab();
    $("#nut-entry-form").addEventListener("submit", submitEntry);
    $("#nut-plan-form").addEventListener("submit", submitPlan);
    $("#nut-entry-cancel").addEventListener("click", resetEntryForm);
    $("#nut-entry-search").addEventListener("input", (e) => searchEntrySoon(e.target.value));
    $("#nut-plan-search").addEventListener("input", (e) => searchPlanSoon(e.target.value));
    $("#nut-app").addEventListener("click", onJournalClick);
    if (PREFILL) {
      const item = { uuid: PREFILL.uuid, title: PREFILL.title, servings: PREFILL.servings };
      if (PREFILL.mode === "plan") {
        pick("plan", item);
        $("#nut-plan-form").scrollIntoView({ block: "center" });
      } else {
        setSource("spell");
        pick("entry", item);
        $("#nut-entry-form").scrollIntoView({ block: "center" });
      }
    }
  }

  if (PAGE === "accept") initAccept();
  else if (PAGE === "profile") initProfile();
  else if (PAGE === "journal") initJournal();
})();
