"""Opponent-adjusted team ratings (model v2).

For each league, a multiplicative model is fitted on every match since 2022, weighting recent
matches more (time decay) and shrinking teams with little data toward a prior:

    E[y for team i vs team j] = base * home^(i at home) * att_i * def_j

for three quantities y: shots, shots on target and non-penalty xG ("quality" signals). Ratings are
relative to the league average (1 = average). When the detailed shot map is available, each match's
numbers are adjusted for game state (shots taken when a team is 2+ goals ahead or behind count less)
and for red cards (only 11 v 11 time is used, scaled to 90 minutes). Penalties are left out of xG.

Output per team: [attSh, defSh, attAcc, defAcc, attQ, defQ] in the engine's format, plus home factor.
"""
import math

TARGETS = ("sh", "sot", "npxg")


def match_values(r):
    """Per-side adjusted values for one match. r: rich record (with sh/gl/rc) or plain team totals.
    Returns ([sh, sot, npxg] home, [..] away, weight)."""
    if r.get("sh") is not None and r["sh"]:
        goals = sorted(r.get("gl") or [], key=lambda g: g[0])
        reds = sorted(r.get("rc") or [], key=lambda x: x[0])
        first_red = reds[0][0] if reds else None
        end = 95.0
        v = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        for s in r["sh"]:
            m, side, xg, ont, goal, sit = s[0], s[1], s[2], s[3], s[4], s[5]
            if first_red is not None and m >= first_red:
                continue
            gd = sum(1 for g in goals if g[0] < m and g[1] == side) - sum(1 for g in goals if g[0] < m and g[1] != side)
            w = 1.0 if abs(gd) <= 1 else 0.6
            v[side][0] += w
            v[side][1] += w * ont
            if sit != "P":
                v[side][2] += w * xg
        mins = (first_red if first_red is not None else end)
        scale = end / max(mins, 30.0)
        return [x * scale for x in v[0]], [x * scale for x in v[1]], min(1.0, mins / 60.0)
    t = r.get("t")
    if not t:
        return None
    # totals only: [[sh, sot, xg] home, [..] away]; penalties unknown, xG used as is
    return [t[0][0], t[0][1], t[0][2]], [t[1][0], t[1][1], t[1][2]], 1.0


def fit(matches, asof, half_life_days=120.0, k_prior=6.0, prior=None, iters=25):
    """matches: list of dicts {ts, h, a, vh, va, w} with ts < asof.
    prior: {team: [att multipliers per target..., def multipliers...]} used as shrinkage target (default 1).
    Returns {team: {"att": [..3], "def": [..3]}, "home": [..3]}."""
    lam = math.log(2) / (half_life_days * 86400.0)
    rows = []
    teams = set()
    for m in matches:
        if m["ts"] >= asof:
            continue
        w = m["w"] * math.exp(-lam * (asof - m["ts"]))
        if w < 1e-3:
            continue
        rows.append((m["h"], m["a"], m["vh"], m["va"], w))
        teams.add(m["h"]); teams.add(m["a"])
    out = {t: {"att": [1.0] * 3, "def": [1.0] * 3} for t in teams}
    home = [1.0] * 3
    if not rows:
        return out, home
    for k in range(3):
        tot = sum(w * (vh[k] + va[k]) for _, _, vh, va, w in rows)
        wsum = sum(2 * w for *_, w in rows)
        base = tot / wsum if wsum else 1.0
        if base <= 0:
            continue
        att = {t: 1.0 for t in teams}
        dfn = {t: 1.0 for t in teams}
        hf = 1.0
        pa = {t: (prior[t][k] if prior and t in prior else 1.0) for t in teams}
        pd = {t: (prior[t][3 + k] if prior and t in prior else 1.0) for t in teams}
        for _ in range(iters):
            num = {t: k_prior * pa[t] for t in teams}
            den = {t: float(k_prior) for t in teams}
            for h, a, vh, va, w in rows:
                num[h] += w * vh[k] / base; den[h] += w * hf * dfn[a]
                num[a] += w * va[k] / base; den[a] += w * dfn[h] / hf
            att = {t: num[t] / den[t] for t in teams}
            num = {t: k_prior * pd[t] for t in teams}
            den = {t: float(k_prior) for t in teams}
            for h, a, vh, va, w in rows:
                num[a] += w * vh[k] / base; den[a] += w * hf * att[h]
                num[h] += w * va[k] / base; den[h] += w * att[a] / hf
            dfn = {t: num[t] / den[t] for t in teams}
            # home factor (square root split between attack at home and defence away)
            hn = sum(w * vh[k] for h, a, vh, va, w in rows) / base
            hd = sum(w * att[h] * dfn[a] for h, a, vh, va, w in rows)
            an = sum(w * va[k] for h, a, vh, va, w in rows) / base
            ad = sum(w * att[a] * dfn[h] for h, a, vh, va, w in rows)
            hf = math.sqrt((hn / hd) / (an / ad)) if hd and ad and an else 1.0
            # keep the average team at 1
            ga = math.exp(sum(math.log(x) for x in att.values()) / len(att))
            gd = math.exp(sum(math.log(x) for x in dfn.values()) / len(dfn))
            att = {t: x / ga for t, x in att.items()}
            dfn = {t: x / gd for t, x in dfn.items()}
        for t in teams:
            out[t]["att"][k] = att[t]
            out[t]["def"][k] = dfn[t]
        home[k] = hf
    return out, home


def engine_rating(r):
    """Convert fitted multipliers into the engine's six numbers."""
    a, d = r["att"], r["def"]
    return [round(a[0], 4), round(d[0], 4), round(a[1] / a[0], 4), round(d[1] / d[0], 4), round(a[2] / a[0], 4), round(d[2] / d[0], 4)]
