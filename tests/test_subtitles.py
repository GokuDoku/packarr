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
