import json, os, re
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
for mid in ("5103676", "5103683"):
    x = S.get("https://www.sportsgambler.com/lineups/lineups-load2.php", params={"id": mid}, timeout=40,
              headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.sportsgambler.com/lineups/football/brazil-serie-a/"})
    t = x.text
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    out[mid] = [x.status_code, len(t), t[:3000], re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", txt))[:2500]]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
