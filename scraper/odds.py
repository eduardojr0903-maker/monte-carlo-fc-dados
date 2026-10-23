"""Historical closing odds from football-data.co.uk (2022 onwards) into data/odds/."""
import os
import time
from curl_cffi import requests as http

S = http.Session(impersonate="chrome")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds")
os.makedirs(OUT, exist_ok=True)
urls = {f"{code}_{season}.csv": f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
        for code in ("E0", "E1", "SP1", "I1", "D1", "F1") for season in ("2223", "2324", "2425", "2526", "2627")}
urls["BRA.csv"] = "https://www.football-data.co.uk/new/BRA.csv"
import sys
if "--current" in sys.argv:  # only the files that still change (current seasons)
    urls = {k: v for k, v in urls.items() if "2627" in k or k == "BRA.csv"}
for name, u in urls.items():
    try:
        r = S.get(u, timeout=60)
        print(name, r.status_code, len(r.content))
        if r.status_code == 200 and len(r.content) > 500:
            open(os.path.join(OUT, name), "wb").write(r.content)
    except Exception as e:  # noqa: BLE001
        print(name, "ERR", e)
    time.sleep(1)
