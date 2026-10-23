import json, os
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
d = S.get("https://www.fotmob.com/api/data/matchDetails?matchId=5103667", timeout=40).json()
c = d["content"]
out = {"shotmap_keys": list((c.get("shotmap") or {}).keys())}
shots = (c.get("shotmap") or {}).get("shots") or []
out["n_shots"] = len(shots); out["shot0"] = shots[0] if shots else None
out["situations"] = sorted({s.get("situation") for s in shots}); out["eventTypes"] = sorted({s.get("eventType") for s in shots})
mf = c.get("matchFacts") or {}
out["mf_keys"] = list(mf.keys())
ev = (mf.get("events") or {}).get("events") or []
out["ev_types"] = sorted({e.get("type") for e in ev})
out["ev_card"] = [e for e in ev if e.get("type") == "Card"][:2]
out["ev_goal"] = [e for e in ev if e.get("type") == "Goal"][:1]
out["general_keys"] = list(d.get("general", {}).keys())
out["header_status"] = (d.get("header") or {}).get("status")
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
