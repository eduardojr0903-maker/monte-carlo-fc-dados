/* Monte Carlo FC — simulation engine (pure functions, no DOM) */
const MC = (() => {
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  // simulation settings (tuned by backtest)
  // Tuned on 9.372 matches (2023–2026, 8 leagues) against results and closing odds.
  const PARAMS = { disp: 50, pdisp: 4, shared: 0, conv: 0, kvol: 7, kvol0: 5, ks: 70, kq: 90, stretch: 1.25, xgw: 0.9, totShrink: 0.6,
    bttsA: 0.144, bttsB: 0.757, useRat: 1, luA: 1, luD: 0.3 };

  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function normal(R) {
    let u = 0, v = 0;
    while (u === 0) u = R();
    v = R();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  // Marsaglia–Tsang, shape >= 1
  function gamma(R, shape, scale) {
    const d = shape - 1 / 3, c = 1 / Math.sqrt(9 * d);
    for (;;) {
      let x, v;
      do { x = normal(R); v = 1 + c * x; } while (v <= 0);
      v = v * v * v;
      const u = R();
      if (u < 1 - 0.0331 * x * x * x * x) return d * v * scale;
      if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v * scale;
    }
  }
  function poisson(R, lam) {
    if (lam <= 0) return 0;
    if (lam > 40) return Math.max(0, Math.round(lam + Math.sqrt(lam) * normal(R)));
    const L = Math.exp(-lam);
    let k = 0, p = 1;
    do { k++; p *= R(); } while (p > L);
    return k - 1;
  }

  // ---- data access -------------------------------------------------------
  // Two data formats are supported:
  //  v2 (Sofascore, match by match):
  //    teams:   [id, name]
  //    matches: [ts, homeIdx, awayIdx, hg, ag, hSh, hSoT, hXG, aSh, aSoT, aXG]   (hSh = -1 when no shot data)
  //    players: [pid, name, teamIdx, pos, mp, st, min, sh, sot, xg, g,  rmp, rst, rmin, rsh, rsot, rxg, rg]
  //             (r* = same numbers over the team's last `recentN` matches)
  //  v1 (FBref season totals): see teamObjV1 / playerObjV1.
  const POS = { G: 'GK', D: 'DF', M: 'MF', F: 'FW' };

  function teamObjV1(r) {
    return { id: r[0], name: r[1], hMP: r[2], hGF: r[3], hGA: r[4], aMP: r[5], aGF: r[6], aGA: r[7],
      n: r[8], g: r[9], sh: r[10], sot: r[11], on: r[14], og: r[15], osh: r[16], osot: r[17] };
  }
  function playerObj(r, L) {
    if (L && L.v >= 2) {
      return { pid: String(r[0]), name: r[1], team: L.teams[r[2]][0], pos: POS[r[3]] || r[3] || 'MF',
        mp: r[4], st: r[5], min: r[6], sh: r[7], sot: r[8], xg: r[9] >= 0 ? r[9] : null, g: r[10],
        r: { mp: r[11], st: r[12], min: r[13], sh: r[14], sot: r[15], xg: r[16] >= 0 ? r[16] : null, g: r[17] }, shirt: r[18] || '',
        startMin: r[19] || 0, subMin: r[20] || 0,
        posLabel: r[21] || '', px: r[22] != null && r[22] >= 0 ? r[22] : null, py: r[23] != null && r[23] >= 0 ? r[23] : null,
        luOff: r[24] != null ? r[24] : null, luDef: r[25] != null ? r[25] : null };
    }
    const pos = String(r[3] || 'MF').split(',')[0] || 'MF';
    return { pid: r[0], name: r[1], team: r[2], pos, mp: r[4], st: r[5], min: r[6], g: r[7], sh: r[8], sot: r[9], xg: null, r: null };
  }
  function teamList(L) { return L.teams.map((t) => ({ id: String(t[0]), name: t[1] })); }
  function playersOf(L, teamId) {
    if (L.v >= 2) {
      const idx = L.teams.findIndex((t) => String(t[0]) === String(teamId));
      return L.players.filter((r) => r[2] === idx).map((r) => playerObj(r, L));
    }
    return L.players.filter((r) => r[2] === teamId).map((r) => playerObj(r, L));
  }

  // Weighted team aggregates. halfLife = matches (Infinity = whole season equally).
  function teamAgg(L, teamId, halfLife) {
    if (!(L.v >= 2)) {
      const t = teamObjV1(L.teams.find((r) => r[0] === teamId));
      return { n: t.n, nEff: t.n, sh: t.sh, sot: t.sot, xg: null, g: t.g, osh: t.osh, osot: t.osot, oxg: null, og: t.og, games: t.n, form: null };
    }
    const idx = L.teams.findIndex((t) => String(t[0]) === String(teamId));
    const ms = L.matches.filter((m) => (m[1] === idx || m[2] === idx) && m[5] >= 0).sort((a, b) => b[0] - a[0]);
    const a = { n: 0, w2: 0, sh: 0, sot: 0, xg: 0, g: 0, osh: 0, osot: 0, oxg: 0, og: 0 };
    ms.forEach((m, k) => {
      const w = isFinite(halfLife) ? Math.pow(0.5, k / halfLife) : 1;
      const home = m[1] === idx;
      const f = home ? [m[5], m[6], m[7], m[3]] : [m[8], m[9], m[10], m[4]];
      const o = home ? [m[8], m[9], m[10], m[4]] : [m[5], m[6], m[7], m[3]];
      a.n += w; a.w2 += w * w;
      a.sh += w * f[0]; a.sot += w * f[1]; a.xg += w * f[2]; a.g += w * f[3];
      a.osh += w * o[0]; a.osot += w * o[1]; a.oxg += w * o[2]; a.og += w * o[3];
    });
    // recent form string (last 5): W/D/L
    const form = L.matches.filter((m) => m[1] === idx || m[2] === idx).sort((x, y) => y[0] - x[0]).slice(0, 5).map((m) => {
      const gf = m[1] === idx ? m[3] : m[4], ga = m[1] === idx ? m[4] : m[3];
      return gf > ga ? 'V' : gf === ga ? 'E' : 'D';
    });
    // Express sums per "effective game" so shrinkage uses the effective sample size
    const nEff = a.w2 > 0 ? (a.n * a.n) / a.w2 : 0;
    return { n: a.n, nEff, sh: a.sh, sot: a.sot, xg: a.xg, g: a.g, osh: a.osh, osot: a.osot, oxg: a.oxg, og: a.og, games: ms.length, form };
  }

  const statsCache = new WeakMap();
  function leagueStats(L) {
    if (statsCache.has(L)) return statsCache.get(L);
    const S = leagueStatsRaw(L);
    S.pos = positionPriors(L, S);
    statsCache.set(L, S);
    return S;
  }
  // League averages by position, from every player with minutes: shots/90, on-target rate, xG and goals per shot.
  function positionPriors(L, S) {
    const acc = {};
    for (const r of L.players) {
      const p = playerObj(r, L);
      const a = acc[p.pos] || (acc[p.pos] = { min: 0, sh: 0, sot: 0, xg: 0, xgsh: 0, g: 0 });
      a.min += p.min; a.sh += p.sh; a.sot += p.sot; a.g += p.g;
      if (p.xg != null) { a.xg += p.xg; a.xgsh += p.sh; }
    }
    const def = { GK: [0.02, 0.3, 0.08], DF: [0.45, 0.3, 0.08], MF: [1.0, 0.31, 0.08], FW: [2.2, 0.38, 0.13] };
    const out = {};
    for (const k of ['GK', 'DF', 'MF', 'FW']) {
      const a = acc[k], d = def[k];
      const ok = a && a.min > 2000 && a.sh > 30;
      out[k] = {
        sh90: ok ? a.sh / (a.min / 90) : d[0],
        acc: ok ? a.sot / a.sh : d[1],
        gsh: ok ? (a.g + d[2] * 40) / (a.sh + 40) : d[2],
        xgsh: ok && a.xgsh > 30 ? a.xg / a.xgsh : null
      };
    }
    return out;
  }
  function leagueStatsRaw(L) {
    if (!(L.v >= 2)) {
      let n = 0, sh = 0, sot = 0, g = 0, hGF = 0, hMP = 0, aGF = 0, aMP = 0;
      for (const r of L.teams) {
        const t = teamObjV1(r);
        n += t.n; sh += t.sh; sot += t.sot; g += t.g; hGF += t.hGF; hMP += t.hMP; aGF += t.aGF; aMP += t.aMP;
      }
      const shPer = n > 0 && sh > 0 ? sh / n : 12.5;
      const sotRate = sh > 0 ? sot / sh : 0.34;
      const gPerShot = sh > 0 ? g / sh : 0.105;
      let hfa = 1.2;
      if (hMP >= 20 && aMP >= 20 && aGF > 0) {
        const w = Math.min(1, hMP / 150);
        hfa = clamp(((hGF / hMP) / (aGF / aMP)) * w + 1.2 * (1 - w), 1.0, 1.6);
      }
      return { shPer, sotRate, gPerShot, xgPerShot: null, hfa, games: hMP, hasXg: false };
    }
    let n = 0, sh = 0, sot = 0, xg = 0, g = 0, hq = 0, aq = 0, games = 0;
    for (const m of L.matches) {
      if (m[5] < 0) continue;
      games++; n += 2;
      const hx = m[7] >= 0 ? m[7] : m[3], ax = m[10] >= 0 ? m[10] : m[4];
      sh += m[5] + m[8]; sot += m[6] + m[9]; g += m[3] + m[4];
      if (m[7] >= 0 && m[10] >= 0) xg += m[7] + m[10];
      hq += m[3] + hx; aq += m[4] + ax;
    }
    const shPer = n > 0 ? sh / n : 12.5;
    const sotRate = sh > 0 ? sot / sh : 0.34;
    const gPerShot = sh > 0 ? g / sh : 0.105;
    const xgPerShot = L.hasXg !== false && sh > 0 && xg > 0 ? xg / sh : null;
    let hfa = 1.2;
    if (games >= 10 && aq > 0) {
      const w = Math.min(1, games / 150);
      hfa = clamp((hq / aq) * w + 1.2 * (1 - w), 1.0, 1.6);
    }
    if (L.hist && L.hist.hfa) hfa = clamp(L.hist.hfa, 1.0, 1.6); // league home advantage from 2022+ history
    return { shPer, sotRate, gPerShot, xgPerShot, hfa, games, hasXg: xgPerShot != null };
  }

  // Goals per shot used by the model: mostly xG (stable), partly real goals (finishing/keeping skill).
  function qualityPerShot(g, xg, sh, S) {
    if (xg == null || !S.hasXg) return g;
    return PARAMS.xgw * xg + (1 - PARAMS.xgw) * g;
  }

  // prior = the team's starting level from previous seasons [attSh, defSh, attAcc, defAcc, attQ, defQ] (1 = league average)
  function teamRating(T, S, prior) {
    const P = prior || [1, 1, 1, 1, 1, 1];
    const K = prior ? PARAMS.kvol : PARAMS.kvol0; // games of prior for shot volume (more weight when it is the team's own history)
    const KS = PARAMS.ks, KQ = PARAMS.kq;           // shots of prior for accuracy / quality
    const n = T.nEff || 0, w = T.n > 0 ? n / T.n : 0; // scale weighted sums to effective-sample units
    const sh = T.sh * w, osh = T.osh * w, sot = T.sot * w, osot = T.osot * w;
    const q = qualityPerShot(T.g, T.xg, T.sh, S) * w, oq = qualityPerShot(T.og, T.oxg, T.osh, S) * w;
    const qL = S.hasXg ? PARAMS.xgw * S.xgPerShot + (1 - PARAMS.xgw) * S.gPerShot : S.gPerShot;
    return {
      attSh: ((sh + S.shPer * P[0] * K) / (n + K)) / S.shPer,
      defSh: ((osh + S.shPer * P[1] * K) / (n + K)) / S.shPer,
      attAcc: ((sot + S.sotRate * P[2] * KS) / (sh + KS)) / S.sotRate,
      defAcc: ((osot + S.sotRate * P[3] * KS) / (osh + KS)) / S.sotRate,
      attQ: ((q + qL * P[4] * KQ) / (sh + KQ)) / qL,
      defQ: ((oq + qL * P[5] * KQ) / (osh + KQ)) / qL,
      sh90: T.n > 0 ? T.sh / T.n : S.shPer,
      acc: T.sh > 0 ? T.sot / T.sh : S.sotRate,
      q: T.sh > 0 ? qualityPerShot(T.g, T.xg, T.sh, S) / T.sh : qL,
      qL
    };
  }

  const poolCache = new WeakMap();
  function pooled(L, pid) {
    let m = poolCache.get(L);
    if (!m) {
      m = new Map();
      for (const r of L.players) {
        const p = playerObj(r, L);
        const t = m.get(p.pid) || { min: 0, sh: 0, sot: 0, xg: 0, xgn: 0, g: 0, r: { min: 0, sh: 0, sot: 0 } };
        t.min += p.min; t.sh += p.sh; t.sot += p.sot; t.g += p.g;
        if (p.xg != null) { t.xg += p.xg; t.xgn += p.sh; }
        if (p.r) { t.r.min += p.r.min; t.r.sh += p.r.sh; t.r.sot += p.r.sot; }
        m.set(p.pid, t);
      }
      poolCache.set(L, m);
    }
    return m.get(pid);
  }
  // recent weight for player rates, by team half-life
  function recentWeight(halfLife) { return !isFinite(halfLife) ? 0 : clamp(3 / halfLife, 0.2, 0.8); }

  // Per-player rates, each shrunk toward the league average for the player's position.
  // Stats are pooled across clubs, so a player who changed teams this season keeps his history.
  function playerRates(p, R, S, halfLife, L) {
    const P = (L && pooled(L, p.pid)) || { min: p.min, sh: p.sh, sot: p.sot, xg: p.xg || 0, xgn: p.xg != null ? p.sh : 0, g: p.g, r: p.r || { min: 0, sh: 0, sot: 0 } };
    const PP = (S.pos && S.pos[p.pos]) || { sh90: 1, acc: 0.33, gsh: 0.1, xgsh: null };
    // shots per 90: prior = position average scaled by how much the team shoots
    const prior = PP.sh90 * (R.sh90 / S.shPer);
    const K = 3;
    const rate = (sh, min) => (sh + prior * K) / (min / 90 + K);
    let sh90 = rate(P.sh, P.min);
    const rw = recentWeight(halfLife);
    if (P.r && P.r.min > 0 && rw > 0) sh90 = (1 - rw) * sh90 + rw * ((P.r.sh + sh90 * 4) / (P.r.min / 90 + 4));
    // share of shots on target (25 shots of prior)
    const acc = (P.sot + PP.acc * 25) / (P.sh + 25);
    // goal value per shot: xG per shot (15 shots of prior) times a small finishing factor;
    // without xG, goals per shot with a heavy prior (60 shots), because conversion is mostly noise
    let q;
    if (S.hasXg && PP.xgsh && P.xgn > 0) {
      const xgsh = (P.xg + PP.xgsh * 15) / (P.xgn + 15);
      const finishing = clamp((P.g + 6) / (P.xg + 6), 0.85, 1.2);
      q = xgsh * finishing;
    } else {
      q = (P.g + PP.gsh * 60) / (P.sh + 60);
    }
    return { sh90, acc, q };
  }

  // Expected minutes = the player's average in this competition (as a starter / off the bench)
  function defaultMinutes(p) {
    if (p.startMin > 0) return Math.round(clamp(p.startMin, 1, 90));
    if (p.pos === 'GK') return 90;
    if (p.st > 0) {
      const subApps = Math.max(0, p.mp - p.st);
      return Math.round(clamp((p.min - subApps * 18) / p.st, 45, 90));
    }
    return 70;
  }
  function subMinutes(p) {
    return p.subMin > 0 ? Math.round(clamp(p.subMin, 1, 45)) : 20;
  }


  // Suggested lineup = the most used one: recent starts first (rotation, injuries, transfers), then season starts.
  function usage(p) { return (p.r ? p.r.st * 3 + p.r.mp * 0.5 : 0) + p.st + p.min / 900; }
  function suggestLineup(players) {
    const ps = players.slice().sort((a, b) => usage(b) - usage(a));
    const out = {};
    const gk = ps.find((p) => p.pos === 'GK');
    if (gk) out[gk.pid] = { role: 'T', min: 90 };
    let starters = gk ? 1 : 0;
    for (const p of ps) {
      if (starters >= 11) break;
      if (p.pos === 'GK' || out[p.pid]) continue;
      out[p.pid] = { role: 'T', min: defaultMinutes(p) };
      starters++;
    }
    const subUse = (p) => (p.r ? (p.r.mp - p.r.st) * 3 : 0) + (p.mp - p.st);
    const rest = ps.filter((p) => !out[p.pid] && p.pos !== 'GK').sort((a, b) => subUse(b) - subUse(a) || b.min - a.min);
    for (const p of rest.slice(0, 4)) out[p.pid] = { role: 'R', min: subMinutes(p) };
    return out;
  }

  // Lineup from a source XI (confirmed or predicted): keeps the listed players who exist and are available,
  // fills any gap with the most-used available players, and picks 4 likely substitutes.
  function lineupFrom(players, xi, outSet) {
    const out = {};
    const avail = (p) => !(outSet && outSet.has(p.pid));
    const byId = new Map(players.map((p) => [p.pid, p]));
    let n = 0;
    for (const id of xi || []) {
      const p = byId.get(String(id));
      if (!p || !avail(p) || out[p.pid] || n >= 11) continue;
      out[p.pid] = { role: 'T', min: defaultMinutes(p) };
      n++;
    }
    const ps = players.filter((p) => avail(p) && !out[p.pid]).sort((a, b) => usage(b) - usage(a));
    const hasGk = Object.keys(out).some((id) => byId.get(id).pos === 'GK');
    if (!hasGk) {
      const gk = ps.find((p) => p.pos === 'GK');
      if (gk && n < 11) { out[gk.pid] = { role: 'T', min: 90 }; n++; }
    }
    for (const p of ps) {
      if (n >= 11) break;
      if (p.pos === 'GK' || out[p.pid]) continue;
      out[p.pid] = { role: 'T', min: defaultMinutes(p) };
      n++;
    }
    const subUse = (p) => (p.r ? (p.r.mp - p.r.st) * 3 : 0) + (p.mp - p.st) + p.min / 2000;
    const bench = players.filter((p) => avail(p) && !out[p.pid] && p.pos !== 'GK').sort((a, b) => subUse(b) - subUse(a));
    for (const p of bench.slice(0, 4)) out[p.pid] = { role: 'R', min: subMinutes(p) };
    return out;
  }

  function teamPrior(L, teamId) {
    return (L.hist && L.hist.prior && L.hist.prior[String(teamId)]) || null;
  }
  // Projected xG for/against per game against an average opponent on neutral ground
  function teamStrength(L, teamId, halfLife) {
    const S = leagueStats(L);
    const R = applyRat(L, teamId, teamRating(teamAgg(L, teamId, halfLife), S, teamPrior(L, teamId)));
    return {
      xgFor: S.shPer * R.attSh * R.qL * R.attQ,
      xgAg: S.shPer * R.defSh * R.qL * R.defQ,
      shFor: S.shPer * R.attSh, shAg: S.shPer * R.defSh,
      homeEffect: (L.hist && L.hist.teamHfa && L.hist.teamHfa[String(teamId)]) || 0
    };
  }
  // Opponent-adjusted ratings fitted by the collector (model v2), when present
  function applyRat(L, teamId, R) {
    const r = PARAMS.useRat && L.rat && L.rat[String(teamId)];
    if (!r) return R;
    return Object.assign(R, { attSh: r[0], defSh: r[1], attAcc: r[2], defAcc: r[3], attQ: r[4], defQ: r[5], fromRat: true });
  }
  function buildSide(L, S, teamId, lineup, halfLife) {
    const T = teamAgg(L, teamId, halfLife);
    const R = applyRat(L, teamId, teamRating(T, S, teamPrior(L, teamId)));
    const players = playersOf(L, teamId);
    const sel = [];
    let sumMin = 0;
    for (const p of players) {
      const l = lineup[p.pid];
      if (!l || l.role === 'X' || !(l.min > 0)) continue;
      sel.push({ pid: p.pid, name: p.name, pos: p.pos, role: l.role, min: l.min, ...playerRates(p, R, S, halfLife, L) });
      sumMin += l.min;
    }
    const otherMin = Math.max(0, 990 - sumMin);
    const w = sel.map((p) => p.sh90 * p.min / 90);
    const wOther = (R.sh90 / 10) * 0.8 * otherMin / 90;
    const projected = w.reduce((a, b) => a + b, 0) + wOther;
    // compare with the team's usual lineup (most used players): the team rating already reflects it
    const base = lineupFrom(players, [], null);
    let bSum = 0, bMin = 0;
    for (const p of players) {
      const l = base[p.pid];
      if (!l) continue;
      bSum += playerRates(p, R, S, halfLife, L).sh90 * l.min / 90;
      bMin += l.min;
    }
    bSum += (R.sh90 / 10) * 0.8 * Math.max(0, 990 - bMin) / 90;
    const lineupFactor = bSum > 0 ? clamp(Math.pow(projected / bSum, 0.5), 0.88, 1.12) : 1;
    return { T, R, sel, w, wOther, lineupFactor, sumMin };
  }

  // Lineup strength vs the team's usual lineup, from players' attack (npxG+xA/90) and on-pitch xG conceded
  function lineupStrength(L, teamId, lineup) {
    const u = L.luTeam && L.luTeam[String(teamId)];
    if (!u) return null;
    const ps = playersOf(L, teamId).filter((p) => lineup[p.pid] && lineup[p.pid].role === 'T');
    if (ps.length < 9) return null;
    let o = 0, d = 0;
    for (const p of ps) { o += p.luOff != null ? p.luOff : 0.12; d += p.luDef != null ? p.luDef : 0; }
    o *= 11 / ps.length;
    return [clamp(u[0] > 0 ? o / u[0] : 1, 0.7, 1.4), clamp(Math.exp((d - u[1]) / 1.3), 0.85, 1.2)];
  }
  function prepareMatch(L, homeId, awayId, lineupH, lineupA, opts = {}) {
    if (!opts.lu && L.luTeam) {
      const fh = lineupStrength(L, homeId, lineupH), fa = lineupStrength(L, awayId, lineupA);
      if (fh && fa) opts = Object.assign({}, opts, { lu: [fh[0], fh[1], fa[0], fa[1]] });
    }
    const halfLife = opts.halfLife == null ? Infinity : opts.halfLife;
    const S = leagueStats(L);
    const hfa = opts.neutral ? 1 : S.hfa;
    const H = buildSide(L, S, homeId, lineupH, halfLife);
    const A = buildSide(L, S, awayId, lineupA, halfLife);
    // each team's own home effect (from 2022+ history, already shrunk by how consistent it is)
    const th = L.hist && L.hist.teamHfa && !opts.neutral ? (L.hist.teamHfa[String(homeId)] || 0) + (L.hist.teamHfa[String(awayId)] || 0) : 0;
    const teamHa = Math.exp(th / 4);
    const baseHa = PARAMS.useRat && L.ratHome && H.R.fromRat && A.R.fromRat && !opts.neutral ? L.ratHome[2] : Math.sqrt(hfa);
    const sides = [[H, A, baseHa * teamHa], [A, H, 1 / (baseHa * teamHa)]].map(([X, Y, ha]) => {
      const k = PARAMS.stretch;
      // lineup factors from the players on the pitch (attack of this side, defence of the other)
      const lu = opts.lu || L.lu;
      const luMul = lu ? Math.pow(X === H ? lu[0] : lu[2], PARAMS.luA) * Math.pow(X === H ? lu[3] : lu[1], PARAMS.luD) : 1;
      // with lineup strength available it replaces the simpler shot-volume lineup factor
      const mu = S.shPer * Math.pow(X.R.attSh * Y.R.defSh, k) * ha * (lu ? 1 : X.lineupFactor) * luMul;
      const pSot = clamp(S.sotRate * Math.pow(X.R.attAcc * Y.R.defAcc, k), 0.12, 0.7);
      const pGshot = clamp(X.R.qL * Math.pow(X.R.attQ * Y.R.defQ, k), 0.03, 0.3);
      const weights = X.w.concat([X.wOther]);
      const tot = weights.reduce((a, b) => a + b, 0) || 1;
      const accRaw = X.sel.map((p) => p.acc).concat([X.R.acc]);
      const qRaw = X.sel.map((p) => p.q).concat([X.R.q]);
      const avgAcc = weights.reduce((s, wi, i) => s + wi * accRaw[i], 0) / tot;
      const acc = accRaw.map((a) => clamp(a * pSot / avgAcc, 0.03, 0.95));
      const avgQ = weights.reduce((s, wi, i) => s + wi * qRaw[i], 0) / tot;
      // P(goal | shot on target) = P(goal per shot) / P(on target)
      const fin = qRaw.map((q, i) => clamp((q * pGshot / avgQ) / acc[i], 0.02, 0.92));
      const cum = [];
      let c = 0;
      for (const wi of weights) { c += wi / tot; cum.push(c); }
      cum[cum.length - 1] = 1;
      return { mu, pSot, pG: pGshot / pSot, cum, wts: weights.map((wi) => wi / tot), acc, fin, xg: mu * pGshot };
    });
    // Expected total goals pulled toward the league average (the backtest shows the raw spread of totals is too wide);
    // the split between the teams is kept. Applied to conversion, so shot numbers are unchanged.
    if (PARAMS.totShrink !== 1) {
      const qL = H.R.qL, tl = 2 * S.shPer * qL;
      const tm = sides[0].xg + sides[1].xg;
      if (tm > 0 && tl > 0) {
        const f = Math.exp(Math.log(tl) + PARAMS.totShrink * (Math.log(tm) - Math.log(tl))) / tm;
        for (const sd of sides) { sd.fin = sd.fin.map((x) => clamp(x * f, 0.02, 0.95)); sd.xg *= f; sd.pG *= f; }
      }
    }
    return { S, hfa, halfLife, H, A, sides, lu: opts.lu || L.lu || null, rated: !!(H.R.fromRat && A.R.fromRat) };
  }

  // Two model variants, chosen by backtest, simulated and combined (60% / 40%):
  // v1 = season data + historical prior; v2 = opponent-adjusted ratings since 2022 (when available).
  const ENSEMBLE = [{ w: 0.6, set: { useRat: 0, stretch: 1.25 } }, { w: 0.4, set: { useRat: 1, stretch: 1.45 } }];
  function prepareEnsemble(L, homeId, awayId, lineupH, lineupA, opts = {}) {
    const keep = Object.assign({}, PARAMS);
    const out = [];
    for (const e of ENSEMBLE) {
      if (e.set.useRat && !L.rat) continue;
      Object.assign(PARAMS, e.set);
      out.push({ w: e.w, M: prepareMatch(L, homeId, awayId, lineupH, lineupA, opts) });
      Object.assign(PARAMS, keep);
    }
    const tw = out.reduce((a, b) => a + b.w, 0);
    out.forEach((o) => { o.w /= tw; });
    return out;
  }

  // Run N simulations. Returns raw arrays + per-player threshold counts.
  function simulate(M, N, seed, onProgress) {
    const R = mulberry32(seed || (Date.now() & 0x7fffffff));
    const DISP = PARAMS.disp;     // negative-binomial size for each team's shots
    const PDISP = PARAMS.pdisp;   // spread of a player's share of team shots from game to game
    const SHARED = PARAMS.shared; // common tempo of the match (0 = off): both teams shoot more or less together
    const CONV = PARAMS.conv;     // match-level finishing luck per team (0 = off)
    const gH = new Uint8Array(N), gA = new Uint8Array(N), sH = new Uint8Array(N), sA = new Uint8Array(N), tH = new Uint8Array(N), tA = new Uint8Array(N);
    const nP = M.sides.map((s) => s.cum.length);
    const MAXK = 6;
    const pShots = nP.map((n) => new Uint32Array(n * (MAXK + 1)));
    const pSot = nP.map((n) => new Uint32Array(n * (MAXK + 1)));
    const pGoal = nP.map((n) => new Uint32Array(n * (MAXK + 1)));
    const pSum = nP.map((n) => new Float64Array(n * 3));
    const tmpC = nP.map((n) => new Float64Array(n));
    const tmpS = nP.map((n) => new Uint8Array(n)), tmpT = nP.map((n) => new Uint8Array(n)), tmpG = nP.map((n) => new Uint8Array(n));
    for (let s = 0; s < N; s++) {
      const tempo = SHARED > 0 ? gamma(R, SHARED, 1 / SHARED) : 1;
      for (let side = 0; side < 2; side++) {
        const X = M.sides[side];
        const lam = gamma(R, DISP, X.mu * tempo / DISP);
        const luck = CONV > 0 ? gamma(R, CONV, 1 / CONV) : 1;
        const k = poisson(R, lam);
        let sot = 0, goals = 0;
        const n = X.cum.length, ts = tmpS[side], tt = tmpT[side], tg = tmpG[side], cum = tmpC[side];
        // per-game player variance (role, minutes, game script): gamma-distributed share multipliers
        let ctot = 0;
        for (let i = 0; i < n; i++) { const f = i === n - 1 ? 1 : gamma(R, PDISP, 1 / PDISP); cum[i] = X.wts[i] * f; ctot += cum[i]; }
        let acc = 0;
        for (let i = 0; i < n; i++) { acc += cum[i] / ctot; cum[i] = acc; }
        cum[n - 1] = 1;
        ts.fill(0); tt.fill(0); tg.fill(0);
        for (let j = 0; j < k; j++) {
          const u = R();
          let i = 0;
          while (i < n - 1 && u > cum[i]) i++;
          ts[i]++;
          if (R() < X.acc[i]) { sot++; tt[i]++; if (R() < Math.min(0.95, X.fin[i] * luck)) { goals++; tg[i]++; } }
        }
        const kk = Math.min(k, 255);
        if (side === 0) { gH[s] = goals; sH[s] = kk; tH[s] = sot; } else { gA[s] = goals; sA[s] = kk; tA[s] = sot; }
        const PS = pShots[side], PT = pSot[side], PG = pGoal[side], SUM = pSum[side];
        for (let i = 0; i < n; i++) {
          SUM[i * 3] += ts[i]; SUM[i * 3 + 1] += tt[i]; SUM[i * 3 + 2] += tg[i];
          PS[i * (MAXK + 1) + Math.min(ts[i], MAXK)]++;
          PT[i * (MAXK + 1) + Math.min(tt[i], MAXK)]++;
          PG[i * (MAXK + 1) + Math.min(tg[i], MAXK)]++;
        }
      }
      if (onProgress && (s & 4095) === 0) onProgress(s / N);
    }
    const geq = (H, i, k) => { let c = 0; for (let x = k; x <= MAXK; x++) c += H[i * (MAXK + 1) + x]; return c / N; };
    const players = [0, 1].map((side) => {
      const X = side === 0 ? M.H : M.A;
      return X.sel.map((p, i) => ({
        pid: p.pid, name: p.name, pos: p.pos, role: p.role, min: p.min,
        xSh: pSum[side][i * 3] / N, xSot: pSum[side][i * 3 + 1] / N, xG: pSum[side][i * 3 + 2] / N,
        sh: [1, 2, 3, 4].map((k) => geq(pShots[side], i, k)),
        sot: [1, 2, 3].map((k) => geq(pSot[side], i, k)),
        goal: [1, 2].map((k) => geq(pGoal[side], i, k))
      }));
    });
    return { N, gH, gA, sH, sA, tH, tA, players };
  }

  // ---- markets -------------------------------------------------------------
  function summarize(sim, M, names) {
    const { N, gH, gA, sH, sA, tH, tA } = sim;
    const frac = (f) => { let c = 0; for (let i = 0; i < N; i++) if (f(i)) c++; return c / N; };
    const mean = (a) => { let s = 0; for (let i = 0; i < N; i++) s += a[i]; return s / N; };
    const [hn, an] = names;
    const pH = frac((i) => gH[i] > gA[i]), pD = frac((i) => gH[i] === gA[i]), pA = 1 - pH - pD;
    const groups = [];
    groups.push({ id: 'res', title: 'Resultado final', rows: [
      { key: '1', label: 'Vitória ' + hn, p: pH },
      { key: 'X', label: 'Empate', p: pD },
      { key: '2', label: 'Vitória ' + an, p: pA }
    ] });
    groups.push({ id: 'dc', title: 'Dupla chance e empate anula', rows: [
      { key: '1X', label: hn + ' ou empate', p: pH + pD },
      { key: 'X2', label: an + ' ou empate', p: pA + pD },
      { key: '12', label: hn + ' ou ' + an, p: pH + pA },
      { key: 'DNB1', label: hn + ' (empate anula)', p: pH / (pH + pA), fairOverride: (pH + pA) / pH },
      { key: 'DNB2', label: an + ' (empate anula)', p: pA / (pH + pA), fairOverride: (pH + pA) / pA }
    ] });
    groups.push({ id: 'hcp', title: 'Handicap', rows: [
      { key: 'H-1.5', label: hn + ' −1,5', p: frac((i) => gH[i] - gA[i] >= 2) },
      { key: 'A+1.5', label: an + ' +1,5', p: frac((i) => gH[i] - gA[i] <= 1) },
      { key: 'A-1.5', label: an + ' −1,5', p: frac((i) => gA[i] - gH[i] >= 2) },
      { key: 'H+1.5', label: hn + ' +1,5', p: frac((i) => gA[i] - gH[i] <= 1) }
    ] });
    const goalRows = [];
    for (const l of [0.5, 1.5, 2.5, 3.5, 4.5]) {
      const o = frac((i) => gH[i] + gA[i] > l);
      goalRows.push({ key: 'G>' + l, label: 'Mais de ' + fmtLine(l) + ' gols', p: o });
      goalRows.push({ key: 'G<' + l, label: 'Menos de ' + fmtLine(l) + ' gols', p: 1 - o });
    }
    groups.push({ id: 'gols', title: 'Total de gols', rows: goalRows });
    const bttsRaw = frac((i) => gH[i] > 0 && gA[i] > 0);
    // calibration fitted on the backtest (raw simulation under-predicts both teams scoring)
    const lgt = (p) => Math.log(clamp(p, 0.01, 0.99) / (1 - clamp(p, 0.01, 0.99)));
    const btts = 1 / (1 + Math.exp(-(PARAMS.bttsA + PARAMS.bttsB * lgt(bttsRaw))));
    const bttsK = bttsRaw > 0 ? btts / bttsRaw : 1;
    groups.push({ id: 'btts', title: 'Ambas marcam', rows: [
      { key: 'BTTS-S', label: 'Ambas marcam: sim', p: btts },
      { key: 'BTTS-N', label: 'Ambas marcam: não', p: 1 - btts },
      { key: 'BTTS-1', label: 'Ambas marcam e ' + hn + ' vence', p: Math.min(0.99, bttsK * frac((i) => gH[i] > 0 && gA[i] > 0 && gH[i] > gA[i])) },
      { key: 'BTTS-2', label: 'Ambas marcam e ' + an + ' vence', p: Math.min(0.99, bttsK * frac((i) => gH[i] > 0 && gA[i] > 0 && gA[i] > gH[i])) }
    ] });
    const teamGoals = [];
    for (const [nm, arr, s] of [[hn, gH, 'H'], [an, gA, 'A']]) {
      for (const l of [0.5, 1.5, 2.5]) {
        const o = frac((i) => arr[i] > l);
        teamGoals.push({ key: s + 'G>' + l, label: nm + ' mais de ' + fmtLine(l), p: o });
      }
      teamGoals.push({ key: s + 'CS', label: nm + ' sem sofrer gol', p: frac((i) => (s === 'H' ? gA[i] : gH[i]) === 0) });
    }
    groups.push({ id: 'tg', title: 'Gols por equipe', rows: teamGoals });

    const lineRows = (arr, label, step, keyp, unit) => {
      const m = mean(arr);
      const c = Math.floor(m) + 0.5;
      const lines = [c - 2 * step, c - step, c, c + step, c + 2 * step].filter((l) => l > 0);
      const rows = [];
      for (const l of lines) {
        const o = frac((i) => arr[i] > l);
        rows.push({ key: keyp + '>' + l, label: label + ' mais de ' + fmtLine(l) + unit, p: o });
        rows.push({ key: keyp + '<' + l, label: label + ' menos de ' + fmtLine(l) + unit, p: 1 - o });
      }
      return { mean: m, rows };
    };
    const tot = new Uint16Array(N), totT = new Uint16Array(N);
    for (let i = 0; i < N; i++) { tot[i] = sH[i] + sA[i]; totT[i] = tH[i] + tA[i]; }
    const shotsTot = lineRows(tot, 'Total', 2, 'S', ' chutes');
    const shotsH = lineRows(sH, hn, 2, 'SH', ' chutes');
    const shotsA = lineRows(sA, an, 2, 'SA', ' chutes');
    const sotTot = lineRows(totT, 'Total', 1, 'T', ' no alvo');
    const sotH = lineRows(tH, hn, 1, 'TH', ' no alvo');
    const sotA = lineRows(tA, an, 1, 'TA', ' no alvo');
    const shotGroups = [
      { id: 'st', title: 'Total de chutes', rows: shotsTot.rows },
      { id: 'sh', title: 'Chutes · ' + hn, rows: shotsH.rows },
      { id: 'sa', title: 'Chutes · ' + an, rows: shotsA.rows },
      { id: 'tt', title: 'Total de chutes no alvo', rows: sotTot.rows },
      { id: 'th', title: 'No alvo · ' + hn, rows: sotH.rows },
      { id: 'ta', title: 'No alvo · ' + an, rows: sotA.rows }
    ];
    // correct scores
    const sc = new Map();
    for (let i = 0; i < N; i++) { const k = Math.min(gH[i], 9) + '-' + Math.min(gA[i], 9); sc.set(k, (sc.get(k) || 0) + 1); }
    const scores = [...sc.entries()].map(([k, c]) => ({ key: 'CS' + k, label: k.replace('-', ' × '), p: c / N })).sort((a, b) => b.p - a.p).slice(0, 12);
    // goal distribution 0..6+
    const dist = (arr) => { const d = new Array(7).fill(0); for (let i = 0; i < N; i++) d[Math.min(arr[i], 6)]++; return d.map((x) => x / N); };
    return {
      pH, pD, pA,
      means: { gH: mean(gH), gA: mean(gA), sH: mean(sH), sA: mean(sA), tH: mean(tH), tA: mean(tA) },
      groups, shotGroups, scores,
      distH: dist(gH), distA: dist(gA)
    };
  }

  function fmtLine(l) { return String(l).replace('.', ','); }

  return { PARAMS, prepareEnsemble, lineupStrength, leagueStats, lineupFrom, subMinutes, teamStrength, teamAgg, teamList, playersOf, playerObj, suggestLineup, defaultMinutes, prepareMatch, simulate, summarize };
})();
