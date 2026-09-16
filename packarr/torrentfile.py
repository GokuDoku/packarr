"""Minimal bencode reader + Nyaa .torrent fetch.

Why .torrent files instead of magnets: metadata exchange over a VPN with no inbound port can sit at
"0 B / 0 peers" forever, while the same torrent added from its .torrent file connects in seconds.
It also lets Packarr mark unwanted files *before* the client pre-allocates them.
"""

from __future__ import annotations

import re
import urllib.request

_UA = "Mozilla/5.0 (X11; Linux x86_64) packarr/0.1"


def bdecode(b: bytes, i: int = 0):
    c = b[i:i + 1]
    if c == b"i":
        j = b.index(b"e", i)
        return int(b[i + 1:j]), j + 1
    if c == b"l":
        out, i = [], i + 1
        while b[i:i + 1] != b"e":
            v, i = bdecode(b, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        out, i = {}, i + 1
        while b[i:i + 1] != b"e":
            k, i = bdecode(b, i)
            v, i = bdecode(b, i)
            out[k] = v
        return out, i + 1
    j = b.index(b":", i)
    n = int(b[i:j])
    return b[j + 1:j + 1 + n], j + 1 + n


def files(torrent: bytes) -> list[tuple[int, str, int]]:
    """[(index, path, length)] for every file in the torrent."""
    info = bdecode(torrent)[0][b"info"]
    if b"files" in info:
        return [(i, b"/".join(f[b"path"]).decode("utf-8", "replace"), f[b"length"]) for i, f in enumerate(info[b"files"])]
    return [(0, info[b"name"].decode("utf-8", "replace"), info[b"length"])]


def fetch(info_url: str = "", download_url: str = "") -> bytes | None:
    """Download the .torrent for a Nyaa view URL (or any direct .torrent URL). None when unavailable."""
    url = download_url if download_url.endswith(".torrent") else ""
    m = re.search(r"nyaa\.si/view/(\d+)", info_url or "")
    if m:
        url = f"https://nyaa.si/download/{m.group(1)}.torrent"
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        data = urllib.request.urlopen(req, timeout=60).read()
        return data if data[:1] == b"d" else None
    except Exception:
        return None
