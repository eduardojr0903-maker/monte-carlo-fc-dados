"""Player photos for the lineup pitch, one database document per team.

data/photos/t{team id}.json = {"updated", "team", "p": {player id: small WEBP data URI}}
The app cannot load images from other sites, so each photo is a 64 px WEBP embedded as a data URI
(48 px, about 1.5 KB; a squad stays well under the database's 256 KiB per document).
Photos come from FotMob (the player ids in the league documents are FotMob ids) and are cached in
data/cache/photos/{player id}.txt (empty file = FotMob has no photo). Run weekly.
"""
import base64
import glob
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "cache", "photos")
OUT = os.path.join(ROOT, "data", "photos")
BUDGET = int(os.environ.get("PHOTO_BUDGET", "6000"))  # new downloads per run
REFRESH_DAYS = int(os.environ.get("PHOTO_REFRESH_DAYS", "120"))  # re-check missing photos after this
MAX_PER_TEAM = 30
SIZE = 48
MAX_BYTES = 240_000
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def fetch(pid):
    r = SESSION.get(f"https://images.fotmob.com/image_resources/playerimages/{pid}.png", timeout=30)
    if r.status_code != 200 or len(r.content) < 1500:
        return ""
    im = Image.open(io.BytesIO(r.content)).convert("RGBA")
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=65, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def shrink(uri):
    """Photos cached at an older, larger size are scaled down on the way out."""
    raw = base64.b64decode(uri.split(",", 1)[1])
    im = Image.open(io.BytesIO(raw))
    if max(im.size) <= SIZE:
        return uri
    im = im.convert("RGBA")
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=65, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def photo(pid, state):
    path = os.path.join(CACHE, f"{pid}.txt")
    if os.path.exists(path):
        uri = open(path).read().strip()
        if uri or time.time() - os.path.getmtime(path) < REFRESH_DAYS * 86400:
            return uri or None
    if state["new"] >= BUDGET:
        return None
    state["new"] += 1
    try:
        uri = fetch(pid)
    except Exception as e:  # noqa: BLE001
        print("  foto", pid, e, flush=True)
        return None
    time.sleep(0.12)
    with open(path, "w") as fh:
        fh.write(uri)
    state["got"] += bool(uri)
    return uri or None


def main():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    state = {"new": 0, "got": 0}
    squads = {}  # team id -> [(minutes, player id)]
    names = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "db", "s*.json"))):
        pack = json.loads(json.load(open(f))["pack"])
        teams = pack["teams"]
        for row in pack["players"]:
            try:
                tid = str(teams[row[2]][0])
            except (IndexError, TypeError):
                continue
            squads.setdefault(tid, []).append((row[6] or 0, str(row[0])))
            names[tid] = teams[row[2]][1]
    # most-used players first, so a squad gets its regulars even when the budget runs out
    order = sorted(squads, key=lambda t: names.get(t, ""))
    for tid in order:
        squads[tid] = [pid for _, pid in sorted(squads[tid], reverse=True)[:MAX_PER_TEAM]]
    for rank in range(MAX_PER_TEAM):
        for tid in order:
            if rank < len(squads[tid]):
                photo(squads[tid][rank], state)
    written = 0
    for tid in order:
        p = {}
        for pid in squads[tid]:
            uri = photo(pid, {"new": BUDGET, "got": 0})  # cache only
            if uri:
                p[pid] = shrink(uri)
        while len(json.dumps(p)) > MAX_BYTES and p:
            p.pop(next(reversed(p)))
        path = os.path.join(OUT, f"t{tid}.json")
        old = json.load(open(path)) if os.path.exists(path) else {}
        if old.get("p") == p:
            continue
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "team": names.get(tid), "p": p},
                  open(path, "w"), separators=(",", ":"), ensure_ascii=False)
        written += 1
    print(f"fotos: {len(order)} times, {state['new']} baixadas agora ({state['got']} com foto), {written} documentos alterados", flush=True)


if __name__ == "__main__":
    sys.exit(main())
