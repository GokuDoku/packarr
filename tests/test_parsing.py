from __future__ import annotations

import pytest

from packarr import parsing as P
from packarr.config import Search
from packarr.mapping import clean
from packarr.search import _episodes_in_title, score


@pytest.mark.parametrize("name,n", [
    ("[Judas] Show - S01E07.mkv", None),  # SxxEyy is parsed by SE, not epnum
    ("Show Season 03 - 12 [1080p].mkv", 12),
    ("[Anime Time] Monster - 74.mkv", 74),
    ("[DB]Attack on Titan S3_-_07_(Dual Audio).mkv", 7),
    ("Show - 001.mkv", 1),
    ("Show - OP1.mkv", None),
])
def test_epnum(name, n):
    assert P.epnum(name) == n


@pytest.mark.parametrize("text,season", [
    ("Shingeki no Kyojin Season 3 Part 1", 3),
    ("[DB]Attack on Titan S3_-_07_(Dual Audio).mkv", 3),
    ("S01E05 x265 BD1080p", None),  # SxxEyy is not a bare season token
    ("Foo 2nd Season - 07", 2),
    ("Bluray-1080p x265 10bit", None),
])
def test_season_token(text, season):
    assert P.season_token(text) == season


def test_title_season_ignores_part_and_episode_titles():
    assert P.season_token("Yu-Gi-Oh! - The Dark One Cometh (Part 4)", titles=True) is None
    assert P.season_token("[EMBER] Attack on Titan (Season 4 | Part 02)", titles=True) == 4


@pytest.mark.parametrize("name,se", [
    ("Rurouni Kenshin - 2x11 - The Creator.mkv", (2, 11)),
    ("Bar - 12x03 Title 1080p.mkv", (12, 3)),
    ("Foo 1920x1080 x265 - 07.mkv", None),
])
def test_season_x_episode(name, se):
    m = P.SE.search(P.SEX.sub(r"S\1E\2", name))
    assert (m and (int(m.group(1)), int(m.group(2)))) == se


def test_pack_quality_from_title():
    assert P.pack_quality("[Judas] Show (Season 1) [BD 1080p][HEVC x265 10bit]")["quality"]["id"] == 7
    assert P.pack_quality("[EMBER] Show [1080p] [Dual Audio HEVC WEBRip]")["quality"]["id"] == 3
    assert P.pack_quality("[Anime Time] Monster [DVD][480p]")["quality"]["id"] == 2
    assert P.pack_quality("[Group] Show 01-49 [1080p] [DUAL-AUDIO]") is None  # no source tag -> probe the file


def test_clean_keeps_the_year():
    assert clean("[Grp] Sailor Moon Crystal (2014) [BD 1080p x265 Dual Audio]") == "Sailor Moon Crystal 2014"


def test_episode_count_from_title():
    assert _episodes_in_title("[AnimeRG] Mobile Suit Gundam Wing (Complete Series) 01-49 [1080p]") == 49
    assert _episodes_in_title("[Anime Time] One Piece 0001 ~ 1071") == 1071


def test_search_score_prefers_real_dual_over_optional_dual():
    cfg = Search()
    good = {"title": "[bonkai77] Escaflowne [Dual Audio] [BD 1080p x265] 01-26", "size": 11.8e9, "seeders": 40}
    trap = {"title": "[Trix] Escaflowne [Optional Dual Audio] [AV1] 01-26", "size": 6e9, "seeders": 80}
    assert score(good, cfg)[0] > score(trap, cfg)[0]
    assert "avoid-tag" in score(trap, cfg)[1]


def test_roman_numerals_match_arabic_in_titles():
    assert P.tokens("Rurouni.Kenshin.Reflection.Part.I.2001") & P.tokens("Reflection: After So Many Years Have Lapsed, Part 1") >= {"reflection", "part", "1"}
