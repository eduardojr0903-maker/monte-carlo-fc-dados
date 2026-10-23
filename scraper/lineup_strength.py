"""Team strength from the players on the pitch.

For every player, from the detailed match records:
- attack: non-penalty xG + xA per 90 minutes (penalty xG removed using the shot map);
- defence: the team's xG conceded per 90 while he was on the pitch, against while he was off
  (on/off intervals reconstructed from starts and minutes played).
Both are shrunk toward the average for the position. A lineup's attack and defence factors are its
players' combined numbers relative to the team's usual lineup (its last matches).
"""
import math

END = 94.0


class Tracker:
    def __init__(self, decay_days=200.0):
        self.lam = math.log(2) / (decay_days * 86400.0)
        self.p = {}      # pid -> [ts_last, min, off_value, on_xga, on_min, off_xga, off_min, pos]
        self.team_xi = {}  # team -> list of (ts, [pids])

    def _decay(self, s, ts):
        f = math.exp(-self.lam * (ts - s[0])) if s[0] else 1.0
        for i in range(1, 7):
            s[i] *= f
        s[0] = ts

    def add(self, r, ts):
        """r: rich record with pl, sh, gl, rc, h, a."""
        shots = r.get("sh") or []
        pen = {}
        for s in shots:
            if s[5] == "P" and s[6] is not None:
                pen[str(s[6])] = pen.get(str(s[6]), 0.0) + s[2]
        xga_by_min = {0: [], 1: []}  # side conceding -> list of (minute, xG) excluding penalties
        for s in shots:
            if s[5] != "P":
                xga_by_min[1 - s[1]].append((s[0], s[2]))
        for side, tid in ((0, r["h"]), (1, r["a"])):
            xi = []
            for pl in r.get("pl") or []:
                if pl[1] != side:
                    continue
                pid, st, mins, xg, xa, pos = pl[0], pl[2], pl[3], pl[4], pl[5], pl[9]
                if st:
                    xi.append(pid)
                on, off = (0.0, min(END, float(mins))) if st else (max(0.0, END - mins), END)
                s = self.p.setdefault(pid, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, pos])
                self._decay(s, ts)
                s[7] = pos
                s[1] += mins
                s[2] += max(0.0, xg - pen.get(pid, 0.0)) + xa
                on_x = sum(x for m, x in xga_by_min[side] if on <= m < off)
                tot_x = sum(x for m, x in xga_by_min[side])
                s[3] += on_x; s[4] += off - on
                s[5] += tot_x - on_x; s[6] += END - (off - on)
            if len(xi) >= 9:
                self.team_xi.setdefault(tid, []).append((ts, xi))

    def rates(self, pid, pos_avg):
        s = self.p.get(pid)
        pa = pos_avg.get((s[7] if s else "M"), (0.15, 0.0))
        if not s:
            return pa[0], 0.0
        k_off, k_def = 600.0, 1500.0  # minutes of prior
        off = (s[2] + pa[0] * k_off / 90) / ((s[1] + k_off) / 90)
        on_r = s[3] / (s[4] / 90) if s[4] > 0 else None
        off_r = s[5] / (s[6] / 90) if s[6] > 0 else None
        if on_r is None or off_r is None:
            return off, 0.0
        w = s[4] / (s[4] + k_def)
        return off, w * (on_r - off_r)  # positive = team concedes more with him on

    def pos_averages(self):
        acc = {}
        for s in self.p.values():
            a = acc.setdefault(s[7], [0.0, 0.0])
            a[0] += s[2]; a[1] += s[1]
        return {k: ((v[0] / (v[1] / 90)) if v[1] > 0 else 0.15, 0.0) for k, v in acc.items()}

    def factors(self, tid, xi, pos_avg, n_usual=8):
        """(attack factor, defence factor) of this lineup vs the team's usual one."""
        hist = self.team_xi.get(tid) or []
        if len(hist) < 3 or len(xi) < 9:
            return 1.0, 1.0

        def strength(ids):
            o = d = 0.0
            for pid in ids:
                a, b = self.rates(pid, pos_avg)
                o += a; d += b
            return o, d
        lo, ld = strength(xi)
        us = [strength(x) for _, x in hist[-n_usual:]]
        uo = sum(u[0] for u in us) / len(us)
        ud = sum(u[1] for u in us) / len(us)
        att = lo / uo if uo > 0 else 1.0
        # defence: difference in on-pitch xGA per 90, as a multiplier on xG conceded (~1.3 per game average)
        dfn = math.exp((ld - ud) / 1.3)
        return max(0.7, min(1.4, att)), max(0.85, min(1.2, dfn))
