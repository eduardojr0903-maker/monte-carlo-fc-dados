"""Monte Carlo FC data collector (FotMob + Sportsgambler).

- FotMob: every finished league match of the current season, with team and
  player shots, shots on target, xG, goals, minutes and starters (Opta data),
  for all 7 leagues, Brazilian ones included. For upcoming matches it also
  gives the lineup (confirmed, predicted, or the last starting XI) and the
  players out injured or suspended.
- Sportsgambler: news-based predicted lineups (Brasileirão Série A and the
  five big European leagues), keyed by the same FotMob match id.

Each finished match is downloaded once and cached in data/cache/fotmob/.
Output: one JSON document per league in data/db/s<id>.json for the app.

Run: python scraper/collect.py               (all leagues)
     python scraper/collect.py 268 47        (only these FotMob league ids)
"""
import difflib
import json
import os
import random
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history  # noqa: E402
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "cache", "fotmob")
OUT = os.path.join(ROOT, "data", "db")
RECENT_N = 8
UPCOMING_DAYS = 8
DELAY = float(os.environ.get("DELAY", "0.7"))
MAX_NEW = int(os.environ.get("MAX_NEW_EVENTS", "2500"))
HIST_BUDGET = int(os.environ.get("HIST_BUDGET", "2500"))  # past-season matches downloaded per run
HIST_CACHE = os.path.join(ROOT, "data", "cache", "history")
POS_CACHE = os.path.join(ROOT, "data", "cache", "positions")
POS_BUDGET = int(os.environ.get("POS_BUDGET", "1200"))  # player position lookups per run
POS_MAX_AGE = 30 * 86400
LEFT = {"LB", "LWB", "LCB", "LM", "LW", "LCM", "LDM", "LAM", "LF", "LS"}
RIGHT = {"RB", "RWB", "RCB", "RM", "RW", "RCM", "RDM", "RAM", "RF", "RS"}
pos_budget = [POS_BUDGET]


def player_position(pid):
    """Where the player actually plays (FotMob/Opta): main position label, lateral x (0 = left, 1 = right)
    and depth y (0 = own goal, 1 = attack), weighted by how often he played each position."""
    if not pid or str(pid).startswith("n:"):
        return None
    path = os.path.join(POS_CACHE, f"{pid}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < POS_MAX_AGE:
        return json.load(open(path)) or None
    if pos_budget[0] <= 0:
        return (json.load(open(path)) or None) if os.path.exists(path) else None
    pos_budget[0] -= 1
    out = None
    try:
        d = fm("playerData", id=pid)
        time.sleep(DELAY * 0.6)
        pd = (d or {}).get("positionDescription") or {}
        items = pd.get("positions") or []
        wx = wy = w = 0.0
        main = None
        for it in items:
            lab = ((it.get("strPosShort") or {}).get("label") or "").upper()
            occ = float(it.get("occurences") or 0) or (5.0 if it.get("isMainPosition") else 1.0)
            if it.get("isMainPosition"):
                main = lab
            pp = it.get("pitchPositionData") or {}
            if "right" in pp and "top" in pp:
                x, y = float(pp["right"]), 1 - float(pp["top"])
            else:
                x = 0.17 if lab in LEFT else 0.83 if lab in RIGHT else 0.5
                y = 0.0 if lab == "GK" else 0.3 if lab.endswith("B") or lab.endswith("CB") else 0.55 if "M" in lab else 0.8
            wx += occ * x; wy += occ * y; w += occ
        if w > 0:
            out = {"p": main or "", "x": round(wx / w, 3), "y": round(wy / w, 3)}
    except Exception as e:  # noqa: BLE001
        print("  posição", pid, e, flush=True)
    os.makedirs(POS_CACHE, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out or {}, fh)
    return out

# FotMob league id -> (app doc id, app name, Sportsgambler has predicted lineups)
LEAGUES = {
    268: ("s325", "Brasileirão Série A", True),
    8814: ("s390", "Brasileirão Série B", False),
    47: ("s17", "Premier League", True),
    87: ("s8", "La Liga", True),
    55: ("s23", "Serie A (Itália)", True),
    54: ("s35", "Bundesliga", True),
    53: ("s34", "Ligue 1", True),
    48: ("s18", "Championship", True),
}
POS = {0: "G", 1: "D", 2: "M", 3: "F"}

try:
    from curl_cffi import requests as http
    SESSION = http.Session(impersonate="chrome")
except Exception:  # pragma: no cover
    import requests as http
    SESSION = http.Session()


def get(url, as_json=True, tries=5, **kw):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=45, **kw)
            if r.status_code == 200:
                return r.json() if as_json else r.text
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                return None
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(4 + 8 * i + random.random() * 3)
    raise RuntimeError(f"{url}: {last}")


def fm(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return get(f"https://www.fotmob.com/api/data/{path}?{q}")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).split()


def ts_of(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


# ------------------------------------------------------------------ parsing
def team_stats(content):
    """{key: [home, away]} from the match stats block."""
    out = {}
    try:
        groups = content["stats"]["Periods"]["All"]["stats"]
    except (KeyError, TypeError):
        return out
    for g in groups:
        for s in g.get("stats") or []:
            k = s.get("key")
            raw = s.get("rawStats") or []
            if k and len(raw) == 2 and k not in out:
                try:
                    out[k] = [float(raw[0].get("value") or 0), float(raw[1].get("value") or 0)]
                except (TypeError, ValueError, AttributeError):
                    pass
    return out


def player_stats(ps):
    flat = {}
    for g in ps.get("stats") or []:
        for v in (g.get("stats") or {}).values():
            k = v.get("key")
            st = v.get("stat") or {}
            if k and k not in flat and "value" in st:
                flat[k] = st["value"]
    return flat


def lineup_people(side):
    people = {}
    for grp, starter in (("starters", 1), ("subs", 0)):
        for p in side.get(grp) or []:
            people[str(p["id"])] = {
                "name": p.get("name"), "starter": starter,
                "pos": POS.get(p.get("usualPlayingPositionId"), "G" if p.get("positionId") == 11 else None),
                "shirt": str(p.get("shirtNumber") or ""),
            }
    return people


def match_record(m, d):
    c = d.get("content") or {}
    hs, as_ = (m["status"].get("scoreStr") or "0 - 0").split("-")
    rec = {
        "ts": ts_of(m["status"]["utcTime"]),
        "h": str(m["home"]["id"]), "hn": m["home"].get("shortName") or m["home"]["name"],
        "a": str(m["away"]["id"]), "an": m["away"].get("shortName") or m["away"]["name"],
        "hg": int(hs.strip() or 0), "ag": int(as_.strip() or 0),
        "t": None, "p": [],
    }
    st = team_stats(c)
    if "total_shots" in st:
        rec["t"] = [[st["total_shots"][i], st.get("ShotsOnTarget", [0, 0])[i], round(st.get("expected_goals", [-1, -1])[i], 2)] for i in (0, 1)]
    lu = c.get("lineup") or {}
    people = {}
    for side_key, side in (("homeTeam", 0), ("awayTeam", 1)):
        for pid, info in lineup_people(lu.get(side_key) or {}).items():
            info["side"] = side
            people[pid] = info
    home_id = int(m["home"]["id"])
    for pid, ps in (c.get("playerStats") or {}).items():
        f = player_stats(ps)
        mins = int(f.get("minutes_played") or 0)
        if mins <= 0:
            continue
        info = people.get(str(pid), {})
        side = info.get("side", 0 if ps.get("teamId") == home_id else 1)
        pos = info.get("pos") or ("G" if ps.get("isGoalkeeper") else "M")
        rec["p"].append([str(pid), ps.get("name") or info.get("name"), side, pos, info.get("starter", 1 if mins >= 80 else 0),
                         min(mins, 120), int(f.get("total_shots") or 0), int(f.get("ShotsOnTarget") or 0),
                         round(float(f.get("expected_goals") or 0), 3), int(f.get("goals") or 0), info.get("shirt", "")])
    return rec


# ------------------------------------------------------------------ sportsgambler
def sportsgambler(mid):
    try:
        t = get("https://www.sportsgambler.com/lineups/lineups-load2.php", as_json=False, tries=2,
                params={"id": mid}, headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.sportsgambler.com/lineups/"})
    except Exception:  # noqa: BLE001
        return None
    if not t:
        return None
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", txt))
    kind = "confirmed" if re.search(r"Confirmed Lineup", txt) else "predicted"
    forms = re.findall(r"(?:Predicted|Confirmed) Lineup\|[ |]*(\d(?:-\d){2,4})", txt)
    pairs = re.findall(r"\|(\d{1,3})\| \|([^|]{2,40}?)\|", txt)
    if len(pairs) < 22:
        return None
    forms = (forms + [None, None])[:2]
    return kind, [[n, nm.strip()] for n, nm in pairs[:11]], [[n, nm.strip()] for n, nm in pairs[11:22]], forms


def resolve(entries, squad):
    """Map [shirt, name] entries to FotMob player ids using a squad {pid: {name, shirt}}."""
    out = []
    used = set()
    for shirt, name in entries:
        toks = norm(name)
        best = None
        by_shirt = [pid for pid, s in squad.items() if s.get("shirt") == shirt and pid not in used]
        for pid in by_shirt:
            st = norm(squad[pid]["name"])
            if set(toks) & set(st):
                best = pid
                break
        if not best:
            cands = {pid: " ".join(norm(s["name"])) for pid, s in squad.items() if pid not in used}
            full = " ".join(toks)
            exact = [pid for pid, n in cands.items() if n == full]
            last = [pid for pid, n in cands.items() if toks and n.split() and (n.split()[-1] == toks[-1] or toks[-1] in n.split())]
            if exact:
                best = exact[0]
            elif len(last) == 1:
                best = last[0]
            else:
                mm = difflib.get_close_matches(full, list(cands.values()), n=1, cutoff=0.7)
                if mm:
                    best = [pid for pid, n in cands.items() if n == mm[0]][0]
        if not best and len(by_shirt) == 1:
            best = by_shirt[0]
        if best:
            used.add(best)
            out.append(best)
        else:
            out.append("n:" + name)  # new player the stats have not seen yet
    return out


# ------------------------------------------------------------------ league
def collect_league(lid, budget):
    doc_id, name, has_sg = LEAGUES[lid]
    lg = fm("leagues", id=lid)
    season = (lg.get("details") or {}).get("selectedSeason") or ""
    all_m = (lg.get("fixtures") or {}).get("allMatches") or []
    path = os.path.join(CACHE, f"{lid}_{season.replace('/', '-')}.json")
    cache = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    new = 0
    now = datetime.now(timezone.utc)
    for m in all_m:
        stt = m.get("status") or {}
        if not stt.get("finished") or stt.get("cancelled") or str(m["id"]) in cache:
            continue
        if new >= budget:
            break
        d = fm("matchDetails", matchId=m["id"])
        time.sleep(DELAY + random.random() * 0.4)
        if not d:
            continue
        cache[str(m["id"])] = match_record(m, d)
        new += 1
        if new % 40 == 0:
            _save(path, cache)
            print(f"  {name}: {new} jogos novos", flush=True)
    _save(path, cache)

    # upcoming fixtures: lineups and absences
    upcoming = []
    for m in all_m:
        stt = m.get("status") or {}
        if stt.get("finished") or stt.get("cancelled") or stt.get("started"):
            continue
        t = datetime.fromisoformat(stt["utcTime"].replace("Z", "+00:00"))
        if t - now > timedelta(days=UPCOMING_DAYS):
            continue
        d = fm("matchDetails", matchId=m["id"])
        time.sleep(DELAY)
        lu = ((d or {}).get("content") or {}).get("lineup") or {}
        fx = {"id": str(m["id"]), "ts": ts_of(stt["utcTime"]), "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
              "hn": m["home"].get("shortName") or m["home"]["name"], "an": m["away"].get("shortName") or m["away"]["name"],
              "src": None, "xi": [[], []], "out": [[], []], "squad": [{}, {}], "form": [None, None], "coords": [None, None]}
        lt = lu.get("lineupType")
        has_xi = bool((lu.get("homeTeam") or {}).get("starters"))
        fx["src"] = {"standard": "confirmed", "predicted": "fotmob_predicted", "lastStarting11": "last_xi"}.get(lt) if has_xi else None
        for i, k in enumerate(("homeTeam", "awayTeam")):
            side = lu.get(k) or {}
            fx["xi"][i] = [str(p["id"]) for p in side.get("starters") or []]
            fx["form"][i] = side.get("formation")
            co = [[round((p.get("verticalLayout") or {}).get("x", -1), 3), round((p.get("verticalLayout") or {}).get("y", -1), 3)] for p in side.get("starters") or []]
            fx["coords"][i] = co if co and all(c[0] >= 0 and c[1] >= 0 for c in co) else None
            fx["squad"][i] = lineup_people(side)
            for p in side.get("unavailable") or []:
                u = p.get("unavailability") or {}
                fx["out"][i].append([str(p["id"]), p.get("name"), u.get("type", ""), u.get("expectedReturn", "")])
        if has_sg and fx["src"] != "confirmed":
            sg = sportsgambler(m["id"])
            time.sleep(DELAY)
            if sg:
                fx["sg"] = sg
        upcoming.append(fx)
    return lg, season, cache, upcoming, new


LOGO_DIR = os.path.join(ROOT, "data", "cache", "logos")


def logo(team_id):
    """Club crest as a small data URI (the app cannot load images from other sites)."""
    path = os.path.join(LOGO_DIR, f"{team_id}.txt")
    if os.path.exists(path):
        return open(path).read().strip() or None
    uri = ""
    try:
        import base64
        import io
        from PIL import Image
        r = SESSION.get(f"https://images.fotmob.com/image_resources/logo/teamlogo/{team_id}.png", timeout=30)
        if r.status_code == 200:
            im = Image.open(io.BytesIO(r.content)).convert("RGBA")
            im.thumbnail((64, 64), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=82, method=6)
            uri = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
        time.sleep(0.3)
    except Exception as e:  # noqa: BLE001
        print("  logo", team_id, e, flush=True)
    os.makedirs(LOGO_DIR, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(uri)
    return uri or None


def _save(path, cache):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ build
def build(lid, season, cache, upcoming, hist=None):
    doc_id, name, _ = LEAGUES[lid]
    recs = sorted(cache.values(), key=lambda r: r["ts"])
    teams, tidx = [], {}

    def team(i, n):
        if i not in tidx:
            tidx[i] = len(teams)
            teams.append([i, n])
        return tidx[i]

    matches, team_ts = [], {}
    for r in recs:
        hi, ai = team(r["h"], r["hn"]), team(r["a"], r["an"])
        t = r.get("t")
        if not t and r["p"]:
            t = [[0, 0, 0.0], [0, 0, 0.0]]
            for p in r["p"]:
                t[p[2]][0] += p[6]; t[p[2]][1] += p[7]; t[p[2]][2] += p[8]
        if t:
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], int(t[0][0]), int(t[0][1]), round(t[0][2], 2), int(t[1][0]), int(t[1][1]), round(t[1][2], 2)])
        else:
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], -1, -1, -1, -1, -1, -1])
        team_ts.setdefault(hi, []).append(r["ts"])
        team_ts.setdefault(ai, []).append(r["ts"])
    for fx in upcoming:
        team(fx["h"], fx["hn"]); team(fx["a"], fx["an"])
    cutoff = {t: sorted(v)[-RECENT_N] if len(v) >= RECENT_N else 0 for t, v in team_ts.items()}

    players, posc, shirt, last_ts = {}, {}, {}, {}
    mins_split = {}  # key -> [start minutes, starts, sub minutes, sub apps]
    for r in recs:
        hi, ai = tidx[r["h"]], tidx[r["a"]]
        for p in r["p"]:
            ti = hi if p[2] == 0 else ai
            key = (p[0], ti)
            row = players.setdefault(key, [p[0], p[1], ti, "M"] + [0] * 14 + [""])
            posc.setdefault(key, {}).setdefault(p[3], 0)
            posc[key][p[3]] += 1 + 2 * p[4]
            if len(p) > 10 and p[10]:
                row[18] = p[10]
            last_ts[key] = r["ts"]
            ms = mins_split.setdefault(key, [0, 0, 0, 0])
            if p[4]:
                ms[0] += p[5]; ms[1] += 1
            else:
                ms[2] += p[5]; ms[3] += 1
            for k, v in enumerate([1, p[4], p[5], p[6], p[7], p[8], p[9]]):
                row[4 + k] += v
                if r["ts"] >= cutoff.get(ti, 0):
                    row[11 + k] += v

    # squad members seen only in upcoming lineups (new signings, youngsters)
    nexts = []
    for fx in upcoming:
        sides = []
        for i, tid in enumerate((fx["h"], fx["a"])):
            ti = tidx[tid]
            squad = {}
            for pid, info in fx["squad"][i].items():
                squad[pid] = info
                key = (pid, ti)
                if key not in players:
                    players[key] = [pid, info["name"], ti, info.get("pos") or "M"] + [0] * 14 + [info.get("shirt", "")]
                elif info.get("shirt"):
                    players[key][18] = info["shirt"]
            # also every player of this team in the stats
            for (pid, t2), row in players.items():
                if t2 == ti and pid not in squad:
                    squad[pid] = {"name": row[1], "shirt": row[18]}
            pred, form, coords = None, fx["form"][i], fx["coords"][i]
            if fx.get("sg"):
                kind, h_xi, a_xi, forms = fx["sg"]
                ids = resolve(h_xi if i == 0 else a_xi, squad)
                sg_form = forms[i] or form
                lines = [1] + [int(x) for x in (sg_form or "4-3-3").split("-")]
                slot_pos = []
                for li, cnt in enumerate(lines):
                    slot_pos += [("G" if li == 0 else "D" if li == 1 else "F" if li == len(lines) - 1 else "M")] * cnt
                for k, x in enumerate(ids):
                    if x.startswith("n:") and (x, ti) not in players:
                        players[(x, ti)] = [x, x[2:], ti, slot_pos[k] if k < len(slot_pos) else "M"] + [0] * 14 + [""]
                pred, form, coords = ids, sg_form, None
            sides.append({"xi": fx["xi"][i], "pred": pred, "out": fx["out"][i], "form": form, "coords": coords})
        src = fx["src"]
        if src != "confirmed" and fx.get("sg"):
            src = "sg_" + fx["sg"][0]
        nexts.append([fx["id"], fx["ts"], tidx[fx["h"]], tidx[fx["a"]], src,
                      sides[0]["pred"] or sides[0]["xi"], sides[1]["pred"] or sides[1]["xi"],
                      sides[0]["out"], sides[1]["out"], sides[0]["xi"], sides[1]["xi"],
                      [sides[0]["form"], sides[1]["form"]], [sides[0]["coords"], sides[1]["coords"]]])

    wanted = []
    for n in nexts:
        for ids in (n[5], n[6], n[9], n[10]):
            wanted += [str(x) for x in ids or []]
    seen = set()
    for pid in wanted + [row[0] for row in sorted(players.values(), key=lambda r: -r[6])]:
        if pid in seen:
            continue
        seen.add(pid)
        player_position(pid)
    for key, row in players.items():
        while len(row) < 21:
            row.append(0)
        pp = None
        pth = os.path.join(POS_CACHE, f"{row[0]}.json")
        if os.path.exists(pth):
            try:
                pp = json.load(open(pth)) or None
            except ValueError:
                pp = None
        row[21:24] = [pp["p"], pp["x"], pp["y"]] if pp else ["", -1, -1]
        if key in posc:
            row[3] = max(posc[key].items(), key=lambda kv: kv[1])[0]
        row[9] = round(row[9], 2)
        row[16] = round(row[16], 2)
        ms = mins_split.get(key, [0, 0, 0, 0])
        # average minutes in this competition: as a starter, and when coming off the bench
        row[19:21] = [round(ms[0] / ms[1]) if ms[1] else 0, round(ms[2] / ms[3]) if ms[3] else 0]
    rows = sorted(players.values(), key=lambda x: (x[2], -x[6]))
    has_xg = any(m[7] >= 0 for m in matches)
    logos = {}
    for tid, _n in teams:
        u = logo(tid)
        if u:
            logos[tid] = u
    return doc_id, {
        "v": 3, "comp": int(doc_id[1:]), "name": name, "season": season,
        "source": "fotmob.com" + (" + sportsgambler.com" if any(f.get("sg") for f in upcoming) else ""),
        "hasXg": has_xg, "recentN": RECENT_N,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams, "matches": matches, "players": rows, "next": nexts, "logos": logos, "hist": hist,
    }


def write_doc(doc_id, doc):
    """Rows are packed into one JSON string field ("pack"): the app's database caps a document at 256 KiB."""
    os.makedirs(OUT, exist_ok=True)
    keys = ("teams", "matches", "players", "next", "logos", "hist")
    out = {k: v for k, v in doc.items() if k not in keys}
    out["pack"] = json.dumps({k: doc[k] for k in keys}, ensure_ascii=False, separators=(",", ":"))
    path = os.path.join(OUT, f"{doc_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


def main(argv):
    wanted = [int(a) for a in argv] or list(LEAGUES)
    budget = MAX_NEW
    hist_budget = [HIST_BUDGET]
    report = []
    for lid in wanted:
        doc_id, name, _ = LEAGUES[lid]
        print(f"== {name}", flush=True)
        try:
            lg, season, cache, upcoming, new = collect_league(lid, budget)
            budget -= new
            hist, hnew = None, 0
            try:
                past, hnew = history.collect(lid, lg, fm, team_stats, HIST_CACHE, hist_budget[0], DELAY)
                hist_budget[0] -= hnew
                cur = []
                for r in cache.values():
                    t = r.get("t")
                    cur.append({"h": r["h"], "hn": r["hn"], "a": r["a"], "an": r["an"], "hg": r["hg"], "ag": r["ag"],
                                "s": [[t[0][0], t[1][0]], [t[0][1], t[1][1]], [t[0][2], t[1][2]]] if t else None})
                tids = sorted({r["h"] for r in cache.values()} | {r["a"] for r in cache.values()} | {f["h"] for f in upcoming} | {f["a"] for f in upcoming})
                hist = history.summarize(past, cur, tids) if past else None
            except Exception as e:  # noqa: BLE001
                report.append(f"{name}: histórico falhou ({e})")
            doc_id, doc = build(lid, season, cache, upcoming, hist)
            size = write_doc(doc_id, doc)
            srcs = {}
            for n in doc["next"]:
                srcs[n[4]] = srcs.get(n[4], 0) + 1
            hinfo = f"histórico {hist['nMatches']} jogos (+{hnew}), mando {hist['hfa']}, " if hist else ""
            report.append(f"{name}: {hinfo}{len(doc['matches'])} jogos ({new} novos), {len(doc['players'])} jogadores, xG {'sim' if doc['hasXg'] else 'não'}, "
                          f"{len(doc['next'])} próximos jogos {srcs}, {size // 1024} KB")
        except Exception as e:  # noqa: BLE001
            report.append(f"{name}: FALHOU ({e})")
        if budget <= 0:
            report.append("Limite de jogos novos por execução atingido; o restante entra na próxima execução.")
            break
    if hist_budget[0] <= 0:
        report.append("Histórico: limite por execução atingido; a carga continua na próxima execução.")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n" + "\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1:])
