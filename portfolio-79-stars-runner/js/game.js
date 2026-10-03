/* ===================================================================
 * Subway Runner 3D — Authentic Subway Surfers Engine (Three.js r128)
 *
 * Features:
 * - Vibrant daylight railway environment (realistic steel rails, wooden sleepers, stone ballast).
 * - Full 3D .glb models: Surfer, Characters, Train, Hoverboard, Jetpack.
 * - Procedural character animation: running stride, landing squash, jump arch, rolling slide, hoverboard surfing.
 * - Power-ups: 🛹 Hoverboard (crash shield), 🧲 Coin Magnet, 🚀 Jetpack (flying coin trail), 👟 Super Sneakers, 2X Multiplier.
 * - Zero-asset procedural Web Audio synthesizer (jump, coin chime, slide swoosh, fanfare, crash).
 * - Natural mobile touch gestures: Swipe Up (jump), Swipe Down (slide/fast-fall), Swipe Left/Right (lane change), Double-Tap (hoverboard).
 * - High-performance pooling and zero per-frame garbage collection (60 FPS).
 * =================================================================== */

(function () {
  'use strict';

  // --- Constants & World Coordinates ---
  var LANES = [-2.0, 0.0, 2.0];
  var SPAWN_Z = -105.0;
  var KILL_Z = 14.0;

  // Physics Tuning
  var BASE_SPEED = 12.0;
  var MAX_SPEED = 30.0;
  var SPEED_UP_EVERY = 15.0;
  var SPEED_UP_FACTOR = 1.08;
  var JUMP_V = 14.5;
  var SUPER_JUMP_V = 19.5;
  var GRAVITY = 32.0;
  var JUMP_BUFFER = 0.16;
  var LANE_LERP = 13.0;
  var PLAYER_HX = 0.35;
  var SLIDE_TIME = 0.70;

  // Dimensions
  var H_PLAYER = 1.35;
  var TRAIN_H = 2.7;
  var TRAIN_W = 1.75;
  var TRAIN_LEN = 11.0;
  var TILE_LEN = 24.0;
  var FLOOR_TILES = 10;

  // Pools
  var MAX_OBSTACLES = 36;
  var MAX_COINS = 80;
  var MAX_POWERUPS = 12;
  var MAX_PARTICLES = 50;

  // ==========================================
  // Web Audio Synthesizer (Zero asset sound + Mobile Touch Unlock)
  // ==========================================
  var AudioFX = (function () {
    var ctx = null;
    var muted = false;
    var jetOsc = null;
    var jetGain = null;

    function getContext() {
      if (!ctx && (window.AudioContext || window.webkitAudioContext)) {
        try {
          var AudioContextClass = window.AudioContext || window.webkitAudioContext;
          ctx = new AudioContextClass();
        } catch (e) {}
      }
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(function () {});
      }
      return ctx;
    }

    function unlock() {
      var c = getContext();
      if (c) {
        if (c.state === 'suspended') c.resume().catch(function () {});
        try {
          var b = c.createBuffer(1, 1, 22050);
          var s = c.createBufferSource();
          s.buffer = b;
          s.connect(c.destination);
          s.start(0);
        } catch (e) {}
      }
    }
    window.addEventListener('touchstart', unlock, { once: true, passive: true });
    window.addEventListener('click', unlock, { once: true, passive: true });

    function playTone(freqStart, freqEnd, type, duration, gainLevel) {
      if (muted) return;
      var c = getContext();
      if (!c) return;
      var now = c.currentTime;

      var osc = c.createOscillator();
      var gain = c.createGain();
      osc.type = type || 'sine';

      osc.frequency.setValueAtTime(freqStart, now);
      if (freqEnd && freqEnd !== freqStart) {
        osc.frequency.exponentialRampToValueAtTime(Math.max(10, freqEnd), now + duration);
      }

      var lvl = gainLevel || 0.15;
      gain.gain.setValueAtTime(lvl, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

      osc.connect(gain);
      gain.connect(c.destination);

      osc.start(now);
      osc.stop(now + duration);
    }

    var coinNotes = [523.25, 659.25, 783.99, 987.77, 1046.50, 1318.51, 1567.98];
    var coinNoteIdx = 0;
    var lastCoinTime = 0;

    return {
      toggleMute: function () {
        muted = !muted;
        if (muted) this.stopJetpack();
        return muted;
      },
      isMuted: function () { return muted; },

      coin: function () {
        var now = performance.now();
        if (now - lastCoinTime < 500) {
          coinNoteIdx = (coinNoteIdx + 1) % coinNotes.length;
        } else {
          coinNoteIdx = 0;
        }
        lastCoinTime = now;
        var f = coinNotes[coinNoteIdx];
        playTone(f, f * 1.02, 'sine', 0.11, 0.18);
        playTone(f * 2.4, f * 2.42, 'triangle', 0.07, 0.08);
      },

      jump: function () {
        playTone(200, 560, 'sine', 0.16, 0.22);
        playTone(400, 720, 'triangle', 0.08, 0.08);
      },

      slide: function () {
        playTone(290, 85, 'triangle', 0.22, 0.22);
      },

      powerup: function () {
        var notes = [440, 554, 659, 880];
        notes.forEach(function (f, i) {
          setTimeout(function () { playTone(f, f * 1.04, 'triangle', 0.14, 0.14); }, i * 65);
        });
      },

      board: function () {
        playTone(260, 720, 'sawtooth', 0.22, 0.14);
      },

      shieldBreak: function () {
        playTone(650, 160, 'sawtooth', 0.26, 0.24);
        playTone(900, 220, 'square', 0.18, 0.18);
      },

      crash: function () {
        playTone(160, 25, 'square', 0.38, 0.35);
        playTone(90, 20, 'sawtooth', 0.42, 0.25);
      },

      stumble: function () {
        playTone(210, 80, 'sawtooth', 0.20, 0.25);
        playTone(140, 50, 'triangle', 0.25, 0.20);
      },

      whistle: function () {
        playTone(1800, 1950, 'sine', 0.12, 0.14);
        setTimeout(function () { playTone(2100, 2250, 'sine', 0.16, 0.16); }, 75);
      },

      dogBark: function () {
        if (muted) return;
        playTone(360, 180, 'sawtooth', 0.12, 0.22);
        setTimeout(function () {
          playTone(330, 150, 'sawtooth', 0.10, 0.18);
        }, 110);
      },

      startJetpack: function () {
        if (muted || jetOsc) return;
        var c = getContext();
        if (!c) return;
        try {
          jetOsc = c.createOscillator();
          jetGain = c.createGain();
          jetOsc.type = 'sawtooth';
          jetOsc.frequency.setValueAtTime(80, c.currentTime);
          jetGain.gain.setValueAtTime(0.01, c.currentTime);
          jetGain.gain.linearRampToValueAtTime(0.12, c.currentTime + 0.3);
          jetOsc.connect(jetGain);
          jetGain.connect(c.destination);
          jetOsc.start();
        } catch (e) {}
      },

      mysteryBox: function () {
        if (muted) return;
        var c = getContext();
        if (!c) return;
        var now = c.currentTime;
        var notes = [523.25, 659.25, 783.99, 1046.50, 1318.51];
        for (var i = 0; i < notes.length; i++) {
          playTone(notes[i], notes[i] * 1.05, 'triangle', 0.18, 0.18);
        }
      },

      stopJetpack: function () {
        if (!jetOsc) return;
        try {
          var c = getContext();
          if (c && jetGain) {
            jetGain.gain.linearRampToValueAtTime(0.0001, c.currentTime + 0.2);
          }
          var oldOsc = jetOsc;
          setTimeout(function () {
            try { oldOsc.stop(); oldOsc.disconnect(); } catch (e) {}
          }, 250);
        } catch (e) {}
        jetOsc = null;
        jetGain = null;
      },

      trainHorn: function () {
        if (muted) return;
        playTone(311.13, 311.13, 'sawtooth', 0.45, 0.16);
        playTone(369.99, 369.99, 'sawtooth', 0.45, 0.14);
      },

      fanfare: function () {
        if (muted) return;
        var notes = [523.25, 659.25, 783.99, 1046.50, 1318.51];
        notes.forEach(function (f, idx) {
          setTimeout(function () {
            playTone(f, f * 1.02, 'triangle', 0.22, 0.20);
            playTone(f * 1.5, f * 1.52, 'sine', 0.18, 0.14);
          }, idx * 110);
        });
      }
    };
  })();

  // ==========================================
  // Game Engine State
  // ==========================================
  var G = {
    inited: false,
    state: 'idle', // 'idle' | 'playing' | 'over'
    score: 0.0,
    coins: 0,
    boxesCollected: 0,
    speed: BASE_SPEED,
    elapsed: 0.0,
    level: 0,

    // Lane and vertical physics
    lane: 1,
    targetX: 0.0,
    py: 0.9,
    vy: 0.0,
    grounded: true,
    sliding: 0.0,
    jumpBuf: 0.0,
    spawnAcc: 0.0,
    clock: 0,
    cb: {},
    bestScore: 0,
    newRecordAnnounced: false,

    // Active Character (Jake is the sole hero)
    characterIndex: 0,
    characters: ['jake'],

    // Active Powerups
    powerups: {
      magnet: 0.0,
      jetpack: 0.0,
      sneakers: 0.0,
      multiplier: 0.0,
      hoverboard: 0.0,
      hasShield: false,
    },

    // Three.js instances
    renderer: null,
    scene: null,
    camera: null,
    player: null,
    playerModelWrap: null,
    playerShadow: null,
    hoverboardMesh: null,
    jetpackMesh: null,
    shieldMesh: null,
    jetpackLight: null,
    shake: 0.0,
    guard: null,
    guardParts: null,
    guardX: 0.0,
    guardChasing: false,
    guardChaseTimer: 0.0,
    skyCoinAcc: 0.0,
    distSinceLastBox: 0.0,
    introTimer: 0.0,
    guardMixer: null,
    guardActions: { run: null, dogRun: null, intro: null, catch: null },

    // 3D Prototypes
    trainProto: null,
    trainBaseLen: TRAIN_LEN,
    hoverboardProto: null,
    jetpackProto: null,
    mysteryBoxProto: null,
    characterModels: [],
    animMixer: null,
    animActions: { run: null, jump: null, slide: null, fall: null, taunt: null },

    // Object Pools
    obstaclePool: [],
    coinPool: [],
    powerupPool: [],
    mysteryBoxPool: [],
    particlePool: [],
    floors: [],
    decorations: [],
    geo: {},
    mat: {},
  };

  function triggerCallback(name, a, b, c, d) {
    if (G.cb[name]) {
      try { G.cb[name](a, b, c, d); } catch (e) { console.error('RunnerGame cb error:', e); }
    }
  }

  // ==========================================
  // Initialization & Scene Construction
  // ==========================================
  function init(canvas, callbacks) {
    if (G.inited) return;
    G.cb = callbacks || {};

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        antialias: true,
        powerPreference: 'high-performance',
      });
    } catch (e) {
      triggerCallback('onError', 'WebGL faollashtirilmadi');
      return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    G.renderer = renderer;

    var scene = new THREE.Scene();
    // Vibrant daytime sky color and soft distance fog
    scene.background = new THREE.Color(0x72c4ff);
    scene.fog = new THREE.Fog(0x8bd2fc, 45, 135);
    G.scene = scene;

    // Soft dynamic contact shadow decal under Jake's feet
    var shadowCanvas = document.createElement('canvas');
    shadowCanvas.width = 64;
    shadowCanvas.height = 64;
    var sctx = shadowCanvas.getContext('2d');
    var grad = sctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(0, 0, 0, 0.7)');
    grad.addColorStop(0.5, 'rgba(0, 0, 0, 0.35)');
    grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    sctx.fillStyle = grad;
    sctx.fillRect(0, 0, 64, 64);
    var shadowTex = new THREE.CanvasTexture(shadowCanvas);
    var shadowGeo = new THREE.PlaneGeometry(1.3, 1.3);
    shadowGeo.rotateX(-Math.PI / 2);
    var shadowMat = new THREE.MeshBasicMaterial({
      map: shadowTex,
      transparent: true,
      depthWrite: false,
      opacity: 0.75,
    });
    var pShadow = new THREE.Mesh(shadowGeo, shadowMat);
    pShadow.position.set(0, 0.03, 0);
    scene.add(pShadow);
    G.playerShadow = pShadow;

    // Camera: behind and above
    var camera = new THREE.PerspectiveCamera(62, 1, 0.1, 300);
    camera.position.set(0, 4.6, 7.4);
    camera.lookAt(0, 1.2, -10);
    G.camera = camera;

    // Warm daylight lighting (Subway Surfers sunny aesthetic)
    var hemiLight = new THREE.HemisphereLight(0xffffff, 0x5a758e, 0.85);
    scene.add(hemiLight);

    var sunLight = new THREE.DirectionalLight(0xfff7e6, 1.15);
    sunLight.position.set(10, 20, 8);
    scene.add(sunLight);

    var bounceLight = new THREE.DirectionalLight(0xbde0fe, 0.4);
    bounceLight.position.set(-10, 8, -10);
    scene.add(bounceLight);

    buildSharedAssets();
    buildRailwayEnvironment();
    buildPlayer();
    buildGuard();
    buildTrackPools();
    bindTouchGestures(canvas);
    resize();

    window.addEventListener('resize', resize);

    G.inited = true;
    G.clock = performance.now();
    load3DModels();
    requestAnimationFrame(renderLoop);
  }

  // ==========================================
  // Authentic Railway Assets & Shading
  // ==========================================
  function buildSharedAssets() {
    G.geo.box = new THREE.BoxGeometry(1, 1, 1);
    G.geo.cylinder = new THREE.CylinderGeometry(0.38, 0.38, 0.12, 16);
    G.geo.cylinder.rotateX(Math.PI / 2);
    G.geo.sphere = new THREE.SphereGeometry(0.35, 8, 8);

    // Track geometries
    G.geo.rail = new THREE.BoxGeometry(0.12, 0.14, TILE_LEN);
    G.geo.sleeper = new THREE.BoxGeometry(2.4, 0.12, 0.32);
    G.geo.ballast = new THREE.PlaneGeometry(10, TILE_LEN);

    // Realistic Materials (no neon!)
    G.mat.ballast = new THREE.MeshLambertMaterial({ color: 0x3d444b }); // Gravel ballast bed
    G.mat.sleeper = new THREE.MeshLambertMaterial({ color: 0x54341b }); // Wooden railway ties
    G.mat.rail = new THREE.MeshStandardMaterial({ color: 0xc8d1d9, roughness: 0.3, metalness: 0.7 }); // Shiny steel rails
    G.mat.wall = new THREE.MeshLambertMaterial({ color: 0x756b63 }); // Concrete/brick side walls
    G.mat.wallTrim = new THREE.MeshLambertMaterial({ color: 0xe65100 }); // Orange accent stripe

    // Procedural hazard stripe generator for hurdles and barriers
    function makeStripeTex(col1, col2, borderCol) {
      var c = document.createElement('canvas');
      c.width = 256; c.height = 64;
      var cx = c.getContext('2d');
      cx.fillStyle = col1;
      cx.fillRect(0, 0, 256, 64);
      cx.fillStyle = col2;
      var w = 28;
      for (var x = -64; x < 320; x += w * 2) {
        cx.beginPath();
        cx.moveTo(x, 0);
        cx.lineTo(x + 48, 64);
        cx.lineTo(x + 48 + w, 64);
        cx.lineTo(x + w, 0);
        cx.closePath();
        cx.fill();
      }
      if (borderCol) {
        cx.strokeStyle = borderCol;
        cx.lineWidth = 4;
        cx.strokeRect(2, 2, 252, 60);
      }
      var t = new THREE.CanvasTexture(c);
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.ClampToEdgeWrapping;
      return t;
    }

    // Upgraded Authentic Obstacle Materials
    G.mat.hurdleStripe = new THREE.MeshLambertMaterial({
      map: makeStripeTex('#d32f2f', '#ffffff', '#991b1b')
    });
    G.mat.cautionStripe = new THREE.MeshLambertMaterial({
      map: makeStripeTex('#ffb300', '#1e293b', '#0f172a')
    });
    G.mat.hazardBeacon = new THREE.MeshLambertMaterial({
      color: 0xffa000,
      emissive: 0xff6f00
    });
    G.mat.steelPost = new THREE.MeshStandardMaterial({
      color: 0x94a3b8,
      metalness: 0.6,
      roughness: 0.35
    });
    G.mat.safetyOrange = new THREE.MeshLambertMaterial({
      color: 0xf97316
    });
    G.mat.steelDark = new THREE.MeshStandardMaterial({
      color: 0x334155,
      metalness: 0.5,
      roughness: 0.4
    });

    // Obstacle helper geometries
    G.geo.beaconCyl = new THREE.CylinderGeometry(0.08, 0.09, 0.14, 12);
    G.geo.beaconPlate = new THREE.CylinderGeometry(0.12, 0.12, 0.04, 12);
    G.geo.plaque = new THREE.BoxGeometry(0.60, 0.28, 0.04);
    G.geo.gusset = new THREE.BoxGeometry(0.20, 0.20, 0.08);

    G.mat.barrierRed = new THREE.MeshLambertMaterial({ color: 0xd32f2f });
    G.mat.barrierWhite = new THREE.MeshLambertMaterial({ color: 0xffffff });
    G.mat.warningYellow = new THREE.MeshLambertMaterial({ color: 0xffb300 });
    G.mat.warningDark = new THREE.MeshLambertMaterial({ color: 0x212121 });
    G.mat.trainFallback = new THREE.MeshLambertMaterial({ color: 0x1565c0 }); // Train blue fallback


    // Collectibles & Powerups Materials
    G.mat.goldCoin = new THREE.MeshStandardMaterial({ color: 0xffc107, metalness: 0.8, roughness: 0.25 });
    G.mat.magnet = new THREE.MeshLambertMaterial({ color: 0xe53935 });
    G.mat.jetpack = new THREE.MeshLambertMaterial({ color: 0x03a9f4 });
    G.mat.sneaker = new THREE.MeshLambertMaterial({ color: 0x4caf50 });
    G.mat.board = new THREE.MeshLambertMaterial({ color: 0x9c27b0 });

    // Mystery Gift Box (Sovg'a qutisi)
    G.geo.mysteryBox = new THREE.BoxGeometry(0.85, 0.85, 0.85);
    G.geo.ribbonH = new THREE.BoxGeometry(0.88, 0.88, 0.22);
    G.geo.ribbonV = new THREE.BoxGeometry(0.22, 0.88, 0.88);
    G.geo.ribbonKnot = new THREE.TorusGeometry(0.14, 0.05, 8, 16);
    G.geo.ribbonKnot.rotateX(Math.PI / 2);

    G.mat.mysteryBox = new THREE.MeshStandardMaterial({
      color: 0xee2737,
      roughness: 0.35,
      metalness: 0.15,
    });
    G.mat.boxRibbon = new THREE.MeshStandardMaterial({
      color: 0xffd700,
      roughness: 0.25,
      metalness: 0.65,
    });
    G.mat.boxHalo = new THREE.MeshBasicMaterial({
      color: 0xffea00,
      transparent: true,
      opacity: 0.45,
      wireframe: true,
    });

    // VFX Particle Geometries & Materials (Jetpack fire, smoke, hover sparks, coin sparkles, dust)
    G.geo.particle = new THREE.PlaneGeometry(0.22, 0.22);
    G.mat.fire = new THREE.MeshBasicMaterial({ color: 0xff4500, transparent: true, opacity: 0.95 });
    G.mat.fireYellow = new THREE.MeshBasicMaterial({ color: 0xffcc00, transparent: true, opacity: 0.95 });
    G.mat.smoke = new THREE.MeshBasicMaterial({ color: 0x999999, transparent: true, opacity: 0.45 });
    G.mat.spark = new THREE.MeshBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.95 });
    G.mat.goldSpark = new THREE.MeshBasicMaterial({ color: 0xffd700, transparent: true, opacity: 0.95 });
    G.mat.dust = new THREE.MeshBasicMaterial({ color: 0xc4b59d, transparent: true, opacity: 0.65 });
    G.mat.windStreak = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55 });

    // Procedural Player Fallback Materials
    G.mat.playerSkin = new THREE.MeshLambertMaterial({ color: 0xffd1a4 });
    G.mat.playerShirt = new THREE.MeshLambertMaterial({ color: 0xffffff });
    G.mat.playerHoodie = new THREE.MeshLambertMaterial({ color: 0x0288d1 });
    G.mat.playerJeans = new THREE.MeshLambertMaterial({ color: 0x283593 });
    G.mat.playerCap = new THREE.MeshLambertMaterial({ color: 0xd32f2f });

    // Nostalgic Retro Railway Urban Scenery Materials
    G.mat.bldgBrick = new THREE.MeshLambertMaterial({ color: 0x9b3a2b });   // Classic red brick
    G.mat.bldgOchre = new THREE.MeshLambertMaterial({ color: 0xc98630 });   // Warm vintage ochre
    G.mat.bldgTeal  = new THREE.MeshLambertMaterial({ color: 0x2e6070 });   // Retro subway teal
    G.mat.bldgSand  = new THREE.MeshLambertMaterial({ color: 0xba9570 });   // Urban sandstone / brownstone
    G.mat.bldgOlive = new THREE.MeshLambertMaterial({ color: 0x486349 });   // Retro olive green
    G.mat.bldgRoof  = new THREE.MeshLambertMaterial({ color: 0x262a30 });   // Dark slate parapet / roof
    G.mat.bldgVent  = new THREE.MeshLambertMaterial({ color: 0x616a75 });   // Rooftop AC / industrial vent
    G.mat.waterWood = new THREE.MeshLambertMaterial({ color: 0x5a3825 });   // Wooden water tank
    G.mat.waterLegs = new THREE.MeshLambertMaterial({ color: 0x212529 });   // Water tank metal legs
    G.mat.winWarm   = new THREE.MeshBasicMaterial({ color: 0xffd966 });     // Lit warm window
    G.mat.winDark   = new THREE.MeshBasicMaterial({ color: 0x1a2636 });     // Dark glass window
    G.mat.winTrim   = new THREE.MeshLambertMaterial({ color: 0xd0d7de });   // Window sill / lintel
    G.mat.boardRed  = new THREE.MeshLambertMaterial({ color: 0xd8261e });   // Retro billboard frame
    G.mat.boardFace = new THREE.MeshBasicMaterial({ color: 0xffeb3b });     // Billboard poster face
    G.mat.treeLeaves= new THREE.MeshLambertMaterial({ color: 0x2e7d32 });   // Trackside foliage / trees
    G.mat.treeTrunk = new THREE.MeshLambertMaterial({ color: 0x4e342e });   // Tree bark trunk

    // Scenery Geometries (pre-allocated once for 60 FPS zero-allocation scrolling)
    G.geo.winPane   = new THREE.PlaneGeometry(0.7, 1.1);
    G.geo.winSill   = new THREE.BoxGeometry(0.85, 0.12, 0.2);
    G.geo.waterTank = new THREE.CylinderGeometry(1.1, 1.1, 1.8, 12);
    G.geo.waterCone = new THREE.ConeGeometry(1.2, 0.7, 12);
    G.geo.treeCone  = new THREE.ConeGeometry(1.2, 2.8, 7);
  }

  function buildRailwayEnvironment() {
    // 10 Scrolling ground tiles
    for (var i = 0; i < FLOOR_TILES; i++) {
      var tileGroup = new THREE.Group();
      tileGroup.position.set(0, 0, 8 - i * TILE_LEN);

      // Gravel roadbed
      var ballast = new THREE.Mesh(G.geo.ballast, G.mat.ballast);
      ballast.rotation.x = -Math.PI / 2;
      tileGroup.add(ballast);

      // Steel Rails for 3 lanes
      for (var l = 0; l < 3; l++) {
        var lx = LANES[l];
        // Left rail
        var railL = new THREE.Mesh(G.geo.rail, G.mat.rail);
        railL.position.set(lx - 0.55, 0.08, 0);
        tileGroup.add(railL);
        // Right rail
        var railR = new THREE.Mesh(G.geo.rail, G.mat.rail);
        railR.position.set(lx + 0.55, 0.08, 0);
        tileGroup.add(railR);

        // Wooden Sleepers along track (every 1.5m)
        var numSleepers = Math.floor(TILE_LEN / 1.5);
        for (var s = 0; s < numSleepers; s++) {
          var sleeper = new THREE.Mesh(G.geo.sleeper, G.mat.sleeper);
          sleeper.position.set(lx, 0.02, -TILE_LEN / 2 + s * 1.5);
          tileGroup.add(sleeper);
        }
      }

      // Concrete Subway Side Walls with orange warning trims
      var wallGeo = new THREE.BoxGeometry(0.5, 4.2, TILE_LEN);
      var trimGeo = new THREE.BoxGeometry(0.55, 0.3, TILE_LEN);

      var wallL = new THREE.Mesh(wallGeo, G.mat.wall);
      wallL.position.set(-4.8, 2.0, 0);
      tileGroup.add(wallL);
      var trimL = new THREE.Mesh(trimGeo, G.mat.wallTrim);
      trimL.position.set(-4.8, 3.8, 0);
      tileGroup.add(trimL);

      var wallR = new THREE.Mesh(wallGeo, G.mat.wall);
      wallR.position.set(4.8, 2.0, 0);
      tileGroup.add(wallR);
      var trimR = new THREE.Mesh(trimGeo, G.mat.wallTrim);
      trimR.position.set(4.8, 3.8, 0);
      tileGroup.add(trimR);

      // Overhead Railway Gantries every tile
      var gantry = new THREE.Group();
      var postL = new THREE.Mesh(G.geo.box, G.mat.wall);
      postL.scale.set(0.3, 5.2, 0.3); postL.position.set(-4.3, 2.6, 0);
      var postR = new THREE.Mesh(G.geo.box, G.mat.wall);
      postR.scale.set(0.3, 5.2, 0.3); postR.position.set(4.3, 2.6, 0);
      var beam = new THREE.Mesh(G.geo.box, G.mat.wallTrim);
      beam.scale.set(9.0, 0.35, 0.35); beam.position.set(0, 5.1, 0);
      gantry.add(postL); gantry.add(postR); gantry.add(beam);
      tileGroup.add(gantry);

      // Nostalgic City Buildings & Retro Subway Scenery (zero-allocation scrolling)
      var bldgMats = [G.mat.bldgBrick, G.mat.bldgOchre, G.mat.bldgTeal, G.mat.bldgSand, G.mat.bldgOlive];
      var sides = [-1, 1]; // Left and Right of tracks
      var zOffsets = [-5.8, 5.8]; // Two retro townhouses per side

      for (var sIdx = 0; sIdx < 2; sIdx++) {
        var side = sides[sIdx];
        var bldgX = side * 9.2;

        for (var zIdx = 0; zIdx < 2; zIdx++) {
          var bz = zOffsets[zIdx];
          var seed = (i * 4 + sIdx * 2 + zIdx);
          var bH = 8.8 + (seed % 5) * 1.5; // Heights: 8.8m to 14.8m tall
          var bMat = bldgMats[seed % bldgMats.length];

          // Townhouse Main Block
          var bldgMesh = new THREE.Mesh(G.geo.box, bMat);
          bldgMesh.scale.set(8.0, bH, 10.6);
          bldgMesh.position.set(bldgX, bH / 2, bz);
          tileGroup.add(bldgMesh);

          // Dark Slate Parapet / Roof Border
          var roofMesh = new THREE.Mesh(G.geo.box, G.mat.bldgRoof);
          roofMesh.scale.set(8.3, 0.45, 10.9);
          roofMesh.position.set(bldgX, bH + 0.22, bz);
          tileGroup.add(roofMesh);

          // Multi-floor Windows facing the tracks
          var numFloors = Math.floor((bH - 4.2) / 2.2);
          for (var fl = 0; fl < numFloors; fl++) {
            var wy = 4.6 + fl * 2.2;
            for (var col = -1; col <= 1; col += 2) {
              var wz = bz + col * 2.6;
              var isLit = ((seed + fl + col) % 3 !== 0);
              var winMat = isLit ? G.mat.winWarm : G.mat.winDark;

              var winMesh = new THREE.Mesh(G.geo.winPane, winMat);
              winMesh.position.set(side * 5.18, wy, wz);
              winMesh.rotation.y = side * (Math.PI / 2);
              tileGroup.add(winMesh);

              var sill = new THREE.Mesh(G.geo.winSill, G.mat.winTrim);
              sill.position.set(side * 5.19, wy - 0.58, wz);
              sill.rotation.y = side * (Math.PI / 2);
              tileGroup.add(sill);
            }
          }

          // Authentic Nostalgic Rooftop Accessories
          var propSeed = seed % 3;
          if (propSeed === 0) {
            // Rooftop Wooden Water Tank on stilts (classic Subway Surfers signature)
            var waterGrp = new THREE.Group();
            waterGrp.position.set(bldgX, bH, bz);

            var legs = new THREE.Mesh(G.geo.box, G.mat.waterLegs);
            legs.scale.set(1.8, 1.2, 1.8);
            legs.position.set(0, 0.6, 0);
            waterGrp.add(legs);

            var tank = new THREE.Mesh(G.geo.waterTank, G.mat.waterWood);
            tank.position.set(0, 2.1, 0);
            waterGrp.add(tank);

            var cap = new THREE.Mesh(G.geo.waterCone, G.mat.bldgRoof);
            cap.position.set(0, 3.35, 0);
            waterGrp.add(cap);

            tileGroup.add(waterGrp);
          } else if (propSeed === 1) {
            // Retro Rooftop Billboard
            var boardGrp = new THREE.Group();
            boardGrp.position.set(side * 5.6, bH + 1.2, bz);

            var frame = new THREE.Mesh(G.geo.box, G.mat.boardRed);
            frame.scale.set(0.2, 2.2, 4.4);
            boardGrp.add(frame);

            var poster = new THREE.Mesh(G.geo.box, G.mat.boardFace);
            poster.scale.set(0.24, 1.9, 4.0);
            poster.position.set(-side * 0.02, 0, 0);
            boardGrp.add(poster);

            tileGroup.add(boardGrp);
          } else {
            // Industrial Rooftop Ventilation Unit
            var duct = new THREE.Mesh(G.geo.box, G.mat.bldgVent);
            duct.scale.set(2.2, 1.2, 2.6);
            duct.position.set(bldgX, bH + 0.6, bz);
            tileGroup.add(duct);
          }
        }

        // Urban Greenery Tree peeking over the side subway wall
        var treeTrunk = new THREE.Mesh(G.geo.box, G.mat.treeTrunk);
        treeTrunk.scale.set(0.4, 4.6, 0.4);
        treeTrunk.position.set(side * 5.5, 2.3, 0);
        tileGroup.add(treeTrunk);

        var treeFoliage = new THREE.Mesh(G.geo.treeCone, G.mat.treeLeaves);
        treeFoliage.scale.set(1.5, 2.8, 1.5);
        treeFoliage.position.set(side * 5.5, 5.2, 0);
        tileGroup.add(treeFoliage);
      }

      G.scene.add(tileGroup);
      G.floors.push(tileGroup);
    }
  }

  // ==========================================
  // Character System & Procedural Limbs
  // ==========================================
  function createLimb(w, h, d, mat, originTop) {
    var geo = new THREE.BoxGeometry(w, h, d);
    if (originTop) geo.translate(0, -h / 2, 0);
    return new THREE.Mesh(geo, mat);
  }

  function buildPlayer() {
    var root = new THREE.Group();
    root.position.set(0, 0.9, 0);

    // Wrapper for loaded GLB or procedural model
    var wrap = new THREE.Group();
    root.add(wrap);

    // Procedural Subway Surfer fallback figure (Jake style)
    var procFig = new THREE.Group();

    var torso = createLimb(0.50, 0.65, 0.28, G.mat.playerHoodie);
    torso.position.set(0, 0.38, 0);

    var head = createLimb(0.34, 0.34, 0.32, G.mat.playerSkin);
    head.position.set(0, 0.90, 0);

    // Backwards red baseball cap
    var cap = createLimb(0.36, 0.12, 0.36, G.mat.playerCap);
    cap.position.set(0, 1.05, 0);
    var capBrim = createLimb(0.34, 0.04, 0.20, G.mat.playerCap);
    capBrim.position.set(0, 1.02, 0.24); // Facing backwards

    var legL = createLimb(0.20, 0.70, 0.22, G.mat.playerJeans, true);
    legL.position.set(-0.15, 0.06, 0);
    var legR = createLimb(0.20, 0.70, 0.22, G.mat.playerJeans, true);
    legR.position.set(0.15, 0.06, 0);

    var armL = createLimb(0.16, 0.65, 0.18, G.mat.playerHoodie, true);
    armL.position.set(-0.35, 0.65, 0);
    var armR = createLimb(0.16, 0.65, 0.18, G.mat.playerHoodie, true);
    armR.position.set(0.35, 0.65, 0);

    procFig.add(torso);
    procFig.add(head);
    procFig.add(cap);
    procFig.add(capBrim);
    procFig.add(legL);
    procFig.add(legR);
    procFig.add(armL);
    procFig.add(armR);

    wrap.add(procFig);
    wrap.userData.procFig = procFig;
    wrap.userData.limbs = { legL: legL, legR: legR, armL: armL, armR: armR, head: head };

    // Attached Hoverboard slot
    var boardSlot = new THREE.Group();
    boardSlot.position.set(0, -0.85, 0);
    boardSlot.visible = false;
    root.add(boardSlot);

    // Attached Jetpack slot (mounted snugly on Jake's upper back/shoulders)
    var jetpackSlot = new THREE.Group();
    jetpackSlot.position.set(0, 0.12, 0.18);
    jetpackSlot.rotation.x = 0.05;
    jetpackSlot.visible = false;
    root.add(jetpackSlot);

    // Attached Hoverboard Energy Forcefield Shield
    var shieldGeo = new THREE.SphereGeometry(1.05, 16, 12);
    var shieldMat = new THREE.MeshBasicMaterial({
      color: 0x00f0ff,
      transparent: true,
      opacity: 0.25,
      wireframe: true
    });
    var shield = new THREE.Mesh(shieldGeo, shieldMat);
    shield.position.set(0, 0.45, 0);
    shield.visible = false;
    root.add(shield);

    // Attached Jetpack Dynamic Fire Light
    var jetLight = new THREE.PointLight(0xff6600, 0, 7.0);
    jetLight.position.set(0, 0.12, 0.38);
    root.add(jetLight);

    G.scene.add(root);
    G.player = root;
    G.playerModelWrap = wrap;
    G.hoverboardMesh = boardSlot;
    G.jetpackMesh = jetpackSlot;
    G.shieldMesh = shield;
    G.jetpackLight = jetLight;
  }

  function buildGuard() {
    var grp = new THREE.Group();
    var procGuard = new THREE.Group();

    var navy = new THREE.MeshLambertMaterial({ color: 0x1a237e });
    var skin = G.mat.playerSkin;
    var dark = new THREE.MeshLambertMaterial({ color: 0x111111 });

    var legL = createLimb(0.24, 0.95, 0.26, navy, true); legL.position.set(-0.16, 1.0, 0);
    var legR = createLimb(0.24, 0.95, 0.26, navy, true); legR.position.set(0.16, 1.0, 0);
    var torso = createLimb(0.65, 0.75, 0.38, navy); torso.position.set(0, 1.38, 0);
    var armL = createLimb(0.18, 0.72, 0.20, navy, true); armL.position.set(-0.42, 1.66, 0);
    var armR = createLimb(0.18, 0.72, 0.20, navy, true); armR.position.set(0.42, 1.66, 0);
    var head = createLimb(0.38, 0.38, 0.36, skin); head.position.set(0, 1.95, 0);
    var cap = createLimb(0.42, 0.15, 0.40, dark); cap.position.set(0, 2.15, 0);
    var brim = createLimb(0.44, 0.05, 0.24, dark); brim.position.set(0, 2.10, -0.28);

    procGuard.add(legL); procGuard.add(legR); procGuard.add(torso);
    procGuard.add(armL); procGuard.add(armR); procGuard.add(head);
    procGuard.add(cap); procGuard.add(brim);
    procGuard.rotation.x = -0.12;

    grp.add(procGuard);
    grp.userData.procGuard = procGuard;

    grp.position.set(0, 0, 6.5);
    grp.visible = false;

    G.scene.add(grp);
    G.guard = grp;
    G.guardParts = { legL: legL, legR: legR, armL: armL, armR: armR };
  }

  // --- Authentic Arcade Obstacle Builders (Low Hurdles & Overhead Gantries) ---
  function buildLowHurdleMesh() {
    var grp = new THREE.Group();

    // 1. Red & White Striped Main Crossbar
    var bar = new THREE.Mesh(G.geo.box, G.mat.hurdleStripe);
    bar.scale.set(1.78, 0.42, 0.16);
    bar.position.set(0, 0.22, 0);
    grp.add(bar);

    // 2. Heavy Steel End Caps
    var capL = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    capL.scale.set(0.06, 0.46, 0.20);
    capL.position.set(-0.90, 0.22, 0);
    grp.add(capL);

    var capR = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    capR.scale.set(0.06, 0.46, 0.20);
    capR.position.set(0.90, 0.22, 0);
    grp.add(capR);

    // 3. Upright Steel A-Frame Legs
    var leg1 = new THREE.Mesh(G.geo.box, G.mat.steelPost);
    leg1.scale.set(0.12, 0.96, 0.14);
    leg1.position.set(-0.74, -0.04, 0);
    grp.add(leg1);

    var leg2 = new THREE.Mesh(G.geo.box, G.mat.steelPost);
    leg2.scale.set(0.12, 0.96, 0.14);
    leg2.position.set(0.74, -0.04, 0);
    grp.add(leg2);

    // 4. Safety Orange Rubber Footings
    var foot1 = new THREE.Mesh(G.geo.box, G.mat.safetyOrange);
    foot1.scale.set(0.26, 0.10, 0.50);
    foot1.position.set(-0.74, -0.47, 0);
    grp.add(foot1);

    var foot2 = new THREE.Mesh(G.geo.box, G.mat.safetyOrange);
    foot2.scale.set(0.26, 0.10, 0.50);
    foot2.position.set(0.74, -0.47, 0);
    grp.add(foot2);

    // 5. Dual Amber Safety Blinker Beacons
    var b1Plate = new THREE.Mesh(G.geo.beaconPlate, G.mat.steelDark);
    b1Plate.position.set(-0.74, 0.45, 0);
    grp.add(b1Plate);
    var b1Dome = new THREE.Mesh(G.geo.beaconCyl, G.mat.hazardBeacon);
    b1Dome.position.set(-0.74, 0.53, 0);
    grp.add(b1Dome);

    var b2Plate = new THREE.Mesh(G.geo.beaconPlate, G.mat.steelDark);
    b2Plate.position.set(0.74, 0.45, 0);
    grp.add(b2Plate);
    var b2Dome = new THREE.Mesh(G.geo.beaconCyl, G.mat.hazardBeacon);
    b2Dome.position.set(0.74, 0.53, 0);
    grp.add(b2Dome);

    return grp;
  }

  function buildHighBarrierMesh() {
    var grp = new THREE.Group();

    // 1. Heavy Overhead Caution Striped Beam
    var beam = new THREE.Mesh(G.geo.box, G.mat.cautionStripe);
    beam.scale.set(1.92, 0.46, 0.22);
    beam.position.set(0, 0.32, 0);
    grp.add(beam);

    // Top & Bottom Steel Protective Flanges
    var flangeTop = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    flangeTop.scale.set(1.96, 0.06, 0.26);
    flangeTop.position.set(0, 0.56, 0);
    grp.add(flangeTop);

    var flangeBot = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    flangeBot.scale.set(1.96, 0.06, 0.26);
    flangeBot.position.set(0, 0.08, 0);
    grp.add(flangeBot);

    // 2. Clearance Plaque (Hanging warning marker)
    var plaque = new THREE.Mesh(G.geo.plaque, G.mat.cautionStripe);
    plaque.position.set(0, -0.06, 0.12);
    grp.add(plaque);

    // 3. Heavy Steel Truss Uprights
    var post1 = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    post1.scale.set(0.16, 2.30, 0.18);
    post1.position.set(-0.84, -0.60, 0);
    grp.add(post1);

    var post2 = new THREE.Mesh(G.geo.box, G.mat.steelDark);
    post2.scale.set(0.16, 2.30, 0.18);
    post2.position.set(0.84, -0.60, 0);
    grp.add(post2);

    // 4. Corner Reinforcement Gussets
    var g1 = new THREE.Mesh(G.geo.gusset, G.mat.steelPost);
    g1.rotation.z = Math.PI / 4;
    g1.position.set(-0.70, 0.16, 0);
    grp.add(g1);

    var g2 = new THREE.Mesh(G.geo.gusset, G.mat.steelPost);
    g2.rotation.z = -Math.PI / 4;
    g2.position.set(0.70, 0.16, 0);
    grp.add(g2);

    // 5. Concrete Track Bed Footings
    var foot1 = new THREE.Mesh(G.geo.box, G.mat.ballast);
    foot1.scale.set(0.32, 0.18, 0.40);
    foot1.position.set(-0.84, -1.66, 0);
    grp.add(foot1);

    var foot2 = new THREE.Mesh(G.geo.box, G.mat.ballast);
    foot2.scale.set(0.32, 0.18, 0.40);
    foot2.position.set(0.84, -1.66, 0);
    grp.add(foot2);

    // 6. Top Center Flashing Hazard Beacon
    var bPlate = new THREE.Mesh(G.geo.beaconPlate, G.mat.steelDark);
    bPlate.scale.set(1.4, 1.4, 1.4);
    bPlate.position.set(0, 0.61, 0);
    grp.add(bPlate);

    var bDome = new THREE.Mesh(G.geo.beaconCyl, G.mat.hazardBeacon);
    bDome.scale.set(1.3, 1.3, 1.3);
    bDome.position.set(0, 0.72, 0);
    grp.add(bDome);

    return grp;
  }

  function buildTrainRampMesh() {
    var grp = new THREE.Group();
    var angle = Math.atan2(TRAIN_H, 4.5);
    var rampLen = Math.sqrt(TRAIN_H * TRAIN_H + 4.5 * 4.5); // ~5.25m

    // Inclined running surface with caution hazard stripes
    var board = new THREE.Mesh(G.geo.box, G.mat.cautionStripe || G.mat.warningYellow);
    board.scale.set(1.68, 0.16, rampLen);
    board.rotation.x = angle;
    board.position.set(0, 0, 0);
    grp.add(board);

    // Heavy steel side guide rails
    var railL = new THREE.Mesh(G.geo.box, G.mat.steelDark || G.mat.steelPost);
    railL.scale.set(0.08, 0.32, rampLen);
    railL.rotation.x = angle;
    railL.position.set(-0.80, 0.12, 0);
    grp.add(railL);

    var railR = new THREE.Mesh(G.geo.box, G.mat.steelDark || G.mat.steelPost);
    railR.scale.set(0.08, 0.32, rampLen);
    railR.rotation.x = angle;
    railR.position.set(0.80, 0.12, 0);
    grp.add(railR);

    // Front ground wedge lip
    var lip = new THREE.Mesh(G.geo.box, G.mat.steelPost);
    lip.scale.set(1.72, 0.08, 0.30);
    lip.position.set(0, -TRAIN_H / 2 + 0.04, 2.25);
    grp.add(lip);

    return grp;
  }

  function buildHeadlightsMesh() {
    var grp = new THREE.Group();
    var casingGeo = new THREE.CylinderGeometry(0.18, 0.18, 0.12, 12);
    casingGeo.rotateX(Math.PI / 2);
    var lensGeo = new THREE.CylinderGeometry(0.14, 0.14, 0.14, 12);
    lensGeo.rotateX(Math.PI / 2);

    var casingMat = G.mat.steelDark || new THREE.MeshLambertMaterial({ color: 0x222222 });
    var lightMat = new THREE.MeshBasicMaterial({ color: 0xfffaed });

    // Left light
    var cL = new THREE.Mesh(casingGeo, casingMat);
    cL.position.set(-0.55, 0.15, 0);
    var lL = new THREE.Mesh(lensGeo, lightMat);
    lL.position.set(-0.55, 0.15, 0.02);
    grp.add(cL);
    grp.add(lL);

    // Right light
    var cR = new THREE.Mesh(casingGeo, casingMat);
    cR.position.set(0.55, 0.15, 0);
    var lR = new THREE.Mesh(lensGeo, lightMat);
    lR.position.set(0.55, 0.15, 0.02);
    grp.add(cR);
    grp.add(lR);

    // Forward shining light beams
    var beamGeo = new THREE.ConeGeometry(0.8, 6.0, 16);
    beamGeo.rotateX(-Math.PI / 2);
    beamGeo.translate(0, 0, 3.0);
    var beamMat = new THREE.MeshBasicMaterial({
      color: 0xfff6a8,
      transparent: true,
      opacity: 0.28,
      depthWrite: false
    });

    var bL = new THREE.Mesh(beamGeo, beamMat);
    bL.position.set(-0.55, 0.15, 0.05);
    grp.add(bL);

    var bR = new THREE.Mesh(beamGeo, beamMat);
    bR.position.set(0.55, 0.15, 0.05);
    grp.add(bR);

    return grp;
  }

  // ==========================================
  // Object Pools (Obstacles, Coins, Powerups, VFX Particles)
  // ==========================================
  function buildTrackPools() {
    var i;

    // Obstacle Pool
    for (i = 0; i < MAX_OBSTACLES; i++) {
      var holder = new THREE.Group();
      holder.visible = false;

      var lowMesh = buildLowHurdleMesh();
      lowMesh.visible = false;
      holder.add(lowMesh);

      var highMesh = buildHighBarrierMesh();
      highMesh.visible = false;
      holder.add(highMesh);

      var trainBox = new THREE.Mesh(G.geo.box, G.mat.trainFallback);
      trainBox.scale.set(1.5, 2.3, 3.2);
      trainBox.visible = false;
      holder.add(trainBox);

      var rampMesh = buildTrainRampMesh();
      rampMesh.visible = false;
      holder.add(rampMesh);

      var headlights = buildHeadlightsMesh();
      headlights.visible = false;
      holder.add(headlights);

      G.scene.add(holder);
      G.obstaclePool.push({
        holder: holder,
        lowMesh: lowMesh,
        highMesh: highMesh,
        fallbackTrain: trainBox,
        trainMesh: null,
        rampMesh: rampMesh,
        headlights: headlights,
        oncoming: false,
        trainSpeed: 0.0,
        hasRamp: false,
        horned: false,
        active: false,
        kind: 'full',
        hx: 0.75,
        hy: 1.1,
        hz: 0.45,
      });
    }

    // Coin Pool (Rotated gold coins)
    for (i = 0; i < MAX_COINS; i++) {
      var coinMesh = new THREE.Mesh(G.geo.cylinder, G.mat.goldCoin);
      coinMesh.visible = false;
      G.scene.add(coinMesh);
      G.coinPool.push({ mesh: coinMesh, active: false });
    }

    // Power-up Pool (Hoverboard, Magnet, Jetpack, Sneakers)
    for (i = 0; i < MAX_POWERUPS; i++) {
      var pGroup = new THREE.Group();
      pGroup.visible = false;

      var icon = new THREE.Mesh(G.geo.box, G.mat.board);
      icon.scale.set(0.65, 0.65, 0.65);
      pGroup.add(icon);

      G.scene.add(pGroup);
      G.powerupPool.push({
        holder: pGroup,
        icon: icon,
        type: 'board',
        active: false,
      });
    }

    // Mystery Gift Box Pool (4 items)
    G.mysteryBoxPool = [];
    for (i = 0; i < 4; i++) {
      var boxHolder = new THREE.Group();
      boxHolder.visible = false;

      var boxMesh = new THREE.Mesh(G.geo.mysteryBox, G.mat.mysteryBox);
      var ribH = new THREE.Mesh(G.geo.ribbonH, G.mat.boxRibbon);
      var ribV = new THREE.Mesh(G.geo.ribbonV, G.mat.boxRibbon);
      var knot1 = new THREE.Mesh(G.geo.ribbonKnot, G.mat.boxRibbon);
      knot1.position.set(-0.07, 0.46, 0);
      knot1.rotation.y = 0.5;
      var knot2 = new THREE.Mesh(G.geo.ribbonKnot, G.mat.boxRibbon);
      knot2.position.set(0.07, 0.46, 0);
      knot2.rotation.y = -0.5;

      var halo = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.72, 16), G.mat.boxHalo);
      halo.rotation.x = -Math.PI / 2;
      halo.position.set(0, -0.42, 0);

      boxHolder.add(boxMesh);
      boxHolder.add(ribH);
      boxHolder.add(ribV);
      boxHolder.add(knot1);
      boxHolder.add(knot2);
      boxHolder.add(halo);

      G.scene.add(boxHolder);
      G.mysteryBoxPool.push({
        holder: boxHolder,
        active: false,
        yBase: 0.85,
        bobTime: Math.random() * 6.0,
      });
    }

    // High-performance VFX Particle Pool (Jetpack fire/smoke, sparks, dust)
    for (i = 0; i < 120; i++) {
      var pMesh = new THREE.Mesh(G.geo.particle, G.mat.fire);
      pMesh.visible = false;
      G.scene.add(pMesh);
      G.particlePool.push({
        mesh: pMesh,
        active: false,
        vx: 0, vy: 0, vz: 0,
        life: 0,
        maxLife: 0.4,
        scale: 1.0,
      });
    }
  }

  function spawnParticle(x, y, z, vx, vy, vz, mat, maxLife, scale) {
    for (var i = 0; i < G.particlePool.length; i++) {
      var p = G.particlePool[i];
      if (!p.active) {
        p.active = true;
        p.mesh.visible = true;
        p.mesh.material = mat;
        p.mesh.position.set(x, y, z);
        p.mesh.scale.setScalar(scale || 1.0);
        p.vx = vx; p.vy = vy; p.vz = vz;
        p.life = 0;
        p.maxLife = maxLife || 0.35;
        p.scale = scale || 1.0;
        return p;
      }
    }
    return null;
  }

  function updateParticles(dt) {
    if (!G.camera) return;
    var camQuat = G.camera.quaternion;
    for (var i = 0; i < G.particlePool.length; i++) {
      var p = G.particlePool[i];
      if (!p.active) continue;
      p.life += dt;
      if (p.life >= p.maxLife) {
        p.active = false;
        p.mesh.visible = false;
        continue;
      }
      var prog = p.life / p.maxLife;
      p.mesh.position.x += p.vx * dt;
      p.mesh.position.y += p.vy * dt;
      p.mesh.position.z += p.vz * dt;
      p.mesh.scale.setScalar(p.scale * (1.0 - prog * 0.65));
      p.mesh.quaternion.copy(camQuat);
    }
  }

  // ==========================================
  // 3D GLB Models Loading & Fitting
  // ==========================================
  function fitModel(obj, targetHeight, targetWidth) {
    var box = new THREE.Box3().setFromObject(obj);
    var size = new THREE.Vector3();
    box.getSize(size);
    if (size.y <= 0) return { scale: 1, minY: 0, size: size };

    var s = targetHeight / size.y;
    if (targetWidth && size.x * s > targetWidth) {
      s = targetWidth / size.x;
    }
    obj.scale.setScalar(s);
    box.setFromObject(obj);
    box.getSize(size);
    return { scale: s, minY: box.min.y, size: size };
  }

  function load3DModels() {
    if (typeof THREE.GLTFLoader === 'undefined') return;
    var loader = new THREE.GLTFLoader();
    var fbxLoader = (typeof THREE.FBXLoader !== 'undefined') ? new THREE.FBXLoader() : null;
    var texLoader = new THREE.TextureLoader();
    var basePath = (window.GAME_MODELS && window.GAME_MODELS.base) || 'models/';

    function makeClipInPlace(clip) {
      if (!clip || !clip.tracks) return clip;
      for (var t = 0; t < clip.tracks.length; t++) {
        var track = clip.tracks[t];
        var lower = track.name.toLowerCase();
        if ((lower.indexOf('hips') !== -1 || lower.indexOf('root') !== -1 || lower.indexOf('pelvis') !== -1 || lower.indexOf('skeleton') !== -1) && track.name.indexOf('.position') !== -1) {
          var initX = track.values[0] || 0;
          var initZ = track.values[2] || 0;
          for (var k = 0; k < track.values.length; k += 3) {
            track.values[k] = initX;
            track.values[k + 2] = initZ;
          }
        }
      }
      return clip;
    }

    function findClip(clips, names) {
      if (!clips || !clips.length) return null;
      for (var n = 0; n < names.length; n++) {
        var target = names[n].toLowerCase();
        for (var i = 0; i < clips.length; i++) {
          if (clips[i].name && clips[i].name.toLowerCase() === target) return clips[i];
        }
      }
      for (var n2 = 0; n2 < names.length; n2++) {
        var target2 = names[n2].toLowerCase();
        for (var j = 0; j < clips.length; j++) {
          if (clips[j].name && clips[j].name.toLowerCase().indexOf(target2) !== -1) return clips[j];
        }
      }
      return null;
    }

    // 1. Train Model (Properly centered in 3D so wheels rest on the railway tracks)
    loader.load(basePath + 'subway_surfers_train.glb', function (gltf) {
      var trainScene = gltf.scene || gltf.scenes[0];
      fitModel(trainScene, TRAIN_H, TRAIN_W);

      var wrapper = new THREE.Group();
      wrapper.add(trainScene);

      var b = new THREE.Box3().setFromObject(wrapper);
      var sz = new THREE.Vector3();
      b.getSize(sz);
      // Align along tracks and flip 180 degrees
      if (sz.x > sz.z) {
        wrapper.rotation.y = (Math.PI / 2) + Math.PI; // 90° track alignment + 180° flip
      } else {
        wrapper.rotation.y = Math.PI; // 180° flip
      }

      b.setFromObject(wrapper);
      var center = new THREE.Vector3();
      b.getCenter(center);
      // Center train inside wrapper so local (0, 0, 0) is the true 3D center
      trainScene.position.x -= center.x;
      trainScene.position.y -= center.y;
      trainScene.position.z -= center.z;

      b.setFromObject(wrapper);
      b.getSize(sz);

      G.trainProto = wrapper;
      G.trainBaseLen = sz.z > 0.1 ? sz.z : TRAIN_LEN;
    }, undefined, function () {});

    // 2. Hoverboard Model
    loader.load(basePath + 'mobile_-_subway_surfers_-_hoverboard.glb', function (gltf) {
      var board = gltf.scene || gltf.scenes[0];
      fitModel(board, 0.25, 0.75);
      board.rotation.y = Math.PI / 2;
      G.hoverboardProto = board;
      if (G.hoverboardMesh) {
        G.hoverboardMesh.add(board.clone());
      }
    }, undefined, function () {});

    // 3. Jetpack Model (Centered geometry, facing backward towards camera, fit snugly to back)
    loader.load(basePath + 'jetpack_-_subway_surfers.glb', function (gltf) {
      var jet = gltf.scene || gltf.scenes[0];
      var b = new THREE.Box3().setFromObject(jet);
      var center = new THREE.Vector3();
      b.getCenter(center);
      jet.position.sub(center); // Center pivot at origin

      var jetWrap = new THREE.Group();
      jetWrap.add(jet);
      fitModel(jetWrap, 0.48, 0.48);
      jetWrap.rotation.y = Math.PI; // Face exhausts back towards camera

      G.jetpackProto = jetWrap;
      if (G.jetpackMesh) {
        while (G.jetpackMesh.children.length > 0) {
          G.jetpackMesh.remove(G.jetpackMesh.children[0]);
        }
        G.jetpackMesh.add(jetWrap.clone());
      }
    }, undefined, function () {});

    // 4. Authentic Inspector & Pitbull Dog Animated NPC Pack (Enemies.fbx)
    var setupInspectorFallbackGlb = function () {
      var guardModelUrl = basePath + 'inspector_subway_surfers.glb';
      loader.load(guardModelUrl, function (gltf) {
        var guardScene = gltf.scene || gltf.scenes[0];
        guardScene.traverse(function (child) {
          if (child.isMesh) {
            child.castShadow = true;
            child.receiveShadow = true;
          }
        });
        var b = new THREE.Box3().setFromObject(guardScene);
        var center = new THREE.Vector3();
        b.getCenter(center);
        guardScene.position.x = -center.x;
        guardScene.position.z = -center.z;
        guardScene.position.y = -b.min.y;

        fitModel(guardScene, 1.85, 1.05);
        guardScene.rotation.y = Math.PI;

        var guardWrap = new THREE.Group();
        guardWrap.add(guardScene);

        if (G.guard) {
          if (G.guard.userData.procGuard) G.guard.userData.procGuard.visible = false;
          G.guard.add(guardWrap);
          G.guard.userData.model3d = guardWrap;
        }
      }, undefined, function (err) {
        console.warn('Inspector GLB load failed, trying guard NPC fallback:', err);
        loader.load(basePath + 'subway_surfers_guard_npc.glb', function (fallbackGltf) {
          var guardScene = fallbackGltf.scene || fallbackGltf.scenes[0];
          fitModel(guardScene, 1.65, 0.90);
          guardScene.rotation.y = Math.PI;
          var guardWrap = new THREE.Group();
          guardWrap.add(guardScene);
          if (G.guard) {
            if (G.guard.userData.procGuard) G.guard.userData.procGuard.visible = false;
            G.guard.add(guardWrap);
            G.guard.userData.model3d = guardWrap;
          }
        });
      });
    };

    if (fbxLoader) {
      var enemiesTex = texLoader.load(basePath + 'enemies/textures/enemies.png');
      enemiesTex.encoding = THREE.sRGBEncoding;

      fbxLoader.load(basePath + 'enemies/source/Enemies.fbx', function (fbx) {
        fbx.traverse(function (child) {
          if (child.isMesh || child.isSkinnedMesh) {
            child.castShadow = true;
            child.receiveShadow = true;
            child.material = new THREE.MeshLambertMaterial({
              map: enemiesTex,
              skinning: true
            });
          }
        });

        // Fit height to authentic Inspector proportions (~1.90m)
        var b = new THREE.Box3().setFromObject(fbx);
        var sz = new THREE.Vector3();
        b.getSize(sz);
        var targetH = 1.90;
        var scale = sz.y > 0.1 ? (targetH / sz.y) : 0.0125;
        fbx.scale.setScalar(scale);

        fbx.updateMatrixWorld(true);
        b.setFromObject(fbx);
        var center = new THREE.Vector3();
        b.getCenter(center);
        fbx.position.x -= center.x;
        fbx.position.z -= center.z;
        fbx.position.y -= b.min.y;
        fbx.updateMatrixWorld(true);

        var guardWrap = new THREE.Group();
        guardWrap.add(fbx);
        guardWrap.rotation.y = Math.PI; // Face forward down the tracks towards Jake

        if (G.guard) {
          if (G.guard.userData.procGuard) G.guard.userData.procGuard.visible = false;
          G.guard.add(guardWrap);
          G.guard.userData.model3d = guardWrap;
        }

        // Setup Animation Mixer & Actions on the FBX root
        G.guardMixer = new THREE.AnimationMixer(fbx);
        if (fbx.animations && fbx.animations.length > 0) {
          // 1. Inspector Running Action
          var guardRun = findClip(fbx.animations, ['guard_run', 'run']);
          if (guardRun) {
            var runClip = makeClipInPlace(guardRun);
            G.guardActions.run = G.guardMixer.clipAction(runClip);
            G.guardActions.run.setEffectiveTimeScale(1.15);
            G.guardActions.run.play();
          }

          // 2. Pitbull Dog Running Action
          var dogRun = findClip(fbx.animations, ['dog_fast run', 'dog_run']);
          if (dogRun) {
            var dogClip = makeClipInPlace(dogRun);
            G.guardActions.dogRun = G.guardMixer.clipAction(dogClip);
            G.guardActions.dogRun.setEffectiveTimeScale(1.15);
            G.guardActions.dogRun.play();
          }

          // 3. Whistle Intro Action
          var intro = findClip(fbx.animations, ['guard_whistle', 'playintro_1', 'playintro']);
          if (intro) {
            var introClip = makeClipInPlace(intro);
            G.guardActions.intro = G.guardMixer.clipAction(introClip);
            G.guardActions.intro.setLoop(THREE.LoopOnce);
            G.guardActions.intro.clampWhenFinished = true;
            G.guardActions.intro.setEffectiveTimeScale(1.2);
          }

          // 4. Catch Player Action
          var catchC = findClip(fbx.animations, ['catchplayer', 'guard_grap after', 'catch2', 'catch']);
          if (catchC) {
            var catchClip = makeClipInPlace(catchC);
            G.guardActions.catch = G.guardMixer.clipAction(catchClip);
            G.guardActions.catch.setLoop(THREE.LoopOnce);
            G.guardActions.catch.clampWhenFinished = true;
            G.guardActions.catch.setEffectiveTimeScale(1.2);
          }
        }
      }, undefined, function (err) {
        console.warn('Enemies FBX load failed, falling back to GLB:', err);
        setupInspectorFallbackGlb();
      });
    } else {
      setupInspectorFallbackGlb();
    }

    // 5. Rigged & Animated Jake Character (Mixamo fast_run.fbx & jumping_up.fbx)
    var setupGlbFallback = function () {
      loader.load(basePath + 'subway_surfers_loves_surfing_in_vanilla_extract.glb', function (gltf) {
        var surfer = gltf.scene || gltf.scenes[0];
        var fitted = fitModel(surfer, H_PLAYER);
        surfer.rotation.y = Math.PI;
        surfer.position.y = -0.4 - fitted.minY;

        var charWrap = new THREE.Group();
        charWrap.add(surfer);
        G.characterModels[0] = charWrap;

        if (G.characterIndex === 0 && G.playerModelWrap) {
          G.playerModelWrap.userData.procFig.visible = false;
          G.playerModelWrap.add(charWrap);
        }
        triggerCallback('onModelsReady');
      }, undefined, function () {});
    };

    if (fbxLoader) {
      var jakeTex = texLoader.load(basePath + 'jake_outfit0_tex.png');
      jakeTex.encoding = THREE.sRGBEncoding;

      // Load 3D Mystery Gift Box FBX Model
      var boxTex = texLoader.load(basePath + 'box/textures/giftTex.png');
      boxTex.encoding = THREE.sRGBEncoding;
      fbxLoader.load(basePath + 'box/source/giftTest.fbx', function (fbx) {
        fbx.traverse(function (child) {
          if (child.isMesh) {
            child.material = new THREE.MeshLambertMaterial({ map: boxTex });
            child.castShadow = true;
          }
        });
        fitModel(fbx, 0.75, 0.75);
        var b = new THREE.Box3().setFromObject(fbx);
        var center = new THREE.Vector3();
        b.getCenter(center);
        fbx.position.sub(center);

        var boxWrap = new THREE.Group();
        boxWrap.add(fbx);
        G.mysteryBoxProto = boxWrap;

        if (G.mysteryBoxPool) {
          for (var m = 0; m < G.mysteryBoxPool.length; m++) {
            var mb = G.mysteryBoxPool[m];
            for (var c = 0; c < mb.holder.children.length; c++) {
              mb.holder.children[c].visible = false;
            }
            mb.holder.add(boxWrap.clone());
          }
        }
      }, undefined, function (err) {
        console.warn('Gift box FBX load fallback to procedural:', err);
      });

      fbxLoader.load(basePath + 'fast_run.fbx', function (fbx) {
        fbx.traverse(function (child) {
          if (child.isMesh || child.isSkinnedMesh) {
            child.castShadow = true;
            child.receiveShadow = true;
            child.material = new THREE.MeshLambertMaterial({
              map: jakeTex,
              skinning: true
            });
          }
        });

        // Fit Jake height to authentic proportions
        var b = new THREE.Box3().setFromObject(fbx);
        var sz = new THREE.Vector3();
        b.getSize(sz);
        var targetH = H_PLAYER;
        var scale = sz.y > 0.1 ? (targetH / sz.y) : 0.0125;
        fbx.scale.setScalar(scale);

        // Center Jake on rails and face forward
        fbx.rotation.y = Math.PI;
        fbx.position.set(0, -0.85, 0);

        // Setup Animation Mixer & Running Action (strictly in-place)
        G.animMixer = new THREE.AnimationMixer(fbx);
        if (fbx.animations && fbx.animations.length > 0) {
          var runClip = makeClipInPlace(fbx.animations[0]);
          G.animActions.run = G.animMixer.clipAction(runClip);
          G.animActions.run.setEffectiveTimeScale(1.15);
          G.animActions.run.play();
        }

        // Load Jumping Action (in-place)
        fbxLoader.load(basePath + 'jumping_up.fbx', function (jumpFbx) {
          if (jumpFbx.animations && jumpFbx.animations.length > 0) {
            var jumpClip = makeClipInPlace(jumpFbx.animations[0]);
            G.animActions.jump = G.animMixer.clipAction(jumpClip);
            G.animActions.jump.setLoop(THREE.LoopOnce);
            G.animActions.jump.clampWhenFinished = true;
          }
        }, undefined, function () {});

        // Load Slide Action (Soccer Tackle FBX)
        fbxLoader.load(basePath + 'soccer_tackle.fbx', function (slideFbx) {
          if (slideFbx.animations && slideFbx.animations.length > 0) {
            var slideClip = makeClipInPlace(slideFbx.animations[0]);
            G.animActions.slide = G.animMixer.clipAction(slideClip);
            G.animActions.slide.setLoop(THREE.LoopOnce);
            G.animActions.slide.clampWhenFinished = true;
            var dur = slideClip.duration || 1.0;
            G.animActions.slide.setEffectiveTimeScale(dur / SLIDE_TIME);
          }
        }, undefined, function (err) {
          console.warn('Slide FBX load failed:', err);
        });

        // Load Sweep Fall / Crash Action (Sweep Fall FBX)
        fbxLoader.load(basePath + 'sweep_fall.fbx', function (fallFbx) {
          if (fallFbx.animations && fallFbx.animations.length > 0) {
            var fallClip = makeClipInPlace(fallFbx.animations[0]);
            G.animActions.fall = G.animMixer.clipAction(fallClip);
            G.animActions.fall.setLoop(THREE.LoopOnce);
            G.animActions.fall.clampWhenFinished = true;
            G.animActions.fall.setEffectiveTimeScale(1.15);
          }
        }, undefined, function (err) {
          console.warn('Sweep Fall FBX load failed:', err);
        });

        // Load Pointing Gesture / Inspector Taunt Action (Pointing Gesture FBX)
        fbxLoader.load(basePath + 'pointing_gesture.fbx', function (tauntFbx) {
          if (tauntFbx.animations && tauntFbx.animations.length > 0) {
            var tauntClip = makeClipInPlace(tauntFbx.animations[0]);
            G.animActions.taunt = G.animMixer.clipAction(tauntClip);
            G.animActions.taunt.setLoop(THREE.LoopOnce);
            G.animActions.taunt.clampWhenFinished = true;
            G.animActions.taunt.setEffectiveTimeScale(1.20);
            // In idle mode, play the taunt animation on menu screen
            if (G.state === 'idle') {
              if (G.animActions.run) G.animActions.run.fadeOut(0.1);
              G.animActions.taunt.reset().fadeIn(0.15).play();
            }
          }
        }, undefined, function (err) {
          console.warn('Pointing Gesture FBX load failed:', err);
        });

        var charWrap = new THREE.Group();
        charWrap.add(fbx);
        G.characterModels[0] = charWrap;

        if (G.playerModelWrap) {
          G.playerModelWrap.userData.procFig.visible = false;
          G.playerModelWrap.add(charWrap);
        }
        triggerCallback('onModelsReady');
      }, undefined, function (err) {
        console.warn('FBX load failed, falling back to GLB:', err);
        setupGlbFallback();
      });
    } else {
      setupGlbFallback();
    }
  }

  function spawnMysteryBox(laneX, z) {
    if (!G.mysteryBoxPool) return null;
    for (var i = 0; i < G.mysteryBoxPool.length; i++) {
      var mb = G.mysteryBoxPool[i];
      if (!mb.active) {
        mb.active = true;
        mb.holder.visible = true;
        mb.holder.position.set(laneX, mb.yBase, z);
        mb.holder.rotation.set(0, 0, 0);
        return mb;
      }
    }
    return null;
  }

  // ==========================================
  // Spawning System: Rows, Powerups, Coins
  // ==========================================
  function isLaneOccupiedNearSpawn(laneX, minDistance) {
    for (var i = 0; i < G.obstaclePool.length; i++) {
      var o = G.obstaclePool[i];
      if (!o.active) continue;
      if (Math.abs(o.holder.position.x - laneX) < 0.5) {
        var dist = Math.abs(o.holder.position.z - SPAWN_Z);
        if (dist < minDistance) {
          return true;
        }
      }
    }
    return false;
  }

  function laneHasObstaclesAhead(laneX) {
    for (var i = 0; i < G.obstaclePool.length; i++) {
      var o = G.obstaclePool[i];
      if (!o.active) continue;
      if (Math.abs(o.holder.position.x - laneX) < 0.5) {
        if (o.holder.position.z > (SPAWN_Z - 5.0) && o.holder.position.z < 12.0) {
          return true; // Lane is already occupied by an obstacle ahead!
        }
      }
    }
    return false;
  }

  function hasAnyOncomingTrain() {
    for (var i = 0; i < G.obstaclePool.length; i++) {
      var o = G.obstaclePool[i];
      if (o.active && o.kind === 'full' && o.oncoming) {
        return true;
      }
    }
    return false;
  }

  function spawnObstacle(kind, laneX, lenScale) {
    for (var i = 0; i < G.obstaclePool.length; i++) {
      var o = G.obstaclePool[i];
      if (o.active) continue;

      o.active = true;
      o.kind = kind;
      o.stumbled = false;
      o.holder.visible = true;
      o.horned = false;

      // Position in assigned lane immediately
      o.holder.position.x = laneX;
      o.holder.position.z = SPAWN_Z;

      // Hide all sub-meshes initially
      if (o.lowMesh) o.lowMesh.visible = false;
      if (o.highMesh) o.highMesh.visible = false;
      if (o.fallbackTrain) o.fallbackTrain.visible = false;
      if (o.rampMesh) o.rampMesh.visible = false;
      if (o.headlights) o.headlights.visible = false;

      if (kind === 'full') {
        // Subway Surfers: Oncoming moving trains vs stationary trains with ramp
        // An oncoming train can ONLY spawn if the entire lane ahead is completely clear
        // and no other oncoming train is currently active!
        var laneClear = !laneHasObstaclesAhead(laneX);
        var alreadyOncoming = hasAnyOncomingTrain();
        var isOncoming = (laneClear && !alreadyOncoming && Math.random() < 0.40 && G.score > 25.0);

        o.oncoming = isOncoming;
        o.trainSpeed = isOncoming ? (7.5 + Math.random() * 4.5 + Math.min(6.0, G.speed * 0.20)) : 0.0;
        // Stationary trains have a 65% chance to feature a ramp leading to the roof!
        o.hasRamp = !isOncoming && (Math.random() < 0.65);

        if (G.trainProto) {
          if (!o.trainMesh) {
            o.trainMesh = G.trainProto.clone();
            o.holder.add(o.trainMesh);
          }
          o.trainMesh.visible = true;
          o.trainMesh.scale.set(1, 1, lenScale || 1.0);
          o.hx = 0.85;
          o.hy = TRAIN_H / 2;
          o.hz = (G.trainBaseLen * (lenScale || 1.0)) / 2;
          o.holder.position.y = TRAIN_H / 2;
        } else {
          if (o.trainMesh) o.trainMesh.visible = false;
          if (o.fallbackTrain) o.fallbackTrain.visible = true;
          o.hx = 0.80; o.hy = TRAIN_H / 2; o.hz = 2.4;
          o.holder.position.y = TRAIN_H / 2;
        }

        if (o.headlights) {
          o.headlights.visible = isOncoming;
          o.headlights.position.set(0, 0, o.hz + 0.08);
        }
        if (o.rampMesh) {
          o.rampMesh.visible = o.hasRamp;
          o.rampMesh.position.set(0, 0, o.hz + 2.25);
        }
      } else {
        o.oncoming = false;
        o.trainSpeed = 0.0;
        o.hasRamp = false;
        if (o.trainMesh) o.trainMesh.visible = false;

        if (kind === 'low') {
          // Authentic Red & White Construction Hurdle (Jump over)
          if (o.lowMesh) o.lowMesh.visible = true;
          o.hx = 0.85; o.hy = 0.50; o.hz = 0.35;
          o.holder.position.y = 0.50;
        } else {
          // Authentic Industrial Yellow & Black Overhead Clearance Barrier (Slide under)
          if (o.highMesh) o.highMesh.visible = true;
          o.hx = 0.85; o.hy = 0.55; o.hz = 0.35;
          o.holder.position.y = 1.65;
        }
      }
      return o;
    }
    return null;
  }

  function spawnCoin(x, y, z) {
    for (var i = 0; i < G.coinPool.length; i++) {
      var c = G.coinPool[i];
      if (!c.active) {
        c.active = true;
        c.mesh.visible = true;
        c.mesh.position.set(x, (y !== undefined && y !== null) ? y : 0.80, z);
        return c;
      }
    }
    return null;
  }

  function spawnPowerup(type, x, z) {
    for (var i = 0; i < G.powerupPool.length; i++) {
      var p = G.powerupPool[i];
      if (!p.active) {
        p.active = true;
        p.type = type;
        p.holder.visible = true;
        p.holder.position.set(x, 0.85, z);

        if (type === 'magnet') p.icon.material = G.mat.magnet;
        else if (type === 'jetpack') p.icon.material = G.mat.jetpack;
        else if (type === 'sneakers') p.icon.material = G.mat.sneaker;
        else p.icon.material = G.mat.board;

        return p;
      }
    }
    return null;
  }

  function spawnRow() {
    var lanes = [0, 1, 2].sort(function () { return Math.random() - 0.5; });
    var numBlocks = Math.random() < 0.50 ? 1 : 2;
    var kinds = ['full', 'low', 'high'];
    var extraGap = 0;
    var spawnedLanes = [];

    for (var i = 0; i < numBlocks; i++) {
      var laneIndex = lanes[i];
      var laneX = LANES[laneIndex];

      // Safe lane check: avoid tailgating or stacking obstacles in the same lane
      if (isLaneOccupiedNearSpawn(laneX, 32.0)) {
        continue;
      }

      var kind = kinds[(Math.random() * 3) | 0];
      var lenScale = (kind === 'full') ? (0.9 + Math.random() * 0.4) : 1.0;
      var obs = spawnObstacle(kind, laneX, lenScale);
      if (obs) {
        spawnedLanes.push(laneIndex);
        if (kind === 'full') {
          extraGap = Math.max(extraGap, obs.hz * 0.7);

          // If train has a ramp: spawn a staircase of guide coins leading up the ramp!
          if (obs.hasRamp) {
            spawnCoin(laneX, 1.45, SPAWN_Z + obs.hz + 3.2);
            spawnCoin(laneX, 2.35, SPAWN_Z + obs.hz + 1.8);
            spawnCoin(laneX, 3.20, SPAWN_Z + obs.hz + 0.4);
          }

          // Spawn coins along the train roof!
          if (Math.random() < 0.75) {
            var roofCoins = 4;
            var startZ = SPAWN_Z + obs.hz * 0.7;
            var stepZ = (obs.hz * 1.4) / roofCoins;
            for (var rc = 0; rc < roofCoins; rc++) {
              spawnCoin(laneX, 3.60, startZ - rc * stepZ);
            }
          }
        }
      }
    }

    // Determine free lane for collectibles
    var freeLaneIndex = lanes.find(function (l) { return !spawnedLanes.includes(l); });
    if (freeLaneIndex === undefined) freeLaneIndex = lanes[2];
    var freeLane = LANES[freeLaneIndex];

    G.distSinceLastBox = (G.distSinceLastBox || 0) + 18.0;
    var spawnRoll = Math.random();
    // Mystery Gift Box: rare & prized reward (only once every ~400-500 meters)
    if (G.distSinceLastBox >= 450.0 || (G.distSinceLastBox >= 260.0 && spawnRoll < 0.04)) {
      spawnMysteryBox(freeLane, SPAWN_Z);
      G.distSinceLastBox = 0.0;
    } else if (spawnRoll < 0.35) {
      // 31% chance to spawn a powerup in the free lane
      var ptypes = ['magnet', 'jetpack', 'sneakers', 'board'];
      var pickedType = ptypes[(Math.random() * ptypes.length) | 0];
      spawnPowerup(pickedType, freeLane, SPAWN_Z);
    } else {
      // Otherwise spawn a line of golden coins on the tracks
      var count = 5;
      var coinY = 0.80; // Always grounded on track level
      for (var k = 0; k < count; k++) {
        spawnCoin(freeLane, coinY, SPAWN_Z - 4 - k * 2.2);
      }
    }
    return extraGap;
  }

  function skipIntro() {
    if (G.state === 'intro') {
      G.introTimer = 0.0;
      G.state = 'playing';
      G.guardChasing = true;
      G.guardChaseTimer = 4.0;
      if (G.animActions && G.animActions.taunt && G.animActions.run) {
        G.animActions.taunt.fadeOut(0.12);
        G.animActions.run.reset().fadeIn(0.12).play();
      }
      if (G.guardActions) {
        if (G.guardActions.intro) G.guardActions.intro.fadeOut(0.12);
        if (G.guardActions.run) G.guardActions.run.reset().fadeIn(0.12).play();
        if (G.guardActions.dogRun) G.guardActions.dogRun.reset().fadeIn(0.12).play();
      }
    }
  }

  function moveLeft() {
    if (G.state === 'intro') skipIntro();
    if (G.state !== 'playing') return;
    if (G.lane > 0) {
      G.lane--;
      G.targetX = LANES[G.lane];
      AudioFX.slide();
    }
  }

  function moveRight() {
    if (G.state === 'intro') skipIntro();
    if (G.state !== 'playing') return;
    if (G.lane < 2) {
      G.lane++;
      G.targetX = LANES[G.lane];
      AudioFX.slide();
    }
  }

  function jump() {
    if (G.state === 'intro') skipIntro();
    if (G.state !== 'playing' || G.powerups.jetpack > 0) return;
    var jumpVelocity = (G.powerups.sneakers > 0) ? SUPER_JUMP_V : JUMP_V;

    if (G.grounded || G.sliding > 0) {
      if (G.sliding > 0) {
        G.sliding = 0.0;
        if (G.animActions && G.animActions.slide) G.animActions.slide.stop();
      }
      G.vy = jumpVelocity;
      G.grounded = false;
      G.jumpBuf = 0.0;
      AudioFX.jump();

      if (G.animActions && G.animActions.jump && G.animActions.run) {
        if (G.animActions.slide) G.animActions.slide.stop();
        G.animActions.run.fadeOut(0.10);
        G.animActions.jump.reset().fadeIn(0.10).play();
      }
    } else {
      G.jumpBuf = JUMP_BUFFER;
    }
  }

  function slide() {
    if (G.state === 'intro') skipIntro();
    if (G.state !== 'playing') return;
    if (!G.grounded) {
      // Air-slide fast-fall
      G.vy = -26.0;
    }
    G.sliding = SLIDE_TIME;
    AudioFX.slide();

    if (G.animActions && G.animActions.slide) {
      if (G.animActions.jump) G.animActions.jump.stop();
      if (G.animActions.run) G.animActions.run.fadeOut(0.08);
      G.animActions.slide.reset().fadeIn(0.08).play();
    }
  }

  function activateHoverboard() {
    if (G.state !== 'playing') return;
    G.powerups.hoverboard = 18.0; // 18 seconds duration
    G.powerups.hasShield = true;
    if (G.hoverboardMesh) G.hoverboardMesh.visible = true;
    if (G.shieldMesh) G.shieldMesh.visible = true;
    AudioFX.board();
    triggerCallback('onPowerup', 'hoverboard', 18);
  }

  function bindTouchGestures(canvas) {
    var touchStartX = 0, touchStartY = 0, touchStartTime = 0;
    var lastTapTime = 0;

    canvas.addEventListener('touchstart', function (e) {
      var t = e.changedTouches[0];
      touchStartX = t.clientX;
      touchStartY = t.clientY;
      touchStartTime = performance.now();
    }, { passive: true });

    canvas.addEventListener('touchend', function (e) {
      if (G.state === 'intro') skipIntro();
      if (G.state !== 'playing') return;
      var t = e.changedTouches[0];
      var dx = t.clientX - touchStartX;
      var dy = t.clientY - touchStartY;
      var now = performance.now();

      // Double-Tap detection -> Activate Hoverboard
      if (Math.abs(dx) < 22 && Math.abs(dy) < 22) {
        if (now - lastTapTime < 320) {
          activateHoverboard();
          lastTapTime = 0;
          return;
        }
        lastTapTime = now;
        // Do NOT jump on single tap! (Prevents unintended jumping bugs on mobile)
        return;
      }

      // Dominant Swipe Direction
      var threshold = 20;
      if (Math.abs(dx) > Math.abs(dy)) {
        if (Math.abs(dx) > threshold) {
          if (dx > 0) moveRight(); else moveLeft();
        }
      } else {
        if (Math.abs(dy) > threshold) {
          if (dy < 0) jump(); else slide();
        }
      }
      e.preventDefault();
    }, { passive: false });

    // Keyboard controls for desktop testing
    window.addEventListener('keydown', function (e) {
      if (G.state === 'intro') skipIntro();
      if (G.state !== 'playing') return;
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') moveLeft();
      else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') moveRight();
      else if (e.key === 'ArrowUp' || e.key === 'w' || e.key === 'W' || e.key === ' ') {
        jump(); e.preventDefault();
      } else if (e.key === 'ArrowDown' || e.key === 's' || e.key === 'S') {
        slide();
      } else if (e.key === 'b' || e.key === 'B') {
        activateHoverboard();
      }
    });
  }

  // ==========================================
  // Game Lifecycle & State Management
  // ==========================================
  function start(options) {
    if (!G.inited) return;
    reset();
    if (options && typeof options.bestScore === 'number') {
      G.bestScore = options.bestScore;
      G.newRecordAnnounced = false;
    }

    if (G.animActions && G.animActions.taunt) {
      G.state = 'intro';
      G.introTimer = 1.25; // 1.25s authentic Subway Surfers taunt intro!
      AudioFX.whistle();
      AudioFX.dogBark();
      if (G.animActions.run) G.animActions.run.fadeOut(0.08);
      G.animActions.taunt.reset().fadeIn(0.08).play();

      if (G.guardActions) {
        if (G.guardActions.run) G.guardActions.run.fadeOut(0.08);
        if (G.guardActions.dogRun) G.guardActions.dogRun.fadeOut(0.08);
        if (G.guardActions.intro) G.guardActions.intro.reset().fadeIn(0.08).play();
      }

      if (G.guard) {
        G.guard.visible = true;
        G.guard.position.set(0, 0, 4.2);
      }
    } else {
      G.state = 'playing';
      G.guardChasing = true;
      G.guardChaseTimer = 4.0;
      if (G.guardActions) {
        if (G.guardActions.run) G.guardActions.run.reset().fadeIn(0.12).play();
        if (G.guardActions.dogRun) G.guardActions.dogRun.reset().fadeIn(0.12).play();
      }
    }
  }

  function stop() {
    G.state = 'idle';
    if (G.animActions && G.animActions.taunt) {
      if (G.animActions.run) G.animActions.run.fadeOut(0.1);
      if (G.animActions.slide) G.animActions.slide.stop();
      if (G.animActions.jump) G.animActions.jump.stop();
      if (G.animActions.fall) G.animActions.fall.stop();
      G.animActions.taunt.reset().fadeIn(0.15).play();
    }
    if (G.guardActions) {
      if (G.guardActions.catch) G.guardActions.catch.stop();
      if (G.guardActions.run) G.guardActions.run.stop();
      if (G.guardActions.dogRun) G.guardActions.dogRun.stop();
      if (G.guardActions.intro) G.guardActions.intro.reset().play();
    }
  }

  function reset() {
    var i;
    AudioFX.stopJetpack();
    G.shake = 0.0;

    if (G.animActions) {
      if (G.animActions.fall) G.animActions.fall.stop();
      if (G.animActions.slide) G.animActions.slide.stop();
      if (G.animActions.jump) G.animActions.jump.stop();
      if (G.animActions.taunt) G.animActions.taunt.stop();
      if (G.animActions.run) G.animActions.run.play();
    }
    if (G.guardActions) {
      if (G.guardActions.catch) G.guardActions.catch.stop();
      if (G.guardActions.intro) G.guardActions.intro.stop();
      if (G.guardActions.run) G.guardActions.run.play();
      if (G.guardActions.dogRun) G.guardActions.dogRun.play();
    }

    for (i = 0; i < G.obstaclePool.length; i++) {
      G.obstaclePool[i].active = false;
      G.obstaclePool[i].stumbled = false;
      G.obstaclePool[i].oncoming = false;
      G.obstaclePool[i].trainSpeed = 0.0;
      G.obstaclePool[i].hasRamp = false;
      G.obstaclePool[i].horned = false;
      G.obstaclePool[i].holder.visible = false;
      G.obstaclePool[i].holder.position.set(0, 0, -999);
      if (G.obstaclePool[i].lowMesh) G.obstaclePool[i].lowMesh.visible = false;
      if (G.obstaclePool[i].highMesh) G.obstaclePool[i].highMesh.visible = false;
      if (G.obstaclePool[i].fallbackTrain) G.obstaclePool[i].fallbackTrain.visible = false;
      if (G.obstaclePool[i].rampMesh) G.obstaclePool[i].rampMesh.visible = false;
      if (G.obstaclePool[i].headlights) G.obstaclePool[i].headlights.visible = false;
    }
    for (i = 0; i < G.coinPool.length; i++) {
      G.coinPool[i].active = false;
      G.coinPool[i].mesh.visible = false;
      G.coinPool[i].mesh.position.set(0, 0.80, -999);
    }
    for (i = 0; i < G.powerupPool.length; i++) {
      G.powerupPool[i].active = false;
      G.powerupPool[i].holder.visible = false;
      G.powerupPool[i].holder.position.set(0, 0.85, -999);
    }
    for (i = 0; i < G.particlePool.length; i++) {
      G.particlePool[i].active = false;
      G.particlePool[i].mesh.visible = false;
    }
    if (G.mysteryBoxPool) {
      for (i = 0; i < G.mysteryBoxPool.length; i++) {
        G.mysteryBoxPool[i].active = false;
        G.mysteryBoxPool[i].holder.visible = false;
        G.mysteryBoxPool[i].holder.position.set(0, 0, -999);
      }
    }
    if (G.playerShadow) {
      G.playerShadow.position.set(0, 0.03, 0);
      G.playerShadow.scale.set(1, 1, 1);
      G.playerShadow.material.opacity = 0.75;
    }

    G.score = 0.0;
    G.coins = 0;
    G.boxesCollected = 0;
    G.speed = BASE_SPEED;
    G.elapsed = 0.0;
    G.level = 0;
    G.lane = 1;
    G.targetX = 0.0;
    G.py = 0.9;
    G.vy = 0.0;
    G.grounded = true;
    G.sliding = 0.0;
    G.jumpBuf = 0.0;
    G.spawnAcc = 0.0;
    G.guardChasing = false;
    G.guardChaseTimer = 0.0;
    G.skyCoinAcc = 0.0;
    G.distSinceLastBox = 0.0;

    // Reset Powerups
    G.powerups.magnet = 0.0;
    G.powerups.jetpack = 0.0;
    G.powerups.sneakers = 0.0;
    G.powerups.multiplier = 0.0;
    G.powerups.hoverboard = 0.0;
    G.powerups.hasShield = false;

    if (G.hoverboardMesh) G.hoverboardMesh.visible = false;
    if (G.jetpackMesh) G.jetpackMesh.visible = false;
    if (G.shieldMesh) G.shieldMesh.visible = false;
    if (G.jetpackLight) G.jetpackLight.intensity = 0;

    G.player.position.set(0, 0.9, 0);
    G.player.scale.set(1, 1, 1);
    G.player.rotation.set(0, 0, 0);
    G.guardX = 0.0;

    if (G.guard) {
      G.guard.position.set(0, 0, 6.5);
      G.guard.visible = false;
    }
  }

  function resize() {
    if (!G.renderer || !G.camera) return;
    var w = window.innerWidth, h = window.innerHeight;
    G.renderer.setSize(w, h, false);
    G.camera.aspect = w / h;
    G.camera.updateProjectionMatrix();
  }

  function setCharacter(index) {
    // Jake is the dedicated hero
    return;
  }

  // ==========================================
  // Main Render Loop & Physics Simulation
  // ==========================================
  function renderLoop(now) {
    requestAnimationFrame(renderLoop);
    if (!G.inited) return;

    var dt = Math.min(0.04, Math.max(0.001, (now - G.clock) / 1000.0 || 0.016));
    G.clock = now;

    var isPlaying = (G.state === 'playing');
    var dz = G.speed * dt;

    if (G.state === 'intro') {
      G.introTimer -= dt;
      if (G.animMixer) G.animMixer.update(dt);
      if (G.guardMixer) G.guardMixer.update(dt);

      if (G.guard) {
        G.guard.visible = true;
        G.guard.position.z += (2.4 - G.guard.position.z) * Math.min(1.0, 2.5 * dt);
        G.guard.position.x = 0;
      }

      var introProgress = Math.max(0, G.introTimer / 1.25);
      var targetCamX = Math.sin(introProgress * Math.PI) * 1.5;
      var targetCamY = 3.8 - introProgress * 0.4;
      var targetCamZ = 6.4 - introProgress * 0.6;
      G.camera.position.x += (targetCamX - G.camera.position.x) * Math.min(1.0, 6.0 * dt);
      G.camera.position.y += (targetCamY - G.camera.position.y) * Math.min(1.0, 6.0 * dt);
      G.camera.position.z += (targetCamZ - G.camera.position.z) * Math.min(1.0, 6.0 * dt);
      G.camera.lookAt(0, 1.0, 0);

      if (G.introTimer <= 0) {
        G.state = 'playing';
        G.guardChasing = true;
        G.guardChaseTimer = 4.0;
        G.camera.position.set(0, 4.2, 7.4);
        if (G.animActions && G.animActions.taunt && G.animActions.run) {
          G.animActions.taunt.fadeOut(0.12);
          G.animActions.run.reset().fadeIn(0.12).play();
        }
        if (G.guardActions) {
          if (G.guardActions.intro) G.guardActions.intro.fadeOut(0.12);
          if (G.guardActions.run) G.guardActions.run.reset().fadeIn(0.12).play();
          if (G.guardActions.dogRun) G.guardActions.dogRun.reset().fadeIn(0.12).play();
        }
      }
    }

    if (isPlaying) {
      G.elapsed += dt;

      // Update Powerups Countdown
      if (G.powerups.magnet > 0) G.powerups.magnet -= dt;
      if (G.powerups.sneakers > 0) G.powerups.sneakers -= dt;
      if (G.powerups.multiplier > 0) G.powerups.multiplier -= dt;
      if (G.powerups.hoverboard > 0) {
        G.powerups.hoverboard -= dt;
        if (G.powerups.hoverboard <= 0) {
          G.powerups.hasShield = false;
          if (G.hoverboardMesh) G.hoverboardMesh.visible = false;
          if (G.shieldMesh) G.shieldMesh.visible = false;
        }
      }
      if (G.powerups.jetpack > 0) {
        G.powerups.jetpack -= dt;
        if (G.powerups.jetpack <= 0) {
          if (G.jetpackMesh) G.jetpackMesh.visible = false;
          AudioFX.stopJetpack();
        }
      }

      // Speed progression
      var currentLevel = Math.floor(G.elapsed / SPEED_UP_EVERY);
      if (currentLevel > G.level) {
        G.level = currentLevel;
        G.speed = Math.min(G.speed * SPEED_UP_FACTOR, MAX_SPEED);
        triggerCallback('onLevel', G.level, G.speed);
      }

      // Score = Distance (Meters)
      var scoreMultiplier = (G.powerups.multiplier > 0) ? 2.0 : 1.0;
      G.score += G.speed * dt * scoreMultiplier;

      // Sky Coin Highway when Jetpack is active (High altitude cruising at 8.5m)
      if (G.powerups.jetpack > 0) {
        G.skyCoinAcc = (G.skyCoinAcc || 0) + dz;
        if (G.skyCoinAcc >= 2.4) {
          G.skyCoinAcc = 0;
          spawnCoin(LANES[G.lane], 8.5, -34.0);
        }
      }

      // Spawning
      G.spawnAcc += dz;
      if (G.spawnAcc > 18.0) {
        G.spawnAcc = -spawnRow();
      }

      // Smooth Lane Lerp
      var curX = G.player.position.x;
      G.player.position.x = curX + (G.targetX - curX) * Math.min(1.0, (LANE_LERP + G.speed * 0.15) * dt);

      // Jetpack flight vs ground gravity (High 8.5m altitude soaring above city and obstacles)
      if (G.powerups.jetpack > 0) {
        G.grounded = false;
        G.py += (8.5 - G.py) * Math.min(1.0, 4.5 * dt);
        G.vy = 0.0;
      } else {
        // --- Calculate Dynamic Floor Height (Ground vs Train Roof vs Train Ramp) ---
        var floorY = 0.90;
        var px = G.player.position.x;
        var pz = G.player.position.z;

        for (var ti = 0; ti < G.obstaclePool.length; ti++) {
          var to = G.obstaclePool[ti];
          if (!to.active || to.kind !== 'full') continue;
          var top = to.holder.position;

          var dx = Math.abs(top.x - px);
          if (dx < (PLAYER_HX + to.hx + 0.15)) {
            // 1. Train Ramp Check (Smooth incline from ground to train roof)
            if (to.hasRamp) {
              var rTipZ = top.z + to.hz + 4.5;
              var rRoofZ = top.z + to.hz;
              if (pz >= (rRoofZ - 0.25) && pz <= (rTipZ + 0.25)) {
                var rampProg = (rTipZ - pz) / 4.5;
                rampProg = Math.max(0.0, Math.min(1.0, rampProg));
                var rampFloor = 0.90 + rampProg * 2.70;
                floorY = Math.max(floorY, rampFloor);
              }
            }

            // 2. Train Roof Check
            var bodyBackZ = top.z - to.hz - 0.35;
            var bodyFrontZ = top.z + to.hz + 0.35;
            if (pz >= bodyBackZ && pz <= bodyFrontZ) {
              // Supported if elevated on roof (climbed ramp, jumped onto roof, or already running)
              if (G.py >= 2.45 || (G.grounded && G.py >= 2.85)) {
                floorY = Math.max(floorY, 3.60);
              }
            }
          }
        }

        // If player walked/ran off the roof into empty space, unground and fall!
        if (G.grounded && G.py > (floorY + 0.25)) {
          G.grounded = false;
        }

        if (!G.grounded) {
          G.vy -= GRAVITY * dt;
          if (G.py > 3.5) {
            G.vy = Math.max(G.vy, -13.0); // Gentle air resistance glide
          }
          G.py += G.vy * dt;
          if (G.jumpBuf > 0) G.jumpBuf -= dt;

          if (G.py <= floorY) {
            G.py = floorY;
            G.vy = 0.0;
            G.grounded = true;

            if (G.animActions && G.animActions.jump && G.animActions.run) {
              G.animActions.jump.fadeOut(0.12);
              G.animActions.run.reset().fadeIn(0.12).play();
            }

            if (G.jumpBuf > 0) {
              G.jumpBuf = 0.0;
              jump();
            }
          }
        } else {
          // Keep snapped to dynamic surface (e.g. running smoothly up a ramp)
          G.py = floorY;
        }
      }

      // Hard clamp player Y (allow 10.0m during Jetpack)
      var maxAllowedY = (G.powerups.jetpack > 0 || G.py > 4.5) ? 10.0 : 6.0;
      G.py = Math.max(0.9, Math.min(maxAllowedY, G.py));

      var wasSliding = (G.sliding > 0);
      if (wasSliding) {
        G.sliding -= dt;
        if (G.sliding <= 0) {
          G.sliding = 0.0;
          if (G.animActions && G.animActions.slide && G.animActions.run) {
            G.animActions.slide.fadeOut(0.12);
            G.animActions.run.reset().fadeIn(0.12).play();
          }
        }
      }
      var isSliding = (G.sliding > 0);

      // Smooth visual crouching & kneeling during slide (no crushed-pancake distortion!)
      var slideDrop = isSliding ? 0.38 : 0.0;
      var targetVisualY = G.py - slideDrop;
      G.player.position.y += (targetVisualY - G.player.position.y) * Math.min(1.0, 18.0 * dt);

      // Natural aerodynamic crouch tuck (slight 0.90 scale instead of deformed 0.45!)
      var targetScaleY = isSliding ? 0.90 : 1.0;
      var targetScaleZ = isSliding ? 1.08 : 1.0;
      G.player.scale.y += (targetScaleY - G.player.scale.y) * Math.min(1.0, 14.0 * dt);
      G.player.scale.z += (targetScaleZ - G.player.scale.z) * Math.min(1.0, 14.0 * dt);

      // Update soft contact shadow under Jake
      if (G.playerShadow) {
        G.playerShadow.position.x = G.player.position.x;
        G.playerShadow.position.z = G.player.position.z;
        var surfaceY = (floorY > 1.2) ? (floorY - 0.90 + 0.04) : 0.03;
        G.playerShadow.position.y = surfaceY;
        var shadowElevation = Math.max(0.0, G.py - floorY);
        var sScale = isSliding ? 1.25 : Math.max(0.65, 1.0 - shadowElevation * 0.12);
        G.playerShadow.scale.set(sScale, 1, sScale);
        G.playerShadow.material.opacity = Math.max(0.2, 0.75 - shadowElevation * 0.15);
      }

      // Banking into lane changes
      G.player.rotation.z = (G.player.position.x - G.targetX) * -0.14;

      // Sleek athletic racing slide lean (~22 degrees forward) vs jump arch vs run lean
      var targetRotX;
      if (isSliding) {
        targetRotX = 0.38; // Dynamic athletic knee-slide lean
      } else if (!G.grounded) {
        targetRotX = -0.32; // Jump arch
      } else {
        targetRotX = -0.10; // Forward sprint lean
      }
      G.player.rotation.x += (targetRotX - G.player.rotation.x) * Math.min(1.0, 14.0 * dt);
    }

    // Update Skinned Character Mixamo Animation (Running, Jumping, Sliding, Falling, Taunting)
    if (G.animMixer) {
      var speedRatio = isPlaying ? (isSliding ? 1.0 : Math.max(0.85, G.speed / BASE_SPEED)) : 1.0;
      G.animMixer.update(dt * speedRatio);
    }
    // Update Inspector & Pitbull Dog Skeletal Animation
    if (G.guardMixer) {
      var gSpeedRatio = isPlaying ? Math.max(0.85, G.speed / BASE_SPEED) : 1.0;
      G.guardMixer.update(dt * gSpeedRatio);
    }

    // Running Stride Animation (Pumping arms & legs)
    var timeSec = (now || 0) / 1000.0;
    if (G.playerModelWrap && G.playerModelWrap.userData.limbs) {
      var l = G.playerModelWrap.userData.limbs;
      if (isPlaying && G.grounded && G.sliding <= 0) {
        // Run stride swing
        var runSwing = Math.sin(timeSec * 14.0);
        l.legL.rotation.x = runSwing * 0.85;
        l.legR.rotation.x = -runSwing * 0.85;
        l.armL.rotation.x = -runSwing * 0.75;
        l.armR.rotation.x = runSwing * 0.75;
        l.head.position.y = 0.90 + Math.abs(runSwing) * 0.04;
      } else if (isPlaying && G.sliding > 0) {
        // Knee slide posture
        l.legL.rotation.x = 1.0;
        l.legR.rotation.x = 0.2;
        l.armL.rotation.x = -0.5;
        l.armR.rotation.x = -0.5;
        l.head.position.y = 0.85;
      } else if (isPlaying && !G.grounded) {
        // Mid-air float pose
        l.legL.rotation.x = 0.4;
        l.legR.rotation.x = -0.5;
        l.armL.rotation.x = -0.9;
        l.armR.rotation.x = -0.9;
      }
    }

    // Inspector (Guard / Boboy) Chaser Animation & Dynamic Distance
    if (G.guard) {
      if (isPlaying || G.state === 'intro') {
        if (G.guardChaseTimer > 0) {
          G.guardChaseTimer -= dt;
          if (G.guardChaseTimer <= 0) {
            G.guardChasing = false;
          }
        }

        var targetGZ = G.guardChasing ? 2.4 : 6.5;
        var curGZ = G.guard.position.z;
        G.guard.position.z += (targetGZ - curGZ) * Math.min(1.0, 3.5 * dt);
        G.guard.visible = (G.guard.position.z < 6.0);

        G.guardX += (G.player.position.x - G.guardX) * Math.min(1.0, 4.5 * dt);
        G.guard.position.x = G.guardX;

        var guardBounce = Math.abs(Math.sin(timeSec * 11.0)) * 0.12;
        G.guard.position.y = guardBounce;

        if (G.guardParts && G.guardParts.legL.visible) {
          var gSwing = Math.sin(timeSec * 11.0);
          G.guardParts.legL.rotation.x = gSwing * 0.8;
          G.guardParts.legR.rotation.x = -gSwing * 0.8;
          G.guardParts.armL.rotation.x = -gSwing * 0.7;
          G.guardParts.armR.rotation.x = gSwing * 0.7;
        }

        if (G.guard.userData.model3d && !G.guardMixer) {
          G.guard.userData.model3d.rotation.z = Math.sin(timeSec * 11.0) * 0.08;
          G.guard.userData.model3d.rotation.x = -0.15;
        }
      } else if (G.state === 'over') {
        G.guard.visible = true;
        var curGZ = G.guard.position.z;
        G.guard.position.z += (0.95 - curGZ) * Math.min(1.0, 6.0 * dt);
        if (G.guardMixer) G.guardMixer.update(dt);
        if (G.animMixer) G.animMixer.update(dt);
      } else {
        G.guard.visible = false;
      }
    }

    // Scroll railway track tiles
    var scrollSpeed = isPlaying ? dz : dt * 3.5;
    for (var f = 0; f < G.floors.length; f++) {
      var tile = G.floors[f];
      tile.position.z += scrollSpeed;
      if (tile.position.z - TILE_LEN / 2.0 > KILL_Z) {
        tile.position.z -= FLOOR_TILES * TILE_LEN;
      }
    }

    // --- Realtime VFX Particle Systems ---
    if (isPlaying) {
      var px = G.player.position.x;
      var py = G.py;
      var pz = G.player.position.z;

      // 1. Jetpack Dual Fire Thrusters & Smoke Trail (mounted snug on back)
      if (G.powerups.jetpack > 0) {
        var nozzleX = [-0.18, 0.18];
        for (var n = 0; n < 2; n++) {
          var nx = px + nozzleX[n];
          var ny = py + 0.04;
          var nz = pz + 0.22;
          // Rocket Fire
          spawnParticle(
            nx + (Math.random() - 0.5) * 0.08,
            ny,
            nz,
            (Math.random() - 0.5) * 0.4,
            -4.5 - Math.random() * 2.0,
            3.5 + Math.random() * 2.0,
            Math.random() < 0.6 ? G.mat.fire : G.mat.fireYellow,
            0.22,
            0.85
          );
          // Rocket Smoke
          if (Math.random() < 0.40) {
            spawnParticle(
              nx,
              ny - 0.15,
              nz + 0.2,
              (Math.random() - 0.5) * 0.5,
              -1.8,
              2.5 + Math.random() * 2.0,
              G.mat.smoke,
              0.45,
              1.2
            );
          }
        }
        if (G.jetpackLight) {
          G.jetpackLight.intensity = 1.6 + Math.random() * 0.8;
        }
      } else {
        if (G.jetpackLight && G.jetpackLight.intensity > 0) {
          G.jetpackLight.intensity = 0;
        }
      }

      // 2. Hoverboard Thruster Sparks & Forcefield Shield Pulse
      if (G.powerups.hoverboard > 0) {
        if (G.shieldMesh) {
          G.shieldMesh.visible = true;
          G.shieldMesh.rotation.y += 2.5 * dt;
          G.shieldMesh.material.opacity = 0.22 + Math.sin(timeSec * 8.0) * 0.08;
        }
        if (Math.random() < 0.65) {
          spawnParticle(
            px + (Math.random() - 0.5) * 0.35,
            0.12,
            pz + 0.2,
            (Math.random() - 0.5) * 0.8,
            0.4 + Math.random() * 0.5,
            3.2,
            G.mat.spark,
            0.28,
            0.7
          );
        }
      } else {
        if (G.shieldMesh) G.shieldMesh.visible = false;
      }

      // 3. Sliding Sneaker Dust Trail
      if (G.sliding > 0 && G.grounded) {
        spawnParticle(
          px - 0.18, 0.08, pz + 0.1,
          -0.3 + (Math.random() - 0.5) * 0.3, 0.8 + Math.random() * 0.6, 3.5,
          G.mat.dust, 0.28, 0.9
        );
        spawnParticle(
          px + 0.18, 0.08, pz + 0.1,
          0.3 + (Math.random() - 0.5) * 0.3, 0.8 + Math.random() * 0.6, 3.5,
          G.mat.dust, 0.28, 0.9
        );
        if (Math.random() < 0.45) {
          spawnParticle(
            px + (Math.random() < 0.5 ? -0.20 : 0.20), 0.08, pz + 0.1,
            (Math.random() - 0.5) * 1.5, 1.2 + Math.random() * 1.0, 2.5,
            G.mat.goldSpark, 0.22, 0.7
          );
        }
      }

      // 4. Speed Wind Streaks VFX (when sprinting at high speeds)
      if (G.speed >= 15.0 && Math.random() < 0.60) {
        var streakSide = Math.random() < 0.5 ? -1 : 1;
        spawnParticle(
          px + streakSide * (1.1 + Math.random() * 1.5),
          py + 0.1 + Math.random() * 1.4,
          pz - 1.0 - Math.random() * 2.5,
          0, 0, G.speed * 1.5,
          G.mat.windStreak || G.mat.spark,
          0.12,
          0.32
        );
      }
    }

    // Update and billboard all active particles
    updateParticles(dt);

    if (isPlaying) {
      updateObstacles(dz, dt);
      updateCoinsAndPowerups(dz, dt);
      checkCollisions();
      triggerCallback('onScore', Math.floor(G.score), G.coins, G.speed);

      // Realtime High Score Announcement!
      if (G.bestScore > 0 && !G.newRecordAnnounced && G.score > G.bestScore) {
        G.newRecordAnnounced = true;
        AudioFX.fanfare();
        triggerCallback('onNewRecord', Math.floor(G.score));
      }
    }

    // --- Dynamic Camera Following (Elevates into sky during Jetpack flight) ---
    var isJet = (G.powerups.jetpack > 0);
    var targetCamY = isJet ? (G.py + 2.8) : (4.2 + (G.py - 0.9) * 0.45);
    var targetCamZ = isJet ? 8.8 : 7.4;
    var targetCamX = G.player.position.x * 0.35;
    var camLerpSpeed = isJet ? 4.5 : 8.5;

    G.camera.position.y += (targetCamY - G.camera.position.y) * Math.min(1.0, camLerpSpeed * dt);
    G.camera.position.z += (targetCamZ - G.camera.position.z) * Math.min(1.0, camLerpSpeed * dt);
    G.camera.position.x += (targetCamX - G.camera.position.x) * Math.min(1.0, 9.0 * dt);

    // Screen Shake Effect (Impact and crashes)
    if (G.shake > 0) {
      G.camera.position.x += (Math.random() - 0.5) * G.shake;
      G.camera.position.y += (Math.random() - 0.5) * G.shake;
      G.shake -= dt * 1.8;
      if (G.shake < 0) G.shake = 0;
    }

    // Dynamic Camera FOV (Sensation of high-speed sprint)
    var targetFov = isPlaying ? (60.0 + Math.min(9.0, (G.speed - BASE_SPEED) / (MAX_SPEED - BASE_SPEED) * 8.0)) : 60.0;
    if (Math.abs(G.camera.fov - targetFov) > 0.05) {
      G.camera.fov += (targetFov - G.camera.fov) * Math.min(1.0, 4.0 * dt);
      G.camera.updateProjectionMatrix();
    }

    var lookAtY = isJet ? (G.py * 0.72 + 1.2) : (1.2 + (G.py - 0.9) * 0.40);
    G.camera.lookAt(G.player.position.x * 0.25, lookAtY, -12);

    // Dynamic Camera Banking Roll on agile lane shifts
    var targetRoll = isPlaying ? (G.player.position.x - G.targetX) * 0.032 : 0.0;
    G.camera.rotation.z += (targetRoll - G.camera.rotation.z) * Math.min(1.0, 9.0 * dt);

    G.renderer.render(G.scene, G.camera);
  }

  function updateObstacles(dz, dt) {
    for (var i = 0; i < G.obstaclePool.length; i++) {
      var o = G.obstaclePool[i];
      if (!o.active) continue;

      if (o.oncoming) {
        // Oncoming moving train drives dynamically forward towards Jake!
        o.holder.position.z += (dz + o.trainSpeed * dt);
        // Play train horn when approaching Jake
        if (!o.horned && o.holder.position.z > -45.0 && o.holder.position.z < 10.0) {
          o.horned = true;
          AudioFX.trainHorn();
        }
      } else {
        o.holder.position.z += dz;
      }

      var maxZ = o.hz + (o.hasRamp ? 5.2 : 0.0);
      if (o.holder.position.z - maxZ > KILL_Z) {
        o.active = false;
        o.holder.visible = false;
        o.holder.position.set(0, 0, -999);
      }
    }
  }

  function updateCoinsAndPowerups(dz, dt) {
    var px = G.player.position.x;
    var py = G.py;
    var pz = G.player.position.z;
    var hasMagnet = G.powerups.magnet > 0;

    // Coins update & Magnet pull
    for (var i = 0; i < G.coinPool.length; i++) {
      var c = G.coinPool[i];
      if (!c.active) continue;
      c.mesh.position.z += dz;
      c.mesh.rotation.z += 4.5 * dt;

      // Lower sky coins smoothly down to track height if jetpack is inactive
      if (G.powerups.jetpack <= 0 && c.mesh.position.y > 5.0) {
        c.mesh.position.y += (0.80 - c.mesh.position.y) * Math.min(1.0, 5.0 * dt);
      }

      // Magnet attraction
      if (hasMagnet) {
        var dx = px - c.mesh.position.x;
        var dy = py - c.mesh.position.y;
        var dDist = Math.abs(c.mesh.position.z - pz);
        if (dDist < 12.0) {
          c.mesh.position.x += dx * Math.min(1.0, 10.0 * dt);
          c.mesh.position.y += dy * Math.min(1.0, 10.0 * dt);
        }
      }

      if (c.mesh.position.z > KILL_Z) {
        c.active = false;
        c.mesh.visible = false;
        c.mesh.position.set(0, 0.80, -999);
      }
    }

    // Power-ups update & gentle hover bobbing
    for (var j = 0; j < G.powerupPool.length; j++) {
      var p = G.powerupPool[j];
      if (!p.active) continue;
      p.holder.position.z += dz;
      p.holder.position.y = 0.85 + Math.sin(G.elapsed * 3.5 + j) * 0.08;
      p.icon.rotation.y += 3.0 * dt;
      if (p.holder.position.z > KILL_Z) {
        p.active = false;
        p.holder.visible = false;
        p.holder.position.set(0, 0.85, -999);
      }
    }

    // Mystery Boxes update & rotation
    if (G.mysteryBoxPool) {
      for (var m = 0; m < G.mysteryBoxPool.length; m++) {
        var mb = G.mysteryBoxPool[m];
        if (!mb.active) continue;
        mb.holder.position.z += dz;
        mb.bobTime += dt * 3.5;
        mb.holder.position.y = mb.yBase + Math.sin(mb.bobTime) * 0.10;
        mb.holder.rotation.y += 2.5 * dt;

        if (mb.holder.position.z > KILL_Z) {
          mb.active = false;
          mb.holder.visible = false;
          mb.holder.position.set(0, 0.85, -999);
        }
      }
    }
  }

  function checkCollisions() {
    var isSliding = (G.sliding > 0);
    var px = G.player.position.x;
    var py = isSliding ? (G.py - 0.38) : G.py;
    var pHx = PLAYER_HX;
    var pHz = 0.40;
    var pHy = isSliding ? 0.22 : 0.42;

    // 1. Coin Collection
    for (var i = 0; i < G.coinPool.length; i++) {
      var c = G.coinPool[i];
      if (!c.active) continue;
      var cp = c.mesh.position;
      if (Math.abs(cp.x - px) < 0.85 && Math.abs(cp.z) < 0.95 && Math.abs(cp.y - py) < 1.2) {
        c.active = false;
        c.mesh.visible = false;
        c.mesh.position.set(0, 0.80, -999);
        G.coins++;
        AudioFX.coin();

        // 6 Gold sparkles burst
        for (var sp = 0; sp < 6; sp++) {
          var ang = (sp / 6) * Math.PI * 2;
          spawnParticle(
            cp.x, cp.y, cp.z,
            Math.cos(ang) * 2.2, Math.sin(ang) * 2.2 + 0.6, 1.5,
            G.mat.goldSpark, 0.32, 0.85
          );
        }
        triggerCallback('onCoin', G.coins);
      }
    }

    // 2. Power-up Collection
    for (var pIdx = 0; pIdx < G.powerupPool.length; pIdx++) {
      var pw = G.powerupPool[pIdx];
      if (!pw.active) continue;
      var pp = pw.holder.position;
      if (Math.abs(pp.x - px) < 0.9 && Math.abs(pp.z) < 0.9 && Math.abs(pp.y - py) < 1.3) {
        pw.active = false;
        pw.holder.visible = false;
        pw.holder.position.set(0, 0.85, -999);
        AudioFX.powerup();

        if (pw.type === 'magnet') G.powerups.magnet = 10.0;
        else if (pw.type === 'sneakers') G.powerups.sneakers = 12.0;
        else if (pw.type === 'jetpack') {
          G.powerups.jetpack = 8.0;
          if (G.jetpackMesh) G.jetpackMesh.visible = true;
          AudioFX.startJetpack();
        } else if (pw.type === 'board') {
          activateHoverboard();
        }
        triggerCallback('onPowerup', pw.type, 10);
      }
    }

    // 3. Mystery Box (🎁 Sovg'a qutisi) Collection
    if (G.mysteryBoxPool) {
      for (var mIdx = 0; mIdx < G.mysteryBoxPool.length; mIdx++) {
        var box = G.mysteryBoxPool[mIdx];
        if (!box.active) continue;
        var bp = box.holder.position;
        if (Math.abs(bp.x - px) < 0.95 && Math.abs(bp.z) < 0.95 && Math.abs(bp.y - py) < 1.3) {
          box.active = false;
          box.holder.visible = false;
          box.holder.position.set(0, 0.85, -999);
          G.boxesCollected = (G.boxesCollected || 0) + 1;
          AudioFX.mysteryBox();

          // Confetti celebration burst (20 particles)
          for (var cf = 0; cf < 20; cf++) {
            var cfAng = Math.random() * Math.PI * 2;
            var cfSpd = 2.0 + Math.random() * 3.5;
            var cfMat = (cf % 3 === 0) ? G.mat.goldSpark : (cf % 3 === 1 ? G.mat.spark : G.mat.fire);
            spawnParticle(
              bp.x, bp.y, bp.z,
              Math.cos(cfAng) * cfSpd, Math.sin(cfAng) * cfSpd + 1.2, (Math.random() - 0.5) * 2.5,
              cfMat, 0.55, 1.2
            );
          }
          triggerCallback('onMysteryBox', G.boxesCollected);
        }
      }
    }

    // 4. Obstacle Collision (Immune during Jetpack flight)
    if (G.powerups.jetpack > 0) return;

    for (var j = 0; j < G.obstaclePool.length; j++) {
      var o = G.obstaclePool[j];
      if (!o.active) continue;

      var op = o.holder.position;
      var dx = Math.abs(op.x - px);
      var xOverlap = (pHx + o.hx) - dx;
      if (xOverlap <= 0) continue;

      // Subway Surfers: Train Roof & Ramp Special Handling
      if (o.kind === 'full') {
        var rTip = op.z + o.hz + 4.5;
        var rRoof = op.z + o.hz;
        var rBack = op.z - o.hz;

        // If train has a ramp and player is running on the ramp: NEVER CRASH!
        if (o.hasRamp && pz >= (rRoof - 0.40) && pz <= (rTip + 0.40)) {
          continue;
        }

        // If player is on or above the train roof: NEVER CRASH!
        if (pz >= (rBack - 0.40) && pz <= (rRoof + 0.40)) {
          if (py >= 2.45 || (py - pHy) >= 2.15) {
            continue; // Safely running on roof or soaring in the air!
          }
        }
      }

      if (Math.abs(op.z) > pHz + o.hz) continue;

      var oTop = op.y + o.hy;
      var oBot = op.y - o.hy;
      var pTop = py + pHy;
      var pBot = py - pHy;

      if (pTop > oBot && pBot < oTop) {
        if (o.kind === 'full') {
          // If feet are at or landing onto roof level, or already supported: NO CRASH!
          if (pBot >= (oTop - 0.45) || py >= 2.45) {
            continue;
          }
        }

        // Hoverboard Shield absorbs collision!
        if (G.powerups.hasShield) {
          G.powerups.hasShield = false;
          G.powerups.hoverboard = 0.0;
          if (G.hoverboardMesh) G.hoverboardMesh.visible = false;
          if (G.shieldMesh) G.shieldMesh.visible = false;
          AudioFX.shieldBreak();
          G.shake = 0.35;
          // Burst 18 electric blue sparks
          for (var b = 0; b < 18; b++) {
            var bAng = Math.random() * Math.PI * 2;
            var bSpd = 2.0 + Math.random() * 3.5;
            spawnParticle(
              px, py + 0.5, 0,
              Math.cos(bAng) * bSpd, Math.sin(bAng) * bSpd, (Math.random() - 0.5) * 3.0,
              G.mat.spark, 0.45, 1.1
            );
          }
          o.active = false;
          o.holder.visible = false;
          o.holder.position.set(0, 0, -999);
          triggerCallback('onShieldBreak');
          return;
        }

        // --- Stumble vs Fatal Crash Check ---
        // Side graze when changing lanes past a train/barrier, or clipping toes on a low hurdle
        var isSideGraze = (xOverlap < 0.28) && (Math.abs(op.z) < o.hz * 0.85);
        var isHurdleClip = (o.kind === 'low') && (pBot > oBot + 0.20);

        if ((isSideGraze || isHurdleClip) && !o.stumbled) {
          o.stumbled = true;
          if (G.guardChasing) {
            // Guard was ALREADY chasing right behind Jake! Inspector catches Jake!
            G.state = 'over';
            AudioFX.stopJetpack();
            AudioFX.crash();
            AudioFX.whistle();
            AudioFX.dogBark();
            G.shake = 0.52;

            if (G.animActions && G.animActions.fall) {
              if (G.animActions.run) G.animActions.run.fadeOut(0.06);
              if (G.animActions.jump) G.animActions.jump.fadeOut(0.06);
              if (G.animActions.slide) G.animActions.slide.fadeOut(0.06);
              G.animActions.fall.reset().fadeIn(0.06).play();
            }

            if (G.guardActions && G.guardActions.catch) {
              if (G.guardActions.run) G.guardActions.run.fadeOut(0.06);
              if (G.guardActions.dogRun) G.guardActions.dogRun.fadeOut(0.06);
              G.guardActions.catch.reset().fadeIn(0.06).play();
            }

            triggerCallback('onGameOver', Math.floor(G.score), G.coins, G.boxesCollected || 0);
            return;
          } else {
            // First stumble: Guard alerts with whistle & bark, begins chasing immediately behind Jake!
            G.guardChasing = true;
            G.guardChaseTimer = 7.0; // 7 seconds chase
            AudioFX.stumble();
            AudioFX.whistle();
            AudioFX.dogBark();
            G.shake = 0.22;
            G.targetX = LANES[G.lane];
            if (G.guardActions) {
              if (G.guardActions.run) G.guardActions.run.setEffectiveTimeScale(1.35);
              if (G.guardActions.dogRun) G.guardActions.dogRun.setEffectiveTimeScale(1.35);
            }
            for (var st = 0; st < 10; st++) {
              spawnParticle(
                px, py + 0.2, 0,
                (Math.random() - 0.5) * 1.8, Math.random() * 1.6, 2.5,
                G.mat.dust, 0.35, 1.1
              );
            }
            continue; // Keep running!
          }
        }

        // Fatal Crash Game Over (Sweep Fall animation)
        G.state = 'over';
        AudioFX.stopJetpack();
        AudioFX.crash();
        AudioFX.dogBark();
        G.shake = 0.48;

        if (G.animActions && G.animActions.fall) {
          if (G.animActions.run) G.animActions.run.fadeOut(0.06);
          if (G.animActions.jump) G.animActions.jump.fadeOut(0.06);
          if (G.animActions.slide) G.animActions.slide.fadeOut(0.06);
          G.animActions.fall.reset().fadeIn(0.06).play();
        }

        if (G.guardActions && G.guardActions.catch) {
          if (G.guardActions.run) G.guardActions.run.fadeOut(0.06);
          if (G.guardActions.dogRun) G.guardActions.dogRun.fadeOut(0.06);
          G.guardActions.catch.reset().fadeIn(0.06).play();
        }

        // Impact explosion of fire and smoke
        for (var c = 0; c < 24; c++) {
          var cAng = Math.random() * Math.PI * 2;
          var cSpd = 2.5 + Math.random() * 4.0;
          spawnParticle(
            px, py + 0.5, 0,
            Math.cos(cAng) * cSpd, Math.sin(cAng) * cSpd, (Math.random() - 0.5) * 4.0,
            Math.random() < 0.5 ? G.mat.fire : G.mat.smoke, 0.55, 1.3
          );
        }
        triggerCallback('onGameOver', Math.floor(G.score), G.coins, G.boxesCollected || 0);
        return;
      }
    }
  }


  // Global RunnerGame Export
  window.RunnerGame = {
    init: init,
    start: start,
    stop: stop,
    reset: reset,
    resize: resize,
    moveLeft: moveLeft,
    moveRight: moveRight,
    jump: jump,
    slide: slide,
    activateHoverboard: activateHoverboard,
    setCharacter: setCharacter,
    playFanfare: function () { AudioFX.fanfare(); },
    playHorn: function () { AudioFX.trainHorn(); },
    toggleSound: function () { return AudioFX.toggleMute(); },
    isMuted: function () { return AudioFX.isMuted(); },
    get state() { return G.state; },
    get score() { return Math.floor(G.score); },
    get coins() { return G.coins; },
    get boxes() { return G.boxesCollected || 0; },
  };

})();
