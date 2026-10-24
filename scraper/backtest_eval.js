// Team-level backtest: calibration and betting return against closing odds.
// Usage: node scraper/backtest_eval.js ENGINE.js CASES.jsonl [params JSON] [halfLife]
const fs = require('fs');
const readline = require('readline');
const MC = new Function(fs.readFileSync(process.argv[2], 'utf8') + ';return MC')();
if (process.argv[4]) Object.assign(MC.PARAMS, JSON.parse(process.argv[4]));
const HL = process.argv[5] ? Number(process.argv[5]) : 6;
const N = Number(process.env.SIMS || 3000);
const dump = process.env.DUMP ? fs.createWriteStream(process.env.DUMP) : null;
const LN = { 268: 'Série A', 8814: 'Série B', 47: 'Premier League', 48: 'Championship', 87: 'La Liga', 55: 'Serie A IT', 54: 'Bundesliga', 53: 'Ligue 1', 57: 'Eredivisie', 61: 'Liga Portugal' };
const L2 = (p) => Math.log(Math.max(p, 1e-4));
const devig = (o) => { const inv = o.map((x) => 1 / x), s = inv.reduce((a, b) => a + b, 0); return inv.map((x) => x / s); };
const st = {};
const S = (k) => st[k] || (st[k] = { n: 0, ll: 0, llm: 0, no: 0, ou: 0, oum: 0, nou: 0, bt: 0, nbt: 0, cal: {}, bets: {} });
function bet(acc, key, p, odd, win) {
  if (!odd) return;
  for (const th of [0.02, 0.05, 0.10]) {
    if (p * odd - 1 < th) continue;
    const b = acc.bets[key + ' ≥' + Math.round(th * 100) + '%'] || (acc.bets[key + ' ≥' + Math.round(th * 100) + '%'] = { n: 0, pl: 0 });
    b.n++; b.pl += win ? odd - 1 : -1;
  }
}
function cal(acc, k, p, y) { const c = acc.cal[k] || (acc.cal[k] = Array.from({ length: 10 }, () => [0, 0, 0])); const b = c[Math.min(9, Math.floor(p * 10))]; b[0]++; b[1] += p; b[2] += y; }
(async () => {
  const rl = readline.createInterface({ input: fs.createReadStream(process.argv[3]) });
  for await (const line of rl) {
    const c = JSON.parse(line);
    if (c.L.matches.length < 30) continue; // skip the first rounds of each season
    const M = MC.prepareMatch(c.L, c.m.h, c.m.a, {}, {}, { halfLife: HL });
    const sim = MC.simulate(M, N, 7 + c.i);
    const s = MC.summarize(sim, M, ['H', 'A']);
    const res = c.m.hg > c.m.ag ? 0 : c.m.hg === c.m.ag ? 1 : 2;
    const P = [s.pH, s.pD, s.pA];
    const pO = s.groups.find((g) => g.id === 'gols').rows.find((r) => r.key === 'G>2.5').p;
    const pB = s.groups.find((g) => g.id === 'btts').rows[0].p;
    const yO = c.m.hg + c.m.ag > 2.5 ? 1 : 0, yB = c.m.hg > 0 && c.m.ag > 0 ? 1 : 0;
    if (dump) dump.write(JSON.stringify([c.lid, c.season, P, pO, pB, res, yO, yB, c.odds || null]) + '\n');
    for (const key of ['TODAS', LN[c.lid] || c.lid]) {
      const a = S(key);
      a.nbt++; a.bt -= yB ? L2(pB) : L2(1 - pB);
      cal(a, '1', P[0], res === 0 ? 1 : 0); cal(a, 'X', P[1], res === 1 ? 1 : 0); cal(a, '2', P[2], res === 2 ? 1 : 0);
      cal(a, '+2.5', pO, yO); cal(a, 'BTTS', pB, yB);
      const o = c.odds;
      if (o && o.pin && o.pin.every((x) => x)) {
        const mk = devig(o.pin);
        a.n++; a.ll -= L2(P[res]); a.llm -= L2(mk[res]);
        const best = o.max && o.max.every((x) => x) ? o.max : null;
        for (let k = 0; k < 3; k++) {
          bet(a, '1X2 Pinnacle', P[k], o.pin[k], res === k);
          if (o.avg && o.avg[k]) bet(a, '1X2 média', P[k], o.avg[k], res === k);
          if (best) bet(a, '1X2 melhor', P[k], best[k], res === k);
        }
      }
      if (o && o.ou_pin && o.ou_pin.every((x) => x)) {
        const mk = devig(o.ou_pin);
        a.nou++; a.ou -= yO ? L2(pO) : L2(1 - pO); a.oum -= yO ? L2(mk[0]) : L2(mk[1]);
        bet(a, 'Gols 2,5 Pinnacle', pO, o.ou_pin[0], yO === 1); bet(a, 'Gols 2,5 Pinnacle', 1 - pO, o.ou_pin[1], yO === 0);
        if (o.ou_avg && o.ou_avg.every((x) => x)) { bet(a, 'Gols 2,5 média', pO, o.ou_avg[0], yO === 1); bet(a, 'Gols 2,5 média', 1 - pO, o.ou_avg[1], yO === 0); }
      }
    }
  }
  const out = {};
  for (const [k, a] of Object.entries(st)) {
    out[k] = {
      jogos_com_odds: a.n, logloss_1x2_modelo: +(a.ll / a.n).toFixed(4), logloss_1x2_pinnacle: +(a.llm / a.n).toFixed(4),
      jogos_ou: a.nou, logloss_ou_modelo: a.nou ? +(a.ou / a.nou).toFixed(4) : null, logloss_ou_pinnacle: a.nou ? +(a.oum / a.nou).toFixed(4) : null,
      logloss_btts_modelo: +(a.bt / a.nbt).toFixed(4),
      apostas: Object.fromEntries(Object.entries(a.bets).map(([kk, b]) => [kk, { n: b.n, roi: +(b.pl / b.n * 100).toFixed(1) }])),
      calibracao: Object.fromEntries(Object.entries(a.cal).map(([kk, c]) => [kk, c.filter((b) => b[0] >= 30).map((b) => `${Math.round(b[1] / b[0] * 100)}→${Math.round(b[2] / b[0] * 100)}% (${b[0]})`).join('  ')]))
    };
  }
  console.log(JSON.stringify(out, null, 1));
})();
