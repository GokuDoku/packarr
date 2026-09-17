"""Which episodes of a series still need the pack - and which files lack a subtitle language?

Order of truth about a file's tracks:
  1. the file itself, via ffprobe (when Packarr can reach the library; results cached by path/size/mtime)
  2. Jellyfin's scanned MediaStreams (when configured)
  3. Sonarr's per-file `languages` tag - only as good as whoever imported the file, and it knows nothing about subtitles
An episode counts as "good" when it has the wanted audio AND every wanted subtitle language.
`--all` on a job skips this and replaces every mapped episode (one consistent encode across the series).
"""

from __future__ import annotations

from collections.abc import Callable

from .clients.jellyfin import Jellyfin
from .clients.sonarr import Sonarr
from .log import log
from .probe import ProbeCache, has_language


def file_tracks(sonarr: Sonarr, series_id: int, eps: list[dict], to_local: Callable[[str], str], cache: ProbeCache) -> dict[tuple[int, int], dict] | None:
    """{(season, episode): {audio, subs}} from ffprobe on the library files, or None if none are reachable."""
    try:
        files = {f["id"]: f for f in sonarr.get("/episodefile", seriesId=series_id)}
    except Exception:
        return None
    out, reachable = {}, 0
    for e in eps:
        f = files.get(e.get("episodeFileId"))
        if not f:
            continue
        info = cache.inspect(to_local(f["path"]))
        if info is None:
            continue
        reachable += 1
        out[(e["seasonNumber"], e["episodeNumber"])] = info
    cache.save()
    return out if reachable else None


def good_episodes(sonarr: Sonarr, jellyfin: Jellyfin | None, series_id: int, tvdb_id: int, eps: list[dict], wanted: str,
                  subtitles: list[str] | None, to_local: Callable[[str], str] | None = None, cache: ProbeCache | None = None) -> set[tuple[int, int]] | None:
    """{(season, episode)} whose file carries the wanted audio and every wanted subtitle language. None = no track data at all."""
    subtitles = subtitles or []
    if to_local and cache:
        tracks = file_tracks(sonarr, series_id, eps, to_local, cache)
        if tracks:
            return {k for k, t in tracks.items()
                    if (wanted == "und" or has_language(t["audio"], wanted)) and all(has_language(t["subs"], s) for s in subtitles)}
    if jellyfin:
        try:
            have = jellyfin.episodes_with_audio(tvdb_id, wanted, subtitles)
            if have is not None:
                return have
        except Exception as ex:
            log(f"jellyfin lookup failed ({ex}); falling back to Sonarr's language tags")
    return None


def needed(sonarr: Sonarr, jellyfin: Jellyfin | None, series_id: int, tvdb_id: int, eps: list[dict], wanted: str,
           subtitles: list[str] | None = None, to_local: Callable[[str], str] | None = None, cache: ProbeCache | None = None) -> set[int]:
    """Episode ids that should be (re)imported from a pack."""
    need = {e["id"] for e in eps if not e.get("hasFile")}
    good = good_episodes(sonarr, jellyfin, series_id, tvdb_id, eps, wanted, subtitles, to_local, cache)
    if good is not None:
        need |= {e["id"] for e in eps if e.get("hasFile") and (e["seasonNumber"], e["episodeNumber"]) not in good}
        return need
    if subtitles:
        log("subtitle targets need the library mounted (paths.library_maps) or Jellyfin; Sonarr's tags only cover audio")
    wanted_name = {"eng": "english", "jpn": "japanese"}.get(wanted, wanted)
    try:
        files = {f["id"]: f for f in sonarr.get("/episodefile", seriesId=series_id)}
    except Exception:
        return need
    for e in eps:
        f = files.get(e.get("episodeFileId"))
        if f and not any(lang["name"].lower() == wanted_name for lang in f.get("languages", [])):
            need.add(e["id"])
    return need
