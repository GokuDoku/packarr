"""Release-name and filename heuristics. Every regex here earned its place on a real pack.

The resolver (mapping.py) is the authority; these are the fallbacks and the sanity checks.
"""

from __future__ import annotations

import os
import re

from .clients.sonarr import QUALITY, quality

# "S03E07", "S3 E07", "s03e07"
SE = re.compile(r"S(\d{1,2})[ ._-]?E(\d{1,3})", re.I)
# "2x11" style; the lookarounds keep 1920x1080 and x265 out
SEX = re.compile(r"(?<![\dx])(\d{1,2})x(\d{2,3})(?![\dp])", re.I)
# an episode number: the LAST standalone 2-3 digit token ("Season 03 - 12" -> 12), bracket tags stripped first
EN = re.compile(r"(?:[ ._-]E?|Ep(?:isode)?[ ._]?|_-_)(\d{2,3})(?!\d)(?=[ ._\-v\[\(]|$)")
# an absolute number for --abs selection: 3-4 digits not glued to a resolution/codec ("1080p", "x265")
EPNUM = re.compile(r"(?<![\dx])(\d{3,4})(?![\dp])")
# season tokens in folder names. `_` is a word character, so "S3_-_07" needs the explicit lookarounds
SEASON_DIR = re.compile(r"(?:Season|Series|Part)[ ._]*(\d{1,2})\b|(?<![A-Za-z0-9])S(\d{1,2})(?![0-9A-Za-z])|(\d{1,2})(?:st|nd|rd|th)[ ._]Season", re.I)
# season tokens in pack TITLES: no "Part N" - a cour is not a season, and episode titles say "(Part 2)"
SEASON_TITLE = re.compile(r"Season[ ._]*(\d{1,2})\b|(?<![A-Za-z0-9])S(\d{1,2})(?![0-9A-Za-z])|(\d{1,2})(?:st|nd|rd|th)[ ._]Season", re.I)

EXTRA_DIR = re.compile(r"(^|/)(NC|NCOP|NCED|Extras?|Bonus.*|Specials?|Menus?|PV|CM|Trailers?|OP|ED|Info.*Samples?|Samples?)(/|$)", re.I)
EXTRA_FILE = re.compile(r"^sample\b|^(OP|ED|END|OP ?& ?END)\s?\d*\b|\bNC(OP|ED)\d*\b|[_ .-](OP|ED)\d{0,2}[_ .(\[]|creditless|\b(OP|ED)\s?\d*\b.*(clean|creditless)|clean (op|ed)|\bnotice\b|\bCM\b|commercial|\bPV\b|trailer|teaser|\bspot\b|documentary|interview|release information|opening \(|\bmenu\b|music video|\bBD-?BOX\b|\bDVD-?BOX\b|\bpromo\b", re.I)
SPECIAL_FILE = re.compile(r"E\d{1,3}\.5\b|\b\d{1,3}\.5\b|S\d{1,2}E00(?!\d)|(?:[ _-])00(?:v\d)?\.(mkv|mp4)$|[\[( ](OVA|OAD|ONA|Special|SP\d*|Recap)[\]) ]", re.I)
SPECIAL_DIR = re.compile(r"(^|/)(OVAs?|OADs?|Specials?|Movies?|Films?)(/|$)", re.I)
MOVIEISH = re.compile(r"\b(OVA|OAD|ONA|Special|Movie|Film|Gekijou|Recap|Picture)\b", re.I)

VIDEO = (".mkv", ".mp4", ".avi")

STRIP = re.compile(r"\[[^\]]*\]|\([^)]*\)|\b(1080p|720p|480p|x265|x264|hevc|av1|10bit|10-bit|flac|aac|opus|bd|bdrip|bluray|web-?dl|dual[ -]?audio|eng[ -]?sub|multi[ -]?subs?)\b", re.I)
_STOP = {"the", "a", "of", "and", "movie", "film", "no", "wo", "ni"}


def is_video(name: str) -> bool:
    return name.lower().endswith(VIDEO)


def epnum(name: str) -> int | None:
    base = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", os.path.splitext(os.path.basename(name))[0])
    ms = EN.findall(base)
    return int(ms[-1]) if ms else None


def season_token(text: str, titles: bool = False) -> int | None:
    m = (SEASON_TITLE if titles else SEASON_DIR).search(text)
    return int(next(g for g in m.groups() if g)) if m else None


def clean_title(name: str) -> str:
    t = os.path.splitext(os.path.basename(name))[0]
    t = STRIP.sub(" ", t)
    t = re.sub(r"[._]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -")


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}  # "Reflection Part I" must match "Part 1"; 'v' is left alone (v2 tags)


def tokens(t: str) -> set[str]:
    return {(w.lstrip("0") or "0") if w.isdigit() else _ROMAN.get(w, w) for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _STOP}


def pack_quality(title: str) -> dict | None:
    """Sonarr guesses per file; the pack title is the better witness for the source. None = probe the file."""
    t = title.lower()
    if "remux" in t:
        return quality(QUALITY["remux-1080p"])
    if re.search(r"\b(bd|bluray|blu-ray|bdrip)\b", t):
        return quality(QUALITY["bluray-1080p"] if "1080" in t else QUALITY["bluray-720p"] if "720" in t else QUALITY["bluray-480p"])
    if re.search(r"\bweb", t):
        return quality(QUALITY["webdl-1080p"] if "1080" in t else QUALITY["webdl-720p"] if "720" in t else QUALITY["webdl-480p"])
    if re.search(r"\bdvd", t):
        return quality(QUALITY["dvd"])
    return None


def quality_from_height(h: int) -> dict:
    return quality(QUALITY["bluray-1080p"] if h >= 1000 else QUALITY["bluray-720p"] if h >= 700 else QUALITY["dvd"])
