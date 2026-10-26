/* La balade 3D : l'illustration de l'île devient un relief, grâce à sa carte de profondeur (img/ile-relief.png,
   clair = proche). Le ciel et les bords peints sont découpés et l'île flotte dans une mer et un ciel infinis.
   On tourne autour, on zoome, on se déplace ; la page place les Chubbies et les écrans par-dessus avec project(),
   qui donne pour un point de l'image sa position à l'écran et sa taille apparente. */
(function () {
  'use strict';
  var W = 1548, H = 1016, ZMAX = 300, SX = 387, SY = 254;
  var LIMITS = { yaw: 0.95, pitchLo: -0.2, pitchHi: 0.62, near: 0.22, far: 1.5 };
  var t0 = performance.now(), ocean = null, sky = null;
  var THREE, renderer, scene, camera, depth = null, ready = false, on = false, raf = 0, onFrame = null;
  var cam = { yaw: 0, pitch: 0.12, dist: 1, x: 0, y: -40, z: 0 }, want = {}, base = 1;
  var lastTouch = 0, pointers = {}, drag = null, keys = {};

  function depthAt(x, y) { // 0 (loin) à 1 (proche), interpolé
    if (!depth) return 0.4;
    x = Math.max(0, Math.min(W - 1.001, x)); y = Math.max(0, Math.min(H - 1.001, y));
    var x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, i = y0 * W + x0;
    var a = depth[i] + (depth[i + 1] - depth[i]) * fx, b = depth[i + W] + (depth[i + W + 1] - depth[i + W]) * fx;
    return (a + (b - a) * fy) / 255;
  }
  function world(x, y, sx, sy) { return new THREE.Vector3(x - W / 2, H / 2 - y, depthAt(sx == null ? x : sx, sy == null ? y : sy) * ZMAX); }

  function load(done) {
    var im = new Image();
    im.onload = function () {
      var c = document.createElement('canvas'); c.width = W; c.height = H;
      var g = c.getContext('2d'); g.drawImage(im, 0, 0, W, H);
      var rgba = g.getImageData(0, 0, W, H).data; depth = new Uint8Array(W * H);
      for (var i = 0; i < depth.length; i++) depth[i] = rgba[i * 4];
      done();
    };
    im.src = '/static/img/ile-relief.png';
  }
  function build() {
    THREE = window.THREE;
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.outputEncoding = THREE.sRGBEncoding;
    var cv = renderer.domElement; cv.id = 'relief'; cv.setAttribute('aria-label', "L'île en relief : glisse pour tourner, molette pour zoomer");
    cv.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:0;touch-action:none;cursor:grab;display:none';
    document.body.insertBefore(cv, document.body.firstChild);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(36, 1, 10, 40000);
    // Le relief : une grille dont chaque sommet avance selon la profondeur peinte.
    var geo = new THREE.PlaneBufferGeometry(W, H, SX, SY), pos = geo.attributes.position;
    for (var i = 0; i < pos.count; i++) {
      var x = pos.getX(i) + W / 2, y = H / 2 - pos.getY(i);
      pos.setZ(i, depthAt(x, y) * ZMAX);
    }
    geo.computeVertexNormals();
    var tex = new THREE.TextureLoader().load('/static/img/ile.webp', function () { ready = true; });
    tex.encoding = THREE.sRGBEncoding; tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    // Le ciel et la mer peints sont découpés (profondeur ~ 0 et bords de l'image) : l'île se pose dans un décor infini.
    var mc = document.createElement('canvas'); mc.width = W; mc.height = H;
    var mx = mc.getContext('2d'), md = mx.createImageData(W, H);
    function ss(a, b, v) { var t = Math.max(0, Math.min(1, (v - a) / (b - a))); return t * t * (3 - 2 * t); }
    for (var yy = 0; yy < H; yy++) for (var xx = 0; xx < W; xx++) {
      var dv = depth[yy * W + xx] / 255, cut = yy < 340 ? ss(0.17, 0.25, dv) : ss(0.05, 0.12, dv);
      var edge = ss(0, 170, xx) * ss(0, 170, W - 1 - xx) * ss(0, 150, H - 1 - yy);
      var a = Math.round(255 * cut * edge), j = (yy * W + xx) * 4;
      md.data[j] = md.data[j + 1] = md.data[j + 2] = a; md.data[j + 3] = 255;
    }
    mx.putImageData(md, 0, 0);
    var island = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, alphaMap: new THREE.CanvasTexture(mc), transparent: true, alphaTest: 0.03 }));
    island.renderOrder = 2; scene.add(island);
    buildWorld();

    listen(cv);
    addEventListener('resize', resize); resize();
  }
  // La mer et le ciel infinis. Le dessin est vu d'en haut : la mer est un plan incliné dont l'horizon tombe
  // exactement sur l'horizon peint (y ≈ 235 dans l'image) depuis la vue de départ.
  function buildWorld() {
    var cam0 = new THREE.Vector3(0, -182, 1368), horizon = new THREE.Vector3(0, H / 2 - 235, 0);
    var dir = horizon.clone().sub(cam0).normalize(), up = new THREE.Vector3(1, 0, 0).cross(dir).normalize();
    var anchor = new THREE.Vector3(0, -H / 2 + 40, ZMAX * 0.62);
    var g = new THREE.PlaneBufferGeometry(60000, 60000, 1, 1);
    ocean = new THREE.Mesh(g, new THREE.ShaderMaterial({
      uniforms: { t: { value: 0 }, cam: { value: new THREE.Vector3() } },
      vertexShader: 'varying vec2 p; varying vec3 w; void main(){ p = position.xy; vec4 wp = modelMatrix * vec4(position,1.0); w = wp.xyz; gl_Position = projectionMatrix * viewMatrix * wp; }',
      fragmentShader: [
        'uniform float t; uniform vec3 cam; varying vec2 p; varying vec3 w;',
        'float hash(vec2 q){ return fract(sin(dot(q, vec2(127.1, 311.7))) * 43758.5453); }',
        'float noise(vec2 q){ vec2 i = floor(q), f = fract(q); f = f*f*(3.0-2.0*f);',
        '  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }',
        'float fbm(vec2 q){ float v = 0.0, a = 0.5; mat2 r = mat2(0.8,-0.6,0.6,0.8); for (int i = 0; i < 5; i++){ v += a*noise(q); q = r*q*2.03; a *= 0.5; } return v; }',
        'void main(){',
        '  float d = length(w - cam), far = smoothstep(1800.0, 18000.0, d);',
        '  vec2 q = p * 0.004;',
        '  float a = fbm(q + vec2(t*0.05, t*0.03)), b = fbm(q*2.3 - vec2(t*0.07, -t*0.04));',
        '  float wv = mix(a, b, 0.45);',
        '  vec3 deep = vec3(0.07,0.40,0.70), shallow = vec3(0.24,0.66,0.90), haze = vec3(0.84,0.93,0.99);',
        '  vec3 c = mix(deep, shallow, smoothstep(0.25, 0.75, wv));',
        '  float glint = smoothstep(0.72, 0.82, b) * (1.0 - far) * 0.35;',
        '  c += vec3(glint);',
        '  c = mix(c, haze, pow(far, 1.2));',
        '  gl_FragColor = vec4(c, 1.0);',
        '}'].join('\n')
    }));
    var m = new THREE.Matrix4().makeBasis(new THREE.Vector3(1, 0, 0), dir, up);
    ocean.quaternion.setFromRotationMatrix(m); ocean.position.copy(anchor); ocean.renderOrder = 1; scene.add(ocean);
    sky = new THREE.Mesh(new THREE.SphereBufferGeometry(18000, 32, 16), new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: { up: { value: up }, t: { value: 0 } },
      vertexShader: 'varying vec3 v; void main(){ v = normalize((modelMatrix * vec4(position,1.0)).xyz - cameraPosition); gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position,1.0); }',
      fragmentShader: [
        'uniform vec3 up; uniform float t; varying vec3 v;',
        'float n(vec2 q){ return sin(q.x)*sin(q.y); }',
        'void main(){',
        '  float e = dot(normalize(v), up);',
        '  vec3 zen = vec3(0.30,0.62,0.92), hor = vec3(0.87,0.95,0.99);',
        '  vec3 c = mix(hor, zen, smoothstep(0.0, 0.45, e));',
        // de petits nuages doux qui dérivent
        '  vec2 q = v.xz / max(0.08, e + 0.12) * 3.0 + vec2(t*0.01, 0.0);',
        '  float cl = n(q*1.3) * 0.5 + n(q*2.9 + 1.7) * 0.3 + n(q*6.1 + 4.2) * 0.2;',
        '  c = mix(c, vec3(1.0), smoothstep(0.35, 0.75, cl) * smoothstep(0.0, 0.08, e) * 0.85);',
        '  gl_FragColor = vec4(c, 1.0);',
        '}'].join('\n')
    }));
    sky.renderOrder = 0; scene.add(sky);
  }
  function resize() {
    if (!renderer) return;
    var w = innerWidth, h = innerHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    // À la distance de base, l'île couvre l'écran (comme la carte à plat).
    var v = Math.tan(camera.fov * Math.PI / 360), dw = (W / 2) / (v * camera.aspect), dh = (H / 2) / v;
    base = Math.min(dw, dh) + ZMAX * 0.35;
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function limit(c) {
    c.yaw = clamp(c.yaw, -LIMITS.yaw, LIMITS.yaw); c.pitch = clamp(c.pitch, LIMITS.pitchLo, LIMITS.pitchHi);
    c.dist = clamp(c.dist, LIMITS.near, LIMITS.far); c.x = clamp(c.x, -W / 2, W / 2); c.y = clamp(c.y, -H / 2, H / 2);
  }
  function home() { want = { yaw: 0, pitch: 0.12, dist: 0.88, x: 0, y: -30, z: ZMAX * 0.35 }; }

  // ---------- les gestes : glisser = tourner, clic droit / Maj / deux doigts = se déplacer, molette / pincer = zoomer ----------
  function panBy(dx, dy) {
    var s = want.dist * base / innerHeight * 1.1;
    want.x -= (dx * Math.cos(want.yaw)) * s; want.y += dy * s; limit(want);
  }
  function listen(cv) {
    cv.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    cv.addEventListener('pointerdown', function (e) {
      cv.setPointerCapture(e.pointerId); pointers[e.pointerId] = { x: e.clientX, y: e.clientY }; lastTouch = performance.now();
      drag = { pan: e.button === 2 || e.shiftKey, x: e.clientX, y: e.clientY }; cv.style.cursor = 'grabbing';
    });
    cv.addEventListener('pointermove', function (e) {
      if (!pointers[e.pointerId]) return;
      var ids = Object.keys(pointers), prev = pointers[e.pointerId];
      if (ids.length === 2) { // deux doigts : pincer pour zoomer, glisser pour se déplacer
        var other = pointers[ids[0] == e.pointerId ? ids[1] : ids[0]];
        var d0 = Math.hypot(prev.x - other.x, prev.y - other.y), d1 = Math.hypot(e.clientX - other.x, e.clientY - other.y);
        if (d0 > 0) want.dist *= d0 / d1;
        panBy((e.clientX - prev.x) / 2, (e.clientY - prev.y) / 2); limit(want);
      } else if (drag && drag.pan) panBy(e.clientX - prev.x, e.clientY - prev.y);
      else { want.yaw -= (e.clientX - prev.x) * 0.0032; want.pitch += (e.clientY - prev.y) * 0.0028; limit(want); }
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY }; lastTouch = performance.now();
    });
    function up(e) { delete pointers[e.pointerId]; if (!Object.keys(pointers).length) { drag = null; cv.style.cursor = 'grab'; } }
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('wheel', function (e) { e.preventDefault(); want.dist *= Math.exp(e.deltaY * 0.0011); limit(want); lastTouch = performance.now(); }, { passive: false });
    cv.addEventListener('dblclick', function () { home(); });
    addEventListener('keydown', function (e) { if (on && !e.target.closest('input, select, textarea')) keys[e.key.toLowerCase()] = true; });
    addEventListener('keyup', function (e) { keys[e.key.toLowerCase()] = false; });
  }
  function keyboard() {
    var step = 9, moved = false;
    if (keys.arrowleft || keys.a) { panBy(step, 0); moved = true; } if (keys.arrowright || keys.d) { panBy(-step, 0); moved = true; }
    if (keys.arrowup || keys.w) { panBy(0, step); moved = true; } if (keys.arrowdown || keys.s) { panBy(0, -step); moved = true; }
    if (keys.q) { want.yaw += 0.012; moved = true; } if (keys.e) { want.yaw -= 0.012; moved = true; }
    if (keys['+'] || keys['=']) { want.dist *= 0.985; moved = true; } if (keys['-']) { want.dist *= 1.015; moved = true; }
    if (moved) { limit(want); lastTouch = performance.now(); }
  }

  function frame(t) {
    raf = requestAnimationFrame(frame);
    keyboard();
    // Au repos, l'île se balance doucement, comme vue depuis un bateau.
    var idle = t - lastTouch > 9000, sway = idle ? Math.sin(t / 5200) * 0.07 : 0;
    ['yaw', 'pitch', 'dist', 'x', 'y', 'z'].forEach(function (k) { cam[k] += ((want[k] + (k === 'yaw' ? sway : 0)) - cam[k]) * 0.1; });
    var d = cam.dist * base, target = new THREE.Vector3(cam.x, cam.y, cam.z);
    camera.position.set(target.x + Math.sin(cam.yaw) * Math.cos(cam.pitch) * d, target.y - Math.sin(cam.pitch) * d, target.z + Math.cos(cam.yaw) * Math.cos(cam.pitch) * d);
    camera.up.set(0, 1, 0); camera.lookAt(target);
    camera.updateMatrixWorld();
    var tt = (t - t0) / 1000;
    if (ocean) { ocean.material.uniforms.t.value = tt; ocean.material.uniforms.cam.value.copy(camera.position); }
    if (sky) { sky.position.copy(camera.position); sky.material.uniforms.t.value = tt; }
    renderer.render(scene, camera);
    if (onFrame) onFrame();
  }

  var fwd = null;
  window.Relief = {
    supported: function () { try { var c = document.createElement('canvas'); return !!(c.getContext('webgl') || c.getContext('experimental-webgl')); } catch (e) { return false; } },
    get active() { return on; },
    start: function (cb) {
      onFrame = cb;
      function go() {
        if (!renderer) { build(); home(); cam = Object.assign({}, want, { dist: 1.25, pitch: 0.35 }); }
        on = true; renderer.domElement.style.display = 'block'; lastTouch = performance.now();
        if (!raf) raf = requestAnimationFrame(frame);
      }
      if (window.THREE && depth) return go();
      var pending = 2; function one() { if (--pending === 0) go(); }
      load(one);
      if (window.THREE) one(); else { var s = document.createElement('script'); s.src = '/static/vendor/three.min.js'; s.onload = one; document.head.appendChild(s); }
    },
    stop: function () { on = false; if (raf) cancelAnimationFrame(raf); raf = 0; if (renderer) renderer.domElement.style.display = 'none'; },
    // Où se trouve, à l'écran, le point (x, y) de l'image ; (sx, sy) : où lire la profondeur, si ce n'est pas au même endroit.
    // k : nombre de pixels d'écran pour un pixel de l'image à cet endroit (pour la taille des étiquettes).
    project: function (x, y, sx, sy) {
      if (!camera) return null;
      var p = world(x, y, sx, sy), v = p.clone().project(camera);
      fwd = fwd || new THREE.Vector3(); camera.getWorldDirection(fwd);
      var dz = p.clone().sub(camera.position).dot(fwd);
      return { x: (v.x + 1) / 2 * innerWidth, y: (1 - v.y) / 2 * innerHeight, k: innerHeight / 2 / (Math.tan(camera.fov * Math.PI / 360) * dz), visible: dz > 0 && v.z < 1 };
    },
    // Va voir de près un endroit de l'île (un Chubby qu'on vient de choisir).
    focus: function (x, y) {
      if (!camera) return;
      var p = world(x, y); want.x = p.x; want.y = p.y + 40; want.z = p.z; want.dist = 0.42; want.pitch = Math.max(want.pitch, 0.22); limit(want); lastTouch = performance.now();
    },
    home: function () { home(); lastTouch = performance.now(); }
  };
})();
