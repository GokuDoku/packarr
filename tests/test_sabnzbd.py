"""SABnzbd client: field mapping onto the Transmission vocabulary, the queue+history merge that keeps a
finishing job from looking "vanished", and the paused-trim-then-resume selective add."""

from __future__ import annotations

import pytest

from packarr.clients.sabnzbd import Sabnzbd, _seconds, map_history_slot, map_queue_slot
from packarr.clients.transmission import DOWNLOAD, DOWNLOAD_WAIT, SEED, STOPPED


def test_map_queue_slot_speaks_transmission():
    slot = {"nzo_id": "SABnzbd_nzo_abc", "filename": "Pack", "status": "Downloading",
            "mb": "4000", "mbleft": "1000", "timeleft": "0:12:34"}
    t = map_queue_slot(slot, queue_kbps=500)
    assert t["id"] == t["hashString"] == "SABnzbd_nzo_abc"
    assert t["status"] == DOWNLOAD
    assert t["sizeWhenDone"] == 4_000_000_000 and t["leftUntilDone"] == 1_000_000_000
    assert t["haveValid"] == 3_000_000_000
    assert t["percentDone"] == pytest.approx(0.75)
    assert t["rateDownload"] == 500_000  # only the actively-downloading slot gets the queue's speed
    assert t["eta"] == 754


def test_map_queue_slot_only_downloading_gets_rate():
    slot = {"nzo_id": "x", "filename": "Pack", "status": "Queued", "mb": "100", "mbleft": "100"}
    assert map_queue_slot(slot, queue_kbps=999)["rateDownload"] == 0


@pytest.mark.parametrize("status,expected", [
    ("Downloading", DOWNLOAD), ("Queued", DOWNLOAD_WAIT), ("Fetching", DOWNLOAD_WAIT),
    ("Propagating", DOWNLOAD_WAIT), ("Verifying", None), ("Repairing", None), ("Extracting", None),
    ("Paused", STOPPED), ("SomeFutureStatus", DOWNLOAD_WAIT),
])
def test_queue_status_mapping(status, expected):
    from packarr.clients.transmission import CHECK
    want = CHECK if expected is None else expected
    assert map_queue_slot({"nzo_id": "x", "mb": "0", "mbleft": "0", "status": status})["status"] == want


def test_map_history_slot_completed():
    slot = {"nzo_id": "SABnzbd_nzo_done", "name": "Pack", "status": "Completed", "bytes": 4_000_000_000,
            "storage": "/downloads/Pack"}
    t = map_history_slot(slot)
    assert t["percentDone"] == 1.0
    assert t["status"] == SEED
    assert t["error"] == 0
    assert t["downloadDir"] == "/downloads/Pack"
    assert t["sizeWhenDone"] == t["haveValid"] == 4_000_000_000


def test_map_history_slot_failed():
    slot = {"nzo_id": "x", "name": "Pack", "status": "Failed", "bytes": 0, "fail_message": "Out of retries"}
    t = map_history_slot(slot)
    assert t["status"] == STOPPED
    assert t["error"] == 1
    assert t["errorString"] == "Out of retries"


@pytest.mark.parametrize("hms,secs", [("0:00:05", 5), ("1:02:03", 3723), ("", -1), ("garbage", -1)])
def test_seconds_parses_hms(hms, secs):
    assert _seconds(hms) == secs


def _client():
    sab = Sabnzbd("http://sab:8080", "key", category="tv")
    return sab


def test_add_by_url_returns_id_and_name(monkeypatch):
    sab = _client()
    calls = []

    def fake_call(mode, params=None, files=None):
        calls.append((mode, dict(params or {}), bool(files)))
        if mode == "addurl":
            return {"status": True, "nzo_ids": ["SABnzbd_nzo_new"]}
        if mode == "queue":
            return {"queue": {"slots": [{"nzo_id": "SABnzbd_nzo_new", "filename": "Pack"}]}}
        raise AssertionError(f"unexpected call: {mode}")

    monkeypatch.setattr(sab, "call", fake_call)
    t = sab.add("/downloads", magnet="https://indexer.example/get.nzb")
    assert t == {"id": "SABnzbd_nzo_new", "hashString": "SABnzbd_nzo_new", "name": "Pack"}
    add_call = next(c for c in calls if c[0] == "addurl")
    assert add_call[1]["cat"] == "tv" and add_call[1]["name"] == "https://indexer.example/get.nzb"
    assert "priority" not in add_call[1]  # non-selective: never paused


def test_selective_add_pauses_trims_then_resumes(monkeypatch):
    sab = _client()
    calls = []

    def fake_call(mode, params=None, files=None):
        calls.append((mode, dict(params or {}), bool(files)))
        if mode == "addfile":
            return {"status": True, "nzo_ids": ["SABnzbd_nzo_sel"]}
        if mode == "get_files":
            return {"files": [{"nzf_id": "nzf0"}, {"nzf_id": "nzf1"}, {"nzf_id": "nzf2"}]}
        if mode == "queue" and params.get("name") == "delete_nzf":
            return {"status": True}
        if mode == "queue" and params.get("name") == "resume":
            return {"status": True}
        if mode == "queue":
            return {"queue": {"slots": [{"nzo_id": "SABnzbd_nzo_sel", "filename": "Pack"}]}}
        raise AssertionError(f"unexpected call: {mode} {params}")

    monkeypatch.setattr(sab, "call", fake_call)
    t = sab.add("/downloads", metainfo=b"<nzb/>", files_unwanted=[0, 2])
    assert t["id"] == "SABnzbd_nzo_sel"
    add_call = next(c for c in calls if c[0] == "addfile")
    assert add_call[1]["priority"] == "Paused" and add_call[2] is True  # added paused, as a file upload
    trim = next(c for c in calls if c[0] == "queue" and c[1].get("name") == "delete_nzf")
    assert trim[1]["value2"] == "nzf0,nzf2"  # only the unwanted indices, mapped to their nzf_ids
    assert any(c[0] == "queue" and c[1].get("name") == "resume" for c in calls)  # only then resumed
    # trim happens before resume
    assert calls.index(trim) < next(i for i, c in enumerate(calls) if c[1].get("name") == "resume")


def test_torrents_merges_queue_and_history(monkeypatch):
    sab = _client()

    def fake_call(mode, params=None, files=None):
        if mode == "queue":
            return {"queue": {"kbpersec": "0", "slots": [{"nzo_id": "live", "filename": "Still going",
                                                            "status": "Downloading", "mb": "10", "mbleft": "5"}]}}
        if mode == "history":
            return {"history": {"slots": [{"nzo_id": "done", "name": "Finished", "status": "Completed",
                                            "bytes": 1000, "storage": "/downloads/Finished"}]}}
        raise AssertionError(mode)

    monkeypatch.setattr(sab, "call", fake_call)
    rows = sab.torrents()
    ids = {r["id"] for r in rows}
    assert ids == {"live", "done"}


def test_torrents_checks_history_when_requested_id_not_in_queue(monkeypatch):
    sab = _client()
    history_called = []

    def fake_call(mode, params=None, files=None):
        if mode == "queue":
            return {"queue": {"kbpersec": "0", "slots": []}}
        if mode == "history":
            history_called.append(params)
            return {"history": {"slots": [{"nzo_id": "done", "name": "Finished", "status": "Completed", "bytes": 0}]}}
        raise AssertionError(mode)

    monkeypatch.setattr(sab, "call", fake_call)
    rows = sab.torrents(ids=["done"])
    assert [r["id"] for r in rows] == ["done"]
    assert history_called  # had to fall through to history since "done" wasn't in the live queue


def test_remove_tries_both_queue_and_history(monkeypatch):
    sab = _client()
    calls = []
    monkeypatch.setattr(sab, "call", lambda mode, params=None, files=None: calls.append((mode, params)) or {"status": True})
    sab.remove(["x"], delete_data=True)
    modes = [c[0] for c in calls]
    assert modes == ["queue", "history"]
    assert all(c[1]["del_files"] == "1" for c in calls)
