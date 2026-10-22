import json, os, re
from curl_cffi import requests as r
S = r.Session(impersonate="chrome")
out = {}
def get(u, **kw):
    try:
        x = S.get(u, timeout=40, **kw); return x.status_code, x.text
    except Exception as e:
        return "ERR", str(e)
def trim(o, d=0):
    if d > 5: return "…"
    if isinstance(o, dict): return {k: trim(v, d+1) for k, v in list(o.items())[:30]}
    if isinstance(o, list): return [trim(v, d+1) for v in o[:2]] + ([f"...{len(o)}"] if len(o) > 2 else [])
    if isinstance(o, str): return o[:60]
    return o
st, t = get("https://www.fotmob.com/api/data/leagues?id=268")
j = json.loads(t)
out["lg_keys"] = list(j.keys())
fx = j.get("fixtures") or {}
out["fx_keys"] = list(fx.keys()) if isinstance(fx, dict) else str(type(fx))
allm = (fx.get("allMatches") if isinstance(fx, dict) else None) or []
out["n_matches"] = len(allm)
out["m0"] = trim(allm[0]) if allm else None
up = [m for m in allm if "Santos" in json.dumps(m) and not (m.get("status") or {}).get("finished")]
fin = [m for m in allm if (m.get("status") or {}).get("finished")]
out["up"] = trim(up[:1])
out["stats_keys"] = list((j.get("stats") or {}).keys()) if isinstance(j.get("stats"), dict) else None
for tag, m in (("up", up[0] if up else None), ("fin", fin[-1] if fin else None)):
    if not m: continue
    mid = m.get("id")
    for u in [f"https://www.fotmob.com/api/data/matchDetails?matchId={mid}", f"https://www.fotmob.com/api/matchDetails?matchId={mid}"]:
        st, t = get(u)
        out[f"{tag}_{u.split('/api/')[1][:20]}"] = [st, len(t), t[:200]]
        if st == 200 and t.startswith("{"):
            d = json.loads(t)
            c = d.get("content") or {}
            out[f"{tag}_content_keys"] = list(c.keys())
            lu = c.get("lineup") or {}
            out[f"{tag}_lineup"] = trim(lu)
            stt = c.get("stats") or {}
            out[f"{tag}_stats"] = json.dumps(stt)[:1500]
            ps = c.get("playerStats")
            if isinstance(ps, dict) and ps:
                k0 = list(ps)[0]; out[f"{tag}_playerStats0"] = json.dumps(ps[k0])[:2500]
            break
st, t = get("https://www.sportsgambler.com/lineups/football/brazil-serie-a/")
txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
txt = re.sub(r"<[^>]+>", "|", txt); txt = re.sub(r"\s*\|[\s|]*", "|", txt)
i = txt.find("Santos")
out["sg_santos"] = txt[max(0, i-300): i+1500]
out["sg_links"] = sorted(set(re.findall(r'href="(/lineups/football/[^"]+)"', t)))[:30]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
