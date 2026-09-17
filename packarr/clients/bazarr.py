"""Bazarr: fetch subtitles for an episode that already has its file - no re-download.

Bazarr keys everything on Sonarr's series/episode ids and speaks ISO 639-1 language codes.
`PATCH /api/episodes/subtitles` searches the configured providers and downloads the best match for one language.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

# ISO 639-2 (what Packarr and Jellyfin use) -> ISO 639-1 (what Bazarr wants)
ISO1 = {"eng": "en", "jpn": "ja", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt",
        "rus": "ru", "chi": "zh", "zho": "zh", "kor": "ko", "ara": "ar", "dut": "nl", "nld": "nl", "pol": "pl", "swe": "sv"}


class Bazarr:
    def __init__(self, url: str, api_key: str, timeout: int = 300):
        self.base = url.rstrip("/") + "/api"
        self.key = api_key
        self.timeout = timeout

    def _call(self, path: str, method: str = "GET", params: dict | None = None):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers={"X-API-KEY": self.key, "Accept": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as x:
                raw = x.read()
                return x.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")[:300]
        except (urllib.error.URLError, OSError) as e:  # Bazarr down / restarting: report, never crash the caller
            return 0, f"bazarr unreachable: {e}"

    def ok(self) -> bool:
        code, _ = self._call("/system/status")
        return code == 200

    def series_profile(self, series_id: int) -> int | None:
        code, body = self._call("/series", params={"seriesid[]": series_id})
        if code == 200 and isinstance(body, dict) and body.get("data"):
            return body["data"][0].get("profileId")
        return None

    def set_profile(self, series_id: int, profile_id: int) -> bool:
        """Assign a Bazarr language profile to a series (Bazarr ids == Sonarr ids)."""
        if self.series_profile(series_id) == profile_id:
            return True
        url = self.base + "/series"
        data = urllib.parse.urlencode([("seriesid", series_id), ("profileid", profile_id)]).encode()
        req = urllib.request.Request(url, data=data, headers={"X-API-KEY": self.key, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as x:
                return x.status in (200, 204)
        except (urllib.error.URLError, OSError):
            return False

    def download(self, series_id: int, episode_id: int, lang: str, forced: bool = False, hi: bool = False) -> tuple[bool, str]:
        """Search providers and download the best subtitle in `lang` (ISO 639-2 or 639-1) for one episode."""
        code, body = self._call("/episodes/subtitles", "PATCH", {"seriesid": series_id, "episodeid": episode_id,
                                                                  "language": ISO1.get(lang, lang), "forced": str(forced).lower(), "hi": str(hi).lower()})
        if code in (200, 204):
            return True, "downloaded"
        return False, f"HTTP {code}: {body}"
