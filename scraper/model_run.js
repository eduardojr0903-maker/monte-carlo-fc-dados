// Runs the app's model for every upcoming fixture (next 60 hours) of every league document and stores
// the probability of every market, exactly as the app would show them with the default (probable) lineups.
// Usage: node scraper/model_run.js [hours=60] [sims=20000]  -> data/model/<doc id>.json
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const MC = new Function(fs.readFileSync(path.join(__dirname, 'engine.js'), 'utf8') + ';return MC')();
const HOURS = Number(process.argv[2] || 60);
const SIMS = Number(process.argv[3] || 20000);

function unpack(doc) {
  let x = doc;
  if (typeof x.pack === 'string') x = { ...x, ...JSON.parse(x.pack) };
  const un = (a) => a.map((o) => (Array.isArray(o) ? o : o.r));
  return { ...x, teams: un(x.teams), players: un(x.players || []), matches: x.matches ? un(x.matches) : null, next: x.next || [] };
}

function merge(parts) {
  if (parts.length === 1) return parts[0];
  const N = parts.reduce((a, p) => a + p.N, 0);
  const cat = (k) => { const out = new Uint8Array(N); let o = 0; for (const p of parts) { out.set(p[k], o); o += p.N; } return out; };
  const players = parts[0].players.map((side, s) => side.map((pl, i) => {
    const w = (f) => parts.reduce((a, p) => a + p.players[s][i][f] * p.N, 0) / N;
    const wa = (f, j) => parts.reduce((a, p) => a + p.players[s][i][f][j] * p.N, 0) / N;
    return { ...pl, xSh: w('xSh'), xSot: w('xSot'), xG: w('xG'), sh: pl.sh.map((_, j) => wa('sh', j)), sot: pl.sot.map((_, j) => wa('sot', j)), goal: pl.goal.map((_, j) => wa('goal', j)) };
  }));
  const hasC = parts.every((p) => p.cH);
  return { N, gH: cat('gH'), gA: cat('gA'), sH: cat('sH'), sA: cat('sA'), tH: cat('tH'), tA: cat('tA'), players,
    cH: hasC ? cat('cH') : null, cA: hasC ? cat('cA') : null, cH1: hasC ? cat('cH1') : null, cA1: hasC ? cat('cA1') : null };
}

const r4 = (x) => Math.round(x * 10000) / 10000;
const now = Date.now() / 1000;
const outDir = path.join(ROOT, 'data', 'model');
fs.mkdirSync(outDir, { recursive: true });
for (const f of fs.readdirSync(path.join(ROOT, 'data', 'db')).filter((x) => /^s\d+\.json$/.test(x))) {
  const docId = f.slice(0, -5);
  const L = unpack(JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'db', f), 'utf8')));
  const out = { updated: new Date().toISOString(), league: L.name, comp: L.comp, fixtures: [] };
  for (const fx of L.next) {
    if (fx[1] < now + 3600 || fx[1] > now + HOURS * 3600) continue;
    const [hi, ai] = [fx[2], fx[3]];
    const hId = String(L.teams[hi][0]), aId = String(L.teams[ai][0]);
    const lineup = (teamId, xi, outs) => MC.lineupFrom(MC.playersOf(L, teamId), xi || [], new Set((outs || []).map((o) => String(o[0]))));
    const luH = lineup(hId, fx[5], fx[7]), luA = lineup(aId, fx[6], fx[8]);
    const ctx = fx[13] ? { h: fx[13][0], a: fx[13][1] } : null;
    let Ms;
    try {
      Ms = MC.prepareEnsemble(L, hId, aId, luH, luA, { halfLife: L.v >= 2 ? 12 : Infinity, ctx });
    } catch (e) { console.log('falhou', L.name, fx[0], e.message); continue; }
    const parts = [];
    let left = SIMS;
    Ms.forEach((e, j) => { const k = j === Ms.length - 1 ? left : Math.round(SIMS * e.w); left -= k; if (k > 0) parts.push(MC.simulate(e.M, k, 1234567 + j * 7919 + Number(fx[0]) % 100000)); });
    const sim = merge(parts);
    const names = [L.teams[hi][1], L.teams[ai][1]];
    const s = MC.summarize(sim, Ms[0].M, names);
    const markets = [];
    for (const g of s.groups.concat(s.shotGroups, s.cornerGroups || [])) for (const r of g.rows) markets.push([r.key, r.label, r4(r.p), g.id]);
    for (const r of s.scores) markets.push([r.key, r.label, r4(r.p), 'cs']);
    // every half-goal line the bookmaker may offer (the app shows only the lines around the expected value)
    const have = new Set(markets.map((m) => m[0]));
    const N = sim.N;
    const lines = (key, label, grp, f, from, to) => {
      for (let l = from; l <= to + 1e-9; l += 1) {
        let c = 0; for (let i = 0; i < N; i++) if (f(i) > l) c++;
        const o = c / N, ls = String(l).replace('.', ',');
        if (!have.has(key + '>' + l)) markets.push([key + '>' + l, label + ' mais de ' + ls, r4(o), grp]);
        if (!have.has(key + '<' + l)) markets.push([key + '<' + l, label + ' menos de ' + ls, r4(1 - o), grp]);
      }
    };
    lines('G', 'Total de gols', 'gols', (i) => sim.gH[i] + sim.gA[i], 0.5, 6.5);
    lines('HG', names[0] + ' gols', 'tg', (i) => sim.gH[i], 0.5, 4.5);
    lines('AG', names[1] + ' gols', 'tg', (i) => sim.gA[i], 0.5, 4.5);
    if (sim.cH) {
      lines('C', 'Total de escanteios', 'ct', (i) => sim.cH[i] + sim.cA[i], 3.5, 17.5);
      lines('CHt', names[0] + ' escanteios', 'ch', (i) => sim.cH[i], 0.5, 11.5);
      lines('CAt', names[1] + ' escanteios', 'ca', (i) => sim.cA[i], 0.5, 11.5);
      lines('C1', '1º tempo escanteios', 'c1', (i) => sim.cH1[i] + sim.cA1[i], 0.5, 9.5);
    }
    [0, 1].forEach((side) => {
      for (const p of sim.players[side]) {
        if (p.role !== 'T') continue;  // starters only (subs' minutes are too uncertain to bet)
        const add = (kind, j, prob, label) => markets.push(['P' + side + '_' + p.pid + '_' + kind + j, p.name + ' · ' + label, r4(prob), 'pl']);
        add('sh', 1, p.sh[0], '1+ chute'); add('sh', 2, p.sh[1], '2+ chutes'); add('sh', 3, p.sh[2], '3+ chutes');
        add('sot', 1, p.sot[0], '1+ chute no alvo'); add('sot', 2, p.sot[1], '2+ chutes no alvo');
        add('g', 1, p.goal[0], 'marcar a qualquer momento');
      }
    });
    out.fixtures.push({ fx: String(fx[0]), ts: fx[1], h: hId, a: aId, hn: names[0], an: names[1], src: fx[4],
      means: s.means, corners: s.cornerMeans || null, markets });
    console.log(L.name, names.join(' x '), markets.length, 'mercados');
  }
  fs.writeFileSync(path.join(outDir, docId + '.json'), JSON.stringify(out));
}
