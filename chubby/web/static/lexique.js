/* Lexique : le jargon de trading des Chubbies, expliqué simplement.
   Un bouton « ? » sur chaque page ouvre l'aide-mémoire ; chaque terme repéré dans la page est souligné en pointillés :
   au survol, sa définition s'affiche ; au clic (ou au toucher), le lexique s'ouvre sur ce terme. */
(function () {
  var TERMS = [
    ['Bullish', 'Optimiste : on pense que le cours va monter. « Marché bullish » = le marché est en hausse.', ['bullish']],
    ['Bearish', 'Pessimiste : on pense que le cours va baisser. « Marché bearish » = le marché est en baisse.', ['bearish']],
    ['Long / Short', 'Long : on achète en pariant sur la hausse. Short : on parie sur la baisse.', []],
    ['Trend', 'La direction générale du cours sur plusieurs semaines : vers le haut (uptrend) ou vers le bas. « Le trend s’inverse » = la direction change.', ['trend', 'trends', 'uptrend']],
    ['Momentum', 'La force d’un mouvement : un titre qui monte vite et longtemps a du momentum. On parie qu’il continue.', ['momentum']],
    ['SMA', 'Simple Moving Average : la moyenne des cours de clôture des N dernières séances (SMA 20 = 20 séances, environ un mois ; SMA 200 = environ un an). Cours au-dessus de sa SMA = il monte en général ; en dessous = il baisse.', ['SMA \\d+', 'SMA']],
    ['RSI', 'Relative Strength Index, de 0 à 100. Sous 30 : le titre a beaucoup baissé récemment (oversold). Au-dessus de 70 : il a beaucoup monté (overbought).', ['RSI']],
    ['Oversold', 'Survendu : le titre a tellement baissé en peu de temps qu’un rebond est probable. Mesuré avec le RSI.', ['oversold', 'overbought']],
    ['Stop-loss', 'Un ordre de vente automatique posé à l’avance : si le cours tombe à ce prix, on vend pour limiter la perte.', ['stop-loss', 'stop']],
    ['Rebalance', 'Remettre chaque titre à son poids cible (par exemple 50 % / 50 %) en vendant ce qui a trop monté et en achetant le reste.', ['rebalance', 'rebalancer', 'rebalancing']],
    ['FNB (ETF)', 'Fonds négocié en bourse : un panier de titres qui s’achète comme une action. QQQ = le Nasdaq, SPY = le S&P 500, IWM = les petites sociétés, SMH = les semi-conducteurs.', ['FNB', 'ETF']],
    ['Option', 'Un contrat sur 100 actions d’un titre, avec un prix fixé (le strike) et une date limite (l’expiry). Il coûte bien moins cher que les actions : ça amplifie les gains et les pertes.', []],
    ['Call', 'Une option qui gagne quand le titre monte : le droit d’acheter 100 actions au strike. Acheter des calls = parier sur la hausse (bullish).', ['calls', 'call']],
    ['Put', 'Une option qui gagne quand le titre baisse : le droit de vendre 100 actions au strike. Acheter des puts = parier sur la baisse (bearish).', ['puts', 'put']],
    ['Strike', 'Le prix fixé dans l’option. Un call IWM 295 = le droit d’acheter IWM à 295 $, quel que soit son cours.', ['strike']],
    ['Premium', 'Le prix de l’option : ce que l’acheteur paie, et ce que le vendeur encaisse. Il est donné par action : un premium de 2,64 $ coûte 264 $ pour un contrat (100 actions).', ['premium', 'premiums']],
    ['Expiry', 'La date limite de l’option. Après, elle ne vaut plus rien, sauf si elle est ITM. Plus l’expiry approche, plus l’option perd de la valeur chaque jour.', ['expiry', 'expiries']],
    ['ITM / OTM', 'In The Money / Out of The Money. Un call est ITM quand le cours est au-dessus du strike (il vaudrait déjà quelque chose à l’expiry), OTM quand il est en dessous. Pour un put, c’est l’inverse.', ['ITM', 'OTM']],
    ['Covered call', 'On détient 100 actions et on vend un call dessus : on encaisse le premium, mais si le cours dépasse le strike, on doit vendre les actions à ce prix.', ['covered calls?']],
    ['Cash-secured put', 'On vend un put en gardant assez de cash pour acheter les 100 actions si le cours tombe sous le strike. On encaisse le premium en attendant.', ['cash-secured puts?']],
    ['La roue (wheel)', 'La stratégie de Jalapeño v2 : vendre des cash-secured puts ; si on reçoit les actions, vendre des covered calls dessus ; et recommencer.', ['wheel']],
    ['Levier (leverage)', 'Contrôler beaucoup d’argent avec peu : une option bouge bien plus vite que son titre, en hausse comme en baisse.', ['leverage']],
    ['Bias', 'Sa façon de jouer en ce moment, réglée par le marché (par exemple calls en marché bullish, puts en marché bearish). Changer de bias ne passe pas d’ordre tout de suite.', []],
    ['Dividende', 'La part des profits qu’une société verse à ses actionnaires, en général chaque trimestre.', []],
    ['P&L', 'Profit and Loss : le gain ou la perte en dollars. « P&L réalisé » = sur les titres déjà revendus.', ['P&L']],
    ['Max drawdown', 'La pire baisse depuis un sommet : si le capital passe de 11 000 $ à 9 900 $, le drawdown est de −10 %.', ['drawdown', 'Max drawdown']],
    ['Win rate', 'La part des allers-retours terminés en gain.', ['Win rate']],
    ['Sharpe', 'Le rendement comparé aux secousses pour l’obtenir. Au-dessus de 1 : bon ; au-dessus de 2 : très bon.', ['Sharpe']],
    ['Volatilité', 'L’ampleur des variations du cours. Forte volatilité = ça bouge beaucoup, dans les deux sens.', []]
  ];
  var DEF = {}, alts = [];
  TERMS.forEach(function (t, i) { t[2].forEach(function (a) { alts.push([a, i]); }); });
  alts.sort(function (a, b) { return b[0].length - a[0].length; }); // les expressions longues d'abord (« covered call » avant « call »)
  var RE;
  try { RE = new RegExp('(?<![\\p{L}\\d-])(' + alts.map(function (a) { return a[0]; }).join('|') + ')(?![\\p{L}-])', 'giu'); } catch (e) { RE = null; }
  function find(word) {
    for (var i = 0; i < alts.length; i++) if (new RegExp('^(?:' + alts[i][0] + ')$', 'i').test(word)) return alts[i][1];
    return -1;
  }
  // Ces mots ne sont du jargon qu'en minuscules dans un texte français (« Stop » sur un bouton n'en est pas).
  var STRICT = { stop: 1, put: 1, puts: 1, call: 1, calls: 1 };

  var css = document.createElement('style');
  css.textContent =
    '.lx { text-decoration: underline dotted; text-underline-offset: 3px; text-decoration-thickness: 1px; cursor: help; }' +
    '#lx-btn { position: fixed; right: 14px; bottom: 14px; z-index: 9998; width: 40px; height: 40px; border-radius: 50%; border: 0; ' +
    'background: #6C5CE7; color: #fff; font: 800 18px/1 Nunito, system-ui, sans-serif; box-shadow: 0 4px 14px rgba(0,0,0,0.3); cursor: pointer; }' +
    '#lx-btn:focus-visible, #lx-dlg button:focus-visible { outline: 3px solid #FFD966; outline-offset: 2px; }' +
    '#lx-dlg { position: fixed; inset: 0; z-index: 9999; display: none; align-items: center; justify-content: center; background: rgba(20,18,32,0.55); padding: 16px; }' +
    '#lx-dlg.open { display: flex; }' +
    '#lx-box { background: #FFFDF8; color: #2A2838; border-radius: 18px; width: min(560px, 100%); max-height: min(80vh, 720px); display: flex; flex-direction: column; box-shadow: 0 12px 40px rgba(0,0,0,0.35); font: 600 14px/1.45 Nunito, system-ui, sans-serif; }' +
    '#lx-box header { display: flex; align-items: center; gap: 8px; padding: 14px 16px 8px; }' +
    '#lx-box h2 { margin: 0; font: 800 18px/1.2 Nunito, system-ui, sans-serif; flex: 1; }' +
    '#lx-box input { margin: 0 16px 8px; padding: 8px 12px; border-radius: 10px; border: 1px solid #D8D4E6; font: inherit; color: inherit; background: #fff; }' +
    '#lx-close { border: 0; background: #EEEAF7; color: #2A2838; width: 36px; height: 36px; border-radius: 50%; font-size: 18px; cursor: pointer; }' +
    '#lx-list { overflow: auto; padding: 0 16px 16px; margin: 0; list-style: none; }' +
    '#lx-list li { padding: 8px 10px; border-radius: 10px; }' +
    '#lx-list li.hot { background: #FFF1C2; }' +
    '#lx-list b { display: block; font-weight: 800; color: #4B3BC4; }';
  document.head.appendChild(css);

  var dlg = document.createElement('div');
  dlg.id = 'lx-dlg'; dlg.setAttribute('data-lexique', 'non');
  dlg.innerHTML = '<div id="lx-box" role="dialog" aria-modal="true" aria-labelledby="lx-title"><header><h2 id="lx-title">Lexique du trading</h2>' +
    '<button id="lx-close" type="button" aria-label="Fermer le lexique">×</button></header>' +
    '<input id="lx-q" type="search" placeholder="Chercher un mot…" aria-label="Chercher un mot du lexique">' +
    '<ul id="lx-list">' + TERMS.map(function (t, i) { return '<li data-i="' + i + '"><b>' + t[0] + '</b>' + t[1] + '</li>'; }).join('') + '</ul></div>';
  var btn = document.createElement('button');
  btn.id = 'lx-btn'; btn.type = 'button'; btn.textContent = '?'; btn.title = 'Lexique : le jargon des Chubbies expliqué';
  btn.setAttribute('aria-label', 'Ouvrir le lexique du trading'); btn.setAttribute('data-lexique', 'non');
  var last = null;
  function open(i) {
    last = document.activeElement; dlg.classList.add('open'); var q = dlg.querySelector('#lx-q'); q.value = ''; filter('');
    dlg.querySelectorAll('#lx-list li').forEach(function (li) { li.classList.toggle('hot', +li.dataset.i === i); });
    var hot = dlg.querySelector('#lx-list li.hot');
    if (hot) hot.scrollIntoView({ block: 'center' }); else dlg.querySelector('#lx-list').scrollTop = 0;
    dlg.querySelector('#lx-close').focus();
  }
  function close() { dlg.classList.remove('open'); if (last && last.focus) last.focus(); }
  function filter(q) {
    q = q.trim().toLowerCase();
    dlg.querySelectorAll('#lx-list li').forEach(function (li) { li.hidden = q && li.textContent.toLowerCase().indexOf(q) < 0; });
  }
  btn.addEventListener('click', function () { open(-1); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg || e.target.id === 'lx-close') close(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && dlg.classList.contains('open')) { e.stopPropagation(); close(); } }, true);
  dlg.querySelector('#lx-q').addEventListener('input', function () { filter(this.value); });
  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('.lx');
    if (t) { e.preventDefault(); e.stopPropagation(); open(+t.dataset.i); }
  }, true);

  // Souligner le jargon dans le texte de la page (et dans ce qui s'y ajoute ensuite).
  var SKIP = /^(SCRIPT|STYLE|TEXTAREA|INPUT|SELECT|OPTION|BUTTON|A|SVG|CANVAS|CODE|TITLE)$/;
  function skip(el) {
    for (; el && el !== document.body; el = el.parentNode) {
      if (el.nodeType === 1 && (SKIP.test(el.tagName.toUpperCase()) || el.classList.contains('lx') || el.getAttribute('data-lexique') === 'non' || el.isContentEditable)) return true;
    }
    return false;
  }
  function mark(root) {
    if (!RE || !root) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [], n;
    while ((n = walker.nextNode())) if (n.nodeValue.length > 2) nodes.push(n);
    nodes.forEach(function (node) {
      var text = node.nodeValue; RE.lastIndex = 0;
      if (!RE.test(text) || skip(node.parentNode)) return;
      var frag = document.createDocumentFragment(), pos = 0, m; RE.lastIndex = 0;
      while ((m = RE.exec(text))) {
        var word = m[1], i = find(word);
        if (i < 0 || (STRICT[word.toLowerCase()] && word !== word.toLowerCase())) continue;
        if (m.index > pos) frag.appendChild(document.createTextNode(text.slice(pos, m.index)));
        var s = document.createElement('span'); s.className = 'lx'; s.dataset.i = i; s.title = TERMS[i][0] + ' : ' + TERMS[i][1]; s.textContent = word;
        frag.appendChild(s); pos = m.index + word.length;
      }
      if (!pos) return;
      if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag, node);
    });
  }
  var queue = [], timer = null;
  function flush() { timer = null; var q = queue; queue = []; q.forEach(function (el) { if (el.isConnected) mark(el); }); }
  function start() {
    document.body.appendChild(btn); document.body.appendChild(dlg);
    mark(document.body);
    new MutationObserver(function (list) {
      list.forEach(function (r) {
        r.addedNodes.forEach(function (el) {
          if (el.nodeType === 3) el = el.parentNode;
          if (el && el.nodeType === 1 && !(el.classList && el.classList.contains('lx'))) queue.push(el);
        });
        if (r.type === 'characterData' && r.target.parentNode) queue.push(r.target.parentNode);
      });
      if (queue.length && !timer) timer = setTimeout(flush, 150);
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  window.ChubbyLexique = { open: open, terms: TERMS };
})();
