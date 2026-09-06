/* CauldronLoader — shared loader for The Cauldron (page transitions + in-page).
   Builds a paprika cauldron with spinning rings, orbiting embers and a stir.
   Exposes window.CauldronLoader.{showPage, hidePage, build}. */
(function () {
  var PAPRIKA = '#B23A1F';
  var PAPRIKA_L = '#D86848';
  var CREAM_DIM = 'rgba(240,232,220,0.65)';
  var _pageLoader = null;

  if (!document.getElementById('cauld-loader-kf')) {
    var s = document.createElement('style');
    s.id = 'cauld-loader-kf';
    s.textContent = [
      '@keyframes cauld-spinCW  { to { transform: rotate(360deg); } }',
      '@keyframes cauld-spinCCW { to { transform: rotate(-360deg); } }',
      '@keyframes cauld-pulse   { 0%,100%{opacity:.5;transform:scale(.96)} 50%{opacity:1;transform:scale(1.04)} }',
      '@keyframes cauld-blink   { 0%,100%{opacity:.25;transform:scale(.85)} 50%{opacity:1;transform:scale(1.1)} }',
      '@keyframes cauld-ember   { 0%,100%{opacity:.35;transform:scale(.85)} 50%{opacity:1;transform:scale(1.15)} }',
    ].join('');
    document.head.appendChild(s);
  }

  function buildCauldronLoader(caption, size) {
    var SIZE = size || 180;
    var svgNS = 'http://www.w3.org/2000/svg';
    var wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:20px;';

    var box = document.createElement('div');
    box.style.cssText = 'position:relative;width:' + SIZE + 'px;height:' + SIZE + 'px;';

    var halo = document.createElement('div');
    halo.style.cssText = 'position:absolute;inset:-20%;background:radial-gradient(ellipse,' + PAPRIKA + '33 0%,transparent 60%);animation:cauld-pulse 1.8s ease-in-out infinite;pointer-events:none;';
    box.appendChild(halo);

    [{inset:'6%',dur:4,dir:'CW'},{inset:'18%',dur:6,dir:'CCW'},{inset:'30%',dur:8,dir:'CW',opacity:.7}].forEach(function (r) {
      var ring = document.createElement('div');
      ring.style.cssText = 'position:absolute;inset:' + r.inset + ';border:1px solid ' + PAPRIKA + ';border-radius:50%;border-top-color:transparent;border-right-color:transparent;animation:cauld-spin' + r.dir + ' ' + r.dur + 's linear infinite;' + (r.opacity ? 'opacity:' + r.opacity + ';' : '');
      box.appendChild(ring);
    });

    var orbitCW = document.createElement('div');
    orbitCW.style.cssText = 'position:absolute;inset:0;animation:cauld-spinCW 6s linear infinite;';
    [0,90,180,270].forEach(function (deg) {
      var arm = document.createElement('div');
      arm.style.cssText = 'position:absolute;inset:0;transform:rotate(' + deg + 'deg);';
      var dot = document.createElement('div');
      dot.style.cssText = 'position:absolute;top:3%;left:50%;transform:translateX(-50%);width:6px;height:6px;border-radius:50%;background:' + PAPRIKA + ';box-shadow:0 0 8px ' + PAPRIKA + ',0 0 16px ' + PAPRIKA + '88;';
      arm.appendChild(dot);
      orbitCW.appendChild(arm);
    });
    box.appendChild(orbitCW);

    var orbitCCW = document.createElement('div');
    orbitCCW.style.cssText = 'position:absolute;inset:12%;animation:cauld-spinCCW 10s linear infinite;';
    [45,225].forEach(function (deg) {
      var arm = document.createElement('div');
      arm.style.cssText = 'position:absolute;inset:0;transform:rotate(' + deg + 'deg);';
      var dot = document.createElement('div');
      dot.style.cssText = 'position:absolute;top:0%;left:50%;transform:translateX(-50%);width:4px;height:4px;border-radius:50%;background:' + PAPRIKA_L + ';opacity:.6;box-shadow:0 0 6px ' + PAPRIKA + ';';
      arm.appendChild(dot);
      orbitCCW.appendChild(arm);
    });
    box.appendChild(orbitCCW);

    var center = document.createElement('div');
    center.style.cssText = 'position:absolute;inset:34%;color:' + PAPRIKA + ';display:flex;align-items:center;justify-content:center;';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 64 64');
    svg.style.cssText = 'width:100%;height:100%;';
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    [['line',{x1:'12',y1:'24',x2:'52',y2:'24'}],
     ['path',{d:'M14 24 Q14 48 32 48 Q50 48 50 24'}],
     ['path',{d:'M10 22 Q10 18 14 19'}],
     ['path',{d:'M54 22 Q54 18 50 19'}],
     ['path',{d:'M20 48 L18 54'}],
     ['path',{d:'M32 48 L32 54'}],
     ['path',{d:'M44 48 L46 54'}],
    ].forEach(function (pair) {
      var el = document.createElementNS(svgNS, pair[0]);
      Object.keys(pair[1]).forEach(function (k) { el.setAttribute(k, pair[1][k]); });
      svg.appendChild(el);
    });
    center.appendChild(svg);
    box.appendChild(center);
    wrap.appendChild(box);

    if (caption) {
      var cap = document.createElement('p');
      cap.style.cssText = "font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:1rem;color:" + CREAM_DIM + ";letter-spacing:0.02em;margin:0;";
      cap.textContent = caption;
      wrap.appendChild(cap);
    }

    var dots = document.createElement('div');
    dots.style.cssText = 'display:flex;gap:8px;';
    [0,1,2].forEach(function (i) {
      var d = document.createElement('div');
      d.style.cssText = 'width:5px;height:5px;border-radius:50%;background:' + PAPRIKA + ';animation:cauld-blink 1.2s ease-in-out infinite;animation-delay:' + (i*0.18) + 's;';
      dots.appendChild(d);
    });
    wrap.appendChild(dots);
    return wrap;
  }

  var CAPTIONS = ['Tending the flame…', 'Stirring the pot…', 'The cauldron deliberates…', 'Consulting ancient recipes…'];
  function randomCaption() { return CAPTIONS[Math.floor(Math.random() * CAPTIONS.length)]; }

  window.CauldronLoader = {
    build: buildCauldronLoader,
    randomCaption: randomCaption,
    showPage: function (caption) {
      if (_pageLoader) return;
      _pageLoader = document.createElement('div');
      _pageLoader.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(12,9,7,0.92);display:flex;align-items:center;justify-content:center;';
      _pageLoader.appendChild(buildCauldronLoader(caption || randomCaption()));
      document.body.appendChild(_pageLoader);
    },
    hidePage: function () {
      if (_pageLoader) { _pageLoader.remove(); _pageLoader = null; }
    }
  };

  /* Take over Arcano's page-transition loader on Cauldron pages. */
  if (window.ArcanoLoader) {
    window.ArcanoLoader.showPage = window.CauldronLoader.showPage;
    window.ArcanoLoader.hidePage = window.CauldronLoader.hidePage;
  }

  function isNavLink(a) {
    var href = a.getAttribute('href');
    if (!href || href === '#' || href.charAt(0) === '#') return false;
    if (href.indexOf('mailto:') === 0 || href.indexOf('javascript:') === 0) return false;
    if (a.target === '_blank' || a.target === '_top') return false;
    if (a.hasAttribute('data-no-loader')) return false;
    return true;
  }

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.shiftKey || e.metaKey || e.altKey) return;
    var a = e.target.closest('a[href]');
    if (!a || !isNavLink(a)) return;
    window.CauldronLoader.showPage();
  });

  /* Full-page form posts (e.g. the login form) get the loader too — mirrors
     Arcano's nav wiring, which this file replaces on Cauldron pages. */
  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented || e.target.hasAttribute('data-no-loader')) return;
    window.CauldronLoader.showPage();
  });

  window.addEventListener('pageshow', function () { window.CauldronLoader.hidePage(); });
}());
