/* GrimoireLoader — the Grimoire variant of CauldronLoader: an open book whose
   leaves turn inside a gilt ring. Same API as CauldronLoader
   (window.GrimoireLoader.{build, randomCaption, showPage, hidePage}).

   Load AFTER cauldron-loader.js on Grimoire pages: it takes over the
   Cauldron (and Arcano) page-transition loader, so link clicks and form
   posts show the book instead of the cauldron. Colors come from
   grimoire-tokens.css, falling back to cauldron/arcano tokens.
   prefers-reduced-motion: the book renders open and still, no turning. */
(function () {
  var _pageLoader = null;

  if (!document.getElementById('grim-loader-css')) {
    var s = document.createElement('style');
    s.id = 'grim-loader-css';
    s.textContent = [
      '.grim-loader{display:flex;flex-direction:column;align-items:center;gap:20px;}',
      '.grim-loader-box{position:relative;}',
      '.grim-loader-halo{position:absolute;inset:-20%;pointer-events:none;',
      '  background:radial-gradient(ellipse,var(--grimoire-gilt-dim,var(--arcano-gold-dim)) 0%,transparent 60%);',
      '  animation:grim-pulse 2.4s ease-in-out infinite;}',
      '.grim-loader-ring{position:absolute;border-radius:50%;',
      '  border:1px solid var(--grimoire-gilt,var(--arcano-gold));',
      '  border-top-color:transparent;border-right-color:transparent;}',
      '.grim-loader-ring--outer{inset:4%;animation:grim-spin 9s linear infinite;}',
      '.grim-loader-ring--inner{inset:14%;opacity:.5;animation:grim-spin 13s linear infinite reverse;}',
      '.grim-loader-book{position:absolute;left:22%;right:22%;top:34%;bottom:30%;perspective:600px;}',
      '.grim-loader-page,.grim-loader-leaf{position:absolute;top:0;bottom:0;width:50%;',
      '  background:var(--grimoire-page-raised,var(--cauldron-bg-surface));',
      '  border:1px solid var(--grimoire-gilt,var(--arcano-gold));}',
      '.grim-loader-page::before{content:"";position:absolute;inset:14% 12%;',
      '  background:repeating-linear-gradient(180deg,transparent 0 7px,var(--grimoire-gilt-dim,var(--arcano-gold-dim)) 7px 8px);}',
      '.grim-loader-page--l{left:0;border-radius:6px 0 0 6px;transform:skewY(4deg);transform-origin:right center;}',
      '.grim-loader-page--r{left:50%;border-radius:0 6px 6px 0;transform:skewY(-4deg);transform-origin:left center;}',
      '.grim-loader-leaf{left:50%;border-radius:0 6px 6px 0;transform-origin:left center;opacity:0;',
      '  animation:grim-turn 2.4s cubic-bezier(.45,.05,.35,1) infinite;}',
      '.grim-loader-leaf:nth-child(4){animation-delay:.8s;}',
      '.grim-loader-leaf:nth-child(5){animation-delay:1.6s;}',
      '.grim-loader-spine{position:absolute;left:50%;top:-6%;bottom:-6%;width:1px;',
      '  background:var(--grimoire-gilt,var(--arcano-gold));box-shadow:0 0 8px var(--grimoire-gilt,var(--arcano-gold));}',
      '.grim-loader-spark{position:absolute;left:50%;top:14%;width:8px;height:8px;margin-left:-4px;',
      '  background:var(--grimoire-gilt,var(--arcano-gold));transform:rotate(45deg);',
      '  box-shadow:0 0 10px var(--grimoire-gilt,var(--arcano-gold));animation:grim-blink 2.4s ease-in-out infinite;}',
      '.grim-loader-caption{font-family:var(--arcano-font-italic);font-style:italic;font-size:1rem;',
      '  letter-spacing:.02em;margin:0;color:var(--grimoire-text-dim,var(--cauldron-cream-dim));}',
      '.grim-loader-dots{display:flex;gap:8px;}',
      '.grim-loader-dot{width:5px;height:5px;border-radius:50%;background:var(--grimoire-gilt,var(--arcano-gold));',
      '  animation:grim-blink 1.2s ease-in-out infinite;}',
      '.grim-loader-dot:nth-child(2){animation-delay:.18s;}',
      '.grim-loader-dot:nth-child(3){animation-delay:.36s;}',
      '.grim-loader-backdrop{position:fixed;inset:0;z-index:var(--arcano-z-loader);display:flex;',
      '  align-items:center;justify-content:center;',
      '  background:color-mix(in srgb,var(--grimoire-ink,var(--cauldron-bg-deep)) 92%,transparent);}',
      '@keyframes grim-spin{to{transform:rotate(360deg);}}',
      '@keyframes grim-pulse{0%,100%{opacity:.5;transform:scale(.96)}50%{opacity:1;transform:scale(1.04)}}',
      '@keyframes grim-blink{0%,100%{opacity:.25}50%{opacity:1}}',
      '@keyframes grim-turn{0%{transform:rotateY(0);opacity:0}6%{opacity:1}',
      '  46%{transform:rotateY(-180deg);opacity:1}54%,100%{transform:rotateY(-180deg);opacity:0}}',
      '@media (prefers-reduced-motion: reduce){',
      '  .grim-loader *{animation:none !important;}',
      '  .grim-loader-leaf{display:none;}',
      '  .grim-loader-dot,.grim-loader-spark{opacity:.7;}',
      '}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function el(cls) {
    var d = document.createElement('div');
    d.className = cls;
    return d;
  }

  function buildGrimoireLoader(caption, size) {
    var SIZE = size || 180;
    var wrap = el('grim-loader');
    wrap.setAttribute('role', 'status');
    wrap.setAttribute('aria-label', caption || 'Loading');

    var box = el('grim-loader-box');
    box.style.width = SIZE + 'px';
    box.style.height = SIZE + 'px';
    box.appendChild(el('grim-loader-halo'));
    box.appendChild(el('grim-loader-ring grim-loader-ring--outer'));
    box.appendChild(el('grim-loader-ring grim-loader-ring--inner'));

    var book = el('grim-loader-book');
    book.appendChild(el('grim-loader-page grim-loader-page--l'));
    book.appendChild(el('grim-loader-page grim-loader-page--r'));
    book.appendChild(el('grim-loader-spine'));
    [0, 1, 2].forEach(function () { book.appendChild(el('grim-loader-leaf')); });
    box.appendChild(book);
    box.appendChild(el('grim-loader-spark'));
    wrap.appendChild(box);

    if (caption) {
      var cap = document.createElement('p');
      cap.className = 'grim-loader-caption';
      cap.textContent = caption;
      wrap.appendChild(cap);
    }

    var dots = el('grim-loader-dots');
    [0, 1, 2].forEach(function () { dots.appendChild(el('grim-loader-dot')); });
    wrap.appendChild(dots);
    return wrap;
  }

  var CAPTIONS = ['Turning the pages…', 'Reading the margins…', 'Copying out the spell…', 'The grimoire stirs…'];
  function randomCaption() { return CAPTIONS[Math.floor(Math.random() * CAPTIONS.length)]; }

  window.GrimoireLoader = {
    build: buildGrimoireLoader,
    randomCaption: randomCaption,
    showPage: function (caption) {
      if (_pageLoader) return;
      _pageLoader = el('grim-loader-backdrop');
      _pageLoader.appendChild(buildGrimoireLoader(caption || randomCaption()));
      document.body.appendChild(_pageLoader);
    },
    hidePage: function () {
      if (_pageLoader) { _pageLoader.remove(); _pageLoader = null; }
    }
  };

  /* Take over the Cauldron's (and Arcano's) page-transition loader. The
     click/submit/pageshow wiring in cauldron-loader.js looks these up on
     each event, so swapping the functions is enough. */
  [window.CauldronLoader, window.ArcanoLoader].forEach(function (loader) {
    if (!loader) return;
    loader.showPage = window.GrimoireLoader.showPage;
    loader.hidePage = window.GrimoireLoader.hidePage;
  });
}());
