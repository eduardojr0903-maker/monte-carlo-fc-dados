"""Checks which data sources answer from GitHub's servers."""
import json, os
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
URLS = {
 "sofascore_www": "https://www.sofascore.com/api/v1/unique-tournament/325/seasons",
 "sofascore_api": "https://api.sofascore.com/api/v1/unique-tournament/325/seasons",
 "sofascore_app": "https://api.sofascore.app/api/v1/unique-tournament/325/seasons",
 "espn_scoreboard": "https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/scoreboard?dates=20261001-20261021&limit=200",
 "espn_core": "https://sports.core.api.espn.com/v2/sports/soccer/leagues/bra.1/seasons",
 "understat": "https://understat.com/league/EPL/2026",
 "fotmob": "https://www.fotmob.com/api/leagues?id=268",
 "fbref": "https://fbref.com/en/comps/24/Serie-A-Stats",
 "flashscore": "https://www.flashscore.com/",
}
out = {}
for k, u in URLS.items():
    try:
        x = S.get(u, timeout=30)
        out[k] = [x.status_code, len(x.text), x.text[:160].replace("\n", " ")]
    except Exception as e:
        out[k] = ["ERR", str(e)[:160]]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1)
print(json.dumps(out, indent=1))
