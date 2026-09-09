(() => {
  'use strict';

  const stage = document.querySelector('.hero-stage');
  const heroPin = document.querySelector('.hero-pin');
  const bands = [...document.querySelectorAll('.band')];

  // band map: ranges in scroll progress through the pinned hero
  const BANDS = [
    { el: bands[0], a: 0.00, b: 0.26 },
    { el: bands[1], a: 0.34, b: 0.62 },
    { el: bands[2], a: 0.70, b: 1.00 },
  ];

  // ---- split headlines into word spans, seeded so every load is identical ----
  function splitWords(node) {
    const text = node.textContent.trim();
    const words = text.split(/\s+/);
    const sr = document.createElement('span');
    sr.className = 'sr-only';
    sr.textContent = text;
    const visual = document.createElement('span');
    visual.className = 'split';
    visual.setAttribute('aria-hidden', 'true');
    words.forEach((word, i) => {
      const w = document.createElement('span');
      w.className = 'w';
      w.textContent = word;
      w.style.setProperty('--th', ((i / words.length) * 0.58).toFixed(3));
      visual.appendChild(w);
      if (i < words.length - 1) visual.appendChild(document.createTextNode(' '));
    });
    node.textContent = '';
    node.append(sr, visual);
  }
  document.querySelectorAll('[data-split]').forEach(splitWords);

  // ---- helpers ----
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const smoothstep = (p, e0, e1) => {
    const t = clamp((p - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  };

  function heroProgress() {
    const total = heroPin.offsetHeight - window.innerHeight;
    if (total <= 0) return 1;
    return clamp(-heroPin.getBoundingClientRect().top / total, 0, 1);
  }

  // ---- scroll-driven state, written only on change ----
  let lastP = -1;
  const cache = bands.map(() => ({ op: -1, k: -1 }));

  function draw(p) {
    if (!scrubOn) return;   // the static hero owns the stage; CSS alone drives it
    const ps = p.toFixed(3);
    if (ps !== lastP) {
      lastP = ps;
      stage.style.setProperty('--p', ps);
    }
    BANDS.forEach(({ el, a, b }, i) => {
      const f = Math.min(0.045, (b - a) / 3);
      let opacity;
      if (i === 0) opacity = 1 - smoothstep(p, b - f, b);
      else if (i === BANDS.length - 1) opacity = smoothstep(p, a, a + f);
      else opacity = smoothstep(p, a, a + f) * (1 - smoothstep(p, b - f, b));

      const k = clamp((p - a) / Math.min(0.05, (b - a) * 0.35), 0, 1);
      const effK = i === 0 ? Math.max(k, loadK) : k;

      const c = cache[i];
      const opR = Math.round(opacity * 1000) / 1000;
      if (opR !== c.op) {
        c.op = opR;
        el.style.opacity = opR;
        el.setAttribute('data-active', opR > 0.02 ? '1' : '0');
      }
      const kR = Math.round(effK * 1000) / 1000;
      if (kR !== c.k) {
        c.k = kR;
        el.style.setProperty('--k', kR);
      }
    });
  }

  // ---- drive state ----
  let target = 0, shown = 0, rafId = null, lastTick = 0;

  // band one assembles once on load, then hands over to scroll
  let loadK = 0;
  const loadStart = performance.now();
  function rampIn(now) {
    loadK = Math.min(1, ((now || loadStart) - loadStart) / 850);
    draw(shown);
    if (loadK < 1) requestAnimationFrame(rampIn);
  }

  // ---- rAF drive: ease toward the scroll target, rest when converged ----

  function tick(now) {
    const dt = Math.min(100, now - (lastTick || now));
    lastTick = now;
    shown += (target - shown) * (1 - Math.pow(1 - 0.16, dt / 16.667));
    if (Math.abs(target - shown) < 0.0005) {
      shown = target;
      rafId = null;
      lastTick = 0;
    } else {
      rafId = requestAnimationFrame(tick);
    }
    draw(shown);
  }

  let heroOnScreen = true;
  new IntersectionObserver((entries) => {
    entries.forEach((e) => { heroOnScreen = e.isIntersecting; });
  }, { threshold: 0 }).observe(heroPin);

  function onScroll() {
    target = heroProgress();
    if (rafId === null && heroOnScreen) rafId = requestAnimationFrame(tick);
  }

  // ---- the five static-hero gates, mirrored from style.css, kept live ----
  const GATES = [
    '(max-width: 720px)',
    '(orientation: portrait) and (max-width: 1024px)',
    '(orientation: portrait) and (pointer: coarse)',
    '(orientation: landscape) and (pointer: coarse) and (max-height: 560px)',
    '(prefers-reduced-motion: reduce)',
  ];
  const MQLS = GATES.map((q) => matchMedia(q));
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');

  let scrubOn = false;

  function enableScrub() {
    if (scrubOn) return;
    scrubOn = true;
    addEventListener('scroll', onScroll, { passive: true });
    addEventListener('resize', onScroll);
    cache.forEach((c) => { c.op = -1; c.k = -1; });
    lastP = -1;
    unpinFinalStates();
    shown = target = heroProgress();
    draw(shown);
  }

  function disableScrub() {
    scrubOn = false;
    removeEventListener('scroll', onScroll);
    removeEventListener('resize', onScroll);
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    // hand the hero back to CSS: the static layout owns it from here
    stage.style.removeProperty('--p');
    bands.forEach((el) => { el.style.removeProperty('opacity'); el.style.removeProperty('--k'); });
    cache.forEach((c) => { c.op = -1; c.k = -1; });
    lastP = -1;
  }

  function pinToFinalStates() {
    document.querySelectorAll('.divider, .pillar').forEach((el) => el.classList.add('in'));
    completeHold(true);
  }

  function unpinFinalStates() {
    document.querySelectorAll('.divider, .pillar').forEach((el) => {
      if (!el.dataset.entered) el.classList.remove('in');
    });
  }

  function applyHeroMode() {
    if (GATES.some((q, i) => MQLS[i].matches)) disableScrub();
    else enableScrub();
    if (reduced.matches) pinToFinalStates();
  }

  MQLS.forEach((m) => m.addEventListener('change', applyHeroMode));

  // ---- below-fold entrances ----
  const revealIo = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add('in');
      e.target.dataset.entered = '1';
      revealIo.unobserve(e.target);
    });
  }, { threshold: 0.25 });
  document.querySelectorAll('.divider, .pillar').forEach((el) => revealIo.observe(el));

  // retire the stagger delays once the pillars have arrived, so hovers never lag
  const pillars = document.querySelector('.pillars');
  if (pillars) {
    new IntersectionObserver((entries, obs) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        setTimeout(() => pillars.classList.add('retire'), 1300);
        obs.disconnect();
      });
    }, { threshold: 0.25 }).observe(pillars);
  }

  // ---- the one interactive moment: hold the emblem ----
  const closeBand = document.querySelector('.close-band');
  const holdBtn = document.querySelector('.hold');
  const HOLD_MS = 1150;
  let h = 0, holding = false, holdRaf = null, holdLast = 0, holdDone = false;

  function writeHold() {
    closeBand.style.setProperty('--h', h.toFixed(3));
  }

  function holdTick(now) {
    const dt = Math.min(100, now - (holdLast || now));
    holdLast = now;
    h = holding ? Math.min(1, h + dt / HOLD_MS) : Math.max(0, h - dt / (HOLD_MS * 0.7));
    writeHold();
    if (h >= 1) { completeHold(false); return; }
    if (!holding && h <= 0) { holdRaf = null; holdLast = 0; return; }
    holdRaf = requestAnimationFrame(holdTick);
  }

  function startHold(e) {
    if (holdDone) return;
    if (e && e.type === 'keydown' && e.key !== ' ' && e.key !== 'Enter') return;
    if (e && e.type === 'keydown') e.preventDefault();
    holding = true;
    if (holdRaf === null) holdRaf = requestAnimationFrame(holdTick);
  }

  function endHold() {
    holding = false;
    if (!holdDone && holdRaf === null && h > 0) holdRaf = requestAnimationFrame(holdTick);
  }

  function completeHold(instant) {
    if (holdDone) return;
    holdDone = true;
    holding = false;
    if (holdRaf !== null) { cancelAnimationFrame(holdRaf); holdRaf = null; }
    h = 1;
    writeHold();
    closeBand.classList.add('lit');
    if (holdBtn) {
      holdBtn.setAttribute('aria-disabled', 'true');
      holdBtn.tabIndex = instant ? -1 : holdBtn.tabIndex;
    }
  }

  if (holdBtn) {
    holdBtn.addEventListener('pointerdown', startHold);
    holdBtn.addEventListener('pointerup', endHold);
    holdBtn.addEventListener('pointercancel', endHold);
    holdBtn.addEventListener('pointerleave', endHold);
    holdBtn.addEventListener('keydown', startHold);
    holdBtn.addEventListener('keyup', endHold);
    holdBtn.addEventListener('blur', endHold);
    writeHold();
  }

  // ---- reduced motion, honored live in both directions ----
  reduced.addEventListener('change', (e) => {
    if (e.matches) { disableScrub(); pinToFinalStates(); }
    else applyHeroMode();
  });

  document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('paused', document.hidden);
  });

  applyHeroMode();
  requestAnimationFrame(rampIn);
})();

/* ---------- История движения ----------
   Узлы на волнистой линии переключают снимок в рамке: четыре исторических
   узла показывают архивный кадр, современные — сегодняшние фотографии. */
(() => {
  'use strict';

  const track = document.querySelector('.tl-track');
  const frame = document.querySelector('.tl-frame');
  if (!track || !frame) return;

  const MILESTONES = [
    { year: '1903–1905',      text: 'Учреждение',                                                  img: 'portrait', photo: 'Архивный снимок', era: 'past' },
    { year: '1903–1905',      text: 'Поддержка армии в ходе Русско-японской войны',                 img: 'portrait', photo: 'Архивный снимок', era: 'past' },
    { year: '1906–1909',      text: 'Противостояние забастовочному и революционному движению',      img: 'portrait', photo: 'Архивный снимок', era: 'past' },
    { year: '1914',           text: 'Фактическое прекращение существования',                        img: 'portrait', photo: 'Архивный снимок', era: 'past', faded: true },
    { year: '2020',           text: 'Возрождение клубов академистов',                               img: 'hangout',  photo: 'Сходка',          era: 'now' },
    { year: '15 февраля 2021', text: 'Создание Санкт-Петербургского отделения Академистов',         img: 'sjezd',    photo: 'Съезд академистов', era: 'now' },
    { year: 'Сегодня',        text: 'Съезды, балы, сходки и новые отделения',                       img: 'ball',     photo: 'Бал',             era: 'now' },
  ];

  const W = 1200, H = 160, MID = 80, AMP = 46, X0 = 60, STEP = 180;
  const curveY = (x) => MID - AMP * Math.cos((2 * Math.PI * (x - X0)) / (STEP * 2));

  // the dashed wave the nodes sit on
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', 'tl-curve');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  let d = '';
  for (let x = 0; x <= W; x += 4) d += (x ? 'L' : 'M') + x + ',' + curveY(x).toFixed(2) + ' ';
  const path = document.createElementNS(svgNS, 'path');
  path.setAttribute('d', d.trim());
  svg.appendChild(path);
  track.appendChild(svg);

  const imgs = [...frame.querySelectorAll('.tl-img')];
  const photoLabel = frame.querySelector('.tl-photo-label');
  const yearEl = document.querySelector('.tl-year');
  const textEl = document.querySelector('.tl-text');

  const nodes = MILESTONES.map((m, i) => {
    const x = X0 + i * STEP;
    const y = curveY(x);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'tl-node' + (y > MID ? ' is-below' : '');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
    b.dataset.era = m.era;
    b.tabIndex = i === 0 ? 0 : -1;
    b.style.left = ((x / W) * 100).toFixed(3) + '%';
    b.style.top = ((y / H) * 100).toFixed(3) + '%';
    b.innerHTML = '<span class="tl-dot"></span><span class="tl-node-body">'
                + '<span class="tl-year-label"></span><span class="tl-node-text"></span></span>';
    b.querySelector('.tl-year-label').textContent = m.year;
    b.querySelector('.tl-node-text').textContent = m.text;
    b.addEventListener('click', () => { stopAuto(); select(i); b.focus(); });
    b.addEventListener('mouseenter', () => { if (!autoStopped) select(i); });
    track.appendChild(b);
    return b;
  });

  let current = -1;

  function select(i) {
    if (i === current) return;
    current = i;
    const m = MILESTONES[i];
    imgs.forEach((im) => im.classList.toggle('is-on', im.dataset.key === m.img));
    frame.classList.toggle('is-faded', !!m.faded);
    photoLabel.textContent = m.photo;
    yearEl.textContent = m.year;
    textEl.textContent = m.text;
    nodes.forEach((n, j) => {
      n.setAttribute('aria-selected', j === i ? 'true' : 'false');
      n.tabIndex = j === i ? 0 : -1;
    });
  }

  track.addEventListener('keydown', (e) => {
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (current + 1) % nodes.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (current - 1 + nodes.length) % nodes.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = nodes.length - 1;
    if (next === null) return;
    e.preventDefault();
    stopAuto();
    select(next);
    nodes[next].focus();
  });

  // Пока посетитель не вмешался, снимки сменяются сами: иначе можно пройти
  // мимо секции и не увидеть, что архив переходит в сегодняшний день.
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let timer = null, autoStopped = false, inView = false;

  function tickAuto() { select((current + 1) % MILESTONES.length); }
  function startAuto() {
    if (autoStopped || timer || !inView || document.hidden || reduced.matches) return;
    timer = setInterval(tickAuto, 4200);
  }
  function pauseAuto() { if (timer) { clearInterval(timer); timer = null; } }
  function stopAuto() { autoStopped = true; pauseAuto(); }

  new IntersectionObserver((entries) => {
    entries.forEach((e) => { inView = e.isIntersecting; inView ? startAuto() : pauseAuto(); });
  }, { threshold: 0.35 }).observe(document.querySelector('.tl-section'));

  document.addEventListener('visibilitychange', () => { document.hidden ? pauseAuto() : startAuto(); });
  track.addEventListener('mouseenter', pauseAuto);
  track.addEventListener('mouseleave', startAuto);
  track.addEventListener('focusin', stopAuto);
  reduced.addEventListener('change', () => { reduced.matches ? pauseAuto() : startAuto(); });

  select(0);
})();
