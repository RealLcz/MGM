/**
 * Monte Carlo worker — port of simulation.py (DGM / HGM / MGM).
 * Posts progressive seed results so the UI can monitor curves + distributions live.
 */
(() => {
  const METHODS = ['DGM', 'HGM', 'MGM'];

  function mulberry32(a) {
    return function () {
      let t = (a += 0x6d2b79f5);
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function rngInt(rng, n) {
    return Math.floor(rng() * n);
  }

  function choiceNoReplace(rng, n, k) {
    const idx = new Uint16Array(n);
    for (let i = 0; i < n; i++) idx[i] = i;
    for (let i = 0; i < k; i++) {
      const j = i + rngInt(rng, n - i);
      const tmp = idx[i];
      idx[i] = idx[j];
      idx[j] = tmp;
    }
    return idx.subarray(0, k);
  }

  function makeInitialState(L, d0, rng) {
    const state = new Uint8Array(L);
    state.fill(1);
    const bad = choiceNoReplace(rng, L, d0);
    for (let i = 0; i < bad.length; i++) state[bad[i]] = 0;
    return state;
  }

  function makeTaskPool(N, L, k, rng) {
    const pool = new Array(N);
    for (let i = 0; i < N; i++) pool[i] = Uint16Array.from(choiceNoReplace(rng, L, k));
    return pool;
  }

  function evalTask(state, locs) {
    for (let i = 0; i < locs.length; i++) if (state[locs[i]] === 0) return 0;
    return 1;
  }

  function dOf(state) {
    let d = 0;
    for (let i = 0; i < state.length; i++) if (state[i] === 0) d++;
    return d;
  }

  function applyEdit(state, fixProb, breakProb, rng) {
    const next = state.slice();
    for (let i = 0; i < next.length; i++) {
      if (next[i] === 0) {
        if (rng() < fixProb) next[i] = 1;
      } else if (rng() < breakProb) {
        next[i] = 0;
      }
    }
    return next;
  }

  function interpolate(history, totalBudget, nPts) {
    const out = new Float32Array(nPts);
    let h = 0;
    const d0 = history[0][1];
    for (let i = 0; i < nPts; i++) {
      const cp = (totalBudget * i) / (nPts - 1);
      while (h + 1 < history.length && history[h + 1][0] <= cp) h++;
      out[i] = history[h] ? history[h][1] : d0;
    }
    return out;
  }

  function runDgm(p, rng) {
    const taskPool = makeTaskPool(p.N_tasks, p.L, p.k, rng);
    const init = makeInitialState(p.L, p.d0, rng);
    let cost = 0;
    const history = [[0, dOf(init)]];
    let population = Array.from({ length: p.dgm_pop_size }, () => ({
      state: init.slice(),
      rewards: [],
    }));

    while (cost < p.total_budget) {
      outer: for (let i = 0; i < population.length; i++) {
        for (let e = 0; e < p.dgm_n_eval; e++) {
          const tid = rngInt(rng, p.N_tasks);
          population[i].rewards.push(evalTask(population[i].state, taskPool[tid]));
          cost += p.c_task;
          if (cost >= p.total_budget) break outer;
        }
      }
      let best = Infinity;
      for (const ind of population) best = Math.min(best, dOf(ind.state));
      history.push([cost, best]);
      if (cost >= p.total_budget) break;

      const ranked = population.slice().sort((a, b) => {
        const ma = a.rewards.length ? a.rewards.reduce((s, x) => s + x, 0) / a.rewards.length : 0;
        const mb = b.rewards.length ? b.rewards.reduce((s, x) => s + x, 0) / b.rewards.length : 0;
        return mb - ma;
      });
      const nSelect = Math.max(1, Math.floor(p.dgm_pop_size * p.dgm_selection_frac));
      const selected = ranked.slice(0, nSelect);
      const newPop = [{ state: selected[0].state.slice(), rewards: [] }];
      for (const ind of selected) {
        newPop.push({
          state: applyEdit(ind.state, p.fix_prob_CM, p.break_prob_CM, rng),
          rewards: [],
        });
        cost += p.c_edit_CM;
        if (newPop.length >= p.dgm_pop_size || cost >= p.total_budget) break;
      }
      while (newPop.length < p.dgm_pop_size) {
        newPop.push({ state: selected[0].state.slice(), rewards: [] });
      }
      population = newPop.slice(0, p.dgm_pop_size);
      best = Infinity;
      for (const ind of population) best = Math.min(best, dOf(ind.state));
      history.push([cost, best]);
    }
    return interpolate(history, p.total_budget, p.n_checkpoints);
  }

  function betaMean(s, n) {
    return (s + 1) / (n + 2);
  }

  function ucb(s, n, total, c) {
    if (n === 0) return Infinity;
    return betaMean(s, n) + c * Math.sqrt(Math.log(Math.max(total, 2)) / n);
  }

  function shouldEdit(s, n, minEv, maxEv, thresh) {
    if (n >= maxEv) return true;
    if (n < minEv) return false;
    return (n - s) / n > thresh;
  }

  function chooseStrategyMgm(idx, failedTasks, p, rng) {
    const canRM = failedTasks[idx].size >= p.min_failures_for_RM;
    let canCH = false;
    const nodeFailed = failedTasks[idx];
    if (nodeFailed.size) {
      for (let j = 0; j < failedTasks.length; j++) {
        if (j === idx) continue;
        for (const t of nodeFailed) {
          if (failedTasks[j].has(t)) {
            canCH = true;
            break;
          }
        }
        if (canCH) break;
      }
    }
    if (canRM && rng() < p.prob_RM_given_available) return 'RM';
    if (canCH && rng() < p.prob_CH_given_available) return 'CH';
    return 'CM';
  }

  function runHgmMgm(p, rng, isMgm) {
    const taskPool = makeTaskPool(p.N_tasks, p.L, p.k, rng);
    const init = makeInitialState(p.L, p.d0, rng);
    let cost = 0;
    const history = [[0, dOf(init)]];
    const states = [init];
    const successes = [0];
    const nEvals = [0];
    const failedTasks = [new Set()];
    const edited = [false];
    const FIX = { CM: p.fix_prob_CM, RM: p.fix_prob_RM, CH: p.fix_prob_CH };
    const BREAK = { CM: p.break_prob_CM, RM: p.break_prob_RM, CH: p.break_prob_CH };
    const COST = { CM: p.c_edit_CM, RM: p.c_edit_RM, CH: p.c_edit_CH };

    const bestD = () => {
      let b = Infinity;
      for (const s of states) b = Math.min(b, dOf(s));
      return b;
    };

    let tid = rngInt(rng, p.N_tasks);
    let r = evalTask(states[0], taskPool[tid]);
    successes[0] += r;
    nEvals[0] += 1;
    if (r === 0) failedTasks[0].add(tid);
    cost += p.c_task;
    history.push([cost, bestD()]);

    while (cost < p.total_budget) {
      let active = [];
      for (let i = 0; i < edited.length; i++) if (!edited[i]) active.push(i);
      if (!active.length) active = states.map((_, i) => i);

      let totalEv = 0;
      for (const n of nEvals) totalEv += n;
      totalEv = Math.max(1, totalEv);

      let bestIdx = active[0];
      let bestU = -Infinity;
      for (const i of active) {
        const u = ucb(successes[i], nEvals[i], totalEv, p.ucb_c);
        if (u > bestU) {
          bestU = u;
          bestIdx = i;
        }
      }
      const idx = bestIdx;

      tid = rngInt(rng, p.N_tasks);
      r = evalTask(states[idx], taskPool[tid]);
      successes[idx] += r;
      nEvals[idx] += 1;
      if (r === 0) failedTasks[idx].add(tid);
      cost += p.c_task;
      history.push([cost, bestD()]);
      if (cost >= p.total_budget) break;

      if (
        !edited[idx] &&
        shouldEdit(
          successes[idx],
          nEvals[idx],
          p.min_evals_before_edit,
          p.max_evals_per_node,
          p.edit_fail_threshold
        )
      ) {
        const strategy = isMgm ? chooseStrategyMgm(idx, failedTasks, p, rng) : 'CM';
        const newState = applyEdit(states[idx], FIX[strategy], BREAK[strategy], rng);
        cost += COST[strategy];
        edited[idx] = true;
        states.push(newState);
        successes.push(0);
        nEvals.push(0);
        failedTasks.push(new Set());
        edited.push(false);
        history.push([cost, bestD()]);

        if (cost < p.total_budget) {
          const newIdx = states.length - 1;
          tid = rngInt(rng, p.N_tasks);
          r = evalTask(states[newIdx], taskPool[tid]);
          successes[newIdx] += r;
          nEvals[newIdx] += 1;
          if (r === 0) failedTasks[newIdx].add(tid);
          cost += p.c_task;
          history.push([cost, bestD()]);
        }
      }
    }
    return interpolate(history, p.total_budget, p.n_checkpoints);
  }

  function defaultParams(overrides) {
    const fixCM = 0.25;
    const rho = overrides.rho ?? 2;
    return {
      L: 100,
      d0: overrides.d0 ?? 20,
      N_tasks: 200,
      k: 5,
      c_task: 1,
      c_edit_CM: 1,
      c_edit_RM: 1,
      c_edit_CH: 1,
      fix_prob_CM: fixCM,
      fix_prob_RM: fixCM * rho,
      fix_prob_CH: fixCM * rho,
      break_prob_CM: 0.05,
      break_prob_RM: 0.05,
      break_prob_CH: 0.05,
      dgm_pop_size: 5,
      dgm_n_eval: 10,
      dgm_selection_frac: 0.4,
      ucb_c: 1,
      min_evals_before_edit: 3,
      max_evals_per_node: 20,
      edit_fail_threshold: 0.5,
      prob_RM_given_available: 0.5,
      prob_CH_given_available: 0.8,
      min_failures_for_RM: 2,
      total_budget: overrides.budget ?? 500,
      n_seeds: overrides.nSeeds ?? 60,
      n_checkpoints: overrides.nCheckpoints ?? 120,
      seed: overrides.seed ?? 42,
      rho,
    };
  }

  let runId = 0;

  self.onmessage = (ev) => {
    const msg = ev.data || {};
    if (msg.type !== 'run') return;
    const id = ++runId;
    const p = defaultParams(msg);
    const n = p.n_seeds;
    const nPts = p.n_checkpoints;
    const checkpoints = new Float32Array(nPts);
    for (let i = 0; i < nPts; i++) checkpoints[i] = (p.total_budget * i) / (nPts - 1);

    self.postMessage({
      type: 'start',
      id,
      d0: p.d0,
      rho: p.rho,
      nSeeds: n,
      budget: p.total_budget,
      checkpoints: Array.from(checkpoints),
    });

    for (let seed = 0; seed < n; seed++) {
      if (id !== runId) return;
      const traj = {
        DGM: runDgm(p, mulberry32(p.seed + seed * 3 + 1)),
        HGM: runHgmMgm(p, mulberry32(p.seed + seed * 3 + 2), false),
        MGM: runHgmMgm(p, mulberry32(p.seed + seed * 3 + 3), true),
      };
      self.postMessage(
        {
          type: 'seed',
          id,
          seed,
          nSeeds: n,
          traj: {
            DGM: Array.from(traj.DGM),
            HGM: Array.from(traj.HGM),
            MGM: Array.from(traj.MGM),
          },
        },
        []
      );
    }
    if (id === runId) self.postMessage({ type: 'done', id, nSeeds: n });
  };
})();
