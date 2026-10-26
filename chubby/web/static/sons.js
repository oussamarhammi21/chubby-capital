/* Le son de l'île : la voix de chaque Chubby, les petits bruitages et la musique lo-fi.
   Trois voix au choix : « humaine » (la synthèse vocale du navigateur, une voix et un ton par Chubby, qui lit
   vraiment sa phrase), « mignonne » (un petit hmm / ooh synthétisé) ou « muette ». Tout est fait dans le navigateur. */
(function () {
  'use strict';
  function base(id) { return String(id).replace(/-ia$/, '').replace(/-v\d+$/, ''); }
  function isAI(id) { return /-ia$/.test(String(id)); }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // ---------- Voix humaine : chaque personnalité a sa voix, sa hauteur et son débit ----------
  // order : rang de la voix française choisie (les voix « naturelles » d'abord) ; pitch et rate autour de 1.
  var PEOPLE = {
    tortue:   { order: 0, pitch: 0.75, rate: 0.82 }, // lente et posée
    flan:     { order: 1, pitch: 1.0,  rate: 0.9 },  // douce, rentière tranquille
    dumpling: { order: 2, pitch: 0.95, rate: 0.98 }, // raisonnable
    mochi:    { order: 3, pitch: 1.25, rate: 1.1 },  // enthousiaste
    muffin:   { order: 4, pitch: 1.35, rate: 1.08 }, // petit chasseur nerveux
    matcha:   { order: 5, pitch: 1.1,  rate: 0.95 }, // studieuse
    jalapeno: { order: 6, pitch: 1.2,  rate: 1.22 }  // casse-cou, parle vite
  };
  var synth = window.speechSynthesis, voices = [];
  function loadVoices() {
    if (!synth) return;
    var all = synth.getVoices().filter(function (v) { return /^fr/i.test(v.lang); });
    function rank(v) { return (/natural|online|neural|premium|enhanced/i.test(v.name) ? 0 : 2) + (/CA/i.test(v.lang) ? 0 : 1); }
    voices = all.sort(function (a, b) { return rank(a) - rank(b) || a.name.localeCompare(b.name); });
  }
  if (synth) { loadVoices(); if ('onvoiceschanged' in synth) synth.onvoiceschanged = loadVoices; }
  // Ce que la synthèse lirait mal : symboles, tickers et options.
  function spoken(text) {
    return String(text)
      .replace(/OPT:([A-Z]+):([CP]):([\d.]+):[\d-]+/g, function (m, t, k, s) { return (k === 'C' ? 'call ' : 'put ') + t.split('').join(' ') + ' ' + s; })
      .replace(/\(?\b\d{4}-\d{2}-\d{2}\)?/g, '')
      .replace(/\b([A-Z]{2,4})-([A-Z])\b(?!\.TO)/g, '$1 $2')
      .replace(/\b([A-Z]{1,5})(?:-[A-Z])?\.TO\b/g, function (m, t) { return t.length <= 3 ? t.split('').join(' ') : t; })
      .replace(/\b([A-Z]{2,4})\b/g, function (m) { return /^(ETF|FNB)$/.test(m) ? m : m.split('').join(' '); })
      .replace(/−/g, 'moins ').replace(/\+(?=\d)/g, 'plus ').replace(/\s?%/g, ' pour cent').replace(/\s?\$/g, ' dollars')
      .replace(/[«»()…]/g, ' ').replace(/\s+/g, ' ').trim();
  }
  function human(id, text, done) {
    if (!synth) return 0;
    var p = PEOPLE[base(id)] || { order: 7, pitch: 1, rate: 1 }, u = new SpeechSynthesisUtterance(spoken(text));
    u.lang = 'fr-CA';
    if (voices.length) { u.voice = voices[p.order % voices.length]; u.lang = u.voice.lang; }
    u.pitch = Math.min(2, p.pitch + (isAI(id) ? 0.08 : 0)); u.rate = p.rate; u.volume = 0.95;
    u.onend = u.onerror = function () { if (done) done(); };
    synth.speak(u);
    return Math.min(14, 1 + text.length / (14 * p.rate)); // durée approximative, pour baisser la musique
  }

  // ---------- Audio synthétisé : réverbération, bruitages, voix « mignonne », musique ----------
  var VOICE = { tortue: 196, dumpling: 208, matcha: 220, flan: 233, muffin: 247, mochi: 262, jalapeno: 277 };
  var VOWELS = { m: [[270, 1, 7], [2100, 0.06, 4]], oo: [[330, 1, 6], [860, 0.32, 7]], oh: [[470, 1, 6], [880, 0.4, 7]],
                 ah: [[700, 1, 5], [1150, 0.42, 6]], hee: [[340, 1, 6], [2250, 0.22, 7]] };
  var audio = { ctx: null, on: store('chubby-son') === '1', voice: store('chubby-voix') || 'humaine', last: 0, tempo: 78 };
  var music = { on: false, want: store('chubby-musique') === '1', timer: null, next: 0, step: 0 };
  var speaking = null; // { id, until } : un seul Chubby parle à la fois

  function impulse(c, seconds) {
    var n = Math.floor(c.sampleRate * seconds), buf = c.createBuffer(2, n, c.sampleRate);
    for (var ch = 0; ch < 2; ch++) { var d = buf.getChannelData(ch); for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 2.6); }
    return buf;
  }
  function start() {
    if (audio.ctx) { if (audio.ctx.state === 'suspended') audio.ctx.resume(); return true; }
    var A = window.AudioContext || window.webkitAudioContext; if (!A) return false;
    var c = audio.ctx = new A();
    var out = c.createDynamicsCompressor(); out.connect(c.destination);
    audio.reverb = c.createConvolver(); audio.reverb.buffer = impulse(c, 2.8);
    var wet = c.createGain(); wet.gain.value = 0.5; audio.reverb.connect(wet); wet.connect(out);
    audio.master = c.createGain(); audio.master.gain.value = 0.6; audio.master.connect(out);
    var voiceSend = c.createGain(); voiceSend.gain.value = 0.22; audio.master.connect(voiceSend); voiceSend.connect(audio.reverb);
    audio.music = c.createGain(); audio.music.gain.value = 0;
    var warm = c.createBiquadFilter(); warm.type = 'lowpass'; warm.frequency.value = 5200;
    var musicBus = c.createGain(); musicBus.gain.value = 0.75; audio.music.connect(warm); warm.connect(musicBus); musicBus.connect(out);
    var musicSend = c.createGain(); musicSend.gain.value = 0.3; audio.music.connect(musicSend); musicSend.connect(audio.reverb);
    audio.noise = c.createBuffer(1, c.sampleRate * 2, c.sampleRate);
    var nd = audio.noise.getChannelData(0); for (var i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
    return true;
  }
  function env(g, at, attack, peak, dur) {
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(peak, at + attack); g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  }
  function tone(freq, at, dur, vol, toFreq) {
    var c = audio.ctx, g = c.createGain(); env(g, at, 0.01, vol, dur); g.connect(audio.master);
    [[1, 1], [2, 0.2]].forEach(function (h) {
      var o = c.createOscillator(), hg = c.createGain(); o.type = 'sine'; o.frequency.setValueAtTime(freq * h[0], at);
      if (toFreq) o.frequency.exponentialRampToValueAtTime(toFreq * h[0], at + dur);
      hg.gain.value = h[1]; o.connect(hg); hg.connect(g); o.start(at); o.stop(at + dur + 0.05);
    });
  }
  function vocal(f0, at, dur, vowel, vol, contour, breath) {
    var c = audio.ctx, end = at + dur;
    var g = c.createGain(); g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(vol, at + Math.min(0.09, dur * 0.3));
    g.gain.setValueAtTime(vol, at + dur * 0.55); g.gain.exponentialRampToValueAtTime(0.0001, end); g.connect(audio.master);
    var mix = c.createGain(), soft = c.createBiquadFilter(); soft.type = 'lowpass'; soft.frequency.value = 2600; mix.connect(soft);
    var tri = c.createOscillator(), saw = c.createOscillator(), sawG = c.createGain(); tri.type = 'triangle'; saw.type = 'sawtooth'; sawG.gain.value = 0.18;
    [tri, saw].forEach(function (o) {
      o.frequency.setValueAtTime(f0 * contour[0][1], at);
      contour.slice(1).forEach(function (k) { o.frequency.linearRampToValueAtTime(f0 * k[1], at + dur * k[0]); });
    });
    tri.connect(mix); saw.connect(sawG); sawG.connect(mix);
    var lfo = c.createOscillator(), depth = c.createGain(); lfo.frequency.value = 5.2; depth.gain.setValueAtTime(0, at);
    depth.gain.linearRampToValueAtTime(14, at + dur * 0.6); lfo.connect(depth); depth.connect(tri.detune); depth.connect(saw.detune);
    VOWELS[vowel].forEach(function (f) {
      var bp = c.createBiquadFilter(), fg = c.createGain(); bp.type = 'bandpass'; bp.frequency.value = f[0]; bp.Q.value = f[2]; fg.gain.value = f[1] * 2.4;
      soft.connect(bp); bp.connect(fg); fg.connect(g);
    });
    var body = c.createGain(); body.gain.value = 0.5; soft.connect(body); body.connect(g);
    if (breath) {
      var n = c.createBufferSource(), nb = c.createBiquadFilter(), ng = c.createGain();
      n.buffer = audio.noise; nb.type = 'bandpass'; nb.frequency.value = VOWELS[vowel][0][0] * 2.2; nb.Q.value = 0.8;
      ng.gain.setValueAtTime(0.0001, at); ng.gain.exponentialRampToValueAtTime(vol * breath * 0.9, at + 0.06); ng.gain.exponentialRampToValueAtTime(0.0001, end + 0.05);
      n.connect(nb); nb.connect(ng); ng.connect(audio.master); n.start(at, Math.random()); n.stop(end + 0.1);
    }
    [tri, saw, lfo].forEach(function (o) { o.start(at); o.stop(end + 0.05); });
  }
  // La voix « mignonne » : un hmm, un ooh, un petit rire ou un soupir, selon l'humeur et la phrase.
  function cute(id, text, mood) {
    var f0 = (VOICE[base(id)] || 233) * (isAI(id) ? 1.03 : 1), t = audio.ctx.currentTime + 0.03, v = 0.13;
    var question = /\?/.test(text), happy = mood === 'content' || /!|merci|bravo|super/i.test(text);
    var worried = mood === 'inquiet' || /aïe|stop|perte|baisse|recul/i.test(text);
    if (question) { vocal(f0, t, 0.9, 'm', v, [[0, 0.97], [0.6, 1.0], [1, 1.2]], 0.25); return 1.0; }
    if (worried) {
      vocal(f0 * 1.04, t, 0.45, 'oh', v * 0.8, [[0, 1.02], [1, 0.95]], 0.35);
      vocal(f0 * 0.98, t + 0.5, 1.1, 'ah', v * 0.7, [[0, 1.0], [1, 0.8]], 0.9);
      return 1.65;
    }
    if (happy) {
      vocal(f0, t, 0.85, 'oo', v, [[0, 0.94], [0.55, 1.16], [1, 1.1]], 0.3);
      [1.26, 1.2, 1.14].forEach(function (r, i) { vocal(f0 * r, t + 0.95 + i * 0.15, 0.12, 'hee', v * 0.45, [[0, 1.02], [1, 0.97]], 0.8); });
      return 1.45;
    }
    vocal(f0, t, 1.0, 'm', v, [[0, 1.0], [0.3, 1.06], [1, 0.94]], 0.25);
    return 1.05;
  }

  // Fait parler un Chubby. `force` (un clic) coupe celui qui parle ; sinon on ne parle pas par-dessus quelqu'un.
  function speak(id, text, mood, force) {
    if (!audio.on || audio.voice === 'muette' || !text) return false;
    var now = Date.now();
    if (speaking && speaking.until > now && !force) return false;
    if (synth && force) synth.cancel();
    var seconds;
    if (audio.voice === 'humaine' && synth) seconds = human(id, text, function () { if (speaking && speaking.id === id) speaking.until = 0; });
    else { if (!start()) return false; seconds = cute(id, text, mood); }
    speaking = { id: id, until: now + seconds * 1000 };
    duck(seconds);
    return true;
  }

  function sfx(kind) {
    if (!audio.on || !audio.ctx) return; var t = audio.ctx.currentTime;
    if (t - audio.last < 0.12) return; audio.last = t;
    if (kind === 'coin') { tone(1319, t, 0.3, 0.05); tone(1760, t + 0.09, 0.6, 0.045); }
    else if (kind === 'pop') tone(660, t, 0.18, 0.05, 880);
    else if (kind === 'oops') { tone(440, t, 0.2, 0.06, 392); tone(349, t + 0.18, 0.4, 0.06, 294); }
    else if (kind === 'trade') { tone(784, t, 0.2, 0.045); tone(1047, t + 0.08, 0.35, 0.045); }
    else if (kind === 'learn') [659, 784, 988].forEach(function (f, i) { tone(f, t + i * 0.1, 0.5, 0.04); });
  }

  // ---------- Musique : un petit morceau lo-fi composé (16 mesures qui bouclent) ----------
  var CH = {
    F: { root: 41, v: [57, 60, 64, 67] }, Em: { root: 40, v: [55, 59, 62, 64] }, Dm: { root: 38, v: [53, 57, 60, 64] },
    C: { root: 36, v: [52, 55, 59, 62] }, Gs: { root: 43, v: [53, 57, 60, 62] }, Am: { root: 45, v: [55, 60, 64, 67] },
    G7: { root: 43, v: [53, 59, 62, 65] }
  };
  var SONG = ['F', 'Em', 'Dm', 'C', 'F', 'Em', 'Dm', 'Gs', 'Am', 'Dm', 'G7', 'C', 'F', 'Em', 'Dm', 'Gs'];
  var MELODY = [
    [[0, 76, 2], [2, 72, 2], [4, 77, 3], [7, 76, 1]], [[0, 74, 3], [3, 71, 1], [4, 67, 4]],
    [[0, 69, 2], [2, 72, 2], [4, 74, 2], [6, 77, 2]], [[0, 76, 6], [6, 74, 1], [7, 72, 1]],
    [[0, 72, 2], [2, 69, 2], [4, 72, 2], [6, 76, 2]], [[0, 79, 3], [3, 76, 1], [4, 74, 4]],
    [[0, 77, 2], [2, 76, 2], [4, 74, 2], [6, 72, 2]], [[0, 71, 4], [4, 74, 2], [6, 67, 2]],
    [[2, 72, 2], [4, 76, 2], [6, 79, 2]], [[0, 77, 4], [4, 74, 4]],
    [[2, 71, 2], [4, 74, 2], [6, 77, 2]], [[0, 76, 8]],
    [[0, 81, 2], [2, 79, 2], [4, 77, 2], [6, 76, 2]], [[0, 74, 3], [3, 76, 1], [4, 79, 4]],
    [[0, 77, 2], [2, 74, 2], [4, 72, 4]], [[0, 74, 4], [4, 71, 2], [6, 67, 2]]
  ];
  function hz(m) { return 440 * Math.pow(2, (m - 69) / 12); }
  function rhodes(m, at, dur, vol, pan) {
    var c = audio.ctx, f = hz(m), car = c.createOscillator(), mod = c.createOscillator(), mg = c.createGain(), g = c.createGain();
    car.frequency.value = f; mod.frequency.value = f;
    mg.gain.setValueAtTime(f * 1.2, at); mg.gain.exponentialRampToValueAtTime(f * 0.15, at + 0.6);
    mod.connect(mg); mg.connect(car.frequency);
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(vol, at + 0.012); g.gain.exponentialRampToValueAtTime(vol * 0.35, at + 0.5);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur); car.connect(g);
    var p = c.createStereoPanner ? c.createStereoPanner() : null;
    if (p) { p.pan.value = pan || 0; g.connect(p); p.connect(audio.music); } else g.connect(audio.music);
    [car, mod].forEach(function (n) { n.start(at); n.stop(at + dur + 0.05); });
  }
  function pad(chord, at, dur) {
    var c = audio.ctx, lp = c.createBiquadFilter(), g = c.createGain();
    lp.type = 'lowpass'; lp.frequency.value = 700; lp.Q.value = 0.3;
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.022, at + 0.8); g.gain.setValueAtTime(0.022, at + dur - 0.6); g.gain.exponentialRampToValueAtTime(0.0001, at + dur + 0.3);
    lp.connect(g); g.connect(audio.music);
    chord.v.forEach(function (m) { [-7, 7].forEach(function (cents) { var o = c.createOscillator(); o.type = 'sawtooth'; o.frequency.value = hz(m); o.detune.value = cents; o.connect(lp); o.start(at); o.stop(at + dur + 0.4); }); });
  }
  function kalimba(m, at, dur, vol) {
    var c = audio.ctx, f = hz(m), g = c.createGain(); env(g, at, 0.005, vol, Math.max(0.5, dur + 0.4)); g.connect(audio.music);
    [[1, 1], [3.01, 0.08], [5.4, 0.03]].forEach(function (h) { var o = c.createOscillator(), hg = c.createGain(); o.frequency.value = f * h[0]; hg.gain.value = h[1]; o.connect(hg); hg.connect(g); o.start(at); o.stop(at + dur + 0.5); });
  }
  function bass(m, at, dur) {
    var c = audio.ctx, o = c.createOscillator(), o2 = c.createOscillator(), lp = c.createBiquadFilter(), g = c.createGain();
    o.type = 'triangle'; o.frequency.value = hz(m); o2.type = 'sine'; o2.frequency.value = hz(m);
    lp.type = 'lowpass'; lp.frequency.setValueAtTime(700, at); lp.frequency.exponentialRampToValueAtTime(200, at + 0.3);
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.11, at + 0.015); g.gain.exponentialRampToValueAtTime(0.04, at + 0.2); g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    o.connect(lp); o2.connect(lp); lp.connect(g); g.connect(audio.music);
    [o, o2].forEach(function (n) { n.start(at); n.stop(at + dur + 0.05); });
  }
  function drum(kind, at, vol) {
    var c = audio.ctx, g = c.createGain();
    if (kind === 'kick') {
      var o = c.createOscillator(); o.frequency.setValueAtTime(110, at); o.frequency.exponentialRampToValueAtTime(45, at + 0.18);
      env(g, at, 0.004, 0.16 * vol, 0.32); o.connect(g); g.connect(audio.music); o.start(at); o.stop(at + 0.35); return;
    }
    var s = c.createBufferSource(), f = c.createBiquadFilter(); s.buffer = audio.noise;
    if (kind === 'snare') { f.type = 'bandpass'; f.frequency.value = 1700; f.Q.value = 0.7; env(g, at, 0.01, 0.06 * vol, 0.22); }
    else { f.type = 'highpass'; f.frequency.value = 7500; env(g, at, 0.002, 0.03 * vol, 0.06); }
    s.connect(f); f.connect(g); g.connect(audio.music); s.start(at, Math.random() * 1.5, 0.3);
  }
  function scheduleMusic() {
    var c = audio.ctx;
    while (music.next < c.currentTime + 0.5) {
      var beat = 60 / audio.tempo, eighth = beat / 2, i = music.step, e = i % 8, barN = Math.floor(i / 8) % SONG.length;
      var at = music.next + (e % 2 ? eighth * 0.16 : 0), ch = CH[SONG[barN]];
      if (e === 0) {
        ch.v.forEach(function (m, k) { rhodes(m, at + k * 0.012, beat * 2.2, 0.045, -0.3 + k * 0.2); });
        pad(ch, at, beat * 4); bass(ch.root, at, beat * 1.6);
      }
      if (e === 5) { ch.v.slice(1).forEach(function (m, k) { rhodes(m, at + k * 0.01, beat * 1.3, 0.028, 0.2 - k * 0.2); }); bass(ch.root + 7, at, beat * 0.8); }
      if (e === 7) bass(ch.root + 12, at, beat * 0.45);
      if (e === 0 || e === 5) drum('kick', at, e ? 0.7 : 1);
      if (e === 2 || e === 6) drum('snare', at, 1);
      drum('hat', at, e % 2 ? 0.6 : 1);
      MELODY[barN].forEach(function (n) { if (n[0] === e) kalimba(n[1], at, n[2] * eighth, 0.05); });
      music.next += eighth; music.step++;
    }
  }
  function duck(seconds) {
    if (!music.on) return; var g = audio.music.gain, t = audio.ctx.currentTime;
    g.cancelScheduledValues(t); g.setTargetAtTime(0.35, t, 0.1); g.setTargetAtTime(1, t + seconds, 0.6);
  }

  window.Sons = {
    get sound() { return audio.on; }, get music() { return music.on; }, get voice() { return audio.voice; },
    wantsMusic: function () { return music.want; },
    humanAvailable: function () { return !!synth; },
    setSound: function (on) {
      audio.on = !!on && start(); store('chubby-son', audio.on ? '1' : '0');
      if (!audio.on && synth) synth.cancel();
      return audio.on;
    },
    setMusic: function (on) {
      music.on = music.want = !!on && start(); store('chubby-musique', music.on ? '1' : '0');
      if (!audio.ctx) return false;
      var t = audio.ctx.currentTime;
      audio.music.gain.cancelScheduledValues(t); audio.music.gain.setTargetAtTime(music.on ? 1 : 0, t, 0.8);
      if (music.on && !music.timer) { music.next = t + 0.1; music.step = 0; music.timer = setInterval(scheduleMusic, 120); }
      if (!music.on && music.timer) { var tm = music.timer; music.timer = null; setTimeout(function () { if (!music.on) clearInterval(tm); else music.timer = tm; }, 3000); }
      return music.on;
    },
    setVoice: function (kind) { audio.voice = kind; store('chubby-voix', kind); if (synth && kind !== 'humaine') synth.cancel(); },
    setTempo: function (bpm) { audio.tempo = bpm; },
    busy: function () { return !!(speaking && speaking.until > Date.now()); },
    speak: speak, sfx: sfx, spoken: spoken
  };
})();
