"""Where do the films and OVAs inside a pack go?

  1. a Sonarr S00 special whose TVDB title matches the file            -> Sonarr ManualImport onto that special
  2. otherwise Radarr: look the title up, add the movie if it is new     -> Radarr ManualImport
Both steps refuse to duplicate: a film Radarr already holds is not re-imported into S00, and a special whose
"file" is really a season episode's file (Sonarr can share one file between two episodes) is left alone,
because importing onto it would delete the season episode.
"""

from __future__ import annotations

import re
import urllib.parse

from . import parsing as P
from .clients.radarr import Radarr
from .clients.sonarr import Sonarr
from .log import log


class Router:
    def __init__(self, sonarr: Sonarr, radarr: Radarr | None, anime_root: str, movie_profile: int):
        self.sonarr = sonarr
        self.radarr = radarr
        self.root = anime_root
        self.profile = movie_profile

    def route(self, job: dict, sonarr_path: str, specials: list[dict], quality: dict, languages: list[dict]) -> str | None:
        """Returns a log line, or None when the file could not be placed (the torrent is then kept for review)."""
        ct = P.clean_title(sonarr_path)
        ft = P.tokens(ct) - P.tokens(job["series"])
        fcore = ft - {"ova", "oad", "ona", "special", "sp", "movie", "film", "bd", "v2", "v3"}
        best = None
        for e in specials:
            et = P.tokens(e["title"]) - P.tokens(job["series"])
            if not et:  # special titled exactly like the series: match on year if the file carries one
                yr = re.search(r"\b(19|20)\d{2}\b", sonarr_path.rsplit("/", 1)[-1])
                ad = (e.get("airDate") or "")[:4]
                if yr and ad.isdigit() and abs(int(yr.group(0)) - int(ad)) <= 1 and best is None:
                    best = (1, e)
                continue
            hit = len(et & ft) / len(et) >= 0.6 or (len(fcore) >= 2 and fcore <= et)  # title words in file, or all file words in title
            if hit and (best is None or len(et & ft) > best[0]):
                best = (len(et & ft), e)
        if best:
            e = best[1]
            held = self._radarr_has(e["title"])
            if held:
                return f"'{e['title'][:30]}' already in Radarr as '{held[:30]}' - skipped"
            if e.get("hasFile"):
                shared = self.sonarr.episodes_sharing_file(e.get("episodeFileId"))
                if any(x["seasonNumber"] != 0 for x in shared or []):
                    return None  # the special's file is a season episode's file - never import over it
                return f"S00E{e['episodeNumber']:02d} '{e['title'][:30]}' already has a file - skipped"
            code, cmd = self.sonarr.manual_import([{"path": sonarr_path, "seriesId": job["seriesId"], "episodeIds": [e["id"]],
                                                    "quality": quality, "languages": languages, "releaseGroup": "", "indexerFlags": 0}])
            return f"-> Sonarr S00E{e['episodeNumber']:02d} '{e['title'][:30]}' (cmd {cmd['id'] if code in (200, 201) else code})"
        if not self.radarr or not self.root or not self.profile:
            return None
        res = self.radarr.lookup(ct)
        cand = [m for m in res[:5] if P.tokens(m["title"]) & P.tokens(job["series"]) or len(P.tokens(m["title"]) & ft) >= max(1, len(P.tokens(m["title"])) // 2)]
        if not cand:
            return None
        m = cand[0]
        mv = self.radarr.by_tmdb(m["tmdbId"])
        if mv:
            if mv.get("hasFile"):
                return f"Radarr '{mv['title'][:35]}' ({mv['year']}) already has a file - skipped"
        else:
            code, mv = self.radarr.add(m, self.root, self.profile)
            if code not in (200, 201):
                return f"Radarr add failed for '{m['title'][:35]}': {str(mv)[:80]}"
            log(f"[{job['series']}] added to Radarr: {mv['title']} ({mv.get('year')})")
        code, cmd = self.radarr.manual_import(sonarr_path, mv["id"], quality, languages)
        return f"-> Radarr '{mv['title'][:35]}' ({mv.get('year')}) (cmd {cmd['id'] if code in (200, 201) else code})"

    def _radarr_has(self, title: str) -> str | None:
        if not self.radarr:
            return None
        try:
            for m in self.radarr.lookup(urllib.parse.unquote(title))[:3]:
                shared = P.tokens(m["title"]) & P.tokens(title)
                if shared and len(shared) >= max(1, len(P.tokens(title)) // 2):
                    have = self.radarr.by_tmdb(m["tmdbId"])
                    return have["title"] if have and have.get("hasFile") else None
        except Exception:
            return None
        return None
