"""Optional Jellyfin lookup: which episodes of a series already have the wanted audio language.

Sonarr's per-file language field is whatever the importer *said* it was, not what the file contains,
so when Jellyfin is configured Packarr asks it for the real audio streams instead.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

# Jellyfin stores whatever the muxer wrote. Most groups tag ISO 639-2, a few write the full name.
_ALIASES = {"eng": {"eng", "en", "english"}, "jpn": {"jpn", "ja", "japanese"}}


class Jellyfin:
    def __init__(self, url: str, api_key: str, timeout: int = 120):
        self.base = url.rstrip("/")
        self.key = api_key
        self.timeout = timeout

    def _get(self, path: str, **params):
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f'MediaBrowser Token="{self.key}", Client="packarr", Device="packarr", DeviceId="packarr", Version="1"'})
        with urllib.request.urlopen(req, timeout=self.timeout) as x:
            return json.loads(x.read())

    def series_by_tvdb(self, tvdb_id: int) -> str | None:
        r = self._get("/Items", IncludeItemTypes="Series", Recursive="true", AnyProviderIdEquals=f"Tvdb.{tvdb_id}", Fields="ProviderIds")
        items = r.get("Items") or []
        return items[0]["Id"] if items else None

    def episodes_with_audio(self, tvdb_id: int, lang: str) -> set[tuple[int, int]] | None:
        """{(season, episode)} whose file carries `lang` audio, or None when Jellyfin doesn't know the series."""
        sid = self.series_by_tvdb(tvdb_id)
        if not sid:
            return None
        want = _ALIASES.get(lang, {lang})
        r = self._get(f"/Shows/{sid}/Episodes", Fields="MediaStreams")
        out = set()
        for it in r.get("Items") or []:
            s, e = it.get("ParentIndexNumber"), it.get("IndexNumber")
            if s is None or e is None:
                continue
            langs = {(st.get("Language") or "und").lower() for st in it.get("MediaStreams") or [] if st.get("Type") == "Audio"}
            if langs & want:
                out.add((int(s), int(e)))
        return out
