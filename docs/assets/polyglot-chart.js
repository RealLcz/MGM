/**
 * Figure 2 — Polyglot performance vs model size (SVG).
 * Data and layout mirrored from aider_polyglot_viz.py.
 */
(() => {
  const svg = document.getElementById('polySvg');
  const chart = document.getElementById('polyglotChart');
  const tip = document.getElementById('polyTip');
  if (!svg || !chart) return;

  const NS = 'http://www.w3.org/2000/svg';
  const W = 720;
  const H = 432;
  const M = { t: 12, r: 4, b: 4, l: 2 };
  const XMIN = 20;
  const XMAX = 6200;
  const YMIN = 28;
  const YMAX = 100;
  const plotW = W - M.l - M.r;
  const plotH = H - M.t - M.b;
  const log20 = Math.log(XMIN);
  const logSpan = Math.log(XMAX) - log20;

  // Open-source: total params (llm-stats / HF). Closed: IKP estimates.
  const baselines = [
    { name: 'GPT-5', score: 88.0, size: 4100, open: false },
    { name: 'Gemini 2.5 Pro Preview', score: 82.2, size: 1200, open: false },
    { name: 'o3', score: 81.3, size: 3000, open: false },
    { name: 'Gemini 2.5 Pro', score: 76.5, size: 1200, open: false },
    { name: 'DeepSeek-V3.2-Exp', score: 74.5, size: 685, open: true },
    { name: 'DeepSeek-R1-0528', score: 71.6, size: 671, open: true },
    { name: 'DeepSeek-V3.1', score: 68.4, size: 671, open: true },
    { name: 'Gemini 2.5 Flash', score: 61.9, size: 207, open: false },
    { name: 'Qwen3-Coder 480B', score: 61.8, size: 480, open: true },
    { name: 'Kimi K2', score: 60.0, size: 1000, open: true },
    { name: 'Qwen3-235B', score: 57.3, size: 235, open: true },
    { name: 'GPT-4.1', score: 51.6, size: 2200, open: false },
    { name: 'Qwen3-80B', score: 49.8, size: 80, open: true },
    { name: 'DeepSeek-V3', score: 49.6, size: 671, open: true },
    { name: 'Magistral Medium', score: 47.1, size: 24, open: true },
    { name: 'Qwen3.6-35B-A3B', score: 50.8, size: 35, open: true },
    { name: 'GPT-4o', score: 30.7, size: 720, open: false },
  ];

  const ours = [
    { name: 'Qwen3.6-35B-A3B + HGM*', score: 77.9, size: 35, marker: 'triangle', r: 7 },
    { name: 'Qwen3.6-35B-A3B + MGM', score: 93.3, size: 35, marker: 'star', r: 9 },
    { name: 'DeepSeek-V4-Pro + MGM (transferred)', score: 96.9, size: 1600, marker: 'star', r: 9 },
  ];

  // [dx, dy, text-anchor] — mirrored from label_cfg in the matplotlib script
  const labelCfg = {
    'GPT-5': [-6, -10, 'end'],
    'Gemini 2.5 Pro Preview': [-6, -10, 'end'],
    'o3': [-4, -12, 'end'],
    'Gemini 2.5 Pro': [8, 12, 'start'],
    'DeepSeek-V3.2-Exp': [-8, -8, 'end'],
    'DeepSeek-R1-0528': [-8, 0, 'end'],
    'DeepSeek-V3.1': [-8, 8, 'end'],
    'Gemini 2.5 Flash': [-8, -8, 'end'],
    'Qwen3-Coder 480B': [0, 16, 'start'],
    'Kimi K2': [8, 6, 'start'],
    'Qwen3-235B': [8, 6, 'start'],
    'GPT-4.1': [8, -8, 'start'],
    'Qwen3-80B': [8, 8, 'start'],
    'DeepSeek-V3': [0, 14, 'middle'],
    'Magistral Medium': [8, 10, 'start'],
    'Qwen3.6-35B-A3B': [8, -10, 'start'],
    'GPT-4o': [-8, -8, 'end'],
    'Qwen3.6-35B-A3B + HGM*': [8, -2, 'start'],
    'Qwen3.6-35B-A3B + MGM': [8, 12, 'start'],
    'DeepSeek-V4-Pro + MGM (transferred)': [0, -12, 'middle'],
  };

  const xOf = (size) => M.l + ((Math.log(size) - log20) / logSpan) * plotW;
  const yOf = (score) => M.t + ((YMAX - score) / (YMAX - YMIN)) * plotH;
  const fmtSize = (s) => (s < 1000 ? `${Math.round(s)}B` : `${(s / 1000).toFixed(s % 1000 === 0 ? 0 : 1)}T`);

  const el = (tag, attrs = {}, parent = svg) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    parent.appendChild(node);
    return node;
  };

  const delayStyle = (sec) => `animation-delay:${sec.toFixed(2)}s`;

  const defs = el('defs');
  const hatch = el('pattern', {
    id: 'polyHatch',
    patternUnits: 'userSpaceOnUse',
    width: '7',
    height: '7',
    patternTransform: 'rotate(135)',
  }, defs);
  el('line', {
    x1: '0', y1: '0', x2: '0', y2: '7',
    stroke: 'rgba(39,51,48,.14)',
    'stroke-width': '1',
  }, hatch);

  const gGrid = el('g', { class: 'grid' });
  [40, 60, 80, 100].forEach((y) => {
    el('line', { class: 'grid-line', x1: M.l, x2: W - M.r, y1: yOf(y), y2: yOf(y) }, gGrid);
  });
  [30, 100, 300, 1000, 3000].forEach((x) => {
    el('line', { class: 'grid-line', x1: xOf(x), x2: xOf(x), y1: M.t, y2: H - M.b }, gGrid);
  });

  el('line', {
    x1: M.l, y1: H - M.b, x2: W - M.r, y2: H - M.b,
    stroke: 'rgba(39,51,48,.35)', 'stroke-width': '1.2',
  });
  el('line', {
    x1: M.l, y1: M.t, x2: M.l, y2: H - M.b,
    stroke: 'rgba(39,51,48,.35)', 'stroke-width': '1.2',
  });

  [40, 60, 80, 100].forEach((y) => {
    const ty = yOf(y);
    el('line', { x1: M.l, x2: M.l + 4, y1: ty, y2: ty, stroke: 'rgba(39,51,48,.35)' });
    const t = el('text', { class: 'tick', x: M.l + 8, y: ty + 3.5, 'text-anchor': 'start' });
    t.textContent = String(y);
  });
  [30, 100, 300, 1000, 3000].forEach((x) => {
    const tx = xOf(x);
    el('line', { x1: tx, x2: tx, y1: H - M.b - 4, y2: H - M.b, stroke: 'rgba(39,51,48,.35)' });
    const t = el('text', { class: 'tick', x: tx, y: H - M.b - 6, 'text-anchor': 'middle' });
    t.textContent = fmtSize(x);
  });

  const bestAt = new Map();
  baselines.forEach(({ size, score }) => {
    bestAt.set(size, Math.max(bestAt.get(size) ?? -Infinity, score));
  });
  const frontier = [];
  let best = -Infinity;
  [...bestAt.keys()].sort((a, b) => a - b).forEach((size) => {
    const score = bestAt.get(size);
    if (score > best) {
      best = score;
      frontier.push({ size, score });
    }
  });
  const prevSota = frontier[frontier.length - 1].score;
  const xRight = xOf(XMAX);
  const yBottom = yOf(YMIN);
  let fPath = '';
  frontier.forEach((p, i) => {
    const x = xOf(p.size);
    const y = yOf(p.score);
    if (i === 0) fPath += `M${x} ${y}`;
    else fPath += ` H${x} V${y}`;
  });
  fPath += ` H${xRight}`;
  // Hatch shade under the frontier, cast toward bottom-right
  const shadePath = `${fPath} V${yBottom} H${xOf(frontier[0].size)} Z`;
  el('path', { class: 'frontier-shade', d: shadePath });
  el('path', { class: 'frontier anim-line', pathLength: '1', d: fPath });

  const sSota = el('text', { class: 'guide-sota', x: xRight - 2, y: yOf(prevSota) - 4, 'text-anchor': 'end' });
  sSota.textContent = prevSota.toFixed(1);

  const starPath = (px, py, r) => {
    const pts = [];
    for (let i = 0; i < 5; i++) {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5;
      const b = a + Math.PI / 5;
      pts.push([px + Math.cos(a) * r, py + Math.sin(a) * r]);
      pts.push([px + Math.cos(b) * r * 0.45, py + Math.sin(b) * r * 0.45]);
    }
    return `M${pts.map((p) => p.join(',')).join('L')}Z`;
  };
  const triPath = (px, py, r) =>
    `M${px},${py - r} L${px + r * 0.9},${py + r * 0.7} L${px - r * 0.9},${py + r * 0.7}Z`;

  const gDots = el('g', { class: 'dots' });
  const gLabels = el('g', { class: 'labels' });
  const gGuides = el('g', { class: 'guides' });
  const hitNodes = [];

  const makeGuide = (attrs) =>
    el('line', { class: 'guide anim-guide', ...attrs }, gGuides);

  const placeLabel = (name, size, score, isOurs, animDelay = null) => {
    const [dx, dy, anchor] = labelCfg[name] || [8, -8, 'start'];
    const attrs = {
      class: `pt-label${isOurs ? ' ours anim-green' : ''}`,
      x: xOf(size) + dx,
      y: yOf(score) + dy,
      'text-anchor': anchor,
    };
    if (animDelay != null) attrs.style = delayStyle(animDelay);
    const t = el('text', attrs, gLabels);
    t.textContent = name;
    return t;
  };

  // Baselines first (non-green)
  baselines.forEach((d, i) => {
    const px = xOf(d.size);
    const py = yOf(d.score);
    const node = el('circle', {
      class: `dot anim-dot ${d.open ? 'open' : 'closed'}`,
      cx: px,
      cy: py,
      r: 5.5,
      style: delayStyle(i * 0.06),
    }, gDots);
    node.dataset.name = d.name;
    node.dataset.score = String(d.score);
    node.dataset.size = fmtSize(d.size);
    node.dataset.kind = d.open ? 'Open-source' : 'Closed-source';
    hitNodes.push(node);
    placeLabel(d.name, d.size, d.score, false);
  });

  // Green phase starts after baselines settle — bottom-left → top-right
  const green0 = baselines.length * 0.06 + 1.35;
  const step = 0.38;
  let t = green0;

  // 1) 35B vertical from bottom + size label
  makeGuide({
    x1: xOf(35), y1: yBottom,
    x2: xOf(35), y2: yOf(93.3),
    style: delayStyle(t),
  });
  const s35 = el('text', {
    class: 'guide-score anim-green',
    x: xOf(35) + 5, y: yBottom - 6, 'text-anchor': 'start',
    style: delayStyle(t + 0.15),
  });
  s35.textContent = '35B';
  t += step;

  // 2) HGM* (lower left green marker)
  {
    const d = ours[0];
    const px = xOf(d.size);
    const py = yOf(d.score);
    const node = el('path', {
      class: 'dot anim-dot ours',
      d: triPath(px, py, d.r),
      style: delayStyle(t),
    }, gDots);
    node.dataset.name = d.name;
    node.dataset.score = String(d.score);
    node.dataset.size = fmtSize(d.size);
    node.dataset.kind = 'Self-improving';
    hitNodes.push(node);
    placeLabel(d.name, d.size, d.score, true, t + 0.1);
    t += step;
  }

  // 3) Qwen + MGM, then horizontal guide from left + score
  {
    const d = ours[1];
    const px = xOf(d.size);
    const py = yOf(d.score);
    const node = el('path', {
      class: 'dot anim-dot ours',
      d: starPath(px, py, d.r),
      style: delayStyle(t),
    }, gDots);
    node.dataset.name = d.name;
    node.dataset.score = String(d.score);
    node.dataset.size = fmtSize(d.size);
    node.dataset.kind = 'Self-improving';
    hitNodes.push(node);
    placeLabel(d.name, d.size, d.score, true, t + 0.1);
    t += step * 0.7;

    makeGuide({
      x1: px, y1: py,
      x2: xRight, y2: py,
      style: delayStyle(t),
    });
    const s933 = el('text', {
      class: 'guide-score anim-green',
      x: xRight - 2, y: py - 4, 'text-anchor': 'end',
      style: delayStyle(t + 0.55),
    });
    s933.textContent = '93.3';
    t += step;
  }

  // 4) 1.6T vertical from bottom + size label
  makeGuide({
    x1: xOf(1600), y1: yBottom,
    x2: xOf(1600), y2: yOf(96.9),
    style: delayStyle(t),
  });
  const s16 = el('text', {
    class: 'guide-score anim-green',
    x: xOf(1600) + 5, y: yBottom - 6, 'text-anchor': 'start',
    style: delayStyle(t + 0.15),
  });
  s16.textContent = '1.6T';
  t += step;

  // 5) DeepSeek + MGM (top-right), then horizontal guide + score
  {
    const d = ours[2];
    const px = xOf(d.size);
    const py = yOf(d.score);
    const node = el('path', {
      class: 'dot anim-dot ours',
      d: starPath(px, py, d.r),
      style: delayStyle(t),
    }, gDots);
    node.dataset.name = d.name;
    node.dataset.score = String(d.score);
    node.dataset.size = fmtSize(d.size);
    node.dataset.kind = 'Self-improving';
    hitNodes.push(node);
    placeLabel(d.name, d.size, d.score, true, t + 0.1);
    t += step * 0.7;

    makeGuide({
      x1: px, y1: py,
      x2: xRight, y2: py,
      style: delayStyle(t),
    });
    const s969 = el('text', {
      class: 'guide-score anim-green',
      x: xRight - 2, y: py - 4, 'text-anchor': 'end',
      style: delayStyle(t + 0.55),
    });
    s969.textContent = '96.9';
  }

  const showTip = (node, clientX, clientY) => {
    tip.innerHTML = `<b>${node.dataset.name}</b><br>${node.dataset.score}% · ${node.dataset.size}<br>${node.dataset.kind}`;
    const rect = chart.getBoundingClientRect();
    tip.style.left = `${clientX - rect.left}px`;
    tip.style.top = `${clientY - rect.top}px`;
    tip.classList.add('on');
  };
  const hideTip = () => tip.classList.remove('on');

  hitNodes.forEach((node) => {
    node.addEventListener('pointerenter', (e) => {
      hitNodes.forEach((n) => n.classList.add(n === node ? 'hi' : 'dim'));
      showTip(node, e.clientX, e.clientY);
    });
    node.addEventListener('pointermove', (e) => showTip(node, e.clientX, e.clientY));
    node.addEventListener('pointerleave', () => {
      hitNodes.forEach((n) => n.classList.remove('hi', 'dim'));
      hideTip();
    });
  });

  const pio = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      chart.classList.add('ready');
      pio.unobserve(chart);
    });
  }, { threshold: 0.25 });
  pio.observe(chart);
})();
