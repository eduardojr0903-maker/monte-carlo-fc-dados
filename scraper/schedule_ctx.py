"""Rest and congestion for a team at a kickoff time, from the full calendar (data/calendar) plus league matches.

context(team, ts) -> [rest days since last match, plays continental competition (0/1),
                      last competition name, days until next match, next competition name, matches in last 14 days]
The backtest (rest.py / engine restB, contB) found: short rest (< 3.6 days) and continental football both go with
doing slightly better than the ratings say (these are the teams still alive in cups); Pinnacle prices most of it.
"""
import bisect
import glob
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, "data", "calendar")
CONT = {"42": "Champions League", "73": "Europa League", "10216": "Conference League", "45": "Libertadores", "299": "Sul-Americana"}
SHORT = {"Copa do Brasil": "Copa do Brasil", "FA Cup": "FA Cup", "EFL Cup": "Copa da Liga", "Copa del Rey": "Copa do Rei",
         "Coppa Italia": "Copa da Itália", "DFB Pokal": "Copa da Alemanha", "Coupe de France": "Copa da França"}
DAY = 86400.0


def _ts(utc):
    return int(datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp())


class Schedule:
    def __init__(self):
        self.g = {}     # team -> sorted [(ts, competition name)]
        self.cont = {}  # team -> sorted [ts] continental
        for f in glob.glob(os.path.join(CAL, "*.json")):
            cid = os.path.basename(f)[:-5]
            try:
                c = json.load(open(f))
            except Exception:  # noqa: BLE001
                continue
            name = CONT.get(cid) or c.get("name", "")[4:]
            name = SHORT.get(name, name)
            for rows in c.get("seasons", {}).values():
                for mid, utc, h, a, fin in rows:
                    if not utc:
                        continue
                    t = _ts(utc)
                    for tm in (h, a):
                        self.g.setdefault(tm, []).append((t, name))
                        if cid in CONT:
                            self.cont.setdefault(tm, []).append(t)
        self._sorted = False

    def add(self, team, ts, name):
        self.g.setdefault(str(team), []).append((int(ts), name))
        self._sorted = False

    def _sort(self):
        if not self._sorted:
            for k in self.g:
                self.g[k] = sorted(set(self.g[k]))
            for k in self.cont:
                self.cont[k] = sorted(set(self.cont[k]))
            self._sorted = True

    def context(self, team, ts):
        self._sort()
        g = self.g.get(str(team), [])
        times = [x[0] for x in g]
        i = bisect.bisect_left(times, ts - 3600)
        j = bisect.bisect_right(times, ts + 3600)
        prev = g[i - 1] if i > 0 else None
        nxt = g[j] if j < len(g) else None
        rest = round(min(14.0, (ts - prev[0]) / DAY), 1) if prev else None
        until = round(min(14.0, (nxt[0] - ts) / DAY), 1) if nxt else None
        n14 = sum(1 for t in times[max(0, i - 8):i] if ts - t <= 14 * DAY)
        cont = 1 if any(abs(t - ts) < 60 * DAY for t in self.cont.get(str(team), [])) else 0
        return [rest, cont, prev[1] if prev else None, until, nxt[1] if nxt else None, n14]
