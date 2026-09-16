"""Radarr v3 API: lookup / add / manual-import for the movies that ride along inside anime packs."""

from __future__ import annotations

from . import Arr


class Radarr(Arr):
    def lookup(self, term: str) -> list[dict]:
        code, res = self.call("/movie/lookup", params={"term": term})
        return res if code == 200 and isinstance(res, list) else []

    def by_tmdb(self, tmdb_id: int) -> dict | None:
        code, res = self.call("/movie", params={"tmdbId": tmdb_id})
        return res[0] if code == 200 and res else None

    def add(self, lookup_row: dict, root: str, profile: int) -> tuple[int, dict | str]:
        body = {"title": lookup_row["title"], "tmdbId": lookup_row["tmdbId"], "year": lookup_row.get("year"),
                "qualityProfileId": profile, "rootFolderPath": root, "monitored": True,
                "addOptions": {"searchForMovie": False}, "titleSlug": lookup_row.get("titleSlug"), "images": lookup_row.get("images", [])}
        return self.call("/movie", "POST", body)

    def manual_import(self, path: str, movie_id: int, quality: dict, languages: list[dict]) -> tuple[int, dict | str]:
        return self.call("/command", "POST", {"name": "ManualImport", "importMode": "move", "files": [
            {"path": path, "movieId": movie_id, "quality": quality, "languages": languages, "releaseGroup": "", "indexerFlags": 0}]})
