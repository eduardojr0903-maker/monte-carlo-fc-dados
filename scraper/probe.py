import json, os, re
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
def get(u, **kw):
    try:
        x = S.get(u, timeout=40, **kw); return x.status_code, x.text
    except Exception as e:
        return "ERR", str(e)
for lid in (8814, 47, 87, 55, 54, 53):
    st, t = get(f"https://www.fotmob.com/api/data/leagues?id={lid}")
    if st != 200: out[lid] = st; continue
    j = json.loads(t); am = j["fixtures"]["allMatches"]
    fin = [m for m in am if m["status"].get("finished")]
    up = [m for m in am if not m["status"].get("finished")]
    rec = {"name": j["details"]["name"], "season": j["details"]["selectedSeason"], "n": len(am), "fin": len(fin)}
    if fin:
        st2, t2 = get(f"https://www.fotmob.com/api/data/matchDetails?matchId={fin[-1]['id']}")
        if st2 == 200:
            d = json.loads(t2)["content"]
            s = json.dumps(d.get("stats"))
            rec["has_xg"] = "expected_goals" in s
            rec["n_playerStats"] = len(d.get("playerStats") or {})
    if up:
        st3, t3 = get(f"https://www.fotmob.com/api/data/matchDetails?matchId={up[0]['id']}")
        if st3 == 200:
            lu = json.loads(t3)["content"].get("lineup") or {}
            rec["up_lineupType"] = [lu.get("lineupType"), lu.get("source")]
    out[lid] = rec
# sportsgambler match page
st, t = get("https://www.sportsgambler.com/lineups/football/brazil-serie-a/")
links = re.findall(r'href="([^"]*lineups[^"]*santos[^"]*bahia[^"]*)"', t, flags=re.I) or re.findall(r'href="([^"]*santos-bahia[^"]*|[^"]*santos-vs-bahia[^"]*)"', t, flags=re.I)
out["sg_links_santos"] = links[:5]
out["sg_any_links"] = re.findall(r'href="(/lineups/football/[^"]+/[^"]+)"', t)[:8]
i = t.find("Santos"); out["sg_raw_santos"] = t[max(0, i-1500): i+3000]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
