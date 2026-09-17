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

    _series_index: dict[str, str] | None = None

    def series_by_tvdb(self, tvdb_id: int) -> str | None:
        """Jellyfin 12 returns EVERY series for AnyProviderIdEquals, so never trust the filter: index all series by their
        own ProviderIds once and look the id up client-side."""
        if self._series_index is None:
            r = self._get("/Items", IncludeItemTypes="Series", Recursive="true", Fields="ProviderIds", Limit=20000)
            self._series_index = {str((it.get("ProviderIds") or {}).get("Tvdb")): it["Id"] for it in r.get("Items") or [] if (it.get("ProviderIds") or {}).get("Tvdb")}
        return self._series_index.get(str(tvdb_id))

    def episodes_with_audio(self, tvdb_id: int, lang: str, subtitles: list[str] | None = None) -> set[tuple[int, int]] | None:
        """{(season, episode)} whose file carries `lang` audio AND every language in `subtitles` as a subtitle track,
        or None when Jellyfin doesn't know the series."""
        sid = self.series_by_tvdb(tvdb_id)
        if not sid:
            return None
        want = _ALIASES.get(lang, {lang})
        want_subs = [_ALIASES.get(x, {x}) for x in (subtitles or [])]
        r = self._get(f"/Shows/{sid}/Episodes", Fields="MediaStreams")
        out = set()
        for it in r.get("Items") or []:
            s, e = it.get("ParentIndexNumber"), it.get("IndexNumber")
            if s is None or e is None:
                continue
            streams = it.get("MediaStreams") or []
            langs = {(st.get("Language") or "und").lower() for st in streams if st.get("Type") == "Audio"}
            subs = {(st.get("Language") or "und").lower() for st in streams if st.get("Type") == "Subtitle"}
            if (lang == "und" or langs & want) and all(subs & ws for ws in want_subs):
                out.add((int(s), int(e)))
        return out
