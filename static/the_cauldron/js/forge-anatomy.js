/* ═══════════════════════════════════════════════
   Arcano Design System — The Cauldron · The Forge
   Anatomy module — front/back body silhouettes with
   per-muscle regions. Worked muscles are highlighted
   by passing their keys to ForgeAnatomy.svg().
   Muscle keys mirror the seeded Muscle catalog.
   Colour comes from forge.css (.fanat-* classes).
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  // Each muscle group → SVG shapes (left/right where the muscle is paired).
  // Coordinates are within a 120×240 viewBox per figure. Front and back are
  // rendered as two separate figures; a muscle only appears on its own side.
  var FRONT = {
    front_delts:
      '<ellipse cx="40" cy="50" rx="9" ry="8"/><ellipse cx="80" cy="50" rx="9" ry="8"/>',
    side_delts:
      '<ellipse cx="32" cy="52" rx="6" ry="7"/><ellipse cx="88" cy="52" rx="6" ry="7"/>',
    chest:
      '<ellipse cx="51" cy="62" rx="12" ry="9"/><ellipse cx="69" cy="62" rx="12" ry="9"/>',
    biceps:
      '<ellipse cx="32" cy="72" rx="6" ry="13"/><ellipse cx="88" cy="72" rx="6" ry="13"/>',
    forearms:
      '<ellipse cx="27" cy="98" rx="5" ry="14"/><ellipse cx="93" cy="98" rx="5" ry="14"/>',
    abs:
      '<rect x="52" y="74" width="16" height="30" rx="4"/>',
    obliques:
      '<ellipse cx="46" cy="90" rx="5" ry="12"/><ellipse cx="74" cy="90" rx="5" ry="12"/>',
    quads:
      '<ellipse cx="51" cy="142" rx="9" ry="23"/><ellipse cx="69" cy="142" rx="9" ry="23"/>',
  };

  var BACK = {
    traps:
      '<path d="M178 40 L202 40 L196 60 L184 60 Z"/>',
    rear_delts:
      '<ellipse cx="170" cy="52" rx="8" ry="7"/><ellipse cx="210" cy="52" rx="8" ry="7"/>',
    triceps:
      '<ellipse cx="163" cy="73" rx="6" ry="13"/><ellipse cx="217" cy="73" rx="6" ry="13"/>',
    lats:
      '<ellipse cx="176" cy="82" rx="9" ry="16"/><ellipse cx="204" cy="82" rx="9" ry="16"/>',
    mid_back:
      '<ellipse cx="190" cy="70" rx="10" ry="10"/>',
    lower_back:
      '<ellipse cx="190" cy="100" rx="9" ry="12"/>',
    glutes:
      '<ellipse cx="181" cy="122" rx="10" ry="11"/><ellipse cx="199" cy="122" rx="10" ry="11"/>',
    hamstrings:
      '<ellipse cx="181" cy="152" rx="9" ry="21"/><ellipse cx="199" cy="152" rx="9" ry="21"/>',
    calves:
      '<ellipse cx="181" cy="190" rx="7" ry="15"/><ellipse cx="199" cy="190" rx="7" ry="15"/>',
  };

  // Neutral body silhouette (no muscle data) drawn behind the muscle groups so
  // the figure reads as a person. Front figure ~cx 60, back figure ~cx 190.
  function silhouette(cx) {
    return (
      '<g class="fanat-base">' +
      '<circle cx="' + cx + '" cy="20" r="13"/>' +
      '<rect x="' + (cx - 6) + '" y="31" width="12" height="9" rx="3"/>' +
      '<rect x="' + (cx - 18) + '" y="44" width="36" height="64" rx="12"/>' +
      '<rect x="' + (cx - 24) + '" y="46" width="11" height="58" rx="5"/>' +
      '<rect x="' + (cx + 13) + '" y="46" width="11" height="58" rx="5"/>' +
      '<rect x="' + (cx - 13) + '" y="104" width="26" height="18" rx="5"/>' +
      '<rect x="' + (cx - 12) + '" y="118" width="10" height="98" rx="5"/>' +
      '<rect x="' + (cx + 2) + '" y="118" width="10" height="98" rx="5"/>' +
      '</g>'
    );
  }

  function groupMarkup(key, shapes, worked) {
    var cls = 'fanat-muscle' + (worked ? ' is-worked' : '');
    return '<g class="' + cls + '" data-muscle="' + key + '">' + shapes + '</g>';
  }

  function figureMarkup(regions, worked, isBack) {
    var cx = isBack ? 190 : 60;
    var body = silhouette(cx);
    Object.keys(regions).forEach(function (key) {
      body += groupMarkup(key, regions[key], worked.has(key));
    });
    var vb = isBack ? '130 0 120 230' : '0 0 120 230';
    return (
      '<figure class="fanat-figure">' +
      '<svg class="fanat-svg" viewBox="' + vb + '" role="img" ' +
      'aria-label="' + (isBack ? 'Back muscle map' : 'Front muscle map') + '">' +
      body +
      '</svg>' +
      '<figcaption class="fanat-caption">' + (isBack ? 'Back' : 'Front') + '</figcaption>' +
      '</figure>'
    );
  }

  // Public: build the two-figure body map. ``workedKeys`` is an array/Set of
  // muscle keys to highlight.
  function bodySvg(workedKeys) {
    var worked = workedKeys instanceof Set ? workedKeys : new Set(workedKeys || []);
    return (
      '<div class="fanat-body">' +
      figureMarkup(FRONT, worked, false) +
      figureMarkup(BACK, worked, true) +
      '</div>'
    );
  }

  window.ForgeAnatomy = {
    svg: bodySvg,
    frontKeys: Object.keys(FRONT),
    backKeys: Object.keys(BACK),
  };
}());
