import json, os
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
for pid in ("400256", "528348", "19533"):  # Rodinei, Éverton, Neymar
    x = S.get(f"https://www.fotmob.com/api/data/playerData?id={pid}", timeout=40)
    out[pid] = [x.status_code]
    if x.status_code == 200:
        j = x.json()
        out[pid].append(list(j.keys()))
        out[pid].append(j.get("positionDescription"))
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
