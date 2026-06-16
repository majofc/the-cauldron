/* ═══════════════════════════════════════════════
   Arcano Design System — The Cauldron · The Forge
   Hammer-and-anvil forging animation.
     ForgeAnvil.scene(el, {loop})      → ambient inline widget
     ForgeAnvil.celebrate(opts)        → full-screen reveal
   Restrained: a few refined sparks, a mystical glow.
   Honours prefers-reduced-motion.
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  var STAGE_W = 320, STAGE_H = 230;
  var IMPACT_X = 150, IMPACT_Y = 120;
  var CYCLE = 1500;

  function sceneSVG() {
    return '' +
    '<svg class="anvil-svg" viewBox="0 0 ' + STAGE_W + ' ' + STAGE_H + '" fill="none" ' +
        'stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<line class="anvil-ground" x1="84" y1="200" x2="236" y2="200"/>' +
      '<g class="anvil-body">' +
        '<path d="M96 128 L112 120 L210 120 L210 134 L176 134 L170 148 L188 178 L132 178 L150 148 L112 134 Z"/>' +
        '<line x1="112" y1="120" x2="112" y2="134" opacity="0.4"/>' +
        '<line x1="176" y1="134" x2="112" y2="134" opacity="0.4"/>' +
      '</g>' +
      '<g class="anvil-hammer">' +
        '<line class="anvil-haft" x1="150" y1="98" x2="232" y2="40"/>' +
        '<g class="anvil-headwrap" transform="rotate(54.73 150 98)">' +
          '<rect class="anvil-head" x="127" y="88" width="46" height="20" rx="5"/>' +
        '</g>' +
      '</g>' +
    '</svg>';
  }

  function fxLayer() {
    var sparks = '';
    var defs = [
      [-72, 64], [-50, 80], [-30, 58], [-12, 86],
      [16, 60], [34, 80], [54, 54], [78, 70]
    ];
    for (var i = 0; i < defs.length; i++) {
      sparks += '<span class="anvil-spark" style="--a:' + defs[i][0] + 'deg;--d:' +
        defs[i][1] + 'px;--delay:' + (i % 3) * 16 + 'ms"></span>';
    }
    var embers = '<span class="anvil-ember" style="--ex:-10px"></span>' +
                 '<span class="anvil-ember" style="--ex:14px;--edelay:120ms"></span>';
    return '<span class="anvil-fx" style="left:' + IMPACT_X + 'px;top:' + IMPACT_Y + 'px">' +
      '<span class="anvil-flash"></span>' +
      '<span class="anvil-ring"></span>' +
      sparks + embers +
      '</span>';
  }

  function buildStage(extraCls) {
    var stage = document.createElement('div');
    stage.className = 'anvil-stage' + (extraCls ? ' ' + extraCls : '');
    stage.style.setProperty('--cycle', CYCLE + 'ms');
    stage.innerHTML =
      '<span class="anvil-halo"></span>' +
      sceneSVG() +
      fxLayer();
    return stage;
  }

  function scene(el, opts) {
    opts = opts || {};
    var stage = buildStage('is-loop');
    el.appendChild(stage);
    return stage;
  }

  /* Full-screen celebration. Uses .forge-anvil-overlay (not .forge-overlay)
     to avoid colliding with the forge app's existing overlay classes. */
  function celebrate(opts) {
    opts = opts || {};
    var strikes = opts.strikes || 2;
    var reduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var overlay = document.createElement('div');
    overlay.className = 'forge-anvil-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-label', opts.title || 'Day complete');

    var card = document.createElement('div');
    card.className = 'forge-anvil-card';
    card.innerHTML =
      '<div class="corner-tl"></div><div class="corner-tr"></div>' +
      '<div class="corner-bl"></div><div class="corner-br"></div>' +
      '<p class="forge-anvil-eyebrow">' + (opts.eyebrow || 'The day is done') + '</p>';

    var stageHost = document.createElement('div');
    stageHost.className = 'forge-anvil-stage-host';
    var stage = buildStage(reduced ? 'is-reduced' : 'is-burst');
    if (!reduced) stage.style.setProperty('--strikes', strikes);
    stageHost.appendChild(stage);
    card.appendChild(stageHost);

    var verdict = document.createElement('div');
    verdict.className = 'forge-anvil-verdict';
    verdict.innerHTML =
      '<h3 class="forge-anvil-title">' + (opts.title || 'Forged.') + '</h3>' +
      '<p class="forge-anvil-sub">' + (opts.sub ||
        'Every rep on today’s list is struck and tempered. Rest — the anvil keeps its heat.') + '</p>';
    card.appendChild(verdict);

    if (opts.content) {
      card.appendChild(opts.content);
    }

    var actions = document.createElement('div');
    actions.className = 'forge-anvil-actions';
    var done = document.createElement('button');
    done.type = 'button';
    done.className = 'btn-cauldron btn-cauldron--primary';
    done.textContent = opts.cta || 'Bank the day';
    actions.appendChild(done);
    card.appendChild(actions);

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    function close() {
      overlay.classList.add('is-closing');
      setTimeout(function () { overlay.remove(); }, 360);
    }
    done.addEventListener('click', function () { close(); if (opts.onDone) opts.onDone(); });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    document.addEventListener('keydown', function esc(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); }
    });

    requestAnimationFrame(function () { overlay.classList.add('is-in'); });

    var revealAt = reduced ? 260 : CYCLE * strikes * 0.62;
    setTimeout(function () {
      stage.classList.add('is-settled');
      card.classList.add('is-revealed');
    }, revealAt);

    setTimeout(function () { done.focus(); }, revealAt + 260);
    return { overlay: overlay, close: close, done: done };
  }

  window.ForgeAnvil = { scene: scene, celebrate: celebrate, CYCLE: CYCLE };
}());
