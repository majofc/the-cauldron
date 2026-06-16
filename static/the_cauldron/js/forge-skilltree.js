/* ═══════════════════════════════════════════════
   Arcano Design System — The Cauldron · The Forge
   Skill tree — six movement-pattern ladders rendered
   as an interactive videogame progression map.
   Depends on ForgeIcons (forge-icons.js).

   Public landing page — the catalog is a static mirror
   of seed_forge.py LADDERS. Keep in sync when catalog
   changes. NOT fetched from the login-gated API.
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  var PATTERNS = [
    {
      id: 'hpush', name: 'Horizontal Push', icon: 'pushup',
      rungs: [
        { icon: 'wallpush',    name: 'Wall Push-up',    state: 'done',
          cue: 'Stand an arm’s length from a wall, body in one line. Bend the elbows to bring your chest near, then press away.' },
        { icon: 'inclinepush', name: 'Incline Push-up', state: 'done',
          cue: 'Hands on a raised edge — the higher the surface, the lighter the load. Lower the surface as you get stronger.' },
        { icon: 'kneepush',    name: 'Knee Push-up',    state: 'current',
          cue: 'From the knees, keep a straight line hip-to-head. A clean half-step before the full push-up.',
          loaded: { icon: 'band', name: 'Banded Knee Push-up', equip: 'Resistance band',
            cue: 'Loop a band across the upper back for accommodating resistance — hardest at lockout.' } },
        { icon: 'pushup',      name: 'Full Push-up',    state: 'next',
          cue: 'Plank-tight, elbows tracking ~45°, chest to the floor and back. The benchmark of horizontal pressing.',
          loaded: { icon: 'plate', name: 'Weighted Push-up', equip: 'Plate / pack',
            cue: 'Rest a plate or loaded pack on the upper back once bodyweight reps feel easy.' } },
        { icon: 'diamondpush', name: 'Diamond Push-up', state: 'locked',
          cue: 'Hands together under the chest forming a diamond. Shifts the load onto the triceps and inner chest.' },
        { icon: 'archerpush',  name: 'Archer Push-up',  state: 'locked', master: true,
          cue: 'Shift your weight over one arm while the other stays straight. The doorway to the one-arm push-up.' }
      ]
    },
    {
      id: 'vpull', name: 'Vertical Pull', icon: 'pullup',
      rungs: [
        { icon: 'row',        name: 'Australian Row',  state: 'done',
          cue: 'Hang under a waist-high bar, body straight, and row your chest to it. Walk the feet forward to ease it.' },
        { icon: 'negpull',    name: 'Negative Pull-up', state: 'current',
          cue: 'Jump or step to the top, then lower yourself as slowly as you can. Builds the strength to pull up.',
          loaded: { icon: 'band', name: 'Banded Pull-up', equip: 'Resistance band',
            cue: 'Stand in a band looped over the bar — it gives the most help at the bottom where you’re weakest.' } },
        { icon: 'pullup',     name: 'Pull-up',          state: 'next',
          cue: 'Dead hang to chin-over-bar, no kip. The gold standard of vertical pulling strength.',
          loaded: { icon: 'plate', name: 'Weighted Pull-up', equip: 'Belt / pack',
            cue: 'Add load on a dip belt once you clear clean reps. The fastest route to raw pulling power.' } },
        { icon: 'archerpull', name: 'Archer Pull-up',   state: 'locked', master: true,
          cue: 'Pull to one hand while the other arm stays straight along the bar. The bridge to a one-arm pull-up.' }
      ]
    },
    {
      id: 'vpush', name: 'Vertical Push', icon: 'pikepush',
      rungs: [
        { icon: 'pikepush', name: 'Incline Pike Push-up',    state: 'done',
          cue: 'Feet raised, hips high in a pike. Press the crown of your head toward the floor and back up.' },
        { icon: 'deeppike', name: 'Pike Push-up',             state: 'current',
          cue: 'Floor pike, hips stacked over the shoulders. Deepen the range until the head taps the ground.' },
        { icon: 'wallhspu', name: 'Wall Handstand Push-up',   state: 'next',
          cue: 'Kick to the wall and press. Chest-to-wall is the honest version; lower under control.' },
        { icon: 'hspu',     name: 'Handstand Push-up',        state: 'locked', master: true,
          cue: 'Free-balanced, full range. Overhead pressing strength meets total-body control.' }
      ]
    },
    {
      id: 'lower', name: 'Lower Unilateral', icon: 'pistol',
      rungs: [
        { icon: 'assistsplit', name: 'Assisted Split Squat',  state: 'done',
          cue: 'Split stance, one hand on a rail for balance. Drop the back knee toward the floor and drive up.' },
        { icon: 'splitsquat',  name: 'Split Squat',           state: 'current',
          cue: 'Unsupported split stance. Vertical torso, front heel planted, full depth each rep.',
          loaded: { icon: 'dumbbell', name: 'Goblet Split Squat', equip: 'Dumbbell / KB',
            cue: 'Hold a weight at the chest. The cleanest way to overload the legs without a barbell.' } },
        { icon: 'bulgarian',   name: 'Bulgarian Split Squat', state: 'next',
          cue: 'Rear foot on a bench. Brutal single-leg range and balance — the legs’ true test.',
          loaded: { icon: 'dumbbell', name: 'Loaded Bulgarian', equip: 'Dumbbells',
            cue: 'A dumbbell in each hand. Among the highest-yield lower-body movements there is.' } },
        { icon: 'pistol',      name: 'Pistol Squat',          state: 'locked', master: true,
          cue: 'Full one-leg squat, other leg extended. Strength, mobility and control in a single feat.' }
      ]
    },
    {
      id: 'core', name: 'Core · Anti-Extension', icon: 'plank',
      rungs: [
        { icon: 'kneeplank', name: 'Knee Plank',       state: 'done',
          cue: 'Forearms down, knees on the floor, hips tucked. Brace as if bracing for a punch.' },
        { icon: 'plank',     name: 'Forearm Plank',    state: 'current',
          cue: 'Full plank, straight line heel-to-head. Squeeze glutes and ribs-down — no sagging hips.' },
        { icon: 'rkcplank',  name: 'RKC Plank',        state: 'next',
          cue: 'A plank turned to maximum tension — everything clenched. Ten brutal seconds beats a lazy minute.' },
        { icon: 'hollow',    name: 'Hollow Body Hold', state: 'locked', master: true,
          cue: 'On your back, lower back pressed flat, arms and legs lifted. The gymnastic core foundation.' }
      ]
    },
    {
      id: 'hinge', name: 'Hinge', icon: 'glutebridge',
      rungs: [
        { icon: 'glutebridge', name: 'Glute Bridge',            state: 'done',
          cue: 'On your back, knees bent, drive the hips up and squeeze. Learn to fire the glutes, not the back.' },
        { icon: 'slbridge',    name: 'Single-leg Glute Bridge',  state: 'current',
          cue: 'One leg extended, bridge with the other. Exposes and fixes side-to-side imbalances.' },
        { icon: 'hipthrust',   name: 'Hip Thrust',              state: 'next',
          cue: 'Shoulders on a bench for a deeper range. The most direct glute builder you can do.',
          loaded: { icon: 'barbell', name: 'Barbell Hip Thrust', equip: 'Barbell',
            cue: 'Load the lap across the hips. Scales near-endlessly — the answer to overloading the hips.' } },
        { icon: 'nordic',      name: 'Nordic Curl',             state: 'locked', master: true,
          cue: 'Ankles anchored, lower your straight body under control. The ultimate hamstring strength feat.' }
      ]
    }
  ];

  var I = window.ForgeIcons;

  function buildNode(rung, pi, ri, isBranch) {
    var node = document.createElement('button');
    node.type = 'button';
    node.className = 'tnode' +
      ' is-' + (isBranch ? 'gear' : rung.state) +
      (rung.master ? ' is-master' : '') +
      (isBranch ? ' is-branch' : '');
    node.setAttribute('data-p', pi);
    node.setAttribute('data-r', ri);
    if (isBranch) node.setAttribute('data-branch', '1');

    var tile = document.createElement('span');
    tile.className = 'tnode-tile';
    if (I) tile.appendChild(I.el(rung.icon));
    node.appendChild(tile);

    var badge = document.createElement('span');
    badge.className = 'tnode-badge';
    if (isBranch) badge.textContent = '';
    else if (rung.state === 'done') badge.innerHTML = '❆';
    else if (rung.state === 'locked') badge.innerHTML = lockSVG();
    else if (rung.master) badge.innerHTML = '★';
    node.appendChild(badge);

    if (rung.master && !isBranch) {
      var crown = document.createElement('span');
      crown.className = 'tnode-master-ring';
      node.appendChild(crown);
    }

    var tip = document.createElement('span');
    tip.className = 'tnode-tip';
    tip.textContent = rung.name;
    node.appendChild(tip);

    return node;
  }

  function lockSVG() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
      'stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="1.6"/>' +
      '<path d="M8 11 V8 a4 4 0 0 1 8 0 v3"/></svg>';
  }

  function buildLadder(p, pi) {
    var ladder = document.createElement('div');
    ladder.className = 'ladder';

    var head = document.createElement('div');
    head.className = 'ladder-head';
    var hi = document.createElement('span');
    hi.className = 'ladder-head-icon';
    if (I) hi.appendChild(I.el(p.icon));
    head.appendChild(hi);
    var hn = document.createElement('span');
    hn.className = 'ladder-head-name';
    hn.textContent = p.name;
    head.appendChild(hn);
    var play = document.createElement('button');
    play.type = 'button';
    play.className = 'ladder-play';
    play.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg><span>Evolve</span>';
    play.setAttribute('aria-label', 'Watch ' + p.name + ' evolve');
    head.appendChild(play);
    ladder.appendChild(head);

    var rail = document.createElement('div');
    rail.className = 'ladder-rail';

    p.rungs.forEach(function (rung, ri) {
      if (ri > 0) {
        var link = document.createElement('span');
        link.className = 'tlink' + (rung.state === 'locked' ? ' is-locked' : '');
        rail.appendChild(link);
      }
      var col = document.createElement('span');
      col.className = 'tcol';

      if (rung.loaded) {
        var bwrap = document.createElement('span');
        bwrap.className = 'tbranch';
        var blink = document.createElement('span');
        blink.className = 'tbranch-link';
        var bnode = buildNode(rung.loaded, pi, ri, true);
        bwrap.appendChild(bnode);
        bwrap.appendChild(blink);
        col.appendChild(bwrap);
      }

      col.appendChild(buildNode(rung, pi, ri, false));
      rail.appendChild(col);
    });

    ladder.appendChild(rail);
    play.addEventListener('click', function () { evolve(rail); });
    return ladder;
  }

  function showDetail(panel, p, rung, isBranch) {
    var statusText, statusCls;
    if (isBranch) { statusText = 'Loaded variant'; statusCls = 'gear'; }
    else if (rung.state === 'done') { statusText = 'Cleared'; statusCls = 'done'; }
    else if (rung.state === 'current') { statusText = 'Your rung'; statusCls = 'current'; }
    else if (rung.state === 'next') { statusText = 'Unlocked — ready'; statusCls = 'next'; }
    else { statusText = 'Locked'; statusCls = 'locked'; }

    panel.querySelector('.tdetail-glyph').innerHTML = '';
    if (I) panel.querySelector('.tdetail-glyph').appendChild(I.el(rung.icon, { sw: '2.2' }));
    panel.querySelector('.tdetail-pattern').textContent = p.name;
    panel.querySelector('.tdetail-name').textContent = rung.name;
    var pill = panel.querySelector('.tdetail-status');
    pill.textContent = statusText;
    pill.className = 'tdetail-status is-' + statusCls;
    panel.querySelector('.tdetail-cue').textContent = rung.cue;

    var equip = panel.querySelector('.tdetail-equip');
    if (isBranch && rung.equip) {
      equip.style.display = '';
      equip.querySelector('.tdetail-equip-val').textContent = rung.equip;
    } else {
      equip.style.display = 'none';
    }
    panel.classList.add('is-active');
  }

  function evolve(rail) {
    var nodes = rail.querySelectorAll('.tcol > .tnode');
    nodes.forEach(function (n, i) {
      setTimeout(function () {
        n.classList.add('is-evolving');
        setTimeout(function () { n.classList.remove('is-evolving'); }, 620);
      }, i * 240);
    });
  }

  function mount(root) {
    var layout = document.createElement('div');
    layout.className = 'tree-layout';

    var tree = document.createElement('div');
    tree.className = 'tree';
    PATTERNS.forEach(function (p, pi) { tree.appendChild(buildLadder(p, pi)); });

    var panel = document.createElement('aside');
    panel.className = 'tdetail';
    panel.innerHTML =
      '<div class="corner-tl"></div><div class="corner-tr"></div>' +
      '<div class="corner-bl"></div><div class="corner-br"></div>' +
      '<div class="tdetail-glyph"></div>' +
      '<p class="tdetail-pattern"></p>' +
      '<h4 class="tdetail-name"></h4>' +
      '<span class="tdetail-status"></span>' +
      '<p class="tdetail-cue"></p>' +
      '<p class="tdetail-equip"><span class="tdetail-equip-k">Requires</span> ' +
      '<span class="tdetail-equip-val"></span></p>' +
      '<p class="tdetail-hint">Hover or tap any node to read its cue.</p>';

    layout.appendChild(tree);
    layout.appendChild(panel);
    root.innerHTML = '';
    root.appendChild(layout);

    function resolve(node) {
      var pi = +node.getAttribute('data-p');
      var ri = +node.getAttribute('data-r');
      var isBranch = node.hasAttribute('data-branch');
      var p = PATTERNS[pi];
      var rung = isBranch ? p.rungs[ri].loaded : p.rungs[ri];
      return { p: p, rung: rung, isBranch: isBranch };
    }

    tree.addEventListener('mouseover', function (e) {
      var node = e.target.closest('.tnode');
      if (!node || !tree.contains(node)) return;
      var r = resolve(node);
      showDetail(panel, r.p, r.rung, r.isBranch);
    });
    tree.addEventListener('click', function (e) {
      var node = e.target.closest('.tnode');
      if (!node) return;
      tree.querySelectorAll('.tnode.is-selected').forEach(function (n) { n.classList.remove('is-selected'); });
      node.classList.add('is-selected');
      var r = resolve(node);
      showDetail(panel, r.p, r.rung, r.isBranch);
    });
    tree.addEventListener('focusin', function (e) {
      var node = e.target.closest('.tnode');
      if (!node) return;
      var r = resolve(node);
      showDetail(panel, r.p, r.rung, r.isBranch);
    });

    for (var pi = 0; pi < PATTERNS.length; pi++) {
      var cur = PATTERNS[pi].rungs.filter(function (x) { return x.state === 'current'; })[0];
      if (cur) { showDetail(panel, PATTERNS[pi], cur, false); break; }
    }
  }

  window.ForgeSkillTree = { mount: mount, patterns: PATTERNS };

  document.addEventListener('DOMContentLoaded', function () {
    /* Support both IDs: fshow-tree (landing) and forge-skilltree (other) */
    var root = document.getElementById('fshow-tree') || document.getElementById('forge-skilltree');
    if (root) mount(root);
  });
}());
