/**
 * Interactive Figures 4–5 — live Monte Carlo in a 3-column theory block.
 * Auto-cycles like the evolution tree: hold, then refresh with random d0/ρ.
 */
(() => {
  const root = document.getElementById('simPanel');
  if (!root) return;

  const evoCanvas = document.getElementById('simEvo');
  const histCanvas = document.getElementById('simHist');
  const d0Range = document.getElementById('simD0');
  const rhoRange = document.getElementById('simRho');
  const d0Val = document.getElementById('simD0Val');
  const rhoVal = document.getElementById('simRhoVal');
  if (!evoCanvas || !histCanvas || !d0Range || !rhoRange) return;

  const evoCtx = evoCanvas.getContext('2d');
  const histCtx = histCanvas.getContext('2d');

  const D0_STEPS = [10, 15, 20, 25, 30, 40, 50, 60, 80];
  const RHO_STEPS = [1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0];
  const RHO_AUTO = RHO_STEPS.filter((r) => r >= 1.2);
  const METHODS = ['DGM', 'HGM', 'MGM'];
  const COLORS = { DGM: '#E74C3C', HGM: '#2980B9', MGM: '#27AE60' };
  const N_SEEDS = 36;
  const N_PTS = 80;
  const BUDGET = 500;
  const BASE_SEED = 42;
  /** Pause on finished curves before auto-refresh (ms), cf. evo-trees holdMs. */
  const HOLD_MS = 3200;
  const PLAY_MS = 2800;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let worker = null;
  let runToken = 0;
  let holdTimer = 0;
  let userPinned = false;
  let cycleRng = mulberry32(BASE_SEED);
  let state = emptyState(20, 2.0);

  function mulberry32(a) {
    return function () {
      let t = (a += 0x6d2b79f5);
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function emptyState(d0, rho) {
    return {
      d0,
      rho,
      checkpoints: Array.from({ length: N_PTS }, (_, i) => (BUDGET * i) / (N_PTS - 1)),
      trajs: { DGM: [], HGM: [], MGM: [] },
      nDone: 0,
      nTarget: N_SEEDS,
      play: 0,
      playing: false,
      raf: 0,
    };
  }

  function css(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  function syncLabels() {
    if (d0Val) d0Val.textContent = String(state.d0);
    if (rhoVal) rhoVal.textContent = state.rho.toFixed(1);
  }

  function setControls(d0, rho) {
    const di = D0_STEPS.indexOf(d0);
    const ri = RHO_STEPS.indexOf(rho);
    d0Range.value = String(di >= 0 ? di : D0_STEPS.indexOf(20));
    rhoRange.value = String(ri >= 0 ? ri : RHO_STEPS.indexOf(2.0));
    syncLabels();
  }

  function resizeCanvases() {
    for (const c of [evoCanvas, histCanvas]) {
      const rect = c.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.floor(rect.width * dpr));
      const h = Math.max(1, Math.floor(rect.height * dpr));
      if (c.width !== w || c.height !== h) {
        c.width = w;
        c.height = h;
      }
    }
    draw();
  }

  function statsAt(method, tIdx) {
    const rows = state.trajs[method];
    const n = rows.length;
    if (!n) return null;
    let sum = 0;
    let sum2 = 0;
    const vals = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const v = rows[i][tIdx];
      vals[i] = v;
      sum += v;
      sum2 += v * v;
    }
    const mu = sum / n;
    const se = Math.sqrt(Math.max(0, sum2 / n - mu * mu) / n);
    return { mu, lo: mu - 1.96 * se, hi: mu + 1.96 * se, vals, n };
  }

  function curveStats(method) {
    const rows = state.trajs[method];
    const n = rows.length;
    if (!n) return null;
    const mu = new Float64Array(N_PTS);
    const lo = new Float64Array(N_PTS);
    const hi = new Float64Array(N_PTS);
    for (let t = 0; t < N_PTS; t++) {
      let sum = 0;
      let sum2 = 0;
      for (let i = 0; i < n; i++) {
        const v = rows[i][t];
        sum += v;
        sum2 += v * v;
      }
      const m = sum / n;
      const se = Math.sqrt(Math.max(0, sum2 / n - m * m) / n);
      mu[t] = m;
      lo[t] = m - 1.96 * se;
      hi[t] = m + 1.96 * se;
    }
    return { mu, lo, hi, n };
  }

  /** Match .polyglot-chart .tick { font-size: 10px }. */
  const POLY_TICK_PX = 10;

  function tickFont(dpr) {
    const mono = css('--mono', 'IBM Plex Mono, monospace');
    let px = POLY_TICK_PX;
    const sample = document.querySelector('#polySvg .tick, .polyglot-chart .tick');
    if (sample) {
      const parsed = parseFloat(getComputedStyle(sample).fontSize);
      if (parsed > 0) px = parsed;
    }
    return `${px * dpr}px ${mono}`;
  }

  /** Polyglot-like inset: tiny margins, ticks inside the plot. */
  function margins(dpr) {
    return { t: 12 * dpr, r: 4 * dpr, b: 4 * dpr, l: 2 * dpr };
  }

  function drawEvo() {
    const ctx = evoCtx;
    const W = evoCanvas.width;
    const H = evoCanvas.height;
    const cssW = Math.max(1, evoCanvas.getBoundingClientRect().width);
    const dpr = W / cssW;
    ctx.clearRect(0, 0, W, H);

    const dim = css('--dim', '#6B7A72');
    const M = margins(dpr);
    const plotW = W - M.l - M.r;
    const plotH = H - M.t - M.b;
    const d0 = state.d0;
    const tMax = Math.max(1, Math.floor(state.play * (N_PTS - 1)));
    const xOf = (t) => M.l + (t / (N_PTS - 1)) * plotW;
    const yOf = (d) => M.t + ((d0 - d) / Math.max(d0, 1)) * plotH;

    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = 'rgba(39,51,48,.08)';
    ctx.lineWidth = 1 * dpr;
    for (const frac of [0.25, 0.5, 0.75, 1]) {
      const y = M.t + plotH * (1 - frac);
      ctx.beginPath();
      ctx.moveTo(M.l, y);
      ctx.lineTo(M.l + plotW, y);
      ctx.stroke();
    }

    ctx.strokeStyle = 'rgba(39,51,48,.35)';
    ctx.lineWidth = 1.2 * dpr;
    ctx.beginPath();
    ctx.moveTo(M.l, M.t);
    ctx.lineTo(M.l, M.t + plotH);
    ctx.lineTo(M.l + plotW, M.t + plotH);
    ctx.stroke();

    ctx.setLineDash([5 * dpr, 4 * dpr]);
    ctx.strokeStyle = 'rgba(39,51,48,.35)';
    ctx.beginPath();
    ctx.moveTo(M.l, yOf(0));
    ctx.lineTo(xOf(tMax), yOf(0));
    ctx.stroke();
    ctx.setLineDash([]);

    for (const m of METHODS) {
      const st = curveStats(m);
      if (!st) continue;
      const color = COLORS[m];
      ctx.beginPath();
      for (let t = 0; t <= tMax; t++) {
        const y = yOf(Math.min(d0 + 0.5, Math.max(-0.5, st.hi[t])));
        if (t === 0) ctx.moveTo(xOf(t), y);
        else ctx.lineTo(xOf(t), y);
      }
      for (let t = tMax; t >= 0; t--) {
        ctx.lineTo(xOf(t), yOf(Math.min(d0 + 0.5, Math.max(-0.5, st.lo[t]))));
      }
      ctx.closePath();
      ctx.fillStyle = color + '2E';
      ctx.fill();

      ctx.beginPath();
      for (let t = 0; t <= tMax; t++) {
        if (t === 0) ctx.moveTo(xOf(t), yOf(st.mu[t]));
        else ctx.lineTo(xOf(t), yOf(st.mu[t]));
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = 2 * dpr;
      ctx.lineJoin = 'round';
      ctx.stroke();
    }

    if (state.nDone > 0 && state.play < 0.999) {
      const px = xOf(tMax);
      ctx.strokeStyle = 'rgba(39,51,48,.18)';
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.moveTo(px, M.t);
      ctx.lineTo(px, M.t + plotH);
      ctx.stroke();
    }

    ctx.fillStyle = dim;
    ctx.font = tickFont(dpr);
    ctx.textAlign = 'left';
    ctx.fillText(String(d0), M.l + 6 * dpr, M.t + 9 * dpr);
    ctx.fillText('0', M.l + 6 * dpr, M.t + plotH - 4 * dpr);
    ctx.textAlign = 'end';
    ctx.fillText(String(BUDGET), M.l + plotW - 2 * dpr, M.t + plotH - 4 * dpr);
  }

  function drawHist() {
    const ctx = histCtx;
    const W = histCanvas.width;
    const H = histCanvas.height;
    const cssW = Math.max(1, histCanvas.getBoundingClientRect().width);
    const dpr = W / cssW;
    ctx.clearRect(0, 0, W, H);

    const dim = css('--dim', '#6B7A72');
    const M = margins(dpr);
    const plotW = W - M.l - M.r;
    const plotH = H - M.t - M.b;
    const d0 = state.d0;
    const tIdx = Math.max(0, Math.floor(state.play * (N_PTS - 1)));
    const bins = d0 + 1;
    const yMax = 0.4;
    const xOf = (d) => M.l + ((d + 0.5) / bins) * plotW;
    const yOf = (dens) => M.t + (1 - dens / yMax) * plotH;
    const barW = (plotW / bins) * 0.78;

    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = 'rgba(39,51,48,.08)';
    ctx.lineWidth = 1 * dpr;
    for (const dens of [0.1, 0.2, 0.3, 0.4]) {
      const y = yOf(dens);
      ctx.beginPath();
      ctx.moveTo(M.l, y);
      ctx.lineTo(M.l + plotW, y);
      ctx.stroke();
    }

    ctx.strokeStyle = 'rgba(39,51,48,.35)';
    ctx.lineWidth = 1.2 * dpr;
    ctx.beginPath();
    ctx.moveTo(M.l, M.t);
    ctx.lineTo(M.l, M.t + plotH);
    ctx.lineTo(M.l + plotW, M.t + plotH);
    ctx.stroke();

    for (const m of METHODS) {
      const st = statsAt(m, tIdx);
      if (!st) continue;
      const counts = new Float64Array(bins);
      for (let i = 0; i < st.n; i++) {
        const d = Math.max(0, Math.min(d0, Math.round(st.vals[i])));
        counts[d] += 1;
      }
      const color = COLORS[m];
      for (let d = 0; d < bins; d++) {
        const dens = counts[d] / st.n;
        if (dens <= 0) continue;
        const x = M.l + (d / bins) * plotW + (plotW / bins - barW) / 2;
        const y = yOf(dens);
        ctx.globalAlpha = 0.55;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, barW, M.t + plotH - y);
      }
      ctx.globalAlpha = 1;
      ctx.setLineDash([4 * dpr, 3 * dpr]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.8 * dpr;
      ctx.beginPath();
      ctx.moveTo(xOf(st.mu), M.t);
      ctx.lineTo(xOf(st.mu), M.t + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.fillStyle = dim;
    ctx.font = tickFont(dpr);
    ctx.textAlign = 'left';
    ctx.fillText('0', M.l + 6 * dpr, M.t + plotH - 4 * dpr);
    ctx.textAlign = 'end';
    ctx.fillText(String(d0), M.l + plotW - 2 * dpr, M.t + plotH - 4 * dpr);
  }

  function draw() {
    drawEvo();
    drawHist();
  }

  function stopPlayback() {
    if (state.raf) cancelAnimationFrame(state.raf);
    state.raf = 0;
    state.playing = false;
  }

  function clearHold() {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = 0;
    }
  }

  function pickRandomParams() {
    const di = Math.floor(cycleRng() * D0_STEPS.length);
    const ri = Math.floor(cycleRng() * RHO_AUTO.length);
    return { d0: D0_STEPS[di], rho: RHO_AUTO[ri] };
  }

  function scheduleRefresh() {
    clearHold();
    if (userPinned || reduceMotion) return;
    holdTimer = setTimeout(() => {
      holdTimer = 0;
      const next = pickRandomParams();
      startRun(next.d0, next.rho, { fromAuto: true });
    }, HOLD_MS);
  }

  function startPlayback() {
    stopPlayback();
    if (reduceMotion) {
      state.play = 1;
      draw();
      scheduleRefresh();
      return;
    }
    state.playing = true;
    const t0 = performance.now();
    const from = state.play;
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / PLAY_MS);
      const eased = 1 - Math.pow(1 - p, 3);
      state.play = from + (1 - from) * eased;
      draw();
      if (p < 1 && state.playing) state.raf = requestAnimationFrame(tick);
      else {
        state.play = 1;
        state.playing = false;
        draw();
        if (state.nDone >= state.nTarget) scheduleRefresh();
      }
    };
    state.raf = requestAnimationFrame(tick);
  }

  function ensureWorker() {
    if (worker) return worker;
    worker = new Worker('assets/sim-panel-worker.js');
    worker.onmessage = (ev) => {
      const msg = ev.data || {};
      if (msg.id !== runToken) return;
      if (msg.type === 'start') {
        state.checkpoints = msg.checkpoints;
      } else if (msg.type === 'seed') {
        for (const m of METHODS) state.trajs[m].push(msg.traj[m]);
        state.nDone = msg.seed + 1;
        if (state.nDone === 1 && !reduceMotion && !state.playing) startPlayback();
        draw();
      } else if (msg.type === 'done') {
        state.nDone = msg.nSeeds;
        if (state.play < 0.999 && !state.playing && !reduceMotion) startPlayback();
        else if (reduceMotion) {
          state.play = 1;
          draw();
          scheduleRefresh();
        } else if (!state.playing && state.play >= 0.999) {
          scheduleRefresh();
        }
        draw();
      }
    };
    return worker;
  }

  function startRun(d0, rho, opts = {}) {
    stopPlayback();
    clearHold();
    runToken += 1;
    state = emptyState(d0, rho);
    state.play = reduceMotion ? 1 : 0.06;
    if (opts.fromAuto) setControls(d0, rho);
    else syncLabels();
    draw();
    ensureWorker().postMessage({
      type: 'run',
      d0,
      rho,
      nSeeds: N_SEEDS,
      nCheckpoints: N_PTS,
      budget: BUDGET,
      seed: BASE_SEED + runToken * 97,
    });
  }

  function readControls() {
    const d0 = D0_STEPS[parseInt(d0Range.value, 10)] ?? 20;
    const rho = RHO_STEPS[parseInt(rhoRange.value, 10)] ?? 2.0;
    return { d0, rho };
  }

  let debounce = 0;
  function onControl() {
    userPinned = true;
    clearHold();
    const { d0, rho } = readControls();
    if (d0Val) d0Val.textContent = String(d0);
    if (rhoVal) rhoVal.textContent = rho.toFixed(1);
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      startRun(d0, rho);
      // Resume auto-cycle after the user-triggered run finishes + hold
      userPinned = false;
    }, 120);
  }

  d0Range.addEventListener('input', onControl);
  rhoRange.addEventListener('input', onControl);
  window.addEventListener('resize', () => {
    clearTimeout(resizeCanvases._t);
    resizeCanvases._t = setTimeout(resizeCanvases, 80);
  });

  d0Range.max = String(D0_STEPS.length - 1);
  rhoRange.max = String(RHO_STEPS.length - 1);
  d0Range.value = String(D0_STEPS.indexOf(20));
  rhoRange.value = String(RHO_STEPS.indexOf(2.0));
  state = emptyState(20, 2.0);
  syncLabels();
  resizeCanvases();

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        const { d0, rho } = readControls();
        startRun(d0, rho);
      });
    },
    { threshold: 0.18 }
  );
  io.observe(root);
})();
