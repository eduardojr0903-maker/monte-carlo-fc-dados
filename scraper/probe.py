"""Explores response shapes of the sources that answer from GitHub."""
import json, os, re
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
def get(u, **kw):
    try:
        x = S.get(u, timeout=30, **kw)
        return x.status_code, x.text
    except Exception as e:
        return "ERR", str(e)
def trim(o, depth=0):
    if depth > 6: return "…"
    if isinstance(o, dict): return {k: trim(v, depth+1) for k, v in list(o.items())[:40]}
    if isinstance(o, list): return [trim(v, depth+1) for v in o[:2]] + ([f"...{len(o)} items"] if len(o) > 2 else [])
    if isinstance(o, str): return o[:80]
    return o
B = "https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/"
for k, u in {"sb_day": B+"scoreboard?dates=20261018", "sb_range": B+"scoreboard?dates=20261001-20261020&limit=300", "sb_plain": B+"scoreboard"}.items():
    st, t = get(u); out[k] = [st, len(t)]
    if st == 200:
        j = json.loads(t); out[k].append(len(j.get("events", [])))
        if k == "sb_day": out["sb_event"] = trim(j["events"][0]); eid = j["events"][0]["id"]
st, t = get(B + f"summary?event={eid}")
if st == 200:
    j = json.loads(t)
    out["sum_keys"] = list(j.keys())
    out["sum_box_team"] = trim(j.get("boxscore", {}).get("teams", [{}])[0])
    ro = j.get("rosters", [{}])[0]
    out["sum_roster_keys"] = list(ro.keys())
    out["sum_roster_p0"] = ro.get("roster", [{}])[0]
    subs = [p for p in ro.get("roster", []) if p.get("subbedIn")]
    out["sum_roster_sub"] = subs[0] if subs else None
    out["sum_keyevents"] = [trim(e) for e in j.get("keyEvents", [])[:3]]
    out["sum_keyevent_types"] = sorted({(e.get("type") or {}).get("text", "") for e in j.get("keyEvents", [])})
st, t = get("https://understat.com/league/EPL/2026")
out["understat_scripts"] = re.findall(r"(?:src|url|fetch)\s*[=(:]\s*['\"]([^'\"]+)['\"]", t)[:20]
out["understat_vars"] = re.findall(r"var\s+(\w+)\s*=", t)[:20]
for u in ["https://understat.com/getLeagueData/EPL/2026", "https://understat.com/main/getLeagueData/EPL/2026"]:
    st, t = get(u, headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://understat.com/league/EPL/2026"})
    out["us_" + u.split("/")[-3]] = [st, len(t), t[:300]]
st, t = get("https://www.flashscore.com/football/brazil/serie-a-betano/results/")
out["fs_ids"] = re.findall(r"g_1_(\w{8})", t)[:5]
FS = {"x-fsign": "SW9D1eZo", "Referer": "https://www.flashscore.com/"}
if out["fs_ids"]:
    mid = out["fs_ids"][0]
    for k, u in {"fs_stats": f"https://global.flashscore.ninja/2/x/feed/df_st_1_{mid}", "fs_lineup": f"https://global.flashscore.ninja/2/x/feed/df_li_1_{mid}"}.items():
        st, t = get(u, headers=FS); out[k] = [st, len(t), t[:600]]
for u in ["https://api.sofascore.app/api/v1/unique-tournament/325/seasons", "https://api.sofascore.app/api/v1/sport/football/events/live"]:
    st, t = get(u, headers={"User-Agent": "SofaScore/23.1 (Android)"}); out["sofaapp_" + u.split("/")[-1]] = [st, len(t), t[:200]]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
