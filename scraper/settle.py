"""Settles registered bets and fills closing odds / CLV.

Input: a directory with the app's `bets` documents (as saved by ArtifactData list with out_dir),
       plus this repository's data (match records and football-data closing odds).
Output: JSON list of updates [{doc_id, if_version, data}] for bets that changed.

Usage: python scraper/settle.py BETS_DIR VERSIONS.json OUT.json
       VERSIONS.json = {"<doc id>": <version>} from the listing of the bets collection.
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_build as bb  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC2LID = {325: 268, 390: 8814, 17: 47, 18: 48, 8: 87, 23: 55, 35: 54, 34: 53}


def load_matches():
    by_id, by_teams = {}, {}
    for f in glob.glob(os.path.join(ROOT, "data", "cache", "fotmob", "*.json")):
        for mid, r in json.load(open(f)).items():
            by_id[mid] = r
            by_teams.setdefault((r["h"], r["a"]), []).append(r)
    # corners (whole match and first half) from the corners cache, same match ids
    for f in glob.glob(os.path.join(ROOT, "data", "cache", "corners", "*.json")):
        for mid, c in json.load(open(f)).items():
            if mid in by_id and c.get("c") and c["c"][0] >= 0:
                by_id[mid]["corners"] = c["c"]
    return by_id, by_teams


def outcome(key, r):
    """True/False/None(void)/'?' (cannot settle) for a market key on a match record."""
    hg, ag = r["hg"], r["ag"]
    t = r.get("t")
    m = re.match(r"^P(\d)_(.+)_(sh|sot|g)(\d)$", key)
    if m:
        side, pid, kind, k = int(m.group(1)), m.group(2), m.group(3), int(m.group(4))
        p = next((x for x in r.get("p", []) if str(x[0]) == pid and x[2] == side), None)
        if p is None:
            return None  # did not play: void
        v = {"sh": p[6], "sot": p[7], "g": p[9]}[kind]
        return v >= k
    simple = {"1": hg > ag, "X": hg == ag, "2": ag > hg, "1X": hg >= ag, "X2": ag >= hg, "12": hg != ag,
              "BTTS-S": hg > 0 and ag > 0, "BTTS-N": not (hg > 0 and ag > 0),
              "BTTS-1": hg > 0 and ag > 0 and hg > ag, "BTTS-2": hg > 0 and ag > 0 and ag > hg,
              "H-1.5": hg - ag >= 2, "A+1.5": hg - ag <= 1, "A-1.5": ag - hg >= 2, "H+1.5": ag - hg <= 1,
              "HCS": ag == 0, "ACS": hg == 0}
    if key in simple:
        return simple[key]
    if key in ("DNB1", "DNB2"):
        return None if hg == ag else (hg > ag) == (key == "DNB1")
    if key.startswith("C") and not key.startswith("CS"):
        c = r.get("corners")
        if not c:
            return "?"
        ch, ca = c[0], c[1]
        if key in ("CX1", "CXX", "CX2"):
            return {"CX1": ch > ca, "CXX": ch == ca, "CX2": ca > ch}[key]
        m = re.match(r"^C([HA])(-?[\d.]+)$", key)
        if m:
            d = (ch - ca) if m.group(1) == "H" else (ca - ch)
            return d + float(m.group(2)) > 0
        m = re.match(r"^(C|CHt|CAt|C1)([<>])([\d.]+)$", key)
        if m:
            if m.group(1) == "C1":
                if c[2] < 0:
                    return "?"
                v = c[2] + c[3]
            else:
                v = {"C": ch + ca, "CHt": ch, "CAt": ca}[m.group(1)]
            line = float(m.group(3))
            return v > line if m.group(2) == ">" else v < line
        return "?"
    m = re.match(r"^CS(\d)-(\d)$", key)
    if m:
        return hg == int(m.group(1)) and ag == int(m.group(2))
    m = re.match(r"^(G|HG|AG|S|SH|SA|T|TH|TA)([<>])([\d.]+)$", key)
    if m:
        kind, op, line = m.group(1), m.group(2), float(m.group(3))
        if kind in ("G", "HG", "AG"):
            v = {"G": hg + ag, "HG": hg, "AG": ag}[kind]
        else:
            if not t:
                return "?"
            v = {"S": t[0][0] + t[1][0], "SH": t[0][0], "SA": t[1][0], "T": t[0][1] + t[1][1], "TH": t[0][1], "TA": t[1][1]}[kind]
        return v > line if op == ">" else v < line
    return "?"


def main(bets_dir, versions_path, out_path):
    versions = json.load(open(versions_path)) if versions_path and os.path.exists(versions_path) else {}
    by_id, by_teams = load_matches()
    ap = os.path.join(ROOT, "data", "autobets", "bets.json")
    auto = json.load(open(ap)) if os.path.exists(ap) else {}
    odds_cache = {}
    updates = []
    for f in glob.glob(os.path.join(bets_dir, "**", "*.json"), recursive=True):
        raw = json.load(open(f))
        doc_id = raw.get("id") or os.path.basename(f)[:-5]
        ver = raw.get("version") or versions.get(doc_id)
        b = raw.get("data", raw)
        if not isinstance(b, dict) or "key" not in b:
            continue
        r = by_id.get(str(b.get("fx"))) if b.get("fx") else None
        if r is None:
            created = datetime.fromisoformat(str(b.get("created")).replace("Z", "+00:00")).timestamp() if b.get("created") else 0
            cands = [x for x in by_teams.get((str(b.get("h")), str(b.get("a"))), []) if x["ts"] >= created - 6 * 3600]
            r = min(cands, key=lambda x: x["ts"]) if cands else None
        if r is None:
            continue
        upd = {}
        if b.get("status") == "pending":
            res = outcome(b["key"], r)
            if res != "?":
                st = "void" if res is None else ("win" if res else "loss")
                upd["status"] = st
                upd["profit"] = 0 if st == "void" else (b["odd"] - 1 if st == "win" else -1)
        if b.get("closeSrc") not in ("pinnacle", "market") and b.get("key") in ("1", "X", "2", "G>2.5", "G<2.5"):
            lid = DOC2LID.get(int(b.get("comp") or 0))
            if lid:
                if lid not in odds_cache:
                    odds_cache[lid] = bb.load_odds(lid)
                day = datetime.fromtimestamp(r["ts"], timezone.utc).date()
                o = bb.find_odds(odds_cache[lid], day, r["hn"], r["an"], r["hg"], r["ag"]) if odds_cache[lid] else None
                if o:
                    # fair closing price: Pinnacle when available, otherwise the market average (both without margin)
                    if b["key"] in ("1", "X", "2"):
                        k, pairs = {"1": 0, "X": 1, "2": 2}[b["key"]], (("pinnacle", o.get("pin")), ("market", o.get("avg")))
                    else:
                        k, pairs = (0 if b["key"] == "G>2.5" else 1), (("pinnacle", o.get("ou_pin")), ("market", o.get("ou_avg")))
                    for src, prices in pairs:
                        if prices and all(prices):
                            fair = [1 / x for x in prices]
                            p = fair[k] / sum(fair)
                            upd.update({"close": prices[k], "closeFairP": round(p, 4), "clv": round(b["odd"] * p - 1, 4), "closeSrc": src})
                            break
        # automatic bets: closing line = last Bet365 price seen before kick-off (kept in data/autobets/bets.json)
        if b.get("auto") and not b.get("closeSrc") and not upd.get("closeSrc") and r.get("ts", 0) <= datetime.now(timezone.utc).timestamp():
            a = auto.get(doc_id) or b
            last = a.get("lastOdd")
            if last:
                upd.update({"close": last, "closeFairP": None, "clv": round(b["odd"] / last - 1, 4), "closeSrc": "bet365"})
        if upd:
            updates.append({"doc_id": doc_id, "if_version": ver, "data": upd})
    json.dump(updates, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(len(updates), "apostas atualizadas")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
