"""Which episodes of a series still need the pack?

Default rule: an episode is imported when Sonarr has no file for it, or when its file lacks the wanted audio.
The "lacks the wanted audio" half needs a source of truth about the *file*, not the release name:
  - Jellyfin, when configured: real MediaStreams from the scan.
  - otherwise Sonarr's own per-file `languages` field (only as good as whoever imported the file).
`--all` on a job skips this and replaces every mapped episode (one consistent encode across the series).
"""

from __future__ import annotations

from .clients.jellyfin import Jellyfin
from .clients.sonarr import Sonarr
from .log import log


def needed(sonarr: Sonarr, jellyfin: Jellyfin | None, series_id: int, tvdb_id: int, eps: list[dict], wanted: str,
           subtitles: list[str] | None = None) -> set[int]:
    """Episode ids that should be (re)imported from a pack.
    `subtitles`: languages the file must also carry as subtitle tracks (Jellyfin only - Sonarr knows nothing about subtitles)."""
    need = {e["id"] for e in eps if not e.get("hasFile")}
    have_audio = None
    if jellyfin:
        try:
            have_audio = jellyfin.episodes_with_audio(tvdb_id, wanted, subtitles)
        except Exception as ex:
            log(f"jellyfin lookup failed ({ex}); falling back to Sonarr's language tags")
    if have_audio is not None:
        need |= {e["id"] for e in eps if e.get("hasFile") and (e["seasonNumber"], e["episodeNumber"]) not in have_audio}
        return need
    if subtitles:
        log("subtitle targets need Jellyfin; without it Packarr can only check audio via Sonarr's tags")
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
