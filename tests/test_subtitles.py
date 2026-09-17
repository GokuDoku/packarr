"""Subtitle targeting: an episode is only 'good' when it carries the wanted subtitle tracks too."""

from __future__ import annotations

from packarr.config import Search
from packarr.probe import has_language
from packarr.search import score


def test_has_language_accepts_iso_and_full_names():
    assert has_language(["eng:English (Full)", "jpn"], "eng")
    assert has_language(["English", "Japanese"], "jpn")
    assert has_language(["en"], "eng")
    assert not has_language(["eng"], "jpn")
    assert not has_language([], "eng")


def test_search_rewards_advertised_subtitles():
    cfg = Search()
    with_subs = {"title": "[Judas] Show (Season 1) [BD 1080p][HEVC x265 10bit][Dual-Audio][Multi-Subs]", "size": 5e9, "seeders": 40}
    without = {"title": "[Judas] Show (Season 1) [BD 1080p][HEVC x265 10bit][Dual-Audio]", "size": 5e9, "seeders": 40}
    assert score(with_subs, cfg)[0] > score(without, cfg)[0]
    assert "subs" in score(with_subs, cfg)[1]


def test_jellyfin_subtitle_filter(monkeypatch):
    from packarr.clients.jellyfin import Jellyfin
    jf = Jellyfin("http://jf", "k")
    items = {"Items": [
        {"ParentIndexNumber": 1, "IndexNumber": 1, "MediaStreams": [{"Type": "Audio", "Language": "eng"}, {"Type": "Subtitle", "Language": "eng"}, {"Type": "Subtitle", "Language": "jpn"}]},
        {"ParentIndexNumber": 1, "IndexNumber": 2, "MediaStreams": [{"Type": "Audio", "Language": "eng"}, {"Type": "Subtitle", "Language": "eng"}]},
        {"ParentIndexNumber": 1, "IndexNumber": 3, "MediaStreams": [{"Type": "Audio", "Language": "jpn"}, {"Type": "Subtitle", "Language": "English"}, {"Type": "Subtitle", "Language": "Japanese"}]},
    ]}
    monkeypatch.setattr(jf, "series_by_tvdb", lambda tvdb: "abc")
    monkeypatch.setattr(jf, "_get", lambda path, **params: items)
    assert jf.episodes_with_audio(1, "eng") == {(1, 1), (1, 2)}
    assert jf.episodes_with_audio(1, "eng", ["eng", "jpn"]) == {(1, 1)}  # E2 lacks Japanese subs, E3 lacks English audio
    assert jf.episodes_with_audio(1, "jpn", ["eng"]) == {(1, 3)}  # full-name tags count


def test_jellyfin_audio_wildcard_for_subtitle_only_checks(monkeypatch):
    from packarr.clients.jellyfin import Jellyfin
    jf = Jellyfin("http://jf", "k")
    items = {"Items": [
        {"ParentIndexNumber": 1, "IndexNumber": 1, "MediaStreams": [{"Type": "Audio", "Language": "jpn"}, {"Type": "Subtitle", "Language": "eng"}]},
        {"ParentIndexNumber": 1, "IndexNumber": 2, "MediaStreams": [{"Type": "Audio", "Language": "jpn"}]},
    ]}
    monkeypatch.setattr(jf, "series_by_tvdb", lambda tvdb: "abc")
    monkeypatch.setattr(jf, "_get", lambda path, **params: items)
    assert jf.episodes_with_audio(1, "und", ["eng"]) == {(1, 1)}


def test_bazarr_language_codes():
    from packarr.clients.bazarr import ISO1
    assert ISO1["eng"] == "en" and ISO1["jpn"] == "ja"


def test_file_inspection_beats_jellyfin(monkeypatch, tmp_path):
    """The file is the truth: with the library reachable, gaps come from ffprobe (cached), not from Jellyfin."""
    from packarr import gaps, probe
    f1, f2 = tmp_path / "S01E01.mkv", tmp_path / "S01E02.mkv"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    fake = {str(f1): {"audio": ["eng", "jpn"], "subs": ["eng", "jpn"], "height": 1080},
            str(f2): {"audio": ["eng", "jpn"], "subs": ["eng"], "height": 1080}}
    monkeypatch.setattr(probe, "streams", lambda p: fake[p])
    cache = probe.ProbeCache(str(tmp_path / "cache.json"))

    class FakeSonarr:
        def get(self, path, **params):
            return [{"id": 11, "path": "/tv/S01E01.mkv"}, {"id": 12, "path": "/tv/S01E02.mkv"}]
    eps = [{"id": 1, "seasonNumber": 1, "episodeNumber": 1, "episodeFileId": 11, "hasFile": True},
           {"id": 2, "seasonNumber": 1, "episodeNumber": 2, "episodeFileId": 12, "hasFile": True}]
    to_local = lambda p: str(tmp_path / p.split("/")[-1])  # noqa: E731

    class ExplodingJellyfin:
        def episodes_with_audio(self, *a, **k):
            raise AssertionError("Jellyfin must not be consulted when the files are reachable")
    good = gaps.good_episodes(FakeSonarr(), ExplodingJellyfin(), 1, 1, eps, "eng", ["eng", "jpn"], to_local, cache)
    assert good == {(1, 1)}  # E2 has no Japanese subtitle track
    assert gaps.needed(FakeSonarr(), ExplodingJellyfin(), 1, 1, eps, "eng", ["eng", "jpn"], to_local, cache) == {2}
    # second pass hits the cache: streams() must not be called again
    monkeypatch.setattr(probe, "streams", lambda p: (_ for _ in ()).throw(AssertionError("not cached")))
    cache2 = probe.ProbeCache(str(tmp_path / "cache.json"))
    assert gaps.good_episodes(FakeSonarr(), None, 1, 1, eps, "eng", ["jpn"], to_local, cache2) == {(1, 1)}


def test_library_path_maps():
    from packarr.config import Config, Paths
    from packarr.pipeline import Pipeline
    p = Pipeline.__new__(Pipeline)
    p.cfg = Config(paths=Paths(library_maps={"/tv": "/media/tv", "/anime": "/mnt/anime"}))
    assert p.library_local("/anime/Show/Season 1/ep.mkv") == "/mnt/anime/Show/Season 1/ep.mkv"
    assert p.library_local("/tv/Show/ep.mkv") == "/media/tv/Show/ep.mkv"
    assert p.library_local("/other/ep.mkv") == "/other/ep.mkv"


def test_jellyfin_series_lookup_matches_provider_id_client_side(monkeypatch):
    """Jellyfin 12 ignores AnyProviderIdEquals and returns every series - the first item was '.hack' for any id."""
    from packarr.clients.jellyfin import Jellyfin
    jf = Jellyfin("http://jf", "k")
    items = {"Items": [{"Id": "hack", "ProviderIds": {"Tvdb": "79099"}}, {"Id": "harem", "ProviderIds": {"Tvdb": "393301"}}, {"Id": "noid", "ProviderIds": {}}]}
    monkeypatch.setattr(jf, "_get", lambda path, **params: items)
    assert jf.series_by_tvdb(393301) == "harem"
    assert jf.series_by_tvdb(79099) == "hack"
    assert jf.series_by_tvdb(1) is None


def test_sidecar_subtitles_count(tmp_path):
    from packarr.probe import sidecar_subs
    v = tmp_path / "Show - S01E01 - Title WEBDL-1080p.mkv"
    v.write_bytes(b"x")
    for n in ("Show - S01E01 - Title WEBDL-1080p.eng.hi.srt", "Show - S01E01 - Title WEBDL-1080p.ja.ass", "Show - S01E01 - Title WEBDL-1080p.srt",
              "Show - S01E02 - Other.eng.srt", "Show - S01E01 - Title WEBDL-1080p-thumb.jpg"):
        (tmp_path / n).write_bytes(b"s")
    assert sorted(sidecar_subs(str(v))) == ["eng", "jpn", "und"]
