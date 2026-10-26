/* Ce que toutes les pages partagent : l'allure des Chubbies (avatars SVG) et la mise en forme des nombres. */
(function () {
  'use strict';
  var LOOK = {
    tortue:   { base: '#B5D6EE', light: '#E8F3FC', dark: '#7FAED6', shape: 'tortue' },
    flan:     { base: '#FBE29A', light: '#FFF6D2', dark: '#E2B84A', shape: 'round' },
    dumpling: { base: '#B4E0DD', light: '#E6F8F6', dark: '#6FB9B3', shape: 'bell' },
    mochi:    { base: '#DCC8E6', light: '#F4EAFA', dark: '#A98CC4', shape: 'star' },
    muffin:   { base: '#F7C9AE', light: '#FFEDE2', dark: '#DE9A74', shape: 'tuft' },
    jalapeno: { base: '#F2A39A', light: '#FFDCD6', dark: '#D9736A', shape: 'arms' },
    'jalapeno-v2': { base: '#F6B98C', light: '#FFE6D2', dark: '#DB8B56', shape: 'arms' },
    'jalapeno-v3': { base: '#E58FA6', light: '#FFDCE6', dark: '#C9637F', shape: 'arms' },
    matcha:   { base: '#C8E6A8', light: '#EEF8E2', dark: '#93C26E', shape: 'round' }
  };
  var DEFAULT_LOOK = { base: '#C9C6D9', light: '#EEEDF5', dark: '#9C99B0', shape: 'round' };
  var SHAPES = {
    round: 'M60 16 C88 16 104 36 104 60 C104 84 86 100 60 100 C34 100 16 84 16 60 C16 36 32 16 60 16 Z',
    bell: 'M22 100 C14 100 16 80 20 62 C24 30 40 12 60 12 C80 12 96 30 100 62 C104 80 106 100 98 100 Z',
    tortue: 'M30 98 C12 98 10 66 18 44 C26 22 48 12 66 15 C86 19 96 40 94 60 C93 72 98 80 106 80 C114 84 110 100 96 99 Z',
    star: 'M60 18 C76 18 84 34 86 46 C98 46 112 50 110 60 C108 68 96 70 90 72 C92 84 90 100 78 100 C70 100 66 94 60 94 C54 94 50 100 42 100 C30 100 28 84 30 72 C24 70 12 68 10 60 C8 50 22 46 34 46 C36 34 44 18 60 18 Z',
    tuft: 'M60 24 C86 24 100 44 100 66 C100 88 84 100 60 100 C36 100 20 88 20 66 C20 44 34 24 60 24 Z',
    arms: 'M60 20 C86 20 100 40 100 62 C100 86 84 100 60 100 C36 100 20 86 20 62 C20 40 34 20 60 20 Z'
  };
  var uid = 0;
  function baseId(id) { return String(id).replace(/-ia$/, ''); }
  function isAI(id) { return /-ia$/.test(String(id)); }
  function look(id) { var b = baseId(id); return LOOK[b] || LOOK[b.replace(/-v\d+$/, '')] || DEFAULT_LOOK; }
  function pretty(t) { var p = String(t).split(':'); return p[0] === 'OPT' ? (p[2] === 'C' ? 'call ' : 'put ') + p[1] + ' ' + p[3] + ' (' + p[4] + ')' : t; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function money(v) { return Math.round(v).toLocaleString('fr-CA') + ' $'; }
  function pct(v, digits) { var p = v * 100; return (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(digits == null ? 1 : digits).replace('.', ',') + ' %'; }
  function cls(v) { return v >= 0 ? 'up' : 'down'; }

  function avatar(id, mood, size, label) {
    var L = look(id), g = 'g' + (++uid), s = 's' + uid;
    var face;
    if (mood === 'content') {
      face = '<path d="M43 57 Q48 51 53 57" stroke="#1E1D26" stroke-width="3.5" fill="none" stroke-linecap="round"/>' +
        '<path d="M67 57 Q72 51 77 57" stroke="#1E1D26" stroke-width="3.5" fill="none" stroke-linecap="round"/>' +
        '<path d="M52 65 Q60 75 68 65 Z" fill="#1E1D26"/>';
    } else if (mood === 'inquiet') {
      face = '<circle cx="48" cy="58" r="5" fill="#1E1D26"/><circle cx="72" cy="58" r="5" fill="#1E1D26"/>' +
        '<path d="M41 47 L53 51 M79 47 L67 51" stroke="#1E1D26" stroke-width="3" stroke-linecap="round"/>' +
        '<path d="M53 73 Q60 67 67 73" stroke="#1E1D26" stroke-width="3.5" fill="none" stroke-linecap="round"/>' +
        '<path d="M92 26 C87 34 87 39 92 39 C97 39 97 34 92 26 Z" fill="#8FC3EA"/>';
    } else {
      face = '<circle cx="48" cy="58" r="5" fill="#1E1D26"/><circle cx="49.6" cy="56.4" r="1.6" fill="#fff"/>' +
        '<circle cx="72" cy="58" r="5" fill="#1E1D26"/><circle cx="73.6" cy="56.4" r="1.6" fill="#fff"/>' +
        '<path d="M55 67 Q60 71 65 67" stroke="#1E1D26" stroke-width="3" fill="none" stroke-linecap="round"/>';
    }
    var extra = '';
    if (L.shape === 'tuft') extra = '<path d="M54 26 C48 12 56 4 60 16 C62 4 72 6 66 24" stroke="' + L.dark + '" stroke-width="7" fill="none" stroke-linecap="round"/>';
    if (L.shape === 'arms') extra = '<path d="M22 62 C12 60 8 70 14 74 M98 62 C108 60 112 70 106 74" stroke="' + L.dark + '" stroke-width="7" fill="none" stroke-linecap="round"/>';
    var aria = label ? ' role="img" aria-label="' + esc(label) + '"' : ' aria-hidden="true"';
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 120 120"' + aria + '>' +
      '<defs><radialGradient id="' + g + '" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="' + L.light + '"/><stop offset="0.55" stop-color="' + L.base + '"/><stop offset="1" stop-color="' + L.dark + '"/></radialGradient>' +
      '<radialGradient id="' + s + '"><stop offset="0" stop-color="#000" stop-opacity="0.5"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient></defs>' +
      '<ellipse cx="60" cy="106" rx="40" ry="7" fill="url(#' + s + ')"/>' + extra +
      '<path d="' + SHAPES[L.shape] + '" fill="url(#' + g + ')"/>' +
      (baseId(id) === 'flan' ? '<path d="M24 40 C34 18 86 18 96 40 C80 33 40 33 24 40 Z" fill="#C88A3E"/>' : '') +
      (baseId(id) === 'matcha' ? '<path d="M60 17 C58 6 67 1 74 3 C72 12 65 17 60 17 Z" fill="#5FA35A"/>' : '') +
      (isAI(id) ? '<path d="M60 17 L60 5" stroke="#8E7BEF" stroke-width="3" stroke-linecap="round"/><circle cx="60" cy="5" r="4.5" fill="#B8A9FF"/>' : '') +
      '<ellipse cx="42" cy="34" rx="12" ry="6.5" fill="#fff" opacity="0.55" transform="rotate(-25 42 34)"/>' + face + '</svg>';
  }
  window.Chubby = { LOOK: LOOK, look: look, avatar: avatar, baseId: baseId, isAI: isAI, pretty: pretty, esc: esc, money: money, pct: pct, cls: cls };
})();
