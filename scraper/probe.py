import json, os, re, datetime
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
def get(u, **kw):
    try:
        x = S.get(u, timeout=30, **kw); return x.status_code, x.text
    except Exception as e:
        return "ERR", str(e)
# ESPN upcoming Santos match
today = datetime.date.today()
for d in range(0, 6):
    day = (today + datetime.timedelta(d)).strftime("%Y%m%d")
    st, t = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/scoreboard?dates={day}")
    if st != 200: continue
    for e in json.loads(t).get("events", []):
        if "Santos" in e["name"] or "Bahia" in e["name"]:
            out.setdefault("espn_upcoming", []).append([e["id"], e["name"], e["date"], e["status"]["type"]["state"]])
if out.get("espn_upcoming"):
    eid = out["espn_upcoming"][0][0]
    st, t = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary?event={eid}")
    j = json.loads(t) if st == 200 else {}
    out["espn_up_rosters"] = [[ro.get("homeAway"), len(ro.get("roster") or [])] for ro in j.get("rosters", [])]
    out["espn_up_keys"] = list(j.keys())
for k, u in {
  "rotowire_epl": "https://www.rotowire.com/soccer/lineups.php",
  "rotowire_liga": "https://www.rotowire.com/soccer/lineups.php?league=LIGA",
  "sportsgambler": "https://www.sportsgambler.com/lineups/football/",
  "sportsgambler_bra": "https://www.sportsgambler.com/lineups/football/brazil-serie-a/",
  "ge_globo": "https://ge.globo.com/futebol/brasileirao-serie-a/",
  "fotmob_page": "https://www.fotmob.com/leagues/268/overview/serie-a",
  "fotmob_api": "https://www.fotmob.com/api/data/leagues?id=268",
  "sofa_app": "https://api.sofascore.app/api/v1/unique-tournament/325/seasons",
  "football_lineups": "https://www.footballlineups.com/",
  "sportsmole": "https://www.sportsmole.co.uk/football/predicted-line-ups/",
}.items():
    st, t = get(u)
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"\s+", " ", txt)
    out[k] = [st, len(t), txt[:700]]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
