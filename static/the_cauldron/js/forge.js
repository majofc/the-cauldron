/* The Forge — adaptive calisthenics front-end.
   Talks to /cauldron/api/ (DRF). No framework; vanilla fetch + DOM. */
(function () {
  "use strict";

  const API = window.FORGE_API; // e.g. /cauldron/api/
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const LOADABLE = ["dumbbells", "bands", "barbell"];

  // ── XSS guard ─────────────────────────────────────────────────────────────
  function esc(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  // ── CSRF (Django) ──────────────────────────────────────────────────────────
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }

  async function api(path, { method = "GET", body } = {}) {
    const opts = {
      method,
      headers: { "X-CSRFToken": getCookie("csrftoken") },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(API + path, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`${resp.status}: ${text}`);
    }
    return resp.status === 204 ? null : resp.json();
  }

  // ── UI helpers ─────────────────────────────────────────────────────────────
  const loader = $("#forge-loader");
  const toast = $("#forge-toast");
  let toastTimer = null;

  function showLoader(on) {
    loader.hidden = !on;
    loader.innerHTML = "";
    if (on && window.CauldronLoader) {
      loader.appendChild(window.CauldronLoader.build(window.CauldronLoader.randomCaption(), 120));
    }
  }
  function notify(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 4000);
  }

  function switchTab(name) {
    $$(".forge-tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
    $$(".forge-panel").forEach((p) => p.classList.toggle("is-active", p.dataset.panel === name));
    if (name === "trial") loadTrial();
    if (name === "today") loadToday();
    if (name === "exercises") {
      if (catalogView === "tree") loadCatalogTree();
      else loadCatalog();
    }
    if (name === "progress") { loadProgress(); loadHistory(); }
  }

  $$(".forge-tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  // ── Forge sound + timers ───────────────────────────────────────────────────
  // Mystical "ping" presets synthesised with the Web Audio API (no asset files).
  let _audioCtx = null;
  function audioCtx() {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return _audioCtx;
  }
  function tone(ctx, freq, t0, dur, gain, type) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type || "sine";
    o.frequency.value = freq;
    o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.start(t0); o.stop(t0 + dur + 0.05);
  }
  const CHIMES = {
    cauldron: (c, t) => { tone(c, 528, t, 1.9, 0.25, "sine"); tone(c, 792, t, 2.3, 0.12, "sine"); tone(c, 1056, t, 2.7, 0.07, "sine"); },
    ember: (c, t) => { tone(c, 880, t, 1.2, 0.25, "triangle"); tone(c, 1320, t, 0.9, 0.10, "sine"); },
    gong: (c, t) => { tone(c, 196, t, 2.8, 0.30, "sine"); tone(c, 233, t, 2.5, 0.12, "sine"); tone(c, 392, t, 1.9, 0.07, "triangle"); },
    triad: (c, t) => { [523.25, 659.25, 783.99].forEach((f, i) => tone(c, f, t + i * 0.16, 1.0, 0.20, "sine")); },
  };
  const CHIME_LABELS = { cauldron: "Cauldron Chime", ember: "Ember Bell", gong: "Deep Gong", triad: "Arcane Triad" };
  function getChime() { return localStorage.getItem("forge-chime") || "cauldron"; }
  function setChime(name) { localStorage.setItem("forge-chime", name); }
  function playChime(name) {
    try {
      const c = audioCtx();
      if (c.state === "suspended") c.resume();
      (CHIMES[name] || CHIMES.cauldron)(c, c.currentTime);
    } catch (e) { /* audio unavailable — silent */ }
  }

  // Count-up stopwatch: fills the row's number input each second until toggled.
  const swTimers = new WeakMap();
  function toggleStopwatch(btn) {
    const cell = btn.closest(".forge-set-row, .forge-trial-row");
    const input = cell && cell.querySelector("input[type=number]");
    if (!input) return;
    if (swTimers.has(input)) {
      clearInterval(swTimers.get(input));
      swTimers.delete(input);
      btn.textContent = "▶ Start"; btn.classList.remove("is-running");
      return;
    }
    if (!input.value || isNaN(+input.value)) input.value = 0;
    btn.textContent = "⏸ Stop"; btn.classList.add("is-running");
    const id = setInterval(() => { input.value = (parseInt(input.value, 10) || 0) + 1; }, 1000);
    swTimers.set(input, id);
  }

  // Rest countdown: pings (mystical chime) when the rest is up.
  const restTimers = new WeakMap();
  function toggleRest(btn) {
    if (restTimers.has(btn)) {
      clearInterval(restTimers.get(btn));
      restTimers.delete(btn);
      btn.classList.remove("is-running");
      btn.textContent = btn.dataset.label;
      return;
    }
    const total = parseInt(btn.dataset.rest, 10) || 60;
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    let remaining = total;
    btn.classList.add("is-running");
    const tick = () => {
      if (remaining <= 0) {
        clearInterval(restTimers.get(btn));
        restTimers.delete(btn);
        btn.classList.remove("is-running");
        btn.textContent = "✓ Rested";
        playChime(getChime());
        setTimeout(() => { btn.textContent = btn.dataset.label; }, 3000);
        return;
      }
      const m = Math.floor(remaining / 60), s = remaining % 60;
      btn.textContent = `⏱ ${m}:${String(s).padStart(2, "0")}`;
      remaining--;
    };
    tick();
    restTimers.set(btn, setInterval(tick, 1000));
  }

  // Delegated handling for dynamically-rendered timer/rest/sound controls.
  $("#forge-app").addEventListener("click", (e) => {
    const sw = e.target.closest(".forge-timer-btn");
    if (sw) { e.preventDefault(); toggleStopwatch(sw); return; }
    const rb = e.target.closest(".forge-rest-btn");
    if (rb) { e.preventDefault(); toggleRest(rb); return; }
    const pv = e.target.closest(".forge-chime-preview");
    if (pv) { e.preventDefault(); playChime(getChime()); return; }
  });

  // ── Results reveal + unlock celebration ────────────────────────────────────
  const revealEl = $("#forge-reveal");
  const revealBody = $("#forge-reveal-body");
  const revealActions = $("#forge-reveal-actions");
  const anvil = $("#forge-anvil");

  function flamesMarkup(lit) {
    let s = `<div class="forge-flames" role="img" aria-label="${lit} out of 10 flames">`;
    for (let i = 0; i < 10; i++) {
      s += `<span class="forge-flame${i < lit ? " lit" : ""}" style="--i:${i}">🔥</span>`;
    }
    return s + "</div>";
  }

  function scoreCardMarkup(item) {
    const sc = item.score || {};
    if (!sc.has_data) {
      return `<div class="forge-score-row forge-score-row--nodata">` +
        `<span class="forge-score-ex">${item.exercise}</span>` +
        `<span class="forge-score-note">${sc.reason || "No peer data yet."}</span></div>`;
    }
    const approx = sc.estimated
      ? `<span class="forge-score-approx" title="Estimated benchmark — not yet from published data">≈ est.</span>`
      : sc.approximate
      ? `<span class="forge-score-approx" title="Interpolated from published percentiles">≈ approx</span>`
      : "";
    const note = sc.note ? `<div class="forge-score-subnote">${sc.note}</div>` : "";
    const topPct = Math.max(1, 100 - Math.round(sc.percentile));
    return `<div class="forge-score-row">` +
      `<div class="forge-score-head"><span class="forge-score-ex">${item.exercise} · ${item.value}</span>` +
      `<span class="forge-score-decile">top ${topPct}% · decile ${sc.decile}/10</span></div>` +
      flamesMarkup(sc.flames) +
      `<div class="forge-score-meta">${sc.flames}/10 🔥 vs peers your age &amp; sex ${approx} · ` +
      `<a href="${sc.url}" target="_blank" rel="noopener" data-no-loader>${sc.label} norms</a></div>` +
      note + `</div>`;
  }

  function igniteFlames(scope) {
    $$(".forge-flame.lit", scope).forEach((f, i) =>
      setTimeout(() => f.classList.add("ablaze"), 200 + i * 110)
    );
  }

  function openOverlay() {
    revealActions.hidden = true;
    revealActions.innerHTML = "";
    revealBody.innerHTML = "";
    revealEl.hidden = false;
    document.body.classList.add("forge-overlay-open");
    anvil.classList.add("forging");
  }
  function closeOverlay() {
    revealEl.hidden = true;
    document.body.classList.remove("forge-overlay-open");
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // Shows one panel of content + buttons; resolves when a button is clicked.
  function step(bodyHtml, buttons) {
    return new Promise((resolve) => {
      revealBody.innerHTML = bodyHtml;
      igniteFlames(revealBody);
      revealActions.innerHTML = buttons
        .map((b) => `<button class="btn-cauldron ${b.cls}" data-act="${b.act}">${b.label}</button>`)
        .join("");
      revealActions.hidden = false;
      revealActions.querySelectorAll("button").forEach((btn) =>
        btn.addEventListener("click", () => resolve(btn.dataset.act), { once: true })
      );
    });
  }

  async function showResults({ peer = [], unlocks = [], title = "The verdict" }) {
    openOverlay();
    await wait(1300); // let the forge roar before the reveal
    anvil.classList.remove("forging");

    // Celebrate each earned unlock first (Accept/Deny).
    for (const u of unlocks) {
      const body =
        `<div class="forge-unlock-burst" aria-hidden="true"><span>🔨</span><span>✨</span><span>🔥</span></div>` +
        `<h3 class="forge-reveal-title forge-unlock-title">New power unlocked!</h3>` +
        `<p class="forge-unlock-text">You've mastered <strong>${u.from_name}</strong>. ` +
        `The path to <strong>${u.to_name}</strong> is open — take it?</p>`;
      const act = await step(body, [
        { act: "accept", cls: "btn-cauldron--primary", label: "Claim it 🔥" },
        { act: "deny", cls: "btn-cauldron--ghost", label: "Not yet" },
      ]);
      try {
        await api(`progression/${u.prescription}/${act}/`, { method: "POST" });
        notify(act === "accept" ? `Advanced to ${u.to_name}!` : "Holding at your current level.");
      } catch (e) { notify("Couldn't update progression."); }
    }

    // Then the flames verdict.
    const scored = peer.filter((p) => p.score && p.score.has_data)
      .sort((a, b) => b.score.flames - a.score.flames);
    let html = `<h3 class="forge-reveal-title">${title}</h3>`;
    if (scored.length) {
      html += scoreCardMarkup(scored[0]);
      const rest = scored.slice(1);
      if (rest.length) {
        html += `<details class="forge-score-more"><summary>Other movements (${rest.length})</summary>` +
          rest.map(scoreCardMarkup).join("") + `</details>`;
      }
    } else {
      const reason = (peer.find((p) => p.score && p.score.reason) || {}).score?.reason ||
        "Add your birth year & sex in Equipment to unlock your 🔥 peer rating.";
      html += `<p class="forge-score-note forge-score-note--center">${reason}</p>`;
    }
    await step(html, [{ act: "done", cls: "btn-cauldron--primary", label: "Continue" }]);
    closeOverlay();
  }

  // ── Equipment ────────────────────────────────────────────────────────────--
  let allEquipment = [];

  async function loadEquipment() {
    showLoader(true);
    try {
      allEquipment = fetchEquipmentList();
      const profile = await api("equipment/");
      renderEquipment(allEquipment, new Set(profile.equipment));
      $("#forge-dumbbells").value = (profile.dumbbell_weights || []).join(", ");
      $("#forge-bands").value = (profile.band_levels || []).join(", ");
      $("#forge-barbell-inc").value = profile.barbell_min_increment || "";
      $("#forge-birth-year").value = profile.birth_year || "";
      $("#forge-sex").value = profile.sex || "undisclosed";
    } catch (e) {
      notify("Couldn't load equipment.");
    } finally {
      showLoader(false);
    }
  }

  // Equipment list (mirrors backend Equipment.Key) with an arcane line icon each.
  const EQUIP_ICON = {
    pullup_bar: '<path d="M4 6h16M7 6v5M17 6v5"/><circle cx="7" cy="13" r="2"/><circle cx="17" cy="13" r="2"/>',
    dumbbells: '<path d="M3 9v6M6 7v10M18 7v10M21 9v6M6 12h12"/>',
    barbell: '<path d="M2 10v4M5 8v8M19 8v8M22 10v4M5 12h14"/>',
    bands: '<path d="M4 5c6 4 10 4 16 0M4 12c6 4 10 4 16 0M4 19c6 4 10 4 16 0"/>',
    rowing_machine: '<path d="M3 18h18M6 18l3-7M15 11l3 7M9 11l6 0M12 11V6"/><circle cx="12" cy="5" r="1.5"/>',
    bench: '<path d="M3 10h18M5 10v8M19 10v8M3 14h18"/>',
    kettlebell: '<path d="M9 6a3 3 0 0 1 6 0"/><path d="M7 9c-1 4-2 5-2 8h14c0-3-1-4-2-8a5 5 0 0 0-10 0Z"/>',
    rings: '<path d="M7 3v5M17 3v5"/><circle cx="7" cy="13" r="4"/><circle cx="17" cy="13" r="4"/>',
  };

  function fetchEquipmentList() {
    return [
      { key: "pullup_bar", name: "Pull-up Bar" },
      { key: "dumbbells", name: "Dumbbells" },
      { key: "barbell", name: "Barbell + Plates" },
      { key: "bands", name: "Resistance Bands" },
      { key: "rowing_machine", name: "Rowing Machine" },
      { key: "bench", name: "Bench" },
      { key: "kettlebell", name: "Kettlebell" },
      { key: "rings", name: "Gymnastic Rings" },
    ];
  }

  function renderEquipment(list, owned) {
    const grid = $("#forge-equipment-grid");
    grid.innerHTML = "";
    list.forEach((eq) => {
      const label = document.createElement("label");
      label.className = "forge-equip-card" + (owned.has(eq.key) ? " is-on" : "");
      const icon =
        `<svg class="forge-equip-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
        `stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${
          EQUIP_ICON[eq.key] || ""
        }</svg>`;
      label.innerHTML =
        `<input type="checkbox" value="${eq.key}" ${owned.has(eq.key) ? "checked" : ""}>` +
        `<span class="forge-equip-glow" aria-hidden="true"></span>` +
        icon +
        `<span class="forge-equip-name">${eq.name}</span>` +
        `<span class="forge-equip-check" aria-hidden="true">✦</span>`;
      const input = label.querySelector("input");
      input.addEventListener("change", () => {
        label.classList.toggle("is-on", input.checked);
        toggleLoadableFields();
      });
      grid.appendChild(label);
    });
    toggleLoadableFields();
  }

  function selectedEquipment() {
    return $$("#forge-equipment-grid input:checked").map((i) => i.value);
  }

  function toggleLoadableFields() {
    const sel = new Set(selectedEquipment());
    $$("#forge-loadable [data-for]").forEach((el) => {
      el.hidden = !sel.has(el.dataset.for);
    });
  }

  function parseList(str) {
    return str.split(",").map((s) => s.trim()).filter(Boolean);
  }

  function fmtRest(sec) {
    if (sec >= 60) {
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return s ? `${m}m ${s}s` : `${m} min`;
    }
    return `${sec}s`;
  }

  $("#forge-save-equipment").addEventListener("click", async () => {
    showLoader(true);
    try {
      const dumbbells = parseList($("#forge-dumbbells").value).map(Number).filter((n) => !isNaN(n));
      const birthYear = parseInt($("#forge-birth-year").value, 10);
      await api("equipment/", {
        method: "PUT",
        body: {
          equipment: selectedEquipment(),
          dumbbell_weights: dumbbells,
          band_levels: parseList($("#forge-bands").value),
          barbell_min_increment: parseFloat($("#forge-barbell-inc").value) || 2.5,
          birth_year: isNaN(birthYear) ? null : birthYear,
          sex: $("#forge-sex").value,
        },
      });
      notify("Equipment saved. Now take the Trial.");
      switchTab("trial");
    } catch (e) {
      notify("Couldn't save equipment.");
    } finally {
      showLoader(false);
    }
  });

  // ── The Trial ────────────────────────────────────────────────────────────--
  const PATTERN_LABELS = {
    horizontal_push: "Horizontal Push",
    vertical_pull: "Vertical Pull",
    vertical_push: "Vertical Push",
    lower_unilateral: "Lower (Unilateral)",
    core_anti_extension: "Core (Anti-Extension)",
    hinge: "Hinge / Posterior Chain",
  };

  async function loadTrial() {
    showLoader(true);
    try {
      const [patterns, exercises, program] = await Promise.all([
        api("patterns/"),
        api("exercises/?equipment=mine"),
        api("program/").catch(() => null),
      ]);
      renderTrial(patterns, exercises);
      $("#forge-retake").hidden = !program;
    } catch (e) {
      notify("Couldn't load the Trial.");
    } finally {
      showLoader(false);
    }
  }

  function renderTrial(patterns, exercises) {
    const list = $("#forge-trial-list");
    list.innerHTML = "";
    patterns.forEach((p) => {
      // Pick an easy-ish anchor exercise the user is eligible for.
      const candidates = exercises
        .filter((e) => e.pattern_key === p.key)
        .sort((a, b) => a.difficulty_rank - b.difficulty_rank);
      if (!candidates.length) return;
      // Prefer the curated assessment anchor (a stable bodyweight test move);
      // fall back to the 2nd-easiest eligible exercise.
      const anchor =
        candidates.find((e) => e.is_assessment_anchor) ||
        candidates[Math.min(1, candidates.length - 1)];
      const row = document.createElement("div");
      row.className = "forge-trial-row";
      row.dataset.pattern = p.key;
      row.dataset.exercise = anchor.uuid;
      row.dataset.unilateral = anchor.is_unilateral ? "1" : "";
      const video = anchor.video_url
        ? `<a class="forge-video-link" href="${anchor.video_url}" target="_blank" rel="noopener" data-no-loader>▶ Watch how</a>`
        : "";
      const unit = anchor.is_timed ? "seconds" : "reps";
      // Rest hint: between sides for unilateral, otherwise before the test set.
      const rest = anchor.rest_seconds
        ? `<div class="forge-trial-rest">⏱ Rest ${fmtRest(anchor.rest_seconds)} ${
            anchor.is_unilateral ? "between sides" : "before testing"
          }</div>`
        : "";
      // Input(s): two per-leg fields for unilateral moves, one otherwise.
      const inputs = anchor.is_unilateral
        ? `<div class="forge-trial-legs">` +
          `<label class="forge-leg"><span>Left</span>` +
          `<input class="forge-trial-input forge-leg-input" data-side="left" type="number" min="0" placeholder="0" aria-label="left-side result for ${anchor.name}"></label>` +
          `<label class="forge-leg"><span>Right</span>` +
          `<input class="forge-trial-input forge-leg-input" data-side="right" type="number" min="0" placeholder="0" aria-label="right-side result for ${anchor.name}"></label>` +
          `</div>` +
          `<div class="forge-trial-asym" hidden></div>`
        : anchor.is_timed
        ? `<div class="forge-timed-cell">` +
          `<input class="forge-trial-input" type="number" min="0" placeholder="0" aria-label="result for ${anchor.name}">` +
          `<button type="button" class="forge-timer-btn" title="Tap to start; tap again when done">▶ Start</button>` +
          `</div>`
        : `<input class="forge-trial-input" type="number" min="0" placeholder="0" aria-label="result for ${anchor.name}">`;
      row.innerHTML =
        `<div>` +
        `<div class="forge-trial-pattern">${PATTERN_LABELS[p.key] || p.key}</div>` +
        `<div class="forge-trial-move">${anchor.name}${
          anchor.is_unilateral ? ` <span class="forge-trial-perleg">· per leg</span>` : ""
        }</div>` +
        `<div class="forge-trial-cues">${anchor.cues || ""} (${unit})</div>` +
        rest +
        video +
        `</div>` +
        `<div class="forge-trial-inputwrap">${inputs}</div>`;
      list.appendChild(row);
    });
    wireAsymmetryHints();
  }

  // Show an imbalance hint when both legs are entered and differ ≥20%.
  function wireAsymmetryHints() {
    $$(".forge-trial-row[data-unilateral='1']").forEach((row) => {
      const legs = $$(".forge-leg-input", row);
      const note = $(".forge-trial-asym", row);
      const check = () => {
        const vals = legs.map((i) => parseInt(i.value, 10)).filter((n) => !isNaN(n));
        if (vals.length < 2) { note.hidden = true; return; }
        const [lo, hi] = [Math.min(...vals), Math.max(...vals)];
        if (hi > 0 && (hi - lo) / hi >= 0.2) {
          note.hidden = false;
          note.textContent = `Imbalance noted — we'll place you from the weaker side (${lo}).`;
        } else {
          note.hidden = true;
        }
      };
      legs.forEach((i) => i.addEventListener("input", check));
    });
  }

  $("#forge-submit-trial").addEventListener("click", async () => {
    const results = $$(".forge-trial-row").map((row) => {
      const legs = $$(".forge-leg-input", row);
      const out = {
        pattern_key: row.dataset.pattern,
        tested_exercise: row.dataset.exercise,
      };
      if (legs.length) {
        // Place from the weaker leg so we never over-prescribe the weak side;
        // persist both sides so asymmetry can be tracked over time.
        const byside = {};
        legs.forEach((i) => {
          const v = parseInt(i.value, 10);
          if (!isNaN(v)) byside[i.dataset.side] = v;
        });
        out.left_reps = byside.left ?? null;
        out.right_reps = byside.right ?? null;
        const vals = Object.values(byside);
        out.reps_or_seconds = vals.length ? Math.min(...vals) : 0;
      } else {
        out.reps_or_seconds = parseInt(row.querySelector("input").value, 10) || 0;
      }
      return out;
    });
    if (!results.length) return notify("Set your equipment first.");
    showLoader(true);
    try {
      const res = await api("assessment/", { method: "POST", body: { split: "full_body_3x", results } });
      showLoader(false);
      await showResults({ peer: res.peer || [], title: "Your Trial verdict" });
      switchTab("today");
    } catch (e) {
      notify("Couldn't forge the program.");
    } finally {
      showLoader(false);
    }
  });

  $("#forge-retake").addEventListener("click", async () => {
    if (!confirm("Retake the Trial? Your current program will be replaced (history is kept).")) return;
    showLoader(true);
    try {
      await api("assessment/retake/", { method: "POST" });
      notify("Fresh Trial opened. Enter your new results.");
      loadTrial();
    } catch (e) {
      notify("Couldn't start a retake.");
    } finally {
      showLoader(false);
    }
  });

  // ── Today ────────────────────────────────────────────────────────────────--
  let currentSession = null;

  async function loadToday() {
    showLoader(true);
    try {
      const program = await api("program/").catch(() => null);
      const select = $("#forge-day-select");
      if (!program) {
        $("#forge-today-list").innerHTML = `<p class="forge-help">No program yet — take the Trial.</p>`;
        $("#forge-log-session").hidden = true;
        $("#forge-earphones").hidden = true;
        return;
      }
      select.innerHTML = program.days
        .map((d) => `<option value="${d.day_index}">${d.name}</option>`)
        .join("");
      select.onchange = () => openDay(parseInt(select.value, 10));
      openDay(parseInt(select.value, 10));
    } catch (e) {
      notify("Couldn't load today.");
    } finally {
      showLoader(false);
    }
  }

  async function openDay(dayIndex) {
    showLoader(true);
    try {
      currentSession = await api(`today/?day=${dayIndex}`);
      renderToday(currentSession);
      $("#forge-log-session").hidden = false;
      $("#forge-earphones").hidden = !voiceSupported();
    } catch (e) {
      notify("Couldn't open that day.");
    } finally {
      showLoader(false);
    }
  }

  function renderToday(session) {
    const list = $("#forge-today-list");
    list.innerHTML = "";
    // Group set logs by exercise.
    const groups = {};
    session.set_logs.forEach((s) => {
      (groups[s.exercise_name] = groups[s.exercise_name] || []).push(s);
    });
    Object.entries(groups).forEach(([name, sets]) => {
      const block = document.createElement("div");
      block.className = "forge-ex-block";
      const meta = sets[0] || {};
      const rest = meta.rest_seconds ? fmtRest(meta.rest_seconds) : "";
      const video = meta.video_url
        ? `<a class="forge-video-link" href="${meta.video_url}" target="_blank" rel="noopener" data-no-loader>▶ Watch how</a>`
        : "";
      const unit = meta.is_timed ? "s" : "reps";
      block.innerHTML =
        `<div class="forge-ex-head">` +
        `<span class="forge-ex-name">${name}</span>${video}` +
        (rest ? `<span class="forge-ex-rest" title="Recommended rest between sets">⏱ Rest ${rest}</span>` : "") +
        `</div>`;
      sets.forEach((s) => {
        const row = document.createElement("div");
        row.className = "forge-set-row" + (s.is_amrap ? " is-amrap" : "");
        const loadStr = s.expected_load != null ? ` @ ${s.expected_load}` : "";
        const input =
          `<input type="number" min="0" class="forge-trial-input forge-actual" ` +
          `data-uuid="${s.uuid}" data-load="${s.expected_load ?? ""}" placeholder="${s.expected_reps}">`;
        const inputCell = meta.is_timed
          ? `<div class="forge-timed-cell">${input}<button type="button" class="forge-timer-btn" title="Tap to start; tap again when done">▶ Start</button></div>`
          : input;
        row.innerHTML =
          `<span class="forge-set-label">Set ${s.set_index + 1}</span>` +
          `<span class="forge-set-expected">target ${s.expected_reps}${loadStr} ${unit}</span>` +
          inputCell;
        block.appendChild(row);
      });
      // Rest timer that pings when done (mystical chime).
      if (meta.rest_seconds) {
        const restRow = document.createElement("div");
        restRow.className = "forge-rest-row";
        restRow.innerHTML =
          `<button type="button" class="forge-rest-btn" data-rest="${meta.rest_seconds}">` +
          `⏱ Start rest (${fmtRest(meta.rest_seconds)})</button>`;
        block.appendChild(restRow);
      }
      list.appendChild(block);
    });
  }

  $("#forge-log-session").addEventListener("click", async () => {
    if (!currentSession) return;
    const sets = {};
    $$(".forge-actual").forEach((inp) => {
      const reps = parseInt(inp.value, 10);
      if (!isNaN(reps)) {
        sets[inp.dataset.uuid] = {
          actual_reps: reps,
          actual_load: inp.dataset.load === "" ? null : parseFloat(inp.dataset.load),
        };
      }
    });
    showLoader(true);
    try {
      const res = await api(`sessions/${currentSession.uuid}/log/`, { method: "POST", body: { sets } });
      $("#forge-log-session").hidden = true;
      showLoader(false);
      const unlocks = (res.progression || []).filter((d) => d.unlock).map((d) => d.unlock);
      await showResults({ peer: res.peer || [], unlocks, title: "Session forged" });
    } catch (e) {
      notify("Couldn't save the session.");
    } finally {
      showLoader(false);
    }
  });

  // ── Exercises (catalog + blocking) ─────────────────────────────────────────
  let catalogGroups = [];
  let catalogMineOnly = false;

  async function loadCatalog() {
    showLoader(true);
    try {
      const data = await api("catalog/");
      catalogGroups = data.groups || [];
      renderCatalog();
    } catch (e) {
      notify("Couldn't load exercises.");
    } finally {
      showLoader(false);
    }
  }

  function renderCatalog() {
    const host = $("#forge-catalog");
    host.innerHTML = "";
    const groups = catalogMineOnly
      ? catalogGroups.filter((g) => g.owned)
      : catalogGroups;
    if (!groups.length) {
      host.innerHTML = `<p class="forge-help">No exercises to show.</p>`;
      return;
    }
    groups.forEach((g) => {
      const section = document.createElement("div");
      section.className = "forge-cat-group" + (g.owned ? "" : " is-unowned");
      const owned = g.owned
        ? ""
        : `<span class="forge-cat-tag" title="You haven't marked this equipment as owned">not owned</span>`;
      let rows = "";
      g.exercises.forEach((ex) => {
        const eligible = ex.eligible;
        const blockedCls = ex.is_blocked ? " is-blocked" : "";
        const sub =
          ex.is_blocked && ex.substitute
            ? `<span class="forge-cat-sub">→ using <strong>${ex.substitute.name}</strong></span>`
            : ex.is_blocked
            ? `<span class="forge-cat-sub forge-cat-sub--none">→ no substitute available</span>`
            : "";
        const btnLabel = ex.is_blocked ? "Unblock" : "Block";
        const btnAct = ex.is_blocked ? "unblock" : "block";
        const fires =
          ex.best_fires != null
            ? `<span class="forge-cat-fires${ex.best_fires_estimated ? " is-estimated" : ""}" ` +
              `title="Your best peer score on ${ex.name}: ${ex.best_fires}/10 fires (${ex.best_fires_value} reps)` +
              `${ex.best_fires_estimated ? " — estimated benchmark, not yet from published data" : ""}">` +
              `🔥 <strong>${ex.best_fires}</strong><span class="forge-cat-fires-max">/10</span>` +
              `${ex.best_fires_estimated ? `<span class="forge-cat-fires-est">est.</span>` : ""}</span>`
            : "";
        rows +=
          `<div class="forge-cat-row${blockedCls}">` +
          `<div class="forge-cat-meta">` +
          `<span class="forge-cat-name">${ex.name}${fires}</span>` +
          `<span class="forge-cat-pattern">${PATTERN_LABELS[ex.pattern_key] || ex.pattern_name}` +
          (eligible ? "" : ` · <span class="forge-cat-tag">needs gear</span>`) + `</span>` +
          sub +
          `</div>` +
          `<button class="btn-cauldron btn-cauldron--ghost forge-cat-btn" ` +
          `data-action="${btnAct}" data-uuid="${ex.uuid}">${btnLabel}</button>` +
          `</div>`;
      });
      section.innerHTML =
        `<h3 class="forge-cat-head">${g.equipment_name}${owned}</h3>` +
        `<div class="forge-cat-rows">${rows}</div>`;
      host.appendChild(section);
    });
  }

  // Event delegation for block/unblock buttons.
  $("#forge-catalog").addEventListener("click", async (e) => {
    const btn = e.target.closest(".forge-cat-btn");
    if (!btn) return;
    const { action, uuid } = btn.dataset;
    btn.disabled = true;
    try {
      const res = await api(`exercises/${uuid}/${action}/`, { method: "POST" });
      if (action === "block") {
        const sub = res.substitute ? ` Substitute: ${res.substitute.name}.` : " No substitute available.";
        const swap = res.swapped ? ` Swapped in ${res.swapped} live prescription(s).` : "";
        notify(`Blocked.${sub}${swap}`);
      } else {
        notify("Unblocked.");
      }
      await loadCatalog(); // refresh blocked flags + substitutes
    } catch (err) {
      notify("Couldn't update that exercise.");
      btn.disabled = false;
    }
  });

  const catalogMineToggle = $("#forge-catalog-mine");
  if (catalogMineToggle) {
    catalogMineToggle.addEventListener("change", () => {
      catalogMineOnly = catalogMineToggle.checked;
      renderCatalog();
    });
  }

  // ── Skill tree ────────────────────────────────────────────────────────────
  let catalogView = "tree"; // "tree" | "cards"

  const PATTERN_ORDER = [
    "horizontal_push", "vertical_pull", "vertical_push",
    "lower_unilateral", "core_anti_extension", "hinge",
  ];

  const PATTERN_MUSCLES = {
    horizontal_push: "Chest · Front Delts · Triceps",
    vertical_pull: "Lats · Biceps · Mid-back",
    vertical_push: "Shoulders · Triceps",
    lower_unilateral: "Quads · Glutes",
    core_anti_extension: "Abs · Deep Core",
    hinge: "Hamstrings · Glutes · Low Back",
  };

  const PATTERN_ICON_PATHS = {
    horizontal_push: '<path d="M4 16h13"/><path d="M17 16l-5-5"/><path d="M17 16l-5 5"/><rect x="22" y="8" width="6" height="16" rx="1"/>',
    vertical_pull:   '<path d="M6 6h20"/><path d="M16 6v15"/><path d="M16 21l-5-5"/><path d="M16 21l5-5"/>',
    vertical_push:   '<path d="M16 26V11"/><path d="M16 11l-5 5"/><path d="M16 11l5 5"/><path d="M8 6h16"/>',
    lower_unilateral:'<path d="M12 5v9l-4 13"/><path d="M12 14l5 4-2 9"/><circle cx="12" cy="5" r="2"/>',
    core_anti_extension:'<path d="M4 20h24"/><path d="M7 20c4-9 14-9 18 0"/><circle cx="16" cy="9" r="2"/>',
    hinge:           '<path d="M5 11v6"/><path d="M27 11v6"/><path d="M5 14h22"/><path d="M9 14V9"/><path d="M23 14V9"/>',
  };

  const TREE_STATE_LABELS = {
    done: "Cleared", current: "Active", next: "Next up",
    locked: "Needs gear", blocked: "Blocked", noprogram: "",
  };

  // Structural equipment: non-loadable, fixed fixtures.
  // Exercises that only require these (plus bodyweight) are part of the
  // bodyweight spine. Exercises that also require loadable gear (dumbbells,
  // barbell, bands, kettlebell) or machines (rowing_machine) go in the gear branch.
  const STRUCTURAL_EQUIP = new Set(["bodyweight", "pullup_bar", "bench", "rings"]);

  function isSpineExercise(ex) {
    return !ex.required_equipment.length ||
      ex.required_equipment.every((k) => STRUCTURAL_EQUIP.has(k));
  }

  function computeExerciseStates(exercises, catalogMap, currentByPattern) {
    const byPattern = {};
    exercises.forEach((ex) => {
      (byPattern[ex.pattern_key] = byPattern[ex.pattern_key] || []).push(ex);
    });
    const stateMap = {};
    Object.entries(byPattern).forEach(([patternKey, exList]) => {
      const currentNames = currentByPattern[patternKey] || new Set();
      let maxCurrentRank = -1;
      exList.forEach((ex) => {
        if (currentNames.has(ex.name)) maxCurrentRank = Math.max(maxCurrentRank, ex.difficulty_rank);
      });
      exList.forEach((ex) => {
        const cat = catalogMap[ex.uuid] || {};
        const isBlocked = cat.is_blocked || false;
        if (currentNames.has(ex.name)) {
          stateMap[ex.uuid] = isBlocked ? "blocked" : "current";
        } else if (maxCurrentRank > -1 && ex.difficulty_rank < maxCurrentRank) {
          stateMap[ex.uuid] = "done";
        } else {
          const eligible = cat.eligible !== undefined ? cat.eligible : false;
          if (isBlocked) stateMap[ex.uuid] = "blocked";
          else if (eligible) stateMap[ex.uuid] = maxCurrentRank === -1 ? "noprogram" : "next";
          else stateMap[ex.uuid] = "locked";
        }
      });
    });
    return stateMap;
  }

  async function loadCatalogTree() {
    showLoader(true);
    try {
      const [exercises, catalog, program] = await Promise.all([
        api("exercises/"),
        api("catalog/"),
        api("program/").catch(() => null),
      ]);
      const catalogMap = {};
      (catalog.groups || []).forEach((g) => {
        g.exercises.forEach((ex) => { catalogMap[ex.uuid] = ex; });
      });
      const currentByPattern = {};
      if (program) {
        program.days.forEach((d) => {
          d.prescriptions.forEach((p) => {
            if (!currentByPattern[p.pattern_key]) currentByPattern[p.pattern_key] = new Set();
            currentByPattern[p.pattern_key].add(p.exercise_name);
          });
        });
      }
      const stateMap = computeExerciseStates(exercises, catalogMap, currentByPattern);
      renderSkillTree(exercises, catalogMap, stateMap);
    } catch (e) {
      notify("Couldn't load the skill tree.");
    } finally {
      showLoader(false);
    }
  }

  function fmtRepsRange(ex) {
    if (ex.rep_range_min == null) return "";
    const range = ex.rep_range_min === ex.rep_range_max
      ? `${ex.rep_range_min}` : `${ex.rep_range_min}–${ex.rep_range_max}`;
    return ex.is_timed ? `${range}s hold` : `${range} reps`;
  }

  function renderSkillTree(exercises, catalogMap, stateMap) {
    const host = $("#forge-tree");
    host.innerHTML = "";
    const byPattern = {};
    exercises.forEach((ex) => {
      (byPattern[ex.pattern_key] = byPattern[ex.pattern_key] || []).push(ex);
    });
    Object.values(byPattern).forEach((arr) =>
      arr.sort((a, b) => a.difficulty_rank - b.difficulty_rank)
    );
    const frag = document.createDocumentFragment();

    PATTERN_ORDER.forEach((patKey) => {
      const exList = byPattern[patKey];
      if (!exList || !exList.length) return;

      const track = document.createElement("div");
      track.className = "ftree-track";

      const head = document.createElement("div");
      head.className = "ftree-track-head";
      head.innerHTML =
        `<svg class="ftree-pattern-icon" viewBox="0 0 32 32" fill="none" stroke="currentColor" ` +
        `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
        (PATTERN_ICON_PATHS[patKey] || "") + `</svg>`;
      const nameEl = document.createElement("div");
      nameEl.className = "ftree-pattern-name";
      nameEl.textContent = PATTERN_LABELS[patKey] || patKey;
      head.appendChild(nameEl);
      track.appendChild(head);

      const musclesEl = document.createElement("div");
      musclesEl.className = "ftree-pattern-muscles";
      musclesEl.textContent = PATTERN_MUSCLES[patKey] || "";
      track.appendChild(musclesEl);

      const spine = exList.filter(isSpineExercise);
      const gear  = exList.filter((ex) => !isSpineExercise(ex));

      const orbIcons = {
        done: "✦", current: "●", next: "○", noprogram: "○", locked: "⊘", blocked: "✕",
      };

      function buildLadder(list) {
        const ladder = document.createElement("div");
        ladder.className = "ftree-ladder";
        list.forEach((ex) => {
          const state = stateMap[ex.uuid] || "locked";
          const cat   = catalogMap[ex.uuid] || {};

          const node = document.createElement("div");
          node.className = "ftree-node";
          node.setAttribute("data-state", state);
          node.setAttribute("data-uuid", ex.uuid);
          node.setAttribute("tabindex", "0");
          node.setAttribute("role", "button");
          node.setAttribute("aria-label", `${ex.name} — ${TREE_STATE_LABELS[state] || state}`);

          const orb = document.createElement("div");
          orb.className = "ftree-node-orb";
          orb.textContent = orbIcons[state] || "○";
          orb.setAttribute("aria-hidden", "true");

          const tile = document.createElement("div");
          tile.className = "ftree-node-tile";

          const info = document.createElement("div");
          info.className = "ftree-node-info";
          const nameDiv = document.createElement("div");
          nameDiv.className = "ftree-node-name";
          nameDiv.textContent = ex.name;
          info.appendChild(nameDiv);
          const reps = fmtRepsRange(ex);
          if (reps) {
            const repsDiv = document.createElement("div");
            repsDiv.className = "ftree-node-reps";
            repsDiv.textContent = reps;
            info.appendChild(repsDiv);
          }
          tile.appendChild(info);

          const label = TREE_STATE_LABELS[state];
          if (label) {
            const badge = document.createElement("span");
            badge.className = `ftree-node-badge ftree-node-badge--${state}`;
            badge.textContent = label;
            tile.appendChild(badge);
          }

          if (cat.best_fires != null) {
            const fires = document.createElement("span");
            fires.className = "ftree-node-fires";
            fires.title = `Best peer score: ${cat.best_fires}/10 flames`;
            fires.textContent = `🔥${cat.best_fires}`;
            tile.appendChild(fires);
          }

          node.appendChild(orb);
          node.appendChild(tile);

          const openModal = () => openExerciseModal(ex, state, cat);
          node.addEventListener("click", openModal);
          node.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openModal(); }
          });

          ladder.appendChild(node);
        });
        return ladder;
      }

      if (spine.length) {
        const lbl = document.createElement("div");
        lbl.className = "ftree-section-label";
        lbl.textContent = "Bodyweight";
        track.appendChild(lbl);
        track.appendChild(buildLadder(spine));
      }
      if (gear.length) {
        const lbl = document.createElement("div");
        lbl.className = "ftree-section-label";
        lbl.textContent = "With equipment";
        track.appendChild(lbl);
        track.appendChild(buildLadder(gear));
      }

      frag.appendChild(track);
    });
    host.appendChild(frag);
  }

  // ── Exercise detail modal ─────────────────────────────────────────────────
  const exModal = $("#forge-ex-modal");
  const exModalBody = $("#forge-ex-modal-body");
  const exModalActions = $("#forge-ex-modal-actions");

  function openExerciseModal(ex, state, cat) {
    exModalBody.innerHTML = "";
    exModalActions.innerHTML = "";

    exModalBody.innerHTML =
      `<p class="forge-ex-modal-name">${esc(ex.name)}</p>` +
      `<p class="forge-ex-modal-pattern">${esc(PATTERN_LABELS[ex.pattern_key] || ex.pattern_key)}</p>`;

    if (TREE_STATE_LABELS[state]) {
      const s = document.createElement("div");
      s.className = `forge-ex-modal-state forge-ex-modal-state--${state}`;
      s.textContent = TREE_STATE_LABELS[state];
      exModalBody.appendChild(s);
    }

    function addSection(label, html) {
      const sec = document.createElement("div");
      sec.className = "forge-ex-modal-section";
      sec.innerHTML = `<div class="forge-ex-modal-label">${label}</div>${html}`;
      exModalBody.appendChild(sec);
    }

    const reps = fmtRepsRange(ex);
    if (reps) addSection("Target", `<div class="forge-ex-modal-value">${reps}</div>`);
    if (ex.cues) addSection("Cues", `<div class="forge-ex-modal-cues">"${esc(ex.cues)}"</div>`);
    if (ex.video_url && /^https:\/\//i.test(ex.video_url)) {
      addSection("Video", `<a class="forge-video-link" href="${esc(ex.video_url)}" target="_blank" rel="noopener" data-no-loader>▶ Watch how</a>`);
    }

    const req = ex.required_equipment.filter((k) => k !== "bodyweight");
    if (req.length) {
      const tags = req.map((k) => `<span class="forge-ex-modal-equip-tag">${esc(k.replace(/_/g, " "))}</span>`).join("");
      addSection("Equipment", `<div class="forge-ex-modal-equip">${tags}</div>`);
    }

    if (cat.best_fires != null) {
      addSection(
        "Peer score",
        flamesMarkup(cat.best_fires) +
        `<div class="forge-score-note">${cat.best_fires}/10 🔥 vs peers your age &amp; sex</div>`
      );
      igniteFlames(exModalBody);
    }

    if (state === "blocked" && cat.substitute) {
      addSection("Substitute", `<div class="forge-ex-modal-value">→ ${esc(cat.substitute.name)}</div>`);
    }

    const isBlocked = state === "blocked";
    const actionBtn = document.createElement("button");
    actionBtn.className = `btn-cauldron ${isBlocked ? "btn-cauldron--primary" : "btn-cauldron--ghost"}`;
    actionBtn.textContent = isBlocked ? "Unblock" : "Block this exercise";
    actionBtn.addEventListener("click", async () => {
      actionBtn.disabled = true;
      const act = isBlocked ? "unblock" : "block";
      try {
        const res = await api(`exercises/${ex.uuid}/${act}/`, { method: "POST" });
        if (act === "block") {
          const sub = res.substitute ? ` Substitute: ${res.substitute.name}.` : " No substitute available.";
          notify(`Blocked.${sub}`);
        } else {
          notify("Unblocked.");
        }
        closeExerciseModal();
        loadCatalogTree();
      } catch (err) {
        notify("Couldn't update that exercise.");
        actionBtn.disabled = false;
      }
    });
    exModalActions.appendChild(actionBtn);

    exModal.hidden = false;
    document.body.classList.add("forge-overlay-open");
  }

  function closeExerciseModal() {
    exModal.hidden = true;
    document.body.classList.remove("forge-overlay-open");
  }

  const exModalCloseBtn = $("#forge-ex-modal-close");
  if (exModalCloseBtn) exModalCloseBtn.addEventListener("click", closeExerciseModal);
  if (exModal) {
    exModal.addEventListener("click", (e) => { if (e.target === exModal) closeExerciseModal(); });
  }

  // ── View toggle ───────────────────────────────────────────────────────────
  const treeContainer = $("#forge-tree");
  const catalogContainer = $("#forge-catalog");
  const catalogFilterLabel = $("#forge-catalog-filter-label");

  $$(".forge-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      if (view === catalogView) return;
      catalogView = view;
      $$(".forge-view-btn").forEach((b) => {
        b.classList.toggle("is-active", b.dataset.view === view);
        b.setAttribute("aria-pressed", b.dataset.view === view ? "true" : "false");
      });
      if (view === "tree") {
        catalogContainer.hidden = true;
        if (catalogFilterLabel) catalogFilterLabel.hidden = true;
        treeContainer.hidden = false;
        loadCatalogTree();
      } else {
        treeContainer.hidden = true;
        if (catalogFilterLabel) catalogFilterLabel.hidden = false;
        catalogContainer.hidden = false;
        loadCatalog();
      }
    });
  });

  // ── History ──────────────────────────────────────────────────────────────--
  async function loadHistory() {
    showLoader(true);
    try {
      const sessions = await api("sessions/");
      const completed = sessions.filter((s) => s.status === "completed");
      const wrap = $("#forge-history");
      if (!completed.length) {
        wrap.innerHTML = `<p class="forge-help">No saved sessions yet.</p>`;
        return;
      }
      wrap.innerHTML = "";
      completed.forEach((s) => {
        const card = document.createElement("div");
        card.className = "forge-history-card";
        const when = s.performed_at ? new Date(s.performed_at).toLocaleString() : "—";
        let rows = `<div class="forge-history-date">${s.day_name || "Session"} · ${when}</div>`;
        s.set_logs.forEach((sl) => {
          const cls = sl.actual_reps >= (sl.expected_reps || 0) ? "forge-delta-up" : "forge-delta-down";
          rows +=
            `<div class="forge-history-set"><span>${sl.exercise_name} · set ${sl.set_index + 1}</span>` +
            `<span class="${cls}">${sl.actual_reps ?? "–"} / ${sl.expected_reps ?? "–"} reps</span></div>`;
        });
        card.innerHTML = rows;
        wrap.appendChild(card);
      });
    } catch (e) {
      notify("Couldn't load history.");
    } finally {
      showLoader(false);
    }
  }

  // ── Progression chart ────────────────────────────────────────────────────--
  let progressPoints = [];
  let chartPeriod = "day";
  let chartMetric = "total_volume";
  let chartExercise = "";
  let decileCutoffs = null; // {10: v, ...} for the selected exercise, or null
  let exerciseFilterReady = false;

  async function loadProgress() {
    try {
      const qs = chartExercise ? `?exercise=${encodeURIComponent(chartExercise)}` : "";
      const data = await api("progress/" + qs);
      progressPoints = data.points || [];
      populateExerciseFilter(data.exercises || []);
      renderAsymmetry(data.asymmetry || []);
    } catch (e) {
      progressPoints = [];
    }
    // Decile reference lines only when a specific exercise with norms is chosen.
    decileCutoffs = null;
    if (chartExercise) {
      try {
        const norms = await api(`norms/?exercise=${encodeURIComponent(chartExercise)}`);
        if (norms.has_data) decileCutoffs = norms.cutoffs;
      } catch (e) { /* no norms for this move */ }
    }
    drawChart();
  }

  function populateExerciseFilter(names) {
    if (exerciseFilterReady) return; // populate once; preserve selection
    const sel = $("#forge-chart-exercise");
    if (!sel) return;
    names.forEach((n) => {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => {
      chartExercise = sel.value;
      loadProgress();
    });
    exerciseFilterReady = true;
  }

  function renderAsymmetry(rows) {
    const card = $("#forge-asym-card");
    const host = $("#forge-asym");
    if (!card || !host) return;
    if (!rows.length) { card.hidden = true; return; }
    card.hidden = false;
    // Latest entry per exercise.
    const latest = {};
    rows.forEach((r) => { latest[r.exercise] = r; });
    host.innerHTML = Object.values(latest).map((r) => {
      const max = Math.max(r.left, r.right, 1);
      const gap = Math.round((Math.abs(r.left - r.right) / max) * 100);
      const weak = r.left < r.right ? "left" : r.right < r.left ? "right" : "even";
      const warn = gap >= 20 ? " is-warn" : "";
      return `<div class="forge-asym-row${warn}">` +
        `<span class="forge-asym-ex">${r.exercise}</span>` +
        `<span class="forge-asym-bars">` +
          `<span class="forge-asym-bar"><i style="width:${(r.left / max) * 100}%"></i>L ${r.left}</span>` +
          `<span class="forge-asym-bar"><i style="width:${(r.right / max) * 100}%"></i>R ${r.right}</span>` +
        `</span>` +
        `<span class="forge-asym-gap">${gap === 0 ? "balanced" : gap + "% gap" + (weak !== "even" ? ` · ${weak} weaker` : "")}</span>` +
        `</div>`;
    }).join("");
  }

  // Bucket key for a date string at the chosen granularity.
  function periodKey(iso, period) {
    const d = new Date(iso);
    if (period === "month") {
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    }
    if (period === "week") {
      // ISO-ish week: year + week number.
      const onejan = new Date(d.getFullYear(), 0, 1);
      const week = Math.ceil(((d - onejan) / 86400000 + onejan.getDay() + 1) / 7);
      return `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
    }
    return iso.slice(0, 10); // day
  }

  function aggregate() {
    const buckets = new Map();
    progressPoints.forEach((p) => {
      const k = periodKey(p.date, chartPeriod);
      const cur = buckets.get(k) || 0;
      // Volume/reps sum within a period; best_amrap takes the max.
      buckets.set(k, chartMetric === "best_amrap"
        ? Math.max(cur, p[chartMetric] || 0)
        : cur + (p[chartMetric] || 0));
    });
    return Array.from(buckets.entries()).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  }

  function drawChart() {
    const host = $("#forge-chart");
    const series = aggregate();
    if (series.length === 0) {
      host.innerHTML = `<p class="forge-help">Log a session to see your progression.</p>`;
      return;
    }
    const W = 640, H = 240, padL = 44, padR = 16, padT = 16, padB = 36;
    const vals = series.map((s) => s[1]);
    const n = series.length;

    // Decile reference lines: only when a specific exercise (with norms) is
    // charted on the AMRAP metric. Show the cutoff just below the lowest value,
    // the one just above the highest, and every cutoff in between.
    const legend = $("#forge-chart-legend");
    let deciles = [];
    if (decileCutoffs && chartMetric === "best_amrap") {
      const cuts = Object.entries(decileCutoffs)
        .map(([d, v]) => ({ d: +d, v: +v })).sort((a, b) => a.v - b.v);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const below = cuts.filter((c) => c.v <= lo).slice(-1);
      const between = cuts.filter((c) => c.v > lo && c.v < hi);
      const above = cuts.filter((c) => c.v >= hi).slice(0, 1);
      deciles = [...below, ...between, ...above];
    }
    if (legend) {
      legend.hidden = deciles.length === 0;
      if (deciles.length) legend.textContent = `Dashed lines = peer deciles for ${chartExercise} (your age & sex).`;
    }

    const decileMax = deciles.length ? Math.max(...deciles.map((c) => c.v)) : 0;
    const maxV = Math.max(...vals, decileMax, 1);
    const xFor = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : (i * (W - padL - padR)) / (n - 1));
    const yFor = (v) => H - padB - (v / maxV) * (H - padT - padB);

    const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const y = padT + f * (H - padT - padB);
      const label = Math.round(maxV * (1 - f));
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="forge-chart-grid"/>` +
        `<text x="${padL - 8}" y="${y + 4}" class="forge-chart-axis" text-anchor="end">${label}</text>`;
    }).join("");

    const decileLines = deciles.map((c) => {
      const y = yFor(c.v);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="forge-chart-decile"/>` +
        `<text x="${W - padR}" y="${y - 3}" class="forge-chart-decile-label" text-anchor="end">D${c.d}</text>`;
    }).join("");

    const linePts = series.map((s, i) => `${xFor(i)},${yFor(s[1])}`).join(" ");
    const area = `M ${padL},${H - padB} L ` + series.map((s, i) => `${xFor(i)},${yFor(s[1])}`).join(" L ") +
      ` L ${xFor(n - 1)},${H - padB} Z`;
    const dots = series.map((s, i) =>
      `<circle cx="${xFor(i)}" cy="${yFor(s[1])}" r="3.5" class="forge-chart-dot"><title>${s[0]}: ${s[1]}</title></circle>`
    ).join("");
    // X labels: show a handful to avoid crowding.
    const step = Math.ceil(n / 6);
    const xLabels = series.map((s, i) =>
      i % step === 0 || i === n - 1
        ? `<text x="${xFor(i)}" y="${H - 12}" class="forge-chart-axis" text-anchor="middle">${s[0].slice(5)}</text>`
        : ""
    ).join("");

    host.innerHTML =
      `<svg viewBox="0 0 ${W} ${H}" class="forge-chart-svg" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Progression chart">` +
      gridLines +
      decileLines +
      `<path d="${area}" class="forge-chart-area"/>` +
      `<polyline points="${linePts}" class="forge-chart-line"/>` +
      dots + xLabels +
      `</svg>`;
  }

  $$(".forge-period").forEach((b) =>
    b.addEventListener("click", () => {
      chartPeriod = b.dataset.period;
      $$(".forge-period").forEach((x) => x.classList.toggle("is-active", x === b));
      drawChart();
    })
  );
  const metricSel = $("#forge-chart-metric");
  if (metricSel) metricSel.addEventListener("change", () => { chartMetric = metricSel.value; drawChart(); });

  // Rest-ping sound picker.
  const chimeSel = $("#forge-chime-select");
  if (chimeSel) {
    chimeSel.value = getChime();
    chimeSel.addEventListener("change", () => { setChime(chimeSel.value); playChime(chimeSel.value); });
  }

  // ── Earphones mode — hands-free, voice-guided Today session ─────────────────
  // Speaks each set (exercise, target, rest) via the Web Speech API and listens
  // for the user's reps / "start"-"stop" / "skip" / "exit". Pure client layer
  // over the existing Today data + the existing sessions/<uuid>/log/ endpoint.
  // Recognition is Chromium-only; everything degrades to on-screen buttons.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  function voiceSupported() {
    return !!(window.speechSynthesis && SR);
  }

  // Voice INPUT (microphone) is opt-in and OFF by default: the browser's speech
  // recognition plays a "ding" earcon every time it starts listening, and it
  // restarts on each silence timeout (rest, between reps) — a constant beep on
  // earphones. Off → spoken guidance still plays, the user taps to log.
  function voiceInputEnabled() { return localStorage.getItem("forge-voice-input") === "on"; }
  function setVoiceInputEnabled(on) { localStorage.setItem("forge-voice-input", on ? "on" : "off"); }
  // Phrase prompts for the active mode: spoken commands vs. on-screen buttons.
  function micCue(sayPhrase, tapPhrase) { return voiceInputEnabled() ? sayPhrase : tapPhrase; }

  const voice = {
    active: false,
    steps: [],       // flattened, ordered set descriptors for the session
    idx: 0,
    collected: {},   // set uuid -> { actual_reps, actual_load }
    state: "idle",   // ready_timed | timing | awaiting_reps | resting | done
    recog: null,
    wantListen: false,
    swId: null,      // count-up stopwatch (timed holds)
    restId: null,    // rest countdown
    elapsed: 0,
    lastAnnounce: "",
    ttsVoice: null,
  };

  const NUM_WORDS = {
    zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
    eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13,
    fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
    nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60,
    seventy: 70, eighty: 80, ninety: 90,
  };
  function wordsToNumber(text) {
    const tokens = String(text).toLowerCase().replace(/[-,]/g, " ").split(/\s+/);
    let total = null;
    for (const tok of tokens) {
      if (Object.prototype.hasOwnProperty.call(NUM_WORDS, tok)) {
        total = (total || 0) + NUM_WORDS[tok];
      }
    }
    return total;
  }
  function parseReps(alts) {
    for (const a of alts) {
      const m = String(a).match(/\d+/);
      if (m) { const n = parseInt(m[0], 10); if (n >= 0 && n <= 999) return n; }
      const w = wordsToNumber(a);
      if (w != null) return w;
    }
    return null;
  }

  // ── DOM helpers ──
  const vEl = (name) => $("#forge-voice-" + name);
  const setProgress = (t) => { vEl("progress").textContent = t; };
  const setExercise = (t) => { vEl("exercise").textContent = t; };
  const setTarget = (t) => { vEl("target").textContent = t || ""; };
  const setInstruction = (t) => { vEl("instruction").textContent = t || ""; };
  const showMic = (on) => { vEl("mic").hidden = !on; };
  function showTimer(on) { vEl("timer").hidden = !on; }
  function updateTimer(sec) {
    const m = Math.floor(sec / 60), s = sec % 60;
    vEl("timer").textContent = `${m}:${String(s).padStart(2, "0")}`;
  }
  function setControls(buttons) {
    vEl("controls").innerHTML = buttons
      .map((b) =>
        `<button type="button" class="btn-cauldron ` +
        `${b.act === "exit" || b.act === "skipset" || b.act === "skiprest" ? "btn-cauldron--ghost" : "btn-cauldron--primary"} ` +
        `forge-voice-cmd" data-act="${b.act}">${b.label}</button>`)
      .join("");
  }
  function setRepsControls() {
    vEl("controls").innerHTML =
      `<div class="forge-voice-reps">` +
      `<input type="number" min="0" id="forge-voice-reps-input" class="forge-trial-input" placeholder="reps" inputmode="numeric">` +
      `<button type="button" class="btn-cauldron btn-cauldron--primary forge-voice-cmd" data-act="logreps">Log</button>` +
      `</div>` +
      `<button type="button" class="btn-cauldron btn-cauldron--ghost forge-voice-cmd" data-act="skipset">Skip</button>` +
      `<button type="button" class="btn-cauldron btn-cauldron--ghost forge-voice-cmd" data-act="exit">Exit</button>`;
  }

  // ── Speech (TTS) ──
  function pickVoice() {
    if (!window.speechSynthesis) return;
    const choose = () => {
      const vs = window.speechSynthesis.getVoices();
      voice.ttsVoice =
        vs.find((v) => /en[-_]US/i.test(v.lang)) ||
        vs.find((v) => /^en/i.test(v.lang)) || null;
    };
    choose();
    if (!voice.ttsVoice) window.speechSynthesis.onvoiceschanged = choose;
  }
  function speak(text) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) return resolve();
      stopListening(); // don't let the mic hear our own voice
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "en-US";
        u.rate = 1.0;
        if (voice.ttsVoice) u.voice = voice.ttsVoice;
        u.onend = resolve;
        u.onerror = resolve;
        window.speechSynthesis.speak(u);
      } catch (e) { resolve(); }
    });
  }

  // ── Speech recognition (STT) ──
  function startListening() {
    if (!SR || !voice.active) return;
    // Mic off → never arm recognition, so the browser never beeps.
    if (!voiceInputEnabled()) { voice.wantListen = false; showMic(false); return; }
    voice.wantListen = true;
    if (voice.recog) return;
    const r = new SR();
    r.lang = "en-US";
    r.continuous = true;
    r.interimResults = false;
    r.maxAlternatives = 4;
    r.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        if (res.isFinal) handleHeard(Array.from(res).map((a) => a.transcript));
      }
    };
    r.onerror = (ev) => {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
        voice.wantListen = false;
        micDenied();
      }
    };
    r.onend = () => {
      voice.recog = null;
      if (voice.active && voice.wantListen) setTimeout(startListening, 250);
    };
    try { r.start(); voice.recog = r; showMic(true); }
    catch (e) { /* start() throws if already running — ignore */ }
  }
  function stopListening() {
    voice.wantListen = false;
    showMic(false);
    if (voice.recog) {
      try { voice.recog.onend = null; voice.recog.stop(); } catch (e) { /* noop */ }
      voice.recog = null;
    }
  }
  function micDenied() {
    showMic(false);
    setInstruction("Microphone blocked — use the on-screen buttons (allow the mic and reopen for voice).");
    notify("Mic permission denied — voice off. Use the buttons.");
  }

  // ── Command routing ──
  function handleHeard(alts) {
    const primary = (alts[0] || "").trim();
    vEl("heard").textContent = primary ? "“" + primary + "”" : "";
    const t = alts.join(" • ").toLowerCase();

    if (/\b(exit|quit|cancel|close|abort)\b/.test(t)) return command("exit");
    if (/\b(repeat|again|pardon)\b/.test(t)) return command("repeat");

    switch (voice.state) {
      case "ready_timed":
        if (/\b(start|begin|go|ready|now)\b/.test(t)) return command("start");
        break;
      case "timing":
        if (/\b(stop|done|finished|finish|end)\b/.test(t)) return command("stop");
        break;
      case "awaiting_reps": {
        const n = parseReps(alts);
        if (n != null) return command("reps", n);
        if (/\b(none|nothing|skip)\b/.test(t)) return command("skipset");
        break;
      }
      case "resting":
        if (/\b(skip|next|ready|continue|go|done)\b/.test(t)) return command("skiprest");
        break;
    }
  }
  function command(act, arg) {
    switch (act) {
      case "exit": return exitVoice();
      case "repeat": return reAnnounce();
      case "start": if (voice.state === "ready_timed") beginHold(); return;
      case "stop": if (voice.state === "timing") endHold(); return;
      case "reps": if (voice.state === "awaiting_reps") recordReps(arg); return;
      case "logreps": {
        const el = $("#forge-voice-reps-input");
        const v = el ? parseInt(el.value, 10) : NaN;
        if (!isNaN(v) && voice.state === "awaiting_reps") recordReps(v);
        return;
      }
      case "skipset":
        if (voice.state === "awaiting_reps" || voice.state === "ready_timed") afterSet();
        return;
      case "skiprest": if (voice.state === "resting") skipRest(); return;
    }
  }

  // ── Flow ──
  function buildSteps(session) {
    const logs = session.set_logs || [];
    const totals = {};
    logs.forEach((s) => { totals[s.exercise_name] = (totals[s.exercise_name] || 0) + 1; });
    const seen = {};
    return logs.map((s) => {
      seen[s.exercise_name] = (seen[s.exercise_name] || 0) + 1;
      return {
        uuid: s.uuid,
        exerciseName: s.exercise_name,
        isTimed: !!s.is_timed,
        isAmrap: !!s.is_amrap,
        targetReps: s.expected_reps,
        expectedLoad: s.expected_load,
        restSeconds: s.rest_seconds || 0,
        cues: s.cues || "",
        setNumber: seen[s.exercise_name],
        totalSets: totals[s.exercise_name],
      };
    });
  }

  async function announceStep(i) {
    if (!voice.active) return; // a late .then() after exit must not revive the UI
    voice.idx = i;
    if (i >= voice.steps.length) return finishVoice();
    const s = voice.steps[i];
    voice.state = s.isTimed ? "ready_timed" : "awaiting_reps";
    setProgress(`Set ${s.setNumber} of ${s.totalSets}${s.isAmrap && !s.isTimed ? " · AMRAP" : ""}`);
    setExercise(s.exerciseName);
    const unit = s.isTimed ? "seconds" : "reps";
    const loadStr = s.expectedLoad != null ? ` at ${s.expectedLoad}` : "";
    setTarget(
      s.isTimed
        ? (s.isAmrap ? "Hold as long as you can" : `Target hold ${s.targetReps} seconds`)
        : s.isAmrap
        ? `As many reps as possible (target ${s.targetReps}+)`
        : `Target ${s.targetReps}${loadStr} ${unit}`
    );

    const startHint = micCue("Say start when you're ready.", "Tap start when you're ready.");
    const repsHint = micCue("Say your reps when you're done.", "Tap your reps when you're done.");
    let say = `${s.exerciseName}. Set ${s.setNumber} of ${s.totalSets}. `;
    if (s.isTimed) {
      say += s.isAmrap
        ? `Hold as long as you can. ${startHint}`
        : `Target hold, ${s.targetReps} seconds. ${startHint}`;
    } else if (s.isAmrap) {
      say += `As many reps as you can. ${repsHint}`;
    } else {
      say += `Target ${s.targetReps} reps${loadStr}. ${repsHint}`;
    }
    if (s.setNumber === 1 && s.cues) say += ` ${s.cues}`;
    voice.lastAnnounce = say;

    if (s.isTimed) {
      setInstruction(micCue("Say “start” to begin the hold.", "Tap Start to begin the hold."));
      setControls([
        { label: "▶ Start", act: "start" },
        { label: "Skip", act: "skipset" },
        { label: "Exit", act: "exit" },
      ]);
    } else {
      setInstruction(micCue("Do your set, then say your reps (a number).", "Do your set, then tap your reps below."));
      setRepsControls();
    }
    await speak(say);
    startListening();
  }

  function reAnnounce() {
    const txt =
      voice.state === "resting" ? "Still resting. Say skip to go now."
      : voice.state === "timing" ? `Holding. ${voice.elapsed} seconds so far. Say stop when done.`
      : voice.lastAnnounce;
    if (txt) speak(txt).then(startListening);
  }

  // Re-label the current step's on-screen instruction for the active input mode
  // (used when the user flips the Voice input toggle mid-session).
  function refreshVoiceInstruction() {
    switch (voice.state) {
      case "ready_timed":   setInstruction(micCue("Say “start” to begin the hold.", "Tap Start to begin the hold.")); break;
      case "timing":        setInstruction(micCue("Holding… say “stop” when you're done.", "Holding… tap Stop when you're done.")); break;
      case "awaiting_reps": setInstruction(micCue("Do your set, then say your reps (a number).", "Do your set, then tap your reps below.")); break;
      case "resting":       setInstruction(micCue("Resting — say “skip” to go now.", "Resting — tap Skip to go now.")); break;
    }
  }

  function beginHold() {
    voice.state = "timing";
    voice.elapsed = 0;
    showTimer(true); updateTimer(0);
    setInstruction(micCue("Holding… say “stop” when you're done.", "Holding… tap Stop when you're done."));
    setControls([{ label: "■ Stop", act: "stop" }, { label: "Exit", act: "exit" }]);
    voice.swId = setInterval(() => { voice.elapsed++; updateTimer(voice.elapsed); }, 1000);
    speak("Go.").then(startListening);
  }
  function endHold() {
    if (voice.state !== "timing") return; // ignore a duplicate "stop"
    voice.state = "busy";                 // block re-entry during the spoken ack
    if (voice.swId) { clearInterval(voice.swId); voice.swId = null; }
    showTimer(false);
    const secs = voice.elapsed;
    recordValue(secs);
    speak(`Recorded ${secs} seconds.`).then(afterSet);
  }

  function recordReps(n) {
    if (voice.state !== "awaiting_reps") return; // ignore a duplicate number
    voice.state = "busy";                        // block re-entry during the ack
    recordValue(n);
    speak(`Logged ${n} ${n === 1 ? "rep" : "reps"}.`).then(afterSet);
  }
  function recordValue(v) {
    const step = voice.steps[voice.idx];
    voice.collected[step.uuid] = { actual_reps: v, actual_load: step.expectedLoad };
    // Mirror into the Today input so a manual Save still works if they exit.
    const inp = $(`.forge-actual[data-uuid="${step.uuid}"]`);
    if (inp) inp.value = v;
  }

  function afterSet() {
    if (!voice.active) return;
    const nextI = voice.idx + 1;
    if (nextI >= voice.steps.length) return finishVoice();
    const rest = voice.steps[voice.idx].restSeconds || 0;
    if (rest > 0) beginRest(rest, nextI);
    else announceStep(nextI);
  }
  function beginRest(total, nextI) {
    voice.state = "resting";
    let remaining = total;
    setProgress("Recover");
    setExercise("Rest");
    setTarget("");
    showTimer(true); updateTimer(remaining);
    setInstruction(micCue(`Rest ${fmtRest(total)} — say “skip” to go now.`, `Rest ${fmtRest(total)} — tap Skip to go now.`));
    setControls([{ label: "⏭ Skip rest", act: "skiprest" }, { label: "Exit", act: "exit" }]);
    voice.restId = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(voice.restId); voice.restId = null;
        showTimer(false);
        playChime(getChime());
        speak("Rest over.").then(() => announceStep(nextI));
        return;
      }
      updateTimer(remaining);
    }, 1000);
    speak(`Rest for ${total} seconds.`).then(startListening);
  }
  function skipRest() {
    if (voice.restId) { clearInterval(voice.restId); voice.restId = null; }
    showTimer(false);
    announceStep(voice.idx + 1);
  }

  async function finishVoice() {
    if (voice.state === "done") return; // guard against a double-advance double-POST
    voice.state = "done";
    stopListening();
    showTimer(false);
    setProgress("Done");
    setExercise("Workout complete");
    setTarget("");
    setControls([]);
    vEl("heard").textContent = "";
    const sets = voice.collected;
    if (!Object.keys(sets).length) {
      setInstruction("No sets logged.");
      await speak("No sets were logged. Exiting earphones mode.");
      return closeVoice();
    }
    setInstruction("Saving your session…");
    await speak("Workout complete. Saving your session.");
    try {
      const res = await api(`sessions/${currentSession.uuid}/log/`, { method: "POST", body: { sets } });
      closeVoice();
      $("#forge-log-session").hidden = true;
      $("#forge-earphones").hidden = true;
      const unlocks = (res.progression || []).filter((d) => d.unlock).map((d) => d.unlock);
      await showResults({ peer: res.peer || [], unlocks, title: "Session forged" });
    } catch (e) {
      notify("Couldn't save the session.");
      setInstruction("Couldn't save — your reps are still on the Today screen.");
      closeVoice();
    }
  }

  function exitVoice() {
    if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) {} }
    speak("Exiting earphones mode.");
    closeVoice();
    notify("Earphones mode closed. Your logged reps are on the Today screen — tap Save session to keep them.");
  }
  function closeVoice() {
    voice.active = false;
    stopListening();
    if (voice.swId) { clearInterval(voice.swId); voice.swId = null; }
    if (voice.restId) { clearInterval(voice.restId); voice.restId = null; }
    if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) {} }
    $("#forge-voice").hidden = true;
    document.body.classList.remove("forge-overlay-open");
  }

  async function startVoice() {
    if (!voiceSupported()) { notify("Earphones mode needs Chrome or Edge."); return; }
    if (!currentSession) { notify("Open a day first."); return; }
    const steps = buildSteps(currentSession);
    if (!steps.length) { notify("Nothing scheduled today."); return; }
    voice.steps = steps; voice.idx = 0; voice.collected = {}; voice.active = true;
    pickVoice();
    try { audioCtx().resume(); } catch (e) { /* gesture unlock */ }
    setProgress("🎧 Earphones mode");
    setExercise("Getting ready…");
    setTarget(""); setInstruction(""); setControls([]);
    vEl("heard").textContent = "";
    $("#forge-voice").hidden = false;
    document.body.classList.add("forge-overlay-open");
    await speak("Earphones mode on. Let's forge.");
    announceStep(0);
  }

  const earBtn = $("#forge-earphones");
  if (earBtn) earBtn.addEventListener("click", startVoice);
  // Discreet corner mic button: reflects + flips the voice-input preference.
  const micToggle = $("#forge-voice-mictoggle");
  function syncMicToggle() {
    if (!micToggle) return;
    const on = voiceInputEnabled();
    micToggle.classList.toggle("is-muted", !on);
    micToggle.setAttribute("aria-pressed", on ? "true" : "false");
    const msg = on
      ? "Voice input on — tap to mute (stops the listening beep)"
      : "Voice input off — tap to enable hands-free commands (your browser beeps while listening)";
    micToggle.title = msg;
    micToggle.setAttribute("aria-label", msg);
  }
  if (micToggle) {
    syncMicToggle();
    micToggle.addEventListener("click", () => {
      const on = !voiceInputEnabled();
      setVoiceInputEnabled(on);
      syncMicToggle();
      if (!voice.active) return;
      // Apply immediately to the in-progress session.
      if (on) {
        if (["ready_timed", "timing", "awaiting_reps", "resting"].indexOf(voice.state) !== -1) startListening();
      } else {
        stopListening();
      }
      refreshVoiceInstruction();
    });
  }
  const exitBtn = $("#forge-voice-exit");
  if (exitBtn) exitBtn.addEventListener("click", () => command("exit"));
  const voiceControls = $("#forge-voice-controls");
  if (voiceControls) {
    voiceControls.addEventListener("click", (e) => {
      const b = e.target.closest(".forge-voice-cmd");
      if (b) command(b.dataset.act);
    });
    // Enter in the reps fallback input logs the value.
    voiceControls.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.id === "forge-voice-reps-input") {
        e.preventDefault(); command("logreps");
      }
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────────--
  loadEquipment();
})();
