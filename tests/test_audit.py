"""Pipeline.audit(): a file's SxxEyy tag vs. what Sonarr currently thinks that episode is. No network -
a FakeSonarr stands in for the real client, exposing exactly the three methods audit() calls."""

from __future__ import annotations

import sys
import tempfile

sys.path.insert(0, ".")

from packarr import config
from packarr.pipeline import Pipeline


class FakeSonarr:
    def __init__(self, all_series, episodes_by_series):
        self._series = all_series
        self._episodes = episodes_by_series

    def series(self):
        return self._series

    def series_one(self, sid):
        return next(s for s in self._series if s["id"] == sid)

    def episodes(self, sid, season=None):
        eps = self._episodes.get(sid, [])
        return [e for e in eps if season is None or e["seasonNumber"] == season]


def _pipeline():
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write("sonarr: { url: http://sonarr.invalid:8989, api_key: fake }\n")
        path = f.name
    cfg = config.load(path)
    import os
    os.unlink(path)
    return Pipeline(cfg)


def ep(eid, season, episode, rel=None, has_file=True):
    e = {"id": eid, "seasonNumber": season, "episodeNumber": episode, "hasFile": has_file}
    if has_file:
        e["episodeFile"] = {"relativePath": rel} if rel else None
        if rel is None:
            e["hasFile"] = False
    return e


def test_matching_file_is_not_flagged():
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show"}]
    eps = {1: [ep(100, 1, 7, "Season 01/Show - S01E07 - Title.mkv")]}
    pipe.sonarr = FakeSonarr(series, eps)
    assert pipe.audit() == []


def test_renumbered_episode_is_flagged():
    """The file was imported as S01E07; TVDB later merged seasons and Sonarr now calls it S02E01."""
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show"}]
    eps = {1: [ep(100, 2, 1, "Season 01/Show - S01E07 - Title.mkv")]}
    pipe.sonarr = FakeSonarr(series, eps)
    rows = pipe.audit()
    assert len(rows) == 1
    r = rows[0]
    assert r["seriesId"] == 1 and r["series"] == "Show" and r["episodeId"] == 100
    assert r["fileSE"] == (1, 7)
    assert r["sonarrSE"] == (2, 1)


def test_episode_without_a_file_is_skipped():
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show"}]
    eps = {1: [ep(100, 1, 1, has_file=False)]}
    pipe.sonarr = FakeSonarr(series, eps)
    assert pipe.audit() == []


def test_filename_with_no_sxxeyy_is_skipped_not_crashed():
    """Custom naming formats, specials named without SxxEyy - nothing to compare against, so skip quietly."""
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show"}]
    eps = {1: [ep(100, 0, 1, "Specials/Show - Behind the Scenes.mkv")]}
    pipe.sonarr = FakeSonarr(series, eps)
    assert pipe.audit() == []


def test_series_filter_restricts_to_one_series():
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show A"}, {"id": 2, "title": "Show B"}]
    eps = {
        1: [ep(100, 2, 1, "Show A - S01E07.mkv")],  # mismatch
        2: [ep(200, 5, 5, "Show B - S01E01.mkv")],  # also a mismatch, but excluded by --series
    }
    pipe.sonarr = FakeSonarr(series, eps)
    rows = pipe.audit(series_id=1)
    assert len(rows) == 1
    assert rows[0]["seriesId"] == 1


def test_multiple_mismatches_across_series_all_reported():
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show A"}, {"id": 2, "title": "Show B"}]
    eps = {
        1: [ep(100, 2, 1, "Show A - S01E07.mkv"), ep(101, 1, 1, "Show A - S01E01.mkv")],  # one mismatch, one clean
        2: [ep(200, 5, 5, "Show B - S01E01.mkv")],  # mismatch
    }
    pipe.sonarr = FakeSonarr(series, eps)
    rows = pipe.audit()
    assert len(rows) == 2
    assert {r["episodeId"] for r in rows} == {100, 200}


def test_alternate_sxxeyy_spellings_in_filename_are_still_recognized():
    """The SE regex accepts s01.e07 / S01-E07 style separators, not just the canonical S01E07."""
    pipe = _pipeline()
    series = [{"id": 1, "title": "Show"}]
    eps = {1: [ep(100, 2, 1, "Show.s01.e07.mkv")]}
    pipe.sonarr = FakeSonarr(series, eps)
    rows = pipe.audit()
    assert rows[0]["fileSE"] == (1, 7)
