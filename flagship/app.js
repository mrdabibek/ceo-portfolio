
var BASE = (typeof window !== 'undefined' && window.BASE) || 'http://localhost:8090';
if (typeof window !== 'undefined') window.BASE = BASE;

function getLiveBaseUrl() {
  if (location.protocol.startsWith('http')) {
    return location.origin;
  }
  return 'https://ceo-portfolio.vercel.app';
}
// ==========================================================
// NOVA Studio — Flagship JavaScript Architecture
// ==========================================================

// Audio synthesizer for haptics
let audioCtx = null;
let soundOn = true;

function initAudio() {
  if (!audioCtx) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch(e) {}
  }
}

function playSnd(type) {
  if (!soundOn) return;
  initAudio();
  if (!audioCtx) return;
  if (audioCtx.state === 'suspended') audioCtx.resume();
  const now = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.connect(gain);
  gain.connect(audioCtx.destination);

  if (type === 'click') {
    osc.frequency.setValueAtTime(800, now);
    osc.frequency.exponentialRampToValueAtTime(300, now + 0.04);
    gain.gain.setValueAtTime(0.04, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.04);
    osc.start(now);
    osc.stop(now + 0.04);
  } else if (type === 'chime') {
    osc.frequency.setValueAtTime(587.33, now);
    osc.frequency.setValueAtTime(880, now + 0.05);
    gain.gain.setValueAtTime(0.06, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
    osc.start(now);
    osc.stop(now + 0.25);
  } else if (type === 'gold') {
    osc.frequency.setValueAtTime(987.77, now); // B5
    osc.frequency.setValueAtTime(1318.51, now + 0.06); // E6
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
    osc.start(now);
    osc.stop(now + 0.3);
  }
}

function toggleSnd() {
  soundOn = !soundOn;
  const btn = document.getElementById('sndToggle');
  btn.textContent = soundOn ? '🔊 Sound: ON' : '🔇 Sound: OFF';
  playSnd('click');
}

// Progress Bar
addEventListener('scroll', () => {
  const h = document.documentElement;
  document.getElementById('progress').style.width = (h.scrollTop / (h.scrollHeight - h.clientHeight) * 100) + '%';
});

// Custom Magnetic Cursor
const cur = document.getElementById('cursor'), dot = document.getElementById('cdot');
addEventListener('mousemove', e => {
  cur.style.left = e.clientX - 18 + 'px';
  cur.style.top = e.clientY - 18 + 'px';
  dot.style.left = e.clientX - 3 + 'px';
  dot.style.top = e.clientY - 3 + 'px';
});

// 3D Card Tilt
document.querySelectorAll('.tilt').forEach(el => el.addEventListener('mousemove', e => {
  const r = el.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width - 0.5;
  const y = (e.clientY - r.top) / r.height - 0.5;
  el.style.transform = `perspective(900px) rotateY(${x * 12}deg) rotateX(${-y * 12}deg)`;
}));
document.querySelectorAll('.tilt').forEach(el => el.addEventListener('mouseleave', () => {
  el.style.transform = '';
}));

// Preloader
let lp = 0;
const li = setInterval(() => {
  lp = Math.min(100, lp + Math.random() * 20);
  document.getElementById('lnum').textContent = Math.floor(lp) + '%';
  document.getElementById('lbar').style.width = lp + '%';
  if (lp >= 100) {
    clearInterval(li);
    setTimeout(() => {
      document.getElementById('loader').classList.add('done');
    }, 250);
  }
}, 70);

// Intersection Observer for Reveal & Counters
const io = new IntersectionObserver(es => es.forEach(e => {
  if (e.isIntersecting) {
    e.target.classList.add('on');
    e.target.querySelectorAll('.cnt').forEach(runCnt);
    if (e.target.classList.contains('cnt')) runCnt(e.target);
  }
}), { threshold: 0.15 });

document.querySelectorAll('.rv, .cnt').forEach(el => io.observe(el));

function runCnt(el) {
  if (el.dataset.done) return;
  el.dataset.done = '1';
  const n = +el.dataset.n;
  let i = 0;
  const step = Math.max(1, Math.ceil(n / 35));
  const t = setInterval(() => {
    i += step;
    if (i >= n) {
      i = n;
      clearInterval(t);
    }
    el.textContent = i;
  }, 35);
}

// Typewriter
const words = ['ship fast.', 'scale clean.', 'feel premium.', 'win $10K contracts.', 'launch on schedule.'];
let wi = 0, ci = 0, del = false;
(function type() {
  const w = words[wi], t = document.getElementById('typed');
  t.textContent = w.slice(0, ci);
  ci += del ? -1 : 1;
  if (!del && ci > w.length + 8) del = true;
  else if (del && ci <= 0) {
    del = false;
    wi = (wi + 1) % words.length;
  }
  setTimeout(type, del ? 35 : 70);
})();

// Particle Canvas Mesh
const nc = document.getElementById('net'), nx = nc.getContext('2d');
let pts = [];
function nsize() {
  nc.width = nc.offsetWidth;
  nc.height = nc.offsetHeight;
  pts = Array.from({ length: 65 }, () => ({
    x: Math.random() * nc.width,
    y: Math.random() * nc.height,
    vx: (Math.random() - 0.5) * 0.5,
    vy: (Math.random() - 0.5) * 0.5
  }));
}
nsize();
addEventListener('resize', nsize);

(function draw() {
  nx.clearRect(0, 0, nc.width, nc.height);
  pts.forEach(p => {
    p.x += p.vx;
    p.y += p.vy;
    if (p.x < 0 || p.x > nc.width) p.vx *= -1;
    if (p.y < 0 || p.y > nc.height) p.vy *= -1;
    nx.fillStyle = '#e9c46a88';
    nx.fillRect(p.x, p.y, 2, 2);
  });
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
      const d = Math.hypot(dx, dy);
      if (d < 130) {
        nx.strokeStyle = `rgba(233,196,106,${(1 - d / 130) * 0.22})`;
        nx.beginPath();
        nx.moveTo(pts[i].x, pts[i].y);
        nx.lineTo(pts[j].x, pts[j].y);
        nx.stroke();
      }
    }
  }
  requestAnimationFrame(draw);
})();

// Live Telemetry Financial Chart Canvas
const cc = document.getElementById('chart'), cx = cc.getContext('2d');
let cd = Array.from({ length: 32 }, () => 45 + Math.random() * 40);

function drawChart() {
  cd.push(50 + Math.random() * 38);
  cd.shift();
  cx.clearRect(0, 0, cc.width, cc.height);
  
  // Grid lines
  cx.strokeStyle = 'rgba(255,255,255,0.06)';
  cx.lineWidth = 1;
  for (let y = 30; y < cc.height; y += 40) {
    cx.beginPath();
    cx.moveTo(0, y);
    cx.lineTo(cc.width, y);
    cx.stroke();
  }

  // Gradient area
  const g = cx.createLinearGradient(0, 0, 0, cc.height);
  g.addColorStop(0, 'rgba(233,196,106,0.4)');
  g.addColorStop(0.7, 'rgba(233,196,106,0.08)');
  g.addColorStop(1, 'transparent');

  cx.beginPath();
  cd.forEach((v, i) => {
    const x = i / (cd.length - 1) * cc.width;
    const y = cc.height - (v / 100 * (cc.height - 30)) - 15;
    if (i === 0) cx.moveTo(x, y);
    else cx.lineTo(x, y);
  });
  cx.strokeStyle = '#e9c46a';
  cx.lineWidth = 2.5;
  cx.stroke();

  cx.lineTo(cc.width, cc.height);
  cx.lineTo(0, cc.height);
  cx.fillStyle = g;
  cx.fill();

  // Glow pulse node at current head
  const lastX = cc.width;
  const lastY = cc.height - (cd[cd.length - 1] / 100 * (cc.height - 30)) - 15;
  cx.beginPath();
  cx.arc(lastX - 2, lastY, 4, 0, Math.PI * 2);
  cx.fillStyle = '#4ade80';
  cx.shadowColor = '#4ade80';
  cx.shadowBlur = 10;
  cx.fill();
  cx.shadowBlur = 0;
}
setInterval(drawChart, 900);
drawChart();

// Selected Works Showcase
const WORKS = [
  ['saas', 'Plainbase SaaS Admin', 'Go & Python backend + JWT Auth + SQLite/Postgres', '$9.8K', '../portfolio-51-fullstack-saas/index.html', 'ceo51'],
  ['mobile', 'PulseFit Mobile App', 'Flutter & Dart iOS/Android health engine with 60 FPS motion', '$8.0K', '../portfolio-54-flutter-fitness/index.html', 'ceo54'],
  ['mobile', 'Nova Bank iPhone 18', 'Native Swift & iOS 18 Dynamic Island banking simulator', '$8.5K', '../portfolio-55-iphone-banking/index.html', 'ceo55'],
  ['game', 'Moss Temple 2D', 'Playable HTML5 Canvas physics platformer with collectibles', '$6.0K', '../portfolio-61-platformer-game/index.html', 'ceo61'],
  ['ai', 'Ember AI Concierge', 'Conversational AI chatbot with neural speech synthesis', '$5.0K', '../portfolio-65-ai-chatbot/index.html', 'ceo65'],
  ['os', 'Boreal 11 WebOS', 'Draggable window desktop environment with terminal & files', '$7.0K', '../portfolio-57-windows11-clone/index.html', 'ceo57'],
  ['ai', 'Loom AI Automation', 'Visual node-based pipeline editor with cron triggers', '$7.5K', '../portfolio-66-ai-automation/index.html', 'ceo66'],
  ['game', 'Neon Circuit 3D Racer', 'Pseudo-3D arcade racer with speedometer & drifting mechanics', '$7.0K', '../portfolio-62-racer-game/index.html', 'ceo62']
];

let wf = 'all';
const wg = document.getElementById('wgrid');
let liveW = WORKS.slice();

function renderW() {
  wg.innerHTML = '';
  liveW.filter(w => wf === 'all' || w[0] === wf).forEach(w => {
    wg.innerHTML += `
      <div class="wcard rv on" onclick="openW('${w[1]}')">
        <div class="wimgw">
          <img loading="lazy" src="https://picsum.photos/seed/${w[5]}/700/400" alt="${w[1]}">
          <span class="go">→</span>
        </div>
        <div>
          <span>${w[0].toUpperCase()} · ${w[3]}</span>
          <br>
          <b>${w[1]}</b>
          <p>${w[2]}</p>
        </div>
      </div>`;
  });
}
renderW();

// REAL backend: project list via gateway, fallback to bundled WORKS when offline/empty
fetch(BASE + '/api/projects')
  .then(r => { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
  .then(items => {
    const arr = Array.isArray(items) ? items : items.items;
    if (arr && arr.length) {
      liveW = arr.map(p => [
        String(p.cat || 'saas').toLowerCase(),
        p.title || p.slug,
        p.demo ? ('Live demo: ' + p.demo) : 'Live case study — open the demo.',
        (typeof p.price === 'number') ? ('$' + p.price.toLocaleString()) : (p.price || ''),
        p.demo || '#',
        p.slug || 'live'
      ]);
      renderW();
    }
  })
  .catch(() => {});

document.querySelectorAll('.filters button').forEach(b => b.onclick = () => {
  playSnd('click');
  document.querySelectorAll('.filters button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  wf = b.dataset.f;
  renderW();
});

let currentModalWork = null;

function openW(title) {
  playSnd('chime');
  const w = WORKS.find(x => x[1] === title);
  if (!w) return;
  currentModalWork = w;

  document.getElementById('wt').textContent = w[1];
  document.getElementById('wcat').textContent = w[0].toUpperCase() + ' · ' + w[3];
  document.getElementById('wd').textContent = w[2] + ' — Engineered for clean architecture, zero bloat, and enterprise security. Test the live simulator below.';
  document.getElementById('wprice').textContent = w[3] + ' Fixed Scope';
  document.getElementById('wopen').href = w[4];
  
  const frame = document.getElementById('wframe');
  frame.src = w[4];

  document.getElementById('wmodal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeW() {
  playSnd('click');
  document.getElementById('wmodal').style.display = 'none';
  document.body.style.overflow = '';
  document.getElementById('wframe').src = 'about:blank';
}

function reloadW() {
  playSnd('click');
  document.getElementById('wframe').contentWindow.location.reload();
}

function expandW() {
  if (currentModalWork) {
    window.open(currentModalWork[4], '_blank');
  }
}

function setWDevice(dev) {
  playSnd('click');
  const wrap = document.getElementById('wframeWrap');
  wrap.className = 'wframe-wrap ' + dev;
  document.getElementById('wdevDesktop').classList.toggle('active', dev === 'desktop');
  document.getElementById('wdevMobile').classList.toggle('active', dev === 'mobile');
}

function copyModalPitch() {
  if (!currentModalWork) return;
  playSnd('chime');
  const cleanPath = currentModalWork[4].replace(/^\.\./, '');
  const fullLiveUrl = getLiveBaseUrl() + cleanPath;
  const pitch = `Hi! I have built and launched "${currentModalWork[1]}" (${currentModalWork[3]} tier) with this exact architecture: ${currentModalWork[2]}. Live case study: ${fullLiveUrl}. I can build your system on time and within fixed budget. Let's discuss your roadmap!`;
  copyText(pitch, '✓ Proposal pitch copied to clipboard!');
}

function copyText(t, okMsg) {
  if (navigator.clipboard && window.isSecureContext !== false) {
    navigator.clipboard.writeText(t).then(
      () => alert(okMsg),
      () => fallbackCopy(t, okMsg)
    );
  } else {
    fallbackCopy(t, okMsg);
  }
}

function fallbackCopy(t, okMsg) {
  try {
    const ta = document.createElement('textarea');
    ta.value = t;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    alert(okMsg);
  } catch (e) {
    alert(t);
  }
}

document.getElementById('wmodal').addEventListener('click', e => {
  if (e.target.id === 'wmodal') closeW();
});

// Playground Tabs
function ptab(n) {
  playSnd('click');
  [0, 1, 2].forEach(i => {
    document.getElementById('pp' + i).style.display = i === n ? 'block' : 'none';
  });
  document.querySelectorAll('.ptabs button').forEach((b, i) => b.classList.toggle('on', i === n));
}

// Project Estimator (REAL backend: POST BASE/api/estimate, offline fallback: local calc)
let estT = null;
function est() {
  const a = +document.getElementById('r1').value;
  const b = +document.getElementById('r2').value;
  const c = +document.getElementById('r3').value;
  const d = +document.getElementById('r4').value;

  document.getElementById('r1Val').textContent = 'Level ' + a;
  document.getElementById('r2Val').textContent = 'Level ' + b;
  document.getElementById('r3Val').textContent = c + ' APIs';
  document.getElementById('r4Val').textContent = 'Level ' + d;

  const fallback = () => {
    const price = 2500 + a * 650 + b * 600 + c * 400 + d * 550;
    const weeks = Math.max(2, Math.ceil(1.5 + a * 0.35 + b * 0.35 + c * 0.25 + d * 0.2));
    document.getElementById('eprice').textContent = '$' + price.toLocaleString();
    document.getElementById('eweek').textContent = weeks + ' weeks';
  };

  clearTimeout(estT);
  estT = setTimeout(() => {
    fetch(BASE + '/api/estimate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: a, design: b, integrations: c })
    })
      .then(r => { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
      .then(j => {
        if (typeof j.price === 'number') document.getElementById('eprice').textContent = '$' + j.price.toLocaleString();
        else fallback();
        if (typeof j.weeks === 'number') document.getElementById('eweek').textContent = j.weeks + ' weeks';
      })
      .catch(fallback);
  }, 250);
}

function copyScopeSummary() {
  playSnd('chime');
  const price = document.getElementById('eprice').textContent;
  const weeks = document.getElementById('eweek').textContent;
  const summary = `Fixed-Scope Proposal Summary:
- Project Quote: ${price}
- Guaranteed Timeline: ${weeks}
- Stack: Go/Python Backend, Production Frontend, Custom APIs & QA
- Guarantee: 100% on-time delivery + 30-day post-launch support.`;
  copyText(summary, '✓ Scope summary copied to clipboard!');
}

// Reflex Speed Benchmark
let hits = 0, streak = 0, t0 = 0;
const tgt = document.getElementById('tgt');

function startGame() {
  playSnd('click');
  hits = 0;
  streak = 0;
  hitsEl();
  moveT();
  t0 = performance.now();
}

function hitsEl() {
  document.getElementById('hits').textContent = hits + '/10';
  document.getElementById('streak').textContent = streak + 'x';
}

function moveT() {
  tgt.style.left = (Math.random() * 80 + 5) + '%';
  tgt.style.top = (Math.random() * 70 + 5) + '%';
}

tgt.onclick = () => {
  hits++;
  streak++;
  playSnd('gold');
  hitsEl();
  if (hits >= 10) {
    const secs = (performance.now() - t0) / 1000;
    const s = secs.toFixed(2) + 's';
    document.getElementById('best').textContent = s;
    // REAL backend: persist reflex best, ignore when offline
    fetch(BASE + '/api/scores', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ game: 'reflex', name: 'guest', value: Math.min(10000, Math.max(1, Math.round(secs * 1000))) })
    }).catch(() => {});
    playSnd('chime');
    hits = 0;
    streak = 0;
    setTimeout(hitsEl, 1000);
  }
  moveT();
};
moveT();

// NOVA AI Concierge (REAL backend: POST BASE/api/ai, offline fallback: keyword replies)
function mockReply(v) {
  let r = "I build $10K-grade digital products across SaaS, mobile, games, and AI pipelines. Share your specs and I will draft a fixed-scope roadmap within 24 hours.";
  if (v.includes('price') || v.includes('tier') || v.includes('cost')) {
    r = `Pricing Tiers:
• Sprint MVP: $1,500 (7 business days)
• Product $10K Build: $9,800 (4–6 weeks full SaaS / Mobile App)
• Partner / CTO: $3,000/mo (Ongoing weekly sprints & architecture)`;
  } else if (v.includes('stack') || v.includes('tech')) {
    r = `Technical Stack:
• Core: Python, Go (Golang), C++, REST, WebSocket
• Graphics & Games: Godot Engine, Three.js, Canvas 2D, WebGL
• Mobile: Flutter, Dart, Swift (iOS 18), Kotlin
• Data: SQLite, PostgreSQL, Redis, Docker`;
  } else if (v.includes('time') || v.includes('deadline')) {
    r = `Delivery Milestones:
• MVP Slices: 7–10 days
• Full SaaS Platforms: 4–5 weeks
• Mobile & Games: 4–6 weeks
We operate with strict weekly sprint demos and 100% on-time record.`;
  } else if (v.includes('mobile') || v.includes('app')) {
    r = `Mobile Architecture:
We build high-performance cross-platform Flutter and native Swift iOS apps with offline-first SQLite synchronization, dynamic animations, and App Store readiness.`;
  } else if (v.includes('game') || v.includes('godot')) {
    r = `Game Development:
We engineer 2D/3D browser games (Three.js/Canvas) and Godot Engine commercial builds with responsive controls, custom physics, and particle shaders.`;
  } else if (v.includes('hi') || v.includes('hello')) {
    r = "Greetings! What type of product are you aiming to launch? A SaaS platform, mobile application, or high-performance game?";
  }
  return r;
}

function typeInto(chat, el, text) {
  let i = 0;
  const t = setInterval(() => {
    el.textContent = text.slice(0, ++i);
    chat.scrollTop = 9999;
    if (i >= text.length) clearInterval(t);
  }, 16);
}

function send() {
  const cin = document.getElementById('cin');
  const q = cin.value.trim();
  if (!q) return;
  playSnd('click');

  const chat = document.getElementById('chat');
  const me = document.createElement('div');
  me.className = 'msg me';
  me.textContent = q;
  chat.appendChild(me);
  cin.value = '';
  setTimeout(() => { chat.scrollTop = 9999; }, 50);

  const b = document.createElement('div');
  b.className = 'msg bot';
  b.textContent = '…';
  chat.appendChild(b);

  fetch(BASE + '/api/ai', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q })
  })
    .then(r => { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
    .then(j => typeInto(chat, b, String(j.a || j.answer || j.reply || mockReply(q.toLowerCase()))))
    .catch(() => typeInto(chat, b, mockReply(q.toLowerCase())));
}

// Testimonials Carousel
const T = [
  ['“Shipped our entire SaaS platform in 5 weeks. Architecture is brilliantly clean and scalable.”', '— Daniel K., SaaS Founder · $9.8K Project'],
  ['“The iPhone app hit top charts. Animations feel buttery smooth and native at 120Hz.”', '— Sara M., Startup CEO · $8K App Build'],
  ['“Our custom AI automation pipeline saves us 30h a week. It paid for itself in the first month.”', '— Timur A., Operations Lead · $5K Automation']
];
let ti = 0;
function tgo(n) {
  ti = n;
  document.getElementById('tq').textContent = T[n][0];
  document.getElementById('ta').textContent = T[n][1];
  document.querySelectorAll('.tdots button').forEach((b, i) => b.classList.toggle('on', i === n));
}
setInterval(() => tgo((ti + 1) % 3), 5500);

// Pricing Package Selector
function choosePackage(pkg) {
  playSnd('chime');
  const sel = document.getElementById('b');
  for (let i = 0; i < sel.options.length; i++) {
    if (sel.options[i].text.includes(pkg) || sel.options[i].value.includes(pkg)) {
      sel.selectedIndex = i;
      break;
    }
  }
  goContact();
}

function goContact() {
  document.getElementById('contact').scrollIntoView({ behavior: 'smooth' });
}

// Contact Form Submit (REAL backend: POST BASE/api/contact, offline fallback: local success)
function submitF(e) {
  e.preventDefault();
  const n = document.getElementById('n').value.trim();
  const em = document.getElementById('e').value.trim();
  const m = document.getElementById('m').value.trim();
  if (!n || !em || !m) return false;

  const ok = () => {
    playSnd('gold');
    document.getElementById('cok').style.display = 'block';
    e.target.querySelector('button').textContent = '✓ Request Dispatched — Reply in 24h';
  };

  fetch(BASE + '/api/contact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: n, email: em, msg: m, budget: document.getElementById('b').value })
  })
    .then(async r => {
      const j = await r.json().catch(() => ({}));
      if (j.error === 'offline') { ok(); return; } // offline stub from api-runtime
      if (r.status === 201 || r.ok || j.id) { ok(); return; }
      alert('Contact backend: ' + (j.error || r.status));
    })
    .catch(() => ok());
  return false;
}

// Mobile Menu
document.getElementById('burger').onclick = () => {
  playSnd('click');
  const m = document.getElementById('mmenu');
  m.style.display = m.style.display === 'flex' ? 'none' : 'flex';
};
function closeM() {
  document.getElementById('mmenu').style.display = 'none';
}

// ESC closes modal / mobile menu; resize resets stale mobile menu
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.getElementById('wmodal').style.display === 'flex') closeW();
    closeM();
  }
});
addEventListener('resize', () => {
  if (innerWidth > 900) closeM();
});
