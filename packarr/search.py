"""Find season / series packs on Nyaa (via Prowlarr) and rank them the way a careful human would.

Lessons baked in:
  - "[Optional Dual Audio]" packs are subs-only; the dub is a separate torrent.
  - a release without "dual" in the title can still be exactly what you want (English-only dub sets of
    dub-era shows). Search by series name, then *look* at the candidates - don't filter them out.
  - [pseudo] on an old show is a pseudo-1080p source flag, not proof of an upscale. Rank it down, not out.
  - MB per episode says more than GB per pack.
"""

from __future__ import annotations

import json
import os
import re

from .clients.prowlarr import Prowlarr
from .config import Search


def _episodes_in_title(title: str) -> int | None:
    m = re.search(r"\b0*(\d{1,4})\s*[-~]\s*0*(\d{1,4})\b", title)  # "01-49", "001 ~ 1071"
    if m and int(m.group(2)) > int(m.group(1)):
        return int(m.group(2)) - int(m.group(1)) + 1
    return None


def score(row: dict, cfg: Search, episodes: int | None = None) -> tuple[int, list[str]]:
    """Higher is better. Returns (score, reasons)."""
    t = row["title"]
    s, why = 0, []
    if re.search(cfg.avoid_regex, t, re.I):
        s -= 50
        why.append("avoid-tag")
    if re.search(cfg.dual_regex, t, re.I):
        s += 20
        why.append("dual")
    if re.search(r"x265|hevc", t, re.I):
        s += 5
    if re.search(r"\b(bd|bluray|blu-ray|bdrip)\b", t, re.I):
        s += 5
    grp = re.match(r"\[([^\]]+)\]", t)
    if grp and any(g.lower() == grp.group(1).lower() for g in cfg.prefer_groups):
        s += 10
        why.append(f"group:{grp.group(1)}")
    if re.search(r"complete|batch|\b(season|series)\b.*\b(1|01)\s*[-~+]", t, re.I) or re.search(r"\bS0?1-S?0?\d\b", t, re.I):
        s += 5
        why.append("complete")
    n = episodes or _episodes_in_title(t)
    if n:
        mb = row["size"] / 1e6 / n
        lo, hi = cfg.mb_per_episode
        if lo <= mb <= hi:
            s += 10
            why.append(f"{mb:.0f}MB/ep")
        else:
            s -= 10
            why.append(f"{mb:.0f}MB/ep!")
    seeds = row.get("seeders") or 0
    if seeds < cfg.min_seeders:
        s -= 30
        why.append("unseeded")
    else:
        s += min(seeds, 50) // 5
    return s, why


def search(prowlarr: Prowlarr, query: str, cfg: Search, episodes: int | None = None, dual_only: bool = False) -> list[dict]:
    rows = []
    for x in prowlarr.search(query):
        gb = x["size"] / 1e9
        if not cfg.min_gb <= gb <= cfg.max_gb:
            continue
        if dual_only and not re.search(cfg.dual_regex, x["title"], re.I):
            continue
        sc, why = score(x, cfg, episodes)
        rows.append({**x, "gb": round(gb, 2), "score": sc, "why": why})
    rows.sort(key=lambda r: (-r["score"], -(r.get("seeders") or 0)))
    return rows


def save_last(rows: list[dict], state_dir: str) -> str:
    p = os.path.join(state_dir, "last-search.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    return p


def load_last(state_dir: str) -> list[dict]:
    p = os.path.join(state_dir, "last-search.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)
