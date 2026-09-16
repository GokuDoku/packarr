"""Sonarr v3 API: the parts Packarr needs, with the traps documented where they bit."""

from __future__ import annotations

from . import Arr

# Sonarr's built-in language ids (Languages table); names are what the API returns.
LANGUAGE_IDS = {"english": 1, "french": 2, "spanish": 3, "german": 4, "italian": 5, "danish": 6, "dutch": 7, "japanese": 8,
                "icelandic": 9, "chinese": 10, "russian": 11, "polish": 12, "vietnamese": 13, "swedish": 14, "norwegian": 15,
                "finnish": 16, "turkish": 17, "portuguese": 18, "flemish": 19, "greek": 20, "korean": 21, "hungarian": 22}

# Quality ids Packarr assigns from the pack title; Sonarr guesses HDTV from a bare "[Group] Show - 01.mkv".
QUALITY = {"bluray-1080p": 7, "bluray-720p": 6, "bluray-480p": 13, "webdl-1080p": 3, "webdl-720p": 5, "webdl-480p": 8,
           "dvd": 2, "remux-1080p": 20, "hdtv-1080p": 9}


def languages(names: list[str]) -> list[dict]:
    return [{"id": LANGUAGE_IDS[n.lower()], "name": n.capitalize()} for n in names if n.lower() in LANGUAGE_IDS]


def quality(qid: int) -> dict:
    return {"quality": {"id": qid, "name": ""}, "revision": {"version": 1, "real": 0, "isRepack": False}}


class Sonarr(Arr):
    def series(self) -> list[dict]:
        return self.get("/series")

    def series_one(self, sid: int) -> dict:
        return self.get(f"/series/{sid}")

    def episodes(self, sid: int, season: int | None = None) -> list[dict]:
        params = {"seriesId": sid}
        if season is not None:
            params["seasonNumber"] = season
        return self.get("/episode", **params)

    def episodes_sharing_file(self, file_id: int) -> list[dict]:
        return self.get("/episode", episodeFileId=file_id)

    def manual_import_list(self, folder: str) -> list[dict]:
        """Files Sonarr can see in `folder` (in Sonarr's own path namespace).

        NEVER pass seriesId here: with it, Sonarr ignores `folder` and returns the series' existing
        library files instead - the first two imports ever run through this tool "succeeded" by
        importing a library onto itself.
        """
        return self.get("/manualimport", folder=folder, filterExistingFiles="false")

    def manual_import(self, files: list[dict], mode: str = "move") -> tuple[int, dict | str]:
        """Each file: {path, seriesId, episodeIds, quality, languages, releaseGroup, indexerFlags}.
        One missing file aborts the WHOLE command, so never delete from the folder while one is queued."""
        return self.call("/command", "POST", {"name": "ManualImport", "files": files, "importMode": mode})

    def command(self, cid: int) -> dict:
        return self.get(f"/command/{cid}")

    def commands(self) -> list[dict]:
        return self.get("/command")

    def search_series(self, sid: int) -> tuple[int, dict | str]:
        return self.call("/command", "POST", {"name": "SeriesSearch", "seriesId": sid})

    def cancel_searches(self, sid: int) -> int:
        """Cancel queued SeriesSearch/MissingEpisodeSearch for a series so usenet singles don't race a pack."""
        n = 0
        for c in self.commands():
            if c.get("status") in ("queued", "started") and c.get("name") in ("SeriesSearch", "MissingEpisodeSearch", "SeasonSearch") \
                    and (c.get("body") or {}).get("seriesId") == sid:
                self.call(f"/command/{c['id']}", "DELETE")
                n += 1
        return n
