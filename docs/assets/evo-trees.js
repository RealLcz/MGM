/**
 * Interactive MGM evolution canvas — OpenRSI-style force layout
 * driven by tree_mgm_web.json (built from docs/assets/mgm_meta).
 * Per-node operator + hybridization peer come from
 *   mgm_meta/<commit_id>/metadata.json
 * via scripts/preprocess_tree_web.py (self_improve_strategy, peer_commit).
 * Edge color = operator; dashed peer springs = CH reference; node fill = utility.
 * Ref: https://frontisai.github.io/OpenRSI/
 *
 * Tune layout / motion via CFG below (or window.MGM_EVO_CFG overrides).
 */
(() => {
  /**
   * Animation / layout hyperparameters.
   * Override from the console, then hard-refresh:
   *   Object.assign(window.MGM_EVO_CFG, { linkRest: 110, peerRest: 220, peerStiffness: 0.002 })
   *   Object.assign(window.MGM_EVO_CFG, { labelFade: false })  // keep a/b labels always on
   * Or edit DEFAULT_CFG below before deploy.
   *
   * Edge springs (independent):
   *   linkRest / linkStiffness  — parent→child tree edges
   *   peerRest / peerStiffness  — hybridization reference (peer→CH)
   * Even spread:
   *   spreadCharge / spreadPack / spreadPackForce
   * Camera fit:
   *   viewPad / viewEase / viewMinScale / viewMaxScale
   */
  const DEFAULT_CFG = {
    seed: 42,
    /** Wall-clock length of one full archive replay (ms). */
    totalMs: 28000,
    /** Pause on the finished tree before fade (ms). */
    holdMs: 3200,
    /** Fade-out duration before looping (ms). */
    fadeMs: 900,
    /** Spawn distance from parent: base + random*spread (px). */
    spawnDistBase: 10,
    spawnDistSpread: 40,
    /**
     * Tree-edge springs (parent → child): ideal length + stiffness.
     * Keep modest so the archive stays inside the canvas.
     */
    linkRest: 52,
    linkStiffness: 0.0007,
    /**
     * Later-born nodes get longer parent/peer rest lengths.
     * scale = 1 + lateBoost * (id-1)/(maxId-1)
     */
    linkRestLateBoost: 0.16,
    /**
     * Hybridization-reference springs (peer → CH child).
     * ~2× tree-edge rest + lower stiffness → more flexible.
     */
    peerRest: 128,
    peerStiffness: 0.00005,
    /** Soft pull toward graph centroid (not canvas). Keep low. */
    gravityX: 0.000004,
    gravityY: 0.000006,
    /**
     * Even-spread forces (full pairwise; cheap with ~25 nodes).
     * Lower charge / pack → less bouncy.
     */
    spreadCharge: 100,
    spreadPack: 0.75,
    spreadPackForce: 0.02,
    /** Camera: fit all nodes with padding; ease toward target view. */
    viewPad: 8,
    viewMaxScale: 1.7,
    viewMinScale: 0.4,
    viewEase: 0.08,
    /** Velocity damping (lower = less bounce). */
    damping: 0.88,
    /** Cursor repulsion radius (px) and strength. */
    mouseRadius: 130,
    mouseForce: 1.7,
    /** Utility → radius mapping (px). max = nodeRMin + nodeRSpan. */
    nodeRMin: 2.0,
    nodeRSpan: 20.0,
    /** How fast displayed utility eases toward the record value. */
    utilityEase: 0.12,
    /** Echo-wave duration after create / utility update (ms). */
    pulseMs: 1100,
    /** Echo-wave max extra radius (px). */
    pulseExtra: 14,
    /**
     * Pass-rate (a/b) hop labels:
     *   labelFade true  — brief pop then fade (uses labelMs)
     *   labelFade false — stay visible once shown
     */
    labelFade: true,
    /** How long the pass-rate hop label stays visible when labelFade is true (ms). */
    labelMs: 2000,
  };

  const CFG = Object.assign({}, DEFAULT_CFG, window.MGM_EVO_CFG || {});
  window.MGM_EVO_CFG = CFG;

  const OPS = {
    clonal: {
      name: "clonal",
      color: "#A8B5AD",
      label: "Clonal Mutation",
    },
    reaction: {
      name: "reaction",
      color: "#E0B84A",
      label: "Reaction-norm Mutation",
    },
    hybridize: {
      name: "hybridize",
      color: "#3DA66A",
      label: "Cross-lineage Hybridization",
    },
  };
  /** Utility fill stops — tuned for dark canvas. */
  const ACC_STOPS = ["#E07A76", "#F0B878", "#F0D56A", "#8FCF7A"];

  const lerp = (a, b, t) => a + (b - a) * t;

  function mulberry32(a) {
    return () => {
      let t = (a += 0x6d2b79f5);
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function lerpColor(hexA, hexB, t) {
    const p = (h) => [
      parseInt(h.slice(1, 3), 16),
      parseInt(h.slice(3, 5), 16),
      parseInt(h.slice(5, 7), 16),
    ];
    const A = p(hexA);
    const B = p(hexB);
    const c = A.map((v, i) => Math.round(lerp(v, B[i], t)));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  function accColor(t) {
    const x = Math.max(0, Math.min(1, t));
    const n = ACC_STOPS.length - 1;
    const i = Math.min(n - 1, Math.floor(x * n));
    return lerpColor(ACC_STOPS[i], ACC_STOPS[i + 1], x * n - i);
  }

  function nodeRadius(u) {
    return CFG.nodeRMin + Math.max(0, Math.min(1, u)) * CFG.nodeRSpan;
  }

  function hexToRgba(hex, a) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  async function boot() {
    const stage = document.getElementById("mgmStage");
    const cv = document.getElementById("mgmCanvas");
    // Observe the stage itself (not a section id) so the demo still boots
    // if the canvas is moved in the page (e.g. from #trees into #archive).
    if (!stage || !cv) return;

    const hudEvals = document.getElementById("hudEvals");
    const hudNodes = document.getElementById("hudNodes");

    let data;
    try {
      const res = await fetch("assets/tree_mgm_web.json");
      if (!res.ok) throw new Error("load failed");
      data = await res.json();
    } catch (err) {
      console.error(err);
      return;
    }

    const meta = new Map(data.nodes.map((n) => [n.id, n]));
    const frames = data.frames;
    const maxNodeId = Math.max(1, ...data.nodes.map((n) => n.id));

    function restScaleFor(id) {
      if (maxNodeId <= 1 || id <= 0) return 1;
      return 1 + CFG.linkRestLateBoost * ((id - 1) / (maxNodeId - 1));
    }

    const ctx = cv.getContext("2d");
    let W = 0;
    let H = 0;
    let dpr = 1;
    const mouse = { x: -1e4, y: -1e4 }; // screen space
    const mouseWorld = { x: -1e4, y: -1e4 };
    let rand = mulberry32(CFG.seed);
    const cam = { scale: 1, tx: 0, ty: 0 };

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = stage.clientWidth;
      H = stage.clientHeight;
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      cv.style.width = W + "px";
      cv.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    new ResizeObserver(resize).observe(stage);

    stage.addEventListener("pointermove", (e) => {
      const r = cv.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    });
    stage.addEventListener("pointerleave", () => {
      mouse.x = -1e4;
      mouse.y = -1e4;
    });

    /** @type {Map<number, any>} */
    let nodes = new Map();
    /** @type {any[]} */
    let links = [];
    let fade = 1;
    let phase = "play";
    let cycleStart = 0;
    let lastFi = -1;
    let epoch = 1;

    function syncMouseWorld() {
      mouseWorld.x = (mouse.x - cam.tx) / cam.scale;
      mouseWorld.y = (mouse.y - cam.ty) / cam.scale;
    }

    function updateCamera() {
      const arr = [...nodes.values()];
      if (!arr.length || W < 2 || H < 2) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const n of arr) {
        const pad = (n.r || 4) + 8;
        if (n.x - pad < minX) minX = n.x - pad;
        if (n.y - pad < minY) minY = n.y - pad;
        if (n.x + pad > maxX) maxX = n.x + pad;
        if (n.y + pad > maxY) maxY = n.y + pad;
      }
      const bw = Math.max(maxX - minX, 40);
      const bh = Math.max(maxY - minY, 40);
      const pad = CFG.viewPad;
      let s = Math.min((W - 2 * pad) / bw, (H - 2 * pad) / bh);
      s = Math.max(CFG.viewMinScale, Math.min(CFG.viewMaxScale, s));
      const midX = (minX + maxX) * 0.5;
      const midY = (minY + maxY) * 0.5;
      const tx = W * 0.5 - s * midX;
      const ty = H * 0.5 - s * midY;
      const ease = CFG.viewEase;
      cam.scale = lerp(cam.scale, s, ease);
      cam.tx = lerp(cam.tx, tx, ease);
      cam.ty = lerp(cam.ty, ty, ease);
      syncMouseWorld();
    }

    function placeNear(parent, id) {
      const ang = rand() * Math.PI * 2;
      const scale = restScaleFor(id || 1);
      const d = (CFG.spawnDistBase + rand() * CFG.spawnDistSpread) * scale;
      if (!parent) return { x: W * 0.5, y: H * 0.52 };
      return {
        x: parent.x + Math.cos(ang) * d,
        y: parent.y + Math.sin(ang) * d,
      };
    }

    function pulse(n, now) {
      n.pulseAt = now;
    }

    function resetCycle(now) {
      rand = mulberry32(CFG.seed + epoch * 997);
      nodes = new Map();
      links = [];
      fade = 1;
      phase = "play";
      cycleStart = now;
      lastFi = -1;
      cam.scale = 1;
      cam.tx = 0;
      cam.ty = 0;
      const rootPos = { x: W * 0.5, y: H * 0.52 };
      nodes.set(0, {
        id: 0,
        x: rootPos.x,
        y: rootPos.y,
        vx: 0,
        vy: 0,
        score: 0,
        targetScore: 0,
        r: 4.2,
        op: OPS.clonal,
        root: true,
        born: now,
        kids: 0,
        pulseAt: -1e9,
      });
      if (hudEvals) hudEvals.textContent = "0";
      if (hudNodes) hudNodes.textContent = "0";
    }

    function ensureNode(id, utils, now) {
      if (nodes.has(id)) return nodes.get(id);
      const m = meta.get(id);
      const parentId = m && m.parent != null ? m.parent : 0;
      const parent = nodes.get(parentId) || nodes.get(0);
      const u =
        utils[id] != null
          ? +utils[id]
          : utils[String(id)] != null
            ? +utils[String(id)]
            : 0;
      const op = (m && OPS[m.strategy]) || OPS.clonal;
      const pos = placeNear(parent, id);
      const scale = restScaleFor(id);
      const n = {
        id,
        x: pos.x,
        y: pos.y,
        vx: (rand() - 0.5) * 0.4,
        vy: (rand() - 0.5) * 0.4,
        score: u,
        targetScore: u,
        r: nodeRadius(u),
        op,
        root: false,
        born: now,
        kids: 0,
        peerId: m && m.peer_id != null ? m.peer_id : null,
        pulseAt: now,
        passLabel: null,
        labelAt: -1e9,
        restScale: scale,
      };
      nodes.set(id, n);
      if (parent) {
        parent.kids = (parent.kids || 0) + 1;
        links.push({
          a: parent,
          b: n,
          op,
          kind: "parent",
          born: now,
          restScale: scale,
        });
      }
      if (n.peerId != null && nodes.has(n.peerId) && op.name === "hybridize") {
        const peer = nodes.get(n.peerId);
        peer.kids = (peer.kids || 0) + 1;
        links.push({
          a: peer,
          b: n,
          op,
          kind: "peer",
          born: now,
          restScale: scale,
        });
      }
      return n;
    }

    function hopLabel(n, passStr, now) {
      if (!passStr) return;
      n.passLabel = passStr;
      n.labelAt = now;
      pulse(n, now);
    }

    function applyFrame(fi, now) {
      const frame = frames[fi];
      const utils = frame.utils || {};
      const passes = frame.pass || {};
      const ids = Object.keys(utils)
        .map(Number)
        .sort((a, b) => a - b);

      for (const id of ids) {
        const wasNew = !nodes.has(id);
        const n = ensureNode(id, utils, now);
        const u = utils[id] != null ? +utils[id] : +utils[String(id)] || 0;
        const passStr = passes[id] || passes[String(id)] || null;
        if (wasNew) {
          hopLabel(n, passStr, now);
        } else if (Math.abs(u - n.targetScore) > 1e-9) {
          hopLabel(n, passStr, now);
        } else if (passStr && passStr !== n.passLabel) {
          // same utility ratio but counts changed (e.g. 2/4 → 3/6)
          hopLabel(n, passStr, now);
        }
        n.targetScore = u;
        if (passStr) n.passLabel = passStr;
      }

      for (const [, n] of nodes) {
        if (n.peerId == null || n.op.name !== "hybridize") continue;
        if (!nodes.has(n.peerId)) continue;
        const hasPeerLink = links.some((l) => l.kind === "peer" && l.b === n);
        if (!hasPeerLink) {
          const peer = nodes.get(n.peerId);
          links.push({
            a: peer,
            b: n,
            op: n.op,
            kind: "peer",
            born: now,
            restScale: n.restScale || restScaleFor(n.id),
          });
        }
      }

      if (hudEvals) hudEvals.textContent = String(frame.evals);
      if (hudNodes) hudNodes.textContent = String(Math.max(0, nodes.size - 1));
    }

    function physics() {
      const arr = [...nodes.values()];
      const nCount = arr.length;
      syncMouseWorld();
      const mouseR2 = CFG.mouseRadius * CFG.mouseRadius;
      // Ideal spacing from a soft world area (not locked to canvas)
      const worldArea = Math.max(W * H, 1);
      const ideal =
        Math.sqrt(worldArea / Math.max(nCount, 1)) * CFG.spreadPack;

      let cx = 0;
      let cy = 0;
      for (const n of arr) {
        cx += n.x;
        cy += n.y;
      }
      cx /= Math.max(nCount, 1);
      cy /= Math.max(nCount, 1);

      // Full pairwise even-spread
      for (let i = 0; i < nCount; i++) {
        for (let j = i + 1; j < nCount; j++) {
          const a = arr[i];
          const b = arr[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) {
            dx = (rand() - 0.5) * 0.2;
            dy = (rand() - 0.5) * 0.2;
            d2 = dx * dx + dy * dy;
          }
          const d = Math.sqrt(d2);
          const coul = CFG.spreadCharge / (d2 + 25);
          const pack = d < ideal ? CFG.spreadPackForce * (ideal - d) : 0;
          const f = (coul + pack) / d;
          const fx = dx * f;
          const fy = dy * f;
          a.vx += fx;
          a.vy += fy;
          b.vx -= fx;
          b.vy -= fy;
        }
      }

      for (const n of arr) {
        n.vx += (cx - n.x) * CFG.gravityX;
        n.vy += (cy - n.y) * CFG.gravityY;

        for (const l of links) {
          if (l.b !== n && l.a !== n) continue;
          const other = l.a === n ? l.b : l.a;
          const dx = other.x - n.x;
          const dy = other.y - n.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const base = l.kind === "peer" ? CFG.peerRest : CFG.linkRest;
          const rest = base * (l.restScale || 1);
          const stiff =
            l.kind === "peer" ? CFG.peerStiffness : CFG.linkStiffness;
          const f = (dist - rest) * stiff;
          n.vx += dx * f;
          n.vy += dy * f;
        }
        const mdx = n.x - mouseWorld.x;
        const mdy = n.y - mouseWorld.y;
        const md2 = mdx * mdx + mdy * mdy;
        if (md2 < mouseR2) {
          const f = (1 - Math.sqrt(md2) / CFG.mouseRadius) * CFG.mouseForce;
          const inv = 1 / Math.sqrt(md2 + 0.1);
          n.vx += mdx * inv * f;
          n.vy += mdy * inv * f;
        }
        n.vx *= CFG.damping;
        n.vy *= CFG.damping;
        n.x += n.vx;
        n.y += n.vy;

        n.score = lerp(
          n.score,
          n.targetScore != null ? n.targetScore : n.score,
          CFG.utilityEase
        );
        n.r = nodeRadius(n.score);
      }
    }

    function drawEcho(n, rr, ts, invS) {
      const age = ts - (n.pulseAt || -1e9);
      if (age < 0 || age > CFG.pulseMs) return;
      const t = age / CFG.pulseMs;
      const waves = 2;
      for (let w = 0; w < waves; w++) {
        const tw = Math.min(1, Math.max(0, t * 1.15 - w * 0.22));
        if (tw <= 0) continue;
        const ring = rr + 4 + tw * CFG.pulseExtra;
        const alpha = (1 - tw) * 0.55 * Math.max(fade, 0);
        ctx.strokeStyle = hexToRgba(n.op.color, alpha);
        ctx.lineWidth = 1.35 * invS;
        ctx.beginPath();
        ctx.arc(n.x, n.y, ring, 0, 7);
        ctx.stroke();
      }
    }

    function draw(ts) {
      // Clear in screen space
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // World → screen camera
      ctx.setTransform(
        dpr * cam.scale,
        0,
        0,
        dpr * cam.scale,
        dpr * cam.tx,
        dpr * cam.ty
      );
      ctx.save();
      ctx.globalAlpha = Math.max(fade, 0);
      const invS = 1 / Math.max(cam.scale, 0.2);

      for (const l of links) {
        const age = Math.min(Math.max((ts - l.born) / 550, 0), 1);
        if (age === 0) continue;
        const isPeer = l.kind === "peer";
        ctx.strokeStyle = l.op.color;
        ctx.globalAlpha = (isPeer ? 0.38 : 0.55) * age * Math.max(fade, 0);
        const lw = isPeer ? 1.5 : l.op.name === "hybridize" ? 1.8 : 1.15;
        ctx.lineWidth = lw * invS;
        ctx.setLineDash(isPeer ? [4 * invS, 5 * invS] : []);
        ctx.beginPath();
        ctx.moveTo(l.a.x, l.a.y);
        ctx.lineTo(l.a.x + (l.b.x - l.a.x) * age, l.a.y + (l.b.y - l.a.y) * age);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = Math.max(fade, 0);

      for (const n of nodes.values()) {
        const age = Math.min(Math.max((ts - n.born) / 480, 0), 1);
        if (age === 0) continue;
        const rr = Math.max(n.r * age, 0.5);

        if (n.root) {
          ctx.fillStyle = "#9BB0A4";
          ctx.globalAlpha = 0.95 * Math.max(fade, 0);
          ctx.beginPath();
          ctx.arc(n.x, n.y, rr, 0, 7);
          ctx.fill();
          ctx.strokeStyle = "rgba(232,238,234,.5)";
          ctx.lineWidth = 1.4 * invS;
          ctx.beginPath();
          ctx.arc(n.x, n.y, rr + 3, 0, 7);
          ctx.stroke();
          ctx.globalAlpha = Math.max(fade, 0);
          continue;
        }

        ctx.fillStyle = accColor(n.score);
        ctx.globalAlpha = (0.62 + n.score * 0.38) * Math.max(fade, 0);
        ctx.beginPath();
        ctx.arc(n.x, n.y, rr, 0, 7);
        ctx.fill();

        ctx.globalAlpha = 0.95 * Math.max(fade, 0);
        ctx.strokeStyle = n.op.color;
        ctx.lineWidth = 1.75 * invS;
        ctx.beginPath();
        ctx.arc(n.x, n.y, rr, 0, 7);
        ctx.stroke();
        ctx.globalAlpha = Math.max(fade, 0);

        drawEcho(n, rr, ts, invS);

        if (n.passLabel) {
          const lage = ts - (n.labelAt || -1e9);
          let alpha = 0;
          let hop = 0;
          if (!CFG.labelFade) {
            // Always stay: brief hop on update, then hold at full opacity.
            if (lage >= 0) {
              const bounce = Math.min(1, lage / 420);
              hop = Math.sin(bounce * Math.PI) * (bounce < 1 ? 10 : 0);
              alpha = Math.max(fade, 0);
            }
          } else if (lage >= 0 && lage < CFG.labelMs) {
            const lt = lage / CFG.labelMs;
            hop = Math.sin(Math.min(1, lt * 2.2) * Math.PI) * 10;
            alpha =
              (lt < 0.15 ? lt / 0.15 : 1 - Math.max(0, (lt - 0.55) / 0.45)) *
              Math.max(fade, 0);
          }
          if (alpha > 0.02) {
            const ring = rr + 5 + hop * 0.15;
            ctx.globalAlpha = alpha;
            ctx.font = `${11 * invS}px "Overpass",system-ui,sans-serif`;
            ctx.fillStyle = n.op.color;
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            ctx.fillText(n.passLabel, n.x + ring + 4, n.y - hop * 0.35);
            ctx.globalAlpha = Math.max(fade, 0);
          }
        }
      }
      ctx.restore();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function tick(ts) {
      if (!cycleStart) resetCycle(ts);

      if (phase === "play") {
        const t = Math.min(1, (ts - cycleStart) / CFG.totalMs);
        const fi = frames.length <= 1 ? 0 : Math.round(t * (frames.length - 1));
        if (fi !== lastFi) {
          const from = Math.max(0, lastFi + 1);
          for (let i = from; i <= fi; i++) applyFrame(i, ts);
          lastFi = fi;
        }
        if (t >= 1) {
          phase = "hold";
          cycleStart = ts;
        }
      } else if (phase === "hold") {
        if (ts - cycleStart > CFG.holdMs) {
          phase = "fade";
          cycleStart = ts;
        }
      } else if (phase === "fade") {
        fade = 1 - (ts - cycleStart) / CFG.fadeMs;
        if (fade <= 0) {
          epoch += 1;
          resetCycle(ts);
        }
      }

      physics();
      updateCamera();
      draw(ts);
      requestAnimationFrame(tick);
    }

    let running = false;
    const start = () => {
      if (running) return;
      running = true;
      requestAnimationFrame(tick);
    };
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) start();
        });
      },
      { threshold: 0.08, rootMargin: "80px 0px" }
    );
    io.observe(stage);
    if (stage.getBoundingClientRect().top < window.innerHeight * 1.1) start();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
