"""qBittorrent client: field mapping onto the Transmission vocabulary, selective add, info-hash."""

from __future__ import annotations

import hashlib

from packarr.clients.qbittorrent import QBittorrent, info_hash, map_torrent
from packarr.clients.transmission import DOWNLOAD, STOPPED


def test_map_torrent_speaks_transmission():
    row = {"hash": "abc", "name": "Pack", "progress": 0.25, "dlspeed": 1_000_000, "eta": 120, "state": "downloading",
           "size": 4_000, "completed": 1_000, "amount_left": 3_000, "num_seeds": 3, "num_leechs": 2, "save_path": "/downloads"}
    files = [{"name": "a/01.mkv", "size": 2000, "progress": 0.5, "priority": 1}, {"name": "a/02.mkv", "size": 2000, "progress": 0.0, "priority": 0}]
    trackers = [{"url": "** [DHT] **", "status": 2}, {"url": "udp://tracker.example:1337/announce", "status": 1, "num_seeds": 5, "num_leeches": 1, "msg": ""}]
    t = map_torrent(row, files, trackers)
    assert t["id"] == t["hashString"] == "abc"
    assert t["percentDone"] == 0.25 and t["rateDownload"] == 1_000_000 and t["status"] == DOWNLOAD
    assert t["sizeWhenDone"] == 4_000 and t["haveValid"] == 1_000 and t["peersConnected"] == 5
    assert t["files"][0]["bytesCompleted"] == 1000 and t["files"][1]["wanted"] is False
    assert len(t["trackerStats"]) == 1 and t["trackerStats"][0]["announceState"] == 0  # "not contacted yet" == parked
    assert map_torrent({"hash": "x", "state": "pausedDL"})["status"] == STOPPED
    assert map_torrent({"hash": "x", "state": "missingFiles"})["error"] == 1


def test_info_hash_is_sha1_of_the_info_dict():
    meta = b"d8:announce18:udp://t.example:14:infod6:lengthi3e4:name5:a.txt12:piece lengthi16384e6:pieces20:" + b"\x00" * 20 + b"ee"
    start = meta.index(b"4:info") + 6
    assert info_hash(meta) == hashlib.sha1(meta[start:-1]).hexdigest()


def test_selective_add_stops_marks_then_starts(monkeypatch):
    qb = QBittorrent("http://qb:8080", "admin", "pw")
    qb.sid = "s"
    calls = []

    def fake_call(path, form=None, params=None, files=None):
        calls.append((path, dict(form or {}), dict(params or {}), bool(files)))
        if path == "/torrents/info":
            return [{"hash": "deadbeef", "name": "Pack"}]
        return "Ok."
    monkeypatch.setattr(qb, "call", fake_call)
    monkeypatch.setattr("packarr.clients.qbittorrent.info_hash", lambda m: "deadbeef")
    t = qb.add("/downloads", metainfo=b"d4:infod0:ee", files_unwanted=[1, 3])
    assert t["id"] == "deadbeef"
    add = next(c for c in calls if c[0] == "/torrents/add")
    assert add[1]["stopped"] == "true" and add[1]["paused"] == "true" and add[3] is True  # added stopped, as multipart
    prio = next(c for c in calls if c[0] == "/torrents/filePrio")
    assert prio[1] == {"hash": "deadbeef", "id": "1|3", "priority": 0}
    assert any(c[0] == "/torrents/start" for c in calls)  # only then started
