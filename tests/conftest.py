"""Test doubles: a resolver you script, Sonarr episode lists you declare, durations you assert.

No network, no ffprobe, no Sonarr - the planner is pure logic once its collaborators are injected.
"""

from __future__ import annotations

import pytest

from packarr.planner import Planner


def episodes(seasons: dict[int, int], runtime: int = 24, specials: list[str] | None = None) -> list[dict]:
    """Sonarr-shaped episode list: {season: count}, absolute numbers running through seasons 1..n."""
    out, eid, absn = [], 1000, 0
    for sn in sorted(seasons):
        for en in range(1, seasons[sn] + 1):
            absn += 1
            out.append({"id": eid, "seasonNumber": sn, "episodeNumber": en, "absoluteEpisodeNumber": absn,
                        "title": f"Episode {sn}.{en}", "runtime": runtime, "hasFile": False})
            eid += 1
    for i, t in enumerate(specials or [], 1):
        out.append({"id": eid, "seasonNumber": 0, "episodeNumber": i, "title": t, "runtime": runtime, "hasFile": False})
        eid += 1
    return out


def items(paths: list[str], root: str = "/downloads/pack") -> list[dict]:
    """What Sonarr's /manualimport returns for a folder: relativePath + absolute path."""
    return [{"relativePath": p, "path": f"{root}/{p}", "size": 300_000_000} for p in paths]


class FakeResolver:
    """resolve(title) -> the scripted result whose key is a substring of the cleaned title (first match wins).
    xem: {(tvdb_id, anidb_episode): (season, episode)} - defaults to no XEM data for anything."""

    def __init__(self, table: dict[str, dict | None], xem: dict[tuple[int, int], tuple[int, int]] | None = None):
        self.table = table
        self.xem = xem or {}
        self.calls: list[str] = []

    def resolve(self, title, expect_tvdb=None):
        from packarr.mapping import clean
        title = clean(title)
        self.calls.append(title)
        for k, v in self.table.items():
            if k.lower() in title.lower():
                return v
        return None

    @staticmethod
    def to_tvdb(res, n):
        from packarr.mapping import Resolver
        return Resolver.to_tvdb(res, n)

    def match_special(self, res, filename, series_title=""):
        return None

    def xem_tvdb(self, tvdb_id, anidb_episode):
        return self.xem.get((tvdb_id, anidb_episode))


def entry(tvdb: int, season, offset: int = 0, episodes_: int | None = None, title: str = "", maps=None) -> dict:
    """An anime-lists style resolution."""
    return {"anilist": 1, "anidb": 1, "tvdbid": str(tvdb), "season": str(season), "offset": offset, "maps": maps or [],
            "episodes": episodes_, "format": "TV", "title": title, "year": 2000}


@pytest.fixture
def make_planner():
    def _make(resolver=None, durations: dict[str, float] | None = None, others: dict[int, list[dict]] | None = None):
        durations = durations or {}

        def duration(path):  # keyed by basename; default = a normal episode
            return durations.get(path.rsplit("/", 1)[-1], 24.0)

        return Planner(resolver or FakeResolver({}), lambda sid: (others or {}).get(sid, []), duration, lambda p: p)
    return _make


def job(series: str, tvdb: int, title: str = "", **kw) -> dict:
    return {"series": series, "seriesId": 1, "tvdb": tvdb, "title": title or series, **kw}


def mapping(plan: list[dict]) -> dict[str, tuple | None]:
    return {r["rel"]: (tuple(r["se"]) if r["se"] else None) for r in plan}
