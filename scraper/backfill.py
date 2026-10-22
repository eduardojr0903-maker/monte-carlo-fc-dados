"""Download all past seasons (2022+) of one league, team stats only, into data/cache/history_full/.
Run by .github/workflows/backfill.yml, one job per league in parallel."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import history  # noqa: E402

lid = int(sys.argv[1])
lg = collect.fm("leagues", id=lid)
full = collect.HIST_CACHE + "_full"
os.makedirs(full, exist_ok=True)
# history.collect saves into cache_dir; point it at the full directory
data, new = history.collect(lid, lg, collect.fm, collect.team_stats, full, 100000, 0.6)
print(lid, "seasons", {k: len(v) for k, v in data.items()}, "new", new)
