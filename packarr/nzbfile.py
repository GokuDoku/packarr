"""Minimal NZB reader + direct .nzb URL fetch - the SABnzbd analog of torrentfile.py.

An NZB is plain XML, so no custom decoder is needed the way bencode needed one; the one real trick is the
filename, which isn't a first-class field - by convention posters put it in double quotes inside the
`subject` attribute (e.g. `... [04/34] - "Show - 07.mkv" yEnc (1/41)"`), verified against SABnzbd's own
parser and independent third-party ones. A file's size is the sum of its segments' `bytes`.

Namespace-agnostic on purpose: NZB's namespace URI (http://www.newzbin.com/DTD/2003/nzb) is usually present
but not load-bearing here, so this matches by local tag name instead of getting it byte-exact.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET

_UA = "Mozilla/5.0 (X11; Linux x86_64) packarr/0.1"
_QUOTED = re.compile(r'"([^"]+)"')
_PART_COUNTER = re.compile(r"\s*[\[(]\d+/\d+[)\]]\s*$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _filename(subject: str, fallback: str) -> str:
    m = _QUOTED.search(subject or "")
    if m:
        return m.group(1).strip()
    return _PART_COUNTER.sub("", subject or "").strip() or fallback


def files(nzb: bytes) -> list[tuple[int, str, int]]:
    """[(index, filename, length)] for every <file> in the NZB, in document order (torrentfile.files()'s shape)."""
    root = ET.fromstring(nzb)
    out = []
    i = 0
    for el in root:
        if _local(el.tag) != "file":
            continue
        size = 0
        for child in el:
            if _local(child.tag) != "segments":
                continue
            for seg in child:
                if _local(seg.tag) == "segment":
                    size += int(seg.get("bytes") or 0)
        out.append((i, _filename(el.get("subject", ""), f"file{i}"), size))
        i += 1
    return out


def fetch(url: str) -> bytes | None:
    """Download the raw .nzb from a direct URL. None when unavailable or not actually an NZB."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        data = urllib.request.urlopen(req, timeout=60).read()
        return data if b"<nzb" in data[:1000].lower() else None
    except Exception:
        return None
