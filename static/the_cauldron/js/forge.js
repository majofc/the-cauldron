/* The Forge — adaptive calisthenics front-end.
   Talks to /cauldron/api/ (DRF). No framework; vanilla fetch + DOM. */
(function () {
  "use strict";

  const API = window.FORGE_API; // e.g. /cauldron/api/
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const LOADABLE = ["dumbbells", "bands", "barbell"];

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
    if (name === "exercises") loadCatalog();
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
    const approx = sc.approximate
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
        rows +=
          `<div class="forge-cat-row${blockedCls}">` +
          `<div class="forge-cat-meta">` +
          `<span class="forge-cat-name">${ex.name}</span>` +
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

  // ── Init ─────────────────────────────────────────────────────────────────--
  loadEquipment();
})();
