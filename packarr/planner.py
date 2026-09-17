"""Plan: propose file -> Sonarr episode for every video in a pack, then verify the proposal.

Order of authority
  1. an explicit hand map on the job                         (`packarr approve --map`)
  2. `--map` season remaps                                     (US-numbered dub packs)
  3. the anime-lists resolver on the folder / pack title       (AniList -> AniDB -> TVDB season+offset)
  4. SxxEyy in the filename, if that episode exists
  5. folder / title season tokens + episode numbers
  6. AniDB / TVDB special titles for the leftovers
Anything the verifier is not happy with becomes an *issue*, and a plan with issues is HELD, never imported.

Every plan row: {rel, path, se, epId, seriesId, title, reason, ok, note, size, mins}
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from . import parsing as P
from .log import log

MOVIE_MIN = 40  # minutes: longer than this and it is a film/compilation, not an episode


def _index(eplist: list[dict]):
    byse = {(e["seasonNumber"], e["episodeNumber"]): e for e in eplist}
    byabs = {e.get("absoluteEpisodeNumber"): e for e in eplist if e.get("absoluteEpisodeNumber") and e["seasonNumber"] > 0}
    sc: dict[int, int] = {}
    for e in eplist:
        sc[e["seasonNumber"]] = sc.get(e["seasonNumber"], 0) + 1
    return byse, byabs, sc


class Planner:
    def __init__(self, resolver, episodes_for: Callable[[int], list[dict]], duration_min: Callable[[str], float],
                 to_local: Callable[[str], str]):
        self.resolver = resolver
        self.episodes_for = episodes_for  # Sonarr episode list for another series id (when a pack mixes shows)
        self.duration_min = duration_min  # ffprobe wrapper on a LOCAL path
        self.to_local = to_local  # Sonarr-namespace path -> Packarr-namespace path

    def plan(self, job: dict, items: list[dict], eps: list[dict], series_by_tvdb: dict[str, dict]) -> tuple[list[dict], list[str]]:
        R = self.resolver
        byse, byabs, scount = _index(eps)
        s1max = scount.get(1, 0)
        my_tvdb = str(job["tvdb"])
        series_title = job["series"]
        pack_title = job.get("title", "")
        remap = {int(a): (int(b), int(c)) for a, b, c in job.get("map") or []}  # src season -> (dst season, ep offset)
        plan: list[dict] = []
        issues: list[str] = []
        if not any(e["seasonNumber"] > 0 for e in eps):
            issues.append("Sonarr has no episodes for this series yet (refresh pending?)")  # never treat a whole pack as extras
        folder_res: dict[str, dict | None] = {}
        other_eps: dict[int, tuple] = {}

        # --- per-folder numbering: a pack whose numbered folders form one continuous run is absolutely numbered
        folder_max: dict[str, int] = {}
        folder_min: dict[str, int] = {}
        for it in items:
            rel = it["relativePath"]
            if not P.is_video(rel) or P.EXTRA_DIR.search(os.path.dirname(rel)) or P.SPECIAL_DIR.search(os.path.dirname(rel)):
                continue
            nn = P.epnum(rel)
            if nn is not None:
                dd = os.path.dirname(rel)
                folder_max[dd] = max(folder_max.get(dd, 0), nn)
                folder_min[dd] = min(folder_min.get(dd, 10**6), nn)
        runs = sorted((folder_min[d], folder_max[d]) for d in folder_min if folder_max[d] > folder_min[d])
        pack_absolute = len(runs) >= 2 and runs[0][0] == 1 and all(runs[i][0] == runs[i - 1][1] + 1 for i in range(1, len(runs)))
        if pack_absolute:
            log(f"[{series_title}] pack is absolutely numbered across {len(runs)} folders (1-{runs[-1][1]})")
        single_folder = len({os.path.dirname(x["relativePath"]) for x in items if P.is_video(x["relativePath"])
                             and not P.EXTRA_DIR.search(os.path.dirname(x["relativePath"])) and not P.SPECIAL_DIR.search(os.path.dirname(x["relativePath"]))}) <= 1

        def resolve_dir(d: str):
            if d in folder_res:
                return folder_res[d]
            base = os.path.basename(d) if d else ""
            base = re.sub(r"^\s*s?\d{1,2}\s*[-.)]+\s*", "", base, flags=re.I)  # "s1-) Sailor Moon" / "2-) Crystal" -> drop ordering prefix
            core = re.sub(r"^\s*\d{1,2}[.)\-_ ]+\s*", "", base)
            bare = bool(base) and bool(re.fullmatch(r"(?i)\s*((season|series|part|s|vol(ume)?)\s*\d+|OVAs?|OADs?|ONAs?|Specials?|Movies?|Films?|Extras?|Bonus)\s*", core))
            parts = [re.sub(r"\[[^\]]*\]|\([^)]*\)", "", x).strip() for x in re.split(r"\s+-\s+", base)]
            parts = [x for x in parts if x and not re.fullmatch(r"(?i)(S\d{1,2}|Season\s*\d+|OVAs?|Specials?|Movies?|Extras?)", x)]
            sub = " ".join(x for x in parts if P.tokens(x) - P.tokens(series_title))
            cands = []
            if not base:
                cands = [pack_title]
            else:
                if sub:
                    cands.append(f"{series_title} {sub}")
                if not bare:
                    cands.append(base)
                cands.append(f"{series_title} {base}")
            stoks = P.tokens(series_title)
            res = None
            for tt in dict.fromkeys(cands):
                try:
                    r = R.resolve(tt, expect_tvdb=my_tvdb)
                except Exception as e:
                    log(f"resolver error for {tt[:40]}: {e}")
                    r = None
                if not r:
                    continue
                if str(r["tvdbid"]) != my_tvdb and not (P.tokens(r.get("title", "")) & stoks):
                    continue  # a "match" sharing no word with the series title is noise (Glass Maiden, Nine, ...)
                res = r
                break
            folder_res[d] = res
            return res

        def other_index(series_id: int):
            if series_id not in other_eps:
                oe = self.episodes_for(series_id)
                other_eps[series_id] = (oe,) + _index(oe)
            return other_eps[series_id]

        for it in items:
            rel = it["relativePath"]
            if not P.is_video(rel):
                continue
            row = {"rel": rel, "path": it["path"], "se": None, "epId": None, "seriesId": job["seriesId"], "title": "",
                   "reason": "", "ok": True, "note": "", "size": it.get("size") or 0}
            name = os.path.basename(rel)
            d = os.path.dirname(rel)
            e = None
            _byse, _byabs = byse, byabs
            explicit = job.get("explicit") or {}
            if rel in explicit:  # a hand map beats every filter
                e = next((x for x in eps if x["id"] == explicit[rel]), None)
                row["reason"] = "explicit"
            elif P.EXTRA_DIR.search(d) or P.EXTRA_FILE.search(name):
                row["reason"] = "extra"
                plan.append(row)
                continue
            elif P.SPECIAL_FILE.search(name):
                row["reason"] = "movie/OVA -> routed after import"
                plan.append(row)
                continue
            else:
                m = P.SE.search(P.SEX.sub(r"S\1E\2", name))
                n = P.epnum(name)
                pre = resolve_dir(d) if d else None
                foreign = bool(pre) and str(pre["tvdbid"]) != my_tvdb
                if not foreign and d:  # an ancestor folder naming another show makes the whole subtree foreign
                    anc = os.path.dirname(d)
                    while anc:
                        ar = resolve_dir(anc)
                        if ar and str(ar["tvdbid"]) != my_tvdb:
                            foreign, pre = True, ar
                            break
                        anc = os.path.dirname(anc)
                # --map: the pack's own season numbering is a lie we were told about up front
                if remap and not foreign:
                    src = int(m.group(1)) if m else P.season_token(d) or P.season_token(name)
                    num = int(m.group(2)) if m else n
                    if src in remap and num is not None:
                        dst, off = remap[src]
                        e = byse.get((dst, num + off))
                        row["reason"] = f"--map S{src:02d} -> S{dst:02d}+{off}"
                        if e is None:
                            row["note"] = "no such episode"
                if e is not None or (remap and row["reason"].startswith("--map")):
                    pass
                elif m and not foreign and (int(m.group(1)), int(m.group(2))) in byse and not (int(m.group(1)) == 1 and int(m.group(2)) > s1max):
                    e = byse[(int(m.group(1)), int(m.group(2)))]
                    row["reason"] = "SxxEyy"
                else:
                    if m and n is None:
                        n = int(m.group(2))
                    res = pre if foreign else resolve_dir(d)
                    if pack_absolute and not foreign and n is not None and n in byabs and res and str(res["tvdbid"]) == my_tvdb and res.get("season") not in (None, "", "a"):
                        e = byabs[n]
                        row["reason"] = "absolute numbering (continuous pack)"
                    elif res and n is not None:
                        if str(res["tvdbid"]) != my_tvdb:
                            other = series_by_tvdb.get(str(res["tvdbid"]))
                            if other:
                                _oe, _byse, _byabs, _ = other_index(other["id"])
                                row["seriesId"] = other["id"]
                                row["reason"] = f"anime-lists -> {other['title'][:30]}"
                            else:
                                row["reason"] = f"other series: tvdb {res['tvdbid']} '{res['title'][:35]}' not in Sonarr"
                                plan.append(row)
                                continue
                        else:
                            row["reason"] = f"anime-lists '{res['title'][:30]}'"
                        tv = R.to_tvdb(res, n)
                        _sc = scount if row["seriesId"] == job["seriesId"] else other_eps[row["seriesId"]][3]
                        # anime-lists tables go stale when TVDB re-cuts seasons: if a mapped range's size disagrees with the
                        # real TVDB season size, distrust the table and map positionally instead
                        stale = any(mm["start"] and mm["end"] and mm["tvdbseason"] > 0 and mm["anidbseason"] == 1
                                    and _sc.get(mm["tvdbseason"]) not in (None, mm["end"] - mm["start"] + 1) for mm in res["maps"])
                        if stale and tv[0] != "abs" and res.get("season") == "a":
                            tv = ("abs", n + res["offset"])
                            row["note"] = "stale anime-lists table; positional"
                        elif stale and tv[0] != "abs" and res.get("episodes") and len([sn for sn, c in _sc.items() if sn > 0 and c == res["episodes"]]) == 1 \
                                and (next(sn for sn, c in _sc.items() if sn > 0 and c == res["episodes"]), n + res["offset"]) in _byse:
                            tv = (next(sn for sn, c in _sc.items() if sn > 0 and c == res["episodes"]), n + res["offset"])  # renumbered: the season whose size matches
                            row["note"] = "stale anime-lists table; season by size"
                        elif stale and tv[0] != "abs" and res.get("season") not in (None, "", "a"):
                            ds = (int(res["season"]), n + res["offset"])  # TVDB merged two table seasons into one (Hetalia S1 = 52 eps)
                            if ds in _byse and tv != ds:
                                tv = ds
                                row["note"] = "stale anime-lists table; default season"
                        if tv[0] == "abs":
                            # positional across TVDB seasons when the entry's episode count equals seasons 1..k exactly
                            # (TVDB absolute numbers are unreliable where recaps/sequels share the sequence)
                            e, total = None, 0
                            for sn in sorted(k for k in _sc if k > 0):
                                if total < tv[1] <= total + _sc[sn] and res.get("episodes") and sum(_sc[k] for k in _sc if 0 < k <= sn) <= res["episodes"]:
                                    e = _byse.get((sn, tv[1] - total))
                                    break
                                total += _sc[sn]
                                if res.get("episodes") and total >= res["episodes"]:
                                    break
                            if e is None:
                                e = _byabs.get(tv[1])
                        else:
                            e = _byse.get(tv)
                        if e is None and tv[0] != "abs" and tv[0] not in _sc and res.get("episodes"):
                            cands = [sn for sn, c in _sc.items() if sn > 0 and c == res["episodes"]]  # stale season number: pick by size
                            if len(cands) == 1:
                                e = _byse.get((cands[0], n))
                                row["reason"] += f" (season {cands[0]} by size)"
                        prior = sum(c for k, c in _sc.items() if 0 < k < (tv[0] if tv[0] != "abs" else 0))
                        if e is None and n in _byabs and tv[0] != "abs" and tv[0] in _sc and n > _sc[tv[0]] and n > prior:
                            e = _byabs[n]
                            row["reason"] += " (absolute no.)"  # folder numbered straight through the series
                        elif e is None:
                            row["note"] = f"anime-lists gave {tv} which does not exist"
                    elif n is not None:
                        season = P.season_token(d) or P.season_token(name) or (P.season_token(pack_title, titles=True) if not d else None)
                        title_season = P.season_token(pack_title, titles=True)
                        if season is not None:
                            if pack_absolute and n in byabs:
                                e = byabs[n]
                                row["reason"] = "absolute numbering (continuous pack)"
                            elif season in scount and folder_max.get(d, 0) > scount[season] and n in byabs:
                                e = byabs[n]
                                row["reason"] = f"folder season {season}, absolute numbering"
                            else:
                                e = byse.get((season, n))
                                row["reason"] = f"folder season {season} (unresolved title)"
                            if e is None and season in scount and n > scount[season] and n in byabs:
                                e = byabs[n]
                                row["reason"] += " (absolute no.)"
                            if e is None and season not in scount:
                                row["reason"] = "movie/OVA -> routed after import"  # "S4 - OVAs" with no TVDB S4
                        elif n > s1max and n in byabs:
                            e = byabs[n]
                            row["reason"] = "absolute (unresolved title)"
                        elif title_season not in (None, 1):
                            row["reason"] = f"unresolved title; pack title says season {title_season} - refusing S1 default"
                        elif (1, n) in byse and single_folder:
                            e = byse[(1, n)]
                            row["reason"] = "S1 default (unresolved title)"  # only safe in a single-folder pack
                        else:
                            row["reason"] = "movie/OVA -> routed after import"
                        if e is None and row["reason"]:
                            row["note"] = "no such episode"
                    else:
                        row["reason"] = "movie/OVA -> routed after import" if (P.SPECIAL_DIR.search(d) or re.search(r"\b(web|special|omake|picture drama|mini|SP\d*(v\d+)?)\b", name, re.I)) else "no number"
            if e is None and row["reason"] != "explicit":
                res = folder_res.get(d) if d in folder_res else resolve_dir(d)
                if res and str(res["tvdbid"]) == my_tvdb:
                    try:
                        sp = R.match_special(res, name, series_title)
                    except Exception as ex:
                        sp = None
                        log(f"special match error: {ex}")
                    if sp and (sp[0], sp[1]) in byse:
                        e = byse[(sp[0], sp[1])]
                        row["reason"] = f"anidb special '{sp[2][:25]}'"
            if e is None and row["reason"] != "explicit":
                # last resort: Sonarr's own S00 (TVDB) special titles
                ft = P.tokens(P.clean_title(name)) - P.tokens(series_title)
                fcore = ft - {"ova", "oad", "ona", "special", "sp", "movie", "film", "bd", "v2", "v3"}
                best = None
                for x in eps:
                    if x["seasonNumber"] != 0:
                        continue
                    et = P.tokens(x["title"]) - P.tokens(series_title)
                    if not et:
                        continue
                    hit = len(et & ft) / len(et) >= 0.6 or (len(fcore) >= 2 and fcore <= et)
                    if hit and (best is None or len(et & ft) > best[0]):
                        best = (len(et & ft), x)
                if best and (len(ft) <= 3 or best[0] >= 2):
                    e = best[1]
                    row["reason"] = f"tvdb special '{e['title'][:25]}'"
            if e is None:
                if row["note"] == "no such episode" or row["note"].startswith("anime-lists gave"):
                    nn = P.epnum(name)
                    if nn is not None and nn in byabs and nn > s1max:
                        e = byabs[nn]
                        row["reason"] += " (absolute no.)"
                        row["note"] = ""
                    else:
                        row["reason"] = "movie/OVA -> routed after import"  # numbered, but no such episode anywhere: a recap/special
                        row["ok"] = True
                else:
                    row["ok"] = False
                if e is None:
                    plan.append(row)
                    continue
            row["se"] = (e["seasonNumber"], e["episodeNumber"])
            row["epId"] = e["id"]
            row["title"] = e["title"]
            plan.append(row)

        # ---- verification -------------------------------------------------------------------------
        runtime = {e["id"]: e.get("runtime") or 0 for e in eps}
        for oe in other_eps.values():
            runtime.update({e["id"]: e.get("runtime") or 0 for e in oe[0]})
        for r in plan:  # durations first, so tiny extras that stole an episode number become 'extra' before duplicate checks
            if not r["epId"]:
                continue
            mins = self.duration_min(self.to_local(r["path"]))
            r["mins"] = round(mins, 1)
            rt = runtime.get(r["epId"], 0)
            if rt and mins and mins < 0.25 * rt and mins < 6:
                r.update(note=f"{mins:.1f} min - treated as extra", reason="extra", epId=None, se=None)
        targets: dict[tuple, list[dict]] = {}
        for r in plan:
            if r["epId"]:
                targets.setdefault((r["seriesId"], r["epId"]), []).append(r)
        for rows in targets.values():
            if len(rows) > 1:
                def core(r):
                    return re.sub(r"\s*\(\d\)|v\d+(?=\.[a-z0-9]+$)", "", os.path.basename(r["rel"]).lower())
                if len({core(r) for r in rows}) == 1:  # same file twice ("- 001.mkv" / "- 001 (1).mkv", v2 variants): keep the largest
                    rows.sort(key=lambda r: -r["size"])
                    for r in rows[1:]:
                        r.update(reason="extra", epId=None, se=None, note="duplicate copy")
                    continue
                for r in rows:
                    r.update(ok=False, note="duplicate target")
                issues.append(f"{len(rows)} files -> S{rows[0]['se'][0]:02d}E{rows[0]['se'][1]:02d}")
        for r in plan:
            if not r["epId"]:
                continue
            mins, rt = r.get("mins") or 0, runtime.get(r["epId"], 0)
            if rt and mins and mins > 2.3 * rt and mins >= MOVIE_MIN:  # a compilation wearing an episode number
                r.update(reason="movie/OVA -> routed after import", epId=None, se=None, note=f"{mins:.0f} min")
                continue
            if rt and mins and (mins < 0.6 * rt or mins > 2.3 * rt) and not r["reason"].startswith(("tvdb special", "anidb special")):
                r.update(ok=False, note=f"duration {mins:.0f} min vs runtime {rt}")
                issues.append(f"{os.path.basename(r['rel'])[:40]}: {r['note']}")
        unmapped = [r for r in plan if r["reason"] not in ("extra", "movie/OVA -> routed after import") and not r["epId"] and not r["reason"].startswith("other series")]
        movieish = [r for r in unmapped if (r.get("mins") or self.duration_min(self.to_local(r["path"]))) >= MOVIE_MIN or P.MOVIEISH.search(r["rel"])]
        hard = [r for r in unmapped if r not in movieish]
        if hard:
            issues.append(f"{len(hard)} unmapped: " + ", ".join(os.path.basename(r["rel"])[:35] for r in hard[:4]))
        for r in movieish:
            r.update(reason="movie/OVA -> routed after import", ok=True)
        others = [r for r in plan if r["reason"].startswith("other series")]
        if others:
            log(f"[{series_title}] {len(others)} files belong to another series not in Sonarr - left out: {others[0]['reason'][14:]}")
        per_season: dict[int, int] = {}
        for r in plan:
            if r["se"] and r["seriesId"] == job["seriesId"]:
                per_season[r["se"][0]] = per_season.get(r["se"][0], 0) + 1
        for sn, c in per_season.items():
            if c > scount.get(sn, 0):
                issues.append(f"season {sn}: {c} files but only {scount.get(sn, 0)} episodes")
        job["folder_resolution"] = {d: ({k: v for k, v in (r or {}).items() if k in ("title", "tvdbid", "season", "offset")} if r else None) for d, r in folder_res.items()}
        return plan, issues
