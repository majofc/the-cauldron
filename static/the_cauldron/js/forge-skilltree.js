/* ═══════════════════════════════════════════════
   The Forge — landing skill tree
   Renders the six movement-pattern ladders as an
   interactive, videogame-style skill tree.

   This is PUBLIC marketing content on an unauthenticated
   landing page, so the catalog is embedded here as a static
   mirror of the seeded data (the_cauldron/management/commands/
   seed_forge.py). It is intentionally NOT fetched from the
   login-gated /cauldron/api/catalog/ endpoint. Keep this list
   in sync with seed_forge.py LADDERS when the catalog changes.
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  // Small line-SVG marks per pattern (viewBox 0 0 32 32, currentColor stroke).
  var ICONS = {
    horizontal_push: '<path d="M4 16h13"/><path d="M17 16l-5-5"/><path d="M17 16l-5 5"/><rect x="22" y="8" width="6" height="16" rx="1"/>',
    vertical_pull:   '<path d="M6 6h20"/><path d="M16 6v15"/><path d="M16 21l-5-5"/><path d="M16 21l5-5"/>',
    vertical_push:   '<path d="M16 26V11"/><path d="M16 11l-5 5"/><path d="M16 11l5 5"/><path d="M8 6h16"/>',
    lower_unilateral:'<path d="M12 5v9l-4 13"/><path d="M12 14l5 4-2 9"/><circle cx="12" cy="5" r="2"/>',
    core_anti_extension:'<path d="M4 20h24"/><path d="M7 20c4-9 14-9 18 0"/><circle cx="16" cy="9" r="2"/>',
    hinge:           '<path d="M5 11v6"/><path d="M27 11v6"/><path d="M5 14h22"/><path d="M9 14V9"/><path d="M23 14V9"/>'
  };

  // Each pattern: bodyweight "spine" (climb-the-ladder) + "gear" branch
  // (needs equipment / load progression). reps shown as the seeded rep range.
  var PATTERNS = [
    {
      key: 'horizontal_push', name: 'Horizontal Push', muscles: 'Chest · Front Delts · Triceps',
      spine: [
        { name: 'Wall Push-up',     reps: '8–15 reps', cue: 'Body in a line; full lockout.' },
        { name: 'Incline Push-up',  reps: '8–15 reps', cue: 'Hands elevated; brace core.' },
        { name: 'Knee Push-up',     reps: '6–12 reps', cue: 'Hips down; chest to floor.' },
        { name: 'Push-up',          reps: '5–12 reps', cue: 'Elbows ~45°; full range.' },
        { name: 'Diamond Push-up',  reps: '5–10 reps', cue: 'Hands together; tuck elbows.' },
        { name: 'Archer Push-up',   reps: '4–8 reps',  cue: 'Shift to one arm; control.' }
      ],
      gear: [
        { name: 'Dumbbell Bench Press', reps: '6–12 reps', gear: 'Dumbbells', cue: 'Drive through chest; full range.' },
        { name: 'Barbell Bench Press',  reps: '5–10 reps', gear: 'Barbell',   cue: 'Bar to chest; tight back.' }
      ]
    },
    {
      key: 'vertical_pull', name: 'Vertical Pull', muscles: 'Lats · Biceps · Mid-back',
      spine: [
        { name: 'Australian Row',  reps: '8–15 reps', cue: 'Body straight; pull chest to bar.' },
        { name: 'Negative Pull-up',reps: '3–6 reps',  cue: '5s lower; control the descent.' },
        { name: 'Pull-up',         reps: '4–10 reps', cue: 'Dead hang; chin over bar.' },
        { name: 'Archer Pull-up',  reps: '3–6 reps',  cue: 'Pull to one side; other arm straight.' }
      ],
      gear: [
        { name: 'Band-Assisted Row',     reps: '8–15 reps', gear: 'Bands', cue: 'Squeeze shoulder blades.' },
        { name: 'Rowing Machine',        reps: '10–20 reps', gear: 'Rower', cue: 'Legs–hips–arms sequence.' },
        { name: 'Dumbbell Row',          reps: '6–12 reps', gear: 'Dumbbells', cue: 'Flat back; row to hip.' },
        { name: 'Band-Assisted Pull-up', reps: '5–10 reps', gear: 'Bar + Bands', cue: 'Full hang to chin over bar.' }
      ]
    },
    {
      key: 'vertical_push', name: 'Vertical Push', muscles: 'Shoulders · Triceps',
      spine: [
        { name: 'Incline Pike Push-up',      reps: '6–12 reps', cue: 'Hips high; head between hands.' },
        { name: 'Pike Push-up',              reps: '5–12 reps', cue: 'Pike position; crown to floor.' },
        { name: 'Wall Handstand Hold',       reps: '15–45s hold', cue: 'Hollow body; push tall.' },
        { name: 'Assisted Handstand Push-up',reps: '3–8 reps',  cue: 'Partial range; control.' }
      ],
      gear: [
        { name: 'Dumbbell Shoulder Press', reps: '6–12 reps', gear: 'Dumbbells', cue: 'Press overhead; ribs down.' },
        { name: 'Barbell Overhead Press',  reps: '5–10 reps', gear: 'Barbell',   cue: 'Bar to overhead; glutes tight.' }
      ]
    },
    {
      key: 'lower_unilateral', name: 'Lower Body · Unilateral', muscles: 'Quads · Glutes',
      spine: [
        { name: 'Assisted Split Squat',  reps: '8–15 reps', cue: 'Hold support; knee tracks toe.' },
        { name: 'Split Squat',           reps: '8–15 reps', cue: 'Tall torso; back knee down.' },
        { name: 'Bulgarian Split Squat', reps: '6–12 reps', cue: 'Rear foot elevated; sink straight.' },
        { name: 'Assisted Pistol Squat', reps: '4–8 reps',  cue: 'Hold support; full depth.' },
        { name: 'Pistol Squat',          reps: '3–8 reps',  cue: 'One leg; controlled descent.' }
      ],
      gear: [
        { name: 'Goblet Squat',                  reps: '6–12 reps', gear: 'Dumbbell / KB', cue: 'Weight at chest; sit between hips.' },
        { name: 'Dumbbell Bulgarian Split Squat',reps: '6–12 reps', gear: 'Dumbbells',     cue: 'Loaded; rear foot elevated.' },
        { name: 'Barbell Back Squat',            reps: '5–10 reps', gear: 'Barbell',        cue: 'Bar on traps; hit depth.' }
      ]
    },
    {
      key: 'core_anti_extension', name: 'Core · Anti-Extension', muscles: 'Abs · Deep Core',
      spine: [
        { name: 'Knee Plank',       reps: '15–45s hold', cue: 'Straight line knees to head.' },
        { name: 'Plank',            reps: '20–60s hold', cue: 'Glutes + abs tight; no sag.' },
        { name: 'Extended Plank',   reps: '15–45s hold', cue: 'Hands forward of shoulders.' },
        { name: 'RKC Plank',        reps: '10–30s hold', cue: 'Max tension; posterior tilt.' },
        { name: 'Hollow Body Hold', reps: '15–45s hold', cue: 'Low back pressed to floor.' }
      ],
      gear: [
        { name: 'Ab Wheel / Band Rollout', reps: '6–12 reps', gear: 'Wheel / Bands', cue: "Brace hard; don't arch." }
      ]
    },
    {
      key: 'hinge', name: 'Hinge · Posterior Chain', muscles: 'Hamstrings · Glutes · Low Back',
      spine: [
        { name: 'Glute Bridge',           reps: '12–20 reps', cue: 'Drive hips; squeeze glutes.' },
        { name: 'Single-Leg Glute Bridge',reps: '8–15 reps',  cue: 'One leg; level hips.' },
        { name: 'Assisted Nordic Curl',   reps: '5–10 reps',  cue: 'Control the lower; use hands.' },
        { name: 'Nordic Curl',            reps: '3–8 reps',   cue: 'Hamstrings lower the body slowly.' }
      ],
      gear: [
        { name: 'Dumbbell Romanian Deadlift', reps: '8–12 reps', gear: 'Dumbbells', cue: 'Soft knees; hinge from hips.' },
        { name: 'Barbell Romanian Deadlift',  reps: '6–10 reps', gear: 'Barbell',   cue: 'Bar close; flat back.' },
        { name: 'Kettlebell Swing',           reps: '12–20 reps', gear: 'Kettlebell', cue: 'Hip snap; not a squat.' }
      ]
    }
  ];

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var prefersReduced = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function makeIcon(key) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'fshow-track-icon');
    svg.setAttribute('viewBox', '0 0 32 32');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = ICONS[key] || '';
    return svg;
  }

  function makeNode(item, opts) {
    opts = opts || {};
    var node = el('div', 'fshow-node' + (opts.master ? ' fshow-node--master' : '') + (opts.gear ? ' fshow-node--gear' : ''));
    node.setAttribute('tabindex', '0');
    node.setAttribute('role', 'button');
    node.setAttribute('aria-label', item.name + ' — ' + item.reps + (item.gear ? ' (' + item.gear + ')' : ''));

    var orb = el('div', 'fshow-orb');
    orb.setAttribute('aria-hidden', 'true');

    var body = el('div', 'fshow-node-body');
    var name = el('div', 'fshow-node-name');
    name.appendChild(document.createTextNode(item.name));
    if (item.gear) {
      var tag = el('span', 'fshow-node-gear-tag');
      tag.appendChild(document.createTextNode(item.gear));
      name.appendChild(tag);
    }
    var meta = el('div', 'fshow-node-meta');
    meta.appendChild(document.createTextNode(item.reps + (opts.master ? ' · mastery' : '')));
    var cue = el('div', 'fshow-cue');
    cue.appendChild(document.createTextNode('“' + item.cue + '”'));

    body.appendChild(name);
    body.appendChild(meta);
    body.appendChild(cue);
    node.appendChild(orb);
    node.appendChild(body);

    // Touch / keyboard: toggle the cue open.
    node.addEventListener('click', function () { node.classList.toggle('is-active'); });
    node.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); node.classList.toggle('is-active'); }
    });
    return node;
  }

  function lightSequence(nodes) {
    // Animate the "evolution": light spine nodes one by one, easy → hard.
    if (prefersReduced) {
      nodes.forEach(function (n) { n.classList.add('is-lit'); });
      return;
    }
    nodes.forEach(function (n) { n.classList.remove('is-lit'); });
    var i = 0;
    (function step() {
      if (i >= nodes.length) {
        setTimeout(function () { nodes.forEach(function (n) { n.classList.remove('is-lit'); }); }, 700);
        return;
      }
      nodes[i].classList.add('is-lit');
      i++;
      setTimeout(step, 420);
    })();
  }

  function buildTrack(p) {
    var track = el('div', 'fshow-track');
    ['tl', 'tr', 'bl', 'br'].forEach(function (c) {
      var d = el('div', 'corner-' + c); d.setAttribute('aria-hidden', 'true'); track.appendChild(d);
    });

    var head = el('div', 'fshow-track-head');
    head.appendChild(makeIcon(p.key));
    var nm = el('div', 'fshow-track-name');
    nm.appendChild(document.createTextNode(p.name));
    head.appendChild(nm);
    track.appendChild(head);

    var muscles = el('div', 'fshow-track-muscles');
    muscles.appendChild(document.createTextNode(p.muscles));
    track.appendChild(muscles);

    // Play-evolution button.
    var play = el('button', 'fshow-play');
    play.setAttribute('type', 'button');
    play.setAttribute('aria-label', 'Play the ' + p.name + ' progression');
    play.setAttribute('title', 'Watch it evolve');
    play.appendChild(document.createTextNode('▶'));
    track.appendChild(play);

    // Bodyweight spine.
    var chain = el('div', 'fshow-chain');
    var spineNodes = [];
    p.spine.forEach(function (item, idx) {
      var master = idx === p.spine.length - 1;
      var node = makeNode(item, { master: master });
      spineNodes.push(node);
      chain.appendChild(node);
    });
    track.appendChild(chain);

    play.addEventListener('click', function () { lightSequence(spineNodes); });

    // Gear branch (equipment / load progression).
    if (p.gear && p.gear.length) {
      var label = el('div', 'fshow-branch-label');
      label.appendChild(document.createTextNode('With equipment'));
      track.appendChild(label);
      var gearChain = el('div', 'fshow-chain');
      p.gear.forEach(function (item) { gearChain.appendChild(makeNode(item, { gear: true })); });
      track.appendChild(gearChain);
    }
    return track;
  }

  function render() {
    var mount = document.getElementById('fshow-tree');
    if (!mount) return;
    var frag = document.createDocumentFragment();
    PATTERNS.forEach(function (p) { frag.appendChild(buildTrack(p)); });
    mount.appendChild(frag);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
}());
