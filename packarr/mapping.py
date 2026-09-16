"""Resolve a pack/folder title to a TVDB (season, episode-offset) the way HAMA, Kometa and Shoko do:

    title -> AniList search (public GraphQL)
          -> AniDB id            (Fribb/anime-lists: anilist_id -> anidb_id)
          -> TVDB season/offset  (Anime-Lists/anime-lists: anime-list-full.xml)

Both tables are downloaded into the state dir on first use and refreshed weekly. Resolutions are cached per
cleaned title so a re-plan never hits the network twice.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

from .log import log

ANIME_LISTS_URL = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list-full.xml"
FRIBB_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
ANILIST_URL = "https://graphql.anilist.co"
REFRESH_DAYS = 7

STRIP = re.compile(r"\[[^\]]*\]|\([^)]*\)|\b(1080p|720p|480p|x265|x264|hevc|av1|10bit|10-bit|flac|aac|opus|bd|bdrip|bluray|web-?dl|webrip|dual[ -]?audio|eng[ -]?subs?|multi[ -]?subs?|batch|complete|uncensored|remux)\b", re.I)
_XL = "{http://www.w3.org/XML/1998/namespace}lang"
_STOP = {"the", "a", "of", "and", "no", "wo", "ni", "to"}


def clean(title: str) -> str:
    years = re.findall(r"\((19|20)(\d{2})\)", title)  # keep "(2014)": it separates sequel entries on AniList
    t = STRIP.sub(" ", title)
    t = re.sub(r"[._]", " ", t)
    t = re.sub(r"\bS(?:eason)?\s*0*(\d+)\b", r"Season \1", t, flags=re.I)
    if years:
        t += " " + years[-1][0] + years[-1][1]
    return re.sub(r"\s+", " ", t).strip(" -|+")


def _tok(t: str) -> set[str]:
    return {(w.lstrip("0") or "0") if w.isdigit() else w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _STOP}


class Resolver:
    def __init__(self, state_dir: str, anidb_cache_dir: str = "", user_agent: str = "packarr/0.1 (+https://github.com/GokuDoku/packarr)"):
        self.dir = os.path.join(state_dir, "anime-lists")
        os.makedirs(self.dir, exist_ok=True)
        self.anidb_dir = anidb_cache_dir
        self.ua = user_agent
        self._al: dict[int, dict] | None = None
        self._fr: dict[int, dict] | None = None
        self._cache_path = os.path.join(self.dir, "resolve-cache.json")
        self._cache: dict | None = None

    # ---- data files -------------------------------------------------------------------------
    def _fresh(self, path: str) -> bool:
        return os.path.exists(path) and time.time() - os.path.getmtime(path) < REFRESH_DAYS * 86400

    def _download(self, url: str, path: str) -> None:
        log(f"mapping: downloading {os.path.basename(path)}")
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        data = urllib.request.urlopen(req, timeout=120).read()
        with open(path + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(path + ".tmp", path)

    def refresh(self, force: bool = False) -> None:
        for url, name in ((ANIME_LISTS_URL, "anime-list-full.xml"), (FRIBB_URL, "fribb.json")):
            p = os.path.join(self.dir, name)
            if force or not self._fresh(p):
                try:
                    self._download(url, p)
                except Exception as e:  # keep using the stale copy if there is one
                    if not os.path.exists(p):
                        raise
                    log(f"mapping: refresh of {name} failed ({e}); using the cached copy")
        self._al = self._fr = None

    def _load(self) -> None:
        if self._al is not None:
            return
        self.refresh()
        al: dict[int, dict] = {}
        for a in ET.parse(os.path.join(self.dir, "anime-list-full.xml")).getroot().findall("anime"):
            maps = []
            for m in a.findall("./mapping-list/mapping"):
                pairs = {}
                for pr in (m.text or "").split(";"):
                    if "-" in pr:
                        x, y = pr.split("-", 1)
                        try:
                            pairs[int(x)] = [int(v) for v in y.split("+")]
                        except ValueError:
                            pass
                maps.append({"anidbseason": int(m.get("anidbseason") or 1), "tvdbseason": int(m.get("tvdbseason") or 0),
                             "start": int(m.get("start") or 0), "end": int(m.get("end") or 0), "offset": int(m.get("offset") or 0), "pairs": pairs})
            al[int(a.get("anidbid"))] = {"tvdbid": a.get("tvdbid"), "season": a.get("defaulttvdbseason"),
                                        "offset": int(a.get("episodeoffset") or 0), "name": a.findtext("name") or "", "maps": maps}
        self._al = al
        with open(os.path.join(self.dir, "fribb.json"), encoding="utf-8") as fh:
            self._fr = {r["anilist_id"]: r for r in json.load(fh) if r.get("anilist_id")}
        self._cache = json.load(open(self._cache_path, encoding="utf-8")) if os.path.exists(self._cache_path) else {}

    # ---- AniList ---------------------------------------------------------------------------
    def anilist(self, title: str) -> list[dict]:
        q = "query($s:String){Page(perPage:10){media(search:$s,type:ANIME){id title{romaji english} episodes format seasonYear synonyms}}}"
        req = urllib.request.Request(ANILIST_URL, data=json.dumps({"query": q, "variables": {"s": title}}).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": self.ua})  # no UA -> Cloudflare 403
        for attempt in range(3):
            try:
                return json.load(urllib.request.urlopen(req, timeout=30))["data"]["Page"]["media"]
            except Exception:
                time.sleep(2 * (attempt + 1))
        return []

    def _candidates(self, title: str) -> list[dict]:
        """AniList's search is literal: '(2014)' or 'Part 1' in the query returns nothing. Retry progressively looser."""
        variants = []
        for part in [title] + [x for x in re.split(r"\s[|/]\s", title) if x.strip()]:  # "Romaji Title | English Title" -> each side
            q = clean(part)
            variants += [q, re.sub(r"\s(19|20)\d{2}$", "", q), re.sub(r"\bPart\s*\d+\b", "", q, flags=re.I)]
            variants.append(re.sub(r"\bPart\s*\d+\b", "", variants[-2], flags=re.I))
        for v in dict.fromkeys(re.sub(r"\s+", " ", x).strip() for x in variants):
            if not v:
                continue
            ms = self.anilist(v)
            if ms:
                return ms
        return []

    # ---- public API --------------------------------------------------------------------------
    def resolve(self, title: str, expect_tvdb: int | str | None = None) -> dict | None:
        """-> {anilist, anidb, tvdbid, season, offset, maps, episodes, format, title, year} or None."""
        self._load()
        key = clean(title).lower()
        hit = self._cache.get(key, "miss")
        if isinstance(hit, dict) and "_miss" in hit:  # a miss is cached for a day - AniList and the tables both improve
            hit = None if time.time() - hit["_miss"] < 86400 else "miss"
        if hit != "miss" and (hit is None or expect_tvdb is None or str(hit.get("tvdbid")) == str(expect_tvdb)):
            return hit
        out = None
        for m in self._candidates(title):
            fr = self._fr.get(m["id"])
            if not fr or not fr.get("anidb_id"):
                continue
            al = self._al.get(fr["anidb_id"])
            if not al:
                continue
            res = {"anilist": m["id"], "anidb": fr["anidb_id"], "tvdbid": al["tvdbid"], "season": al["season"], "offset": al["offset"],
                   "maps": al["maps"], "episodes": m.get("episodes"), "format": m.get("format"),
                   "title": m["title"].get("english") or m["title"].get("romaji"), "year": m.get("seasonYear")}
            if expect_tvdb is None or str(al["tvdbid"]) == str(expect_tvdb):
                out = res
                break
            if out is None:
                out = res  # keep the first hit so the caller can see it belongs to another show
        self._cache[key] = out if out is not None else {"_miss": time.time()}
        with open(self._cache_path, "w", encoding="utf-8") as fh:
            json.dump(self._cache, fh, indent=1)
        return out

    @staticmethod
    def to_tvdb(res: dict, n: int) -> tuple:
        """AniDB-style episode n of this entry -> (tvdb_season, tvdb_episode) or ('abs', n+offset)."""
        for m in res["maps"]:
            if m["anidbseason"] != 1:
                continue
            pairs = {int(k): v for k, v in m["pairs"].items()}
            if n in pairs:
                return (m["tvdbseason"], pairs[n][0])
            if m["start"] and m["end"] and m["start"] <= n <= m["end"]:
                return (m["tvdbseason"], n + m["offset"])
        s = res["season"]
        if s in (None, "", "a"):
            return ("abs", n + res["offset"])
        return (int(s), n + res["offset"])

    # ---- AniDB specials (optional offline dump) -----------------------------------------------
    def anidb_episodes(self, anidb_id: int) -> list[dict]:
        """Episodes from a Shoko-style AnimeDoc_<id>.xml dump, or [] when there is no dump."""
        if not self.anidb_dir:
            return []
        f = os.path.join(self.anidb_dir, f"AnimeDoc_{anidb_id}.xml")
        if not os.path.exists(f):
            return []
        out = []
        for e in ET.parse(f).getroot().findall("episodes/episode"):
            ep = e.find("epno")
            t = e.findtext(f'title[@{_XL}="en"]') or e.findtext(f'title[@{_XL}="x-jat"]') or e.findtext("title") or ""
            num = re.sub(r"^[A-Z]", "", ep.text or "")
            try:
                num = int(num)
            except ValueError:
                num = None
            out.append({"epno": ep.text, "type": ep.get("type"), "num": num, "title": t})
        return out

    def match_special(self, res: dict, filename: str, series_title: str = "") -> tuple | None:
        """Match a special/OVA file to an AniDB special by title, then to TVDB S00 via anime-lists."""
        sp = [e for e in self.anidb_episodes(res["anidb"]) if e["type"] == "2" and e["num"]]
        if not sp:
            return None
        ft = _tok(re.sub(r"\.[a-z0-9]+$", "", os.path.basename(filename))) - _tok(series_title) - _tok(res.get("title", ""))
        best = None
        for e in sp:
            et = _tok(e["title"])
            if not et:
                continue
            score = len(et & ft) / len(et)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, e)
        if not best:
            return None
        e = best[1]
        for m in res["maps"]:
            pairs = {int(k): v for k, v in m["pairs"].items()}
            if m["anidbseason"] == 0 and e["num"] in pairs:
                return (m["tvdbseason"], pairs[e["num"]][0], e["title"])
        return None
