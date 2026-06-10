/* ═══════════════════════════════════════════════
   Arcano Design System — The Cauldron · The Forge
   Icon library — line-SVG exercise glyphs, equipment
   marks, and utility icons. All draw with
   stroke="currentColor"; colour via the parent.
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  /* Reference lines (wall / ground / bar / bench) use .ref;
     motion arrows use .mo — both styled in forge.css. */

  var GLYPHS = {

    /* ───────── Horizontal Push ───────── */
    wallpush:
      '<line class="ref" x1="50" y1="13" x2="50" y2="53"/>' +
      '<line class="ref" x1="9" y1="53" x2="50" y2="53"/>' +
      '<path d="M18 53 L24 35 L29 25"/>' +
      '<circle cx="31" cy="20" r="4"/>' +
      '<path d="M29 26 L48 31"/>',
    inclinepush:
      '<path d="M11 53 L47 33"/>' +
      '<line class="ref" x1="9" y1="53" x2="55" y2="53"/>' +
      '<path d="M19 48 L43 35"/>' +
      '<circle cx="48" cy="32" r="4"/>' +
      '<path d="M41 36 L43 47"/>',
    kneepush:
      '<line class="ref" x1="9" y1="53" x2="55" y2="53"/>' +
      '<path d="M12 53 L25 50"/>' +
      '<path d="M25 50 L44 41"/>' +
      '<circle cx="50" cy="38" r="4"/>' +
      '<path d="M43 42 L44 53"/>',
    pushup:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 52 L44 41"/>' +
      '<circle cx="50" cy="38" r="4"/>' +
      '<path d="M43 42 L45 53"/>' +
      '<path d="M37 44 L38 53"/>',
    diamondpush:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 52 L40 43"/>' +
      '<circle cx="46" cy="40" r="4"/>' +
      '<path d="M40 53 L36 48 L44 48 Z"/>' +
      '<path d="M40 44 L40 48"/>',
    archerpush:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M16 51 L40 42"/>' +
      '<circle cx="46" cy="39" r="4"/>' +
      '<path d="M40 43 L43 53"/>' +
      '<path d="M40 43 L20 53"/>',

    /* ───────── Vertical Pull ───────── */
    row:
      '<line class="ref" x1="12" y1="13" x2="52" y2="13"/>' +
      '<line class="ref" x1="9" y1="53" x2="20" y2="53"/>' +
      '<path d="M13 52 L42 35"/>' +
      '<circle cx="45" cy="31" r="4"/>' +
      '<path d="M44 33 L45 14"/>' +
      '<path d="M40 35 L38 14"/>',
    negpull:
      '<line class="ref" x1="13" y1="12" x2="51" y2="12"/>' +
      '<path d="M26 12 L31 25"/>' +
      '<path d="M38 12 L33 25"/>' +
      '<circle cx="32" cy="27" r="4"/>' +
      '<path d="M32 31 L32 47"/>' +
      '<path class="mo" d="M44 24 L44 40 M40 36 L44 41 L48 36"/>',
    pullup:
      '<line class="ref" x1="13" y1="12" x2="51" y2="12"/>' +
      '<path d="M26 12 L30 22"/>' +
      '<path d="M38 12 L34 22"/>' +
      '<circle cx="32" cy="20" r="4"/>' +
      '<path d="M32 24 L32 42"/>' +
      '<path d="M32 42 L28 50"/>' +
      '<path d="M32 42 L36 50"/>',
    archerpull:
      '<line class="ref" x1="11" y1="12" x2="53" y2="12"/>' +
      '<path d="M24 12 L24 22"/>' +
      '<path d="M24 17 L47 13"/>' +
      '<circle cx="24" cy="25" r="4"/>' +
      '<path d="M24 29 L26 46"/>' +
      '<path d="M26 46 L22 53"/>' +
      '<path d="M26 46 L31 52"/>',

    /* ───────── Vertical Push ───────── */
    pikepush:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M16 53 L32 24 L48 53"/>' +
      '<circle cx="37" cy="41" r="3.6"/>',
    deeppike:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M18 53 L34 20 L46 53"/>' +
      '<circle cx="31" cy="46" r="3.6"/>' +
      '<path class="mo" d="M52 30 L52 44 M48 40 L52 45 L56 40"/>',
    wallhspu:
      '<line class="ref" x1="45" y1="12" x2="45" y2="53"/>' +
      '<line class="ref" x1="9" y1="53" x2="45" y2="53"/>' +
      '<path d="M28 46 L35 18"/>' +
      '<path d="M35 18 L43 16"/>' +
      '<circle cx="26" cy="49" r="4"/>' +
      '<path d="M28 46 L25 53"/>',
    hspu:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M32 47 L32 20"/>' +
      '<path d="M32 20 L26 13"/>' +
      '<path d="M32 20 L38 13"/>' +
      '<circle cx="32" cy="49" r="4"/>' +
      '<path d="M30 51 L26 53"/>' +
      '<path d="M34 51 L38 53"/>',

    /* ───────── Lower Unilateral ───────── */
    assistsplit:
      '<line class="ref" x1="52" y1="14" x2="52" y2="53"/>' +
      '<line class="ref" x1="9" y1="53" x2="52" y2="53"/>' +
      '<circle cx="30" cy="15" r="4"/>' +
      '<path d="M30 19 L30 32"/>' +
      '<path d="M30 32 L23 41 L25 53"/>' +
      '<path d="M30 32 L40 43 L45 53"/>' +
      '<path d="M30 26 L50 31"/>',
    splitsquat:
      '<line class="ref" x1="9" y1="53" x2="55" y2="53"/>' +
      '<circle cx="30" cy="15" r="4"/>' +
      '<path d="M30 19 L30 33"/>' +
      '<path d="M30 33 L22 41 L24 53"/>' +
      '<path d="M30 33 L41 44 L47 53"/>' +
      '<path d="M30 24 L23 33"/>',
    bulgarian:
      '<line class="ref" x1="9" y1="53" x2="40" y2="53"/>' +
      '<rect class="ref" x="42" y="42" width="12" height="5"/>' +
      '<circle cx="26" cy="16" r="4"/>' +
      '<path d="M26 20 L26 33"/>' +
      '<path d="M26 33 L20 41 L22 53"/>' +
      '<path d="M26 33 L40 41 L46 43"/>',
    pistol:
      '<line class="ref" x1="9" y1="53" x2="55" y2="53"/>' +
      '<circle cx="22" cy="25" r="4"/>' +
      '<path d="M22 29 L21 40"/>' +
      '<path d="M21 40 L17 48 L21 53"/>' +
      '<path d="M21 42 L46 41"/>' +
      '<path d="M46 41 L49 39"/>' +
      '<path d="M22 33 L40 38"/>',

    /* ───────── Core (Anti-Extension) ───────── */
    kneeplank:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 53 L23 53"/>' +
      '<path d="M16 53 L23 44"/>' +
      '<path d="M23 44 L40 50"/>' +
      '<path d="M40 50 L48 53"/>' +
      '<circle cx="25" cy="41" r="4"/>',
    plank:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 53 L23 53"/>' +
      '<path d="M16 53 L23 43"/>' +
      '<path d="M23 43 L50 50"/>' +
      '<path d="M50 50 L53 53"/>' +
      '<circle cx="25" cy="40" r="4"/>',
    rkcplank:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 53 L23 53"/>' +
      '<path d="M16 53 L23 43"/>' +
      '<path d="M23 43 L50 50"/>' +
      '<path d="M50 50 L53 53"/>' +
      '<circle cx="25" cy="40" r="4"/>' +
      '<path class="mo" d="M30 33 L30 38 M37 33 L37 38 M44 33 L44 38"/>',
    hollow:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M9 32 L17 37"/>' +
      '<path d="M17 37 Q32 53 47 37"/>' +
      '<path d="M47 37 L55 32"/>' +
      '<circle cx="14" cy="35" r="3.4"/>',

    /* ───────── Hinge ───────── */
    glutebridge:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 52 L32 38 L44 41 L46 53"/>' +
      '<circle cx="14" cy="50" r="3.4"/>',
    slbridge:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M12 52 L31 38 L43 41 L45 53"/>' +
      '<path d="M31 38 L52 30"/>' +
      '<circle cx="14" cy="50" r="3.4"/>',
    hipthrust:
      '<line class="ref" x1="20" y1="53" x2="56" y2="53"/>' +
      '<rect class="ref" x="8" y="38" width="12" height="9"/>' +
      '<path d="M14 38 L34 39 L45 42 L47 53"/>' +
      '<circle cx="16" cy="34" r="3.4"/>',
    nordic:
      '<line class="ref" x1="8" y1="53" x2="56" y2="53"/>' +
      '<path d="M44 49 L53 49"/>' +
      '<path d="M44 49 L24 41"/>' +
      '<circle cx="20" cy="39" r="4"/>' +
      '<path d="M27 43 L19 51"/>',

    /* ───────── Equipment marks ───────── */
    dumbbell:
      '<line x1="16" y1="32" x2="48" y2="32"/>' +
      '<line x1="14" y1="23" x2="14" y2="41"/>' +
      '<line x1="20" y1="26" x2="20" y2="38"/>' +
      '<line x1="50" y1="23" x2="50" y2="41"/>' +
      '<line x1="44" y1="26" x2="44" y2="38"/>',
    plate:
      '<circle cx="32" cy="32" r="17"/>' +
      '<circle cx="32" cy="32" r="4.5"/>' +
      '<line x1="32" y1="15" x2="32" y2="20"/>' +
      '<line x1="32" y1="44" x2="32" y2="49"/>',
    band:
      '<path d="M16 18 Q30 32 16 46"/>' +
      '<path d="M48 18 Q34 32 48 46"/>' +
      '<line x1="16" y1="32" x2="48" y2="32"/>',
    barbell:
      '<line x1="10" y1="32" x2="54" y2="32"/>' +
      '<line x1="16" y1="23" x2="16" y2="41"/>' +
      '<line x1="22" y1="26" x2="22" y2="38"/>' +
      '<line x1="48" y1="23" x2="48" y2="41"/>' +
      '<line x1="42" y1="26" x2="42" y2="38"/>',

    /* ───────── Utility — voice / audio ───────── */
    micOn:
      '<rect x="25" y="11" width="14" height="26" rx="7"/>' +
      '<path d="M19 30 Q19 45 32 45 Q45 45 45 30"/>' +
      '<line x1="32" y1="45" x2="32" y2="52"/>' +
      '<line x1="24" y1="52" x2="40" y2="52"/>',
    micOff:
      '<rect x="25" y="11" width="14" height="26" rx="7"/>' +
      '<path d="M19 30 Q19 45 32 45 Q45 45 45 30"/>' +
      '<line x1="32" y1="45" x2="32" y2="52"/>' +
      '<line x1="24" y1="52" x2="40" y2="52"/>' +
      '<line class="slash" x1="15" y1="12" x2="49" y2="52"/>',
    earOn:
      '<path d="M15 38 L15 30 Q15 11 32 11 Q49 11 49 30 L49 38"/>' +
      '<rect x="11" y="34" width="9" height="14" rx="3.5"/>' +
      '<rect x="44" y="34" width="9" height="14" rx="3.5"/>',
    earOff:
      '<path d="M15 38 L15 30 Q15 11 32 11 Q49 11 49 30 L49 38"/>' +
      '<rect x="11" y="34" width="9" height="14" rx="3.5"/>' +
      '<rect x="44" y="34" width="9" height="14" rx="3.5"/>' +
      '<line class="slash" x1="13" y1="13" x2="51" y2="51"/>',

    /* ───────── Utility — demographics ───────── */
    male:
      '<circle cx="26" cy="38" r="12"/>' +
      '<line x1="35" y1="29" x2="50" y2="14"/>' +
      '<path d="M41 14 L50 14 L50 23"/>',
    female:
      '<circle cx="32" cy="24" r="12"/>' +
      '<line x1="32" y1="36" x2="32" y2="54"/>' +
      '<line x1="24" y1="46" x2="40" y2="46"/>'
  };

  var NS = 'http://www.w3.org/2000/svg';

  function forgeIcon(name, opts) {
    opts = opts || {};
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 64 64');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', opts.sw || '2.4');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'forge-glyph' + (opts.cls ? ' ' + opts.cls : ''));
    svg.innerHTML = GLYPHS[name] || '';
    return svg;
  }

  function forgeIconHTML(name, sw) {
    return '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="' +
      (sw || '2.4') + '" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true" class="forge-glyph">' + (GLYPHS[name] || '') + '</svg>';
  }

  window.ForgeIcons = { glyphs: GLYPHS, el: forgeIcon, html: forgeIconHTML,
    names: Object.keys(GLYPHS) };
}());
