import json, os, re
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
st, t = S.get("https://www.sportsgambler.com/lineups/football/brazil-serie-a/", timeout=40).status_code, None
t = S.get("https://www.sportsgambler.com/lineups/football/brazil-serie-a/", timeout=40).text
i = t.find("reply_click(")
js = [m.start() for m in re.finditer(r"function\s+reply_click", t)]
out["fn"] = [t[k-200:k+2500] for k in js[:1]]
out["scripts"] = re.findall(r'<script[^>]+src="([^"]+)"', t)[:30]
for u in out["scripts"]:
    if "lineup" in u.lower():
        full = u if u.startswith("http") else "https://www.sportsgambler.com" + u
        tt = S.get(full, timeout=40).text
        k = tt.find("reply_click"); out["js_" + u] = tt[max(0, k-300): k+2500]
for cand in ["https://www.sportsgambler.com/lineups/ajax/5103676", "https://www.sportsgambler.com/wp-admin/admin-ajax.php?action=lineups&id=5103676"]:
    x = S.get(cand, timeout=40); out[cand] = [x.status_code, x.text[:500]]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
