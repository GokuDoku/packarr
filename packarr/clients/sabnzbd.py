"""SABnzbd API (a single apikey-authenticated query-string endpoint) behind the same interface as the
torrent clients.

Usenet has no swarm: no peers, no trackers, no seeding - add_trackers/reannounce are no-ops. Job ids are
SABnzbd's nzo_id. A finished job disappears from the queue and reappears in history the instant
post-processing completes, so torrents() merges queue + history - otherwise a job would look "vanished"
to the pipeline for the one tick between the two (Pipeline.run() marks a vanished hash "lost").

Selective pulls (files_unwanted at add time) are supported by this client - add paused, trim via
get_files/delete_nzf, then resume - but nothing in the pipeline currently calls it with files_unwanted for
this client: pipeline.py's selective-pull path only ever computes file indices by parsing raw .torrent
bytes (torrentfile.py), and an NZB has no equivalent fast local listing here yet. Pipeline.add() therefore
refuses --abs/--dirs jobs outright when download_client is sabnzbd, rather than silently queueing a job
that can never start. Whole-pack pulls are unaffected.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .transmission import CHECK, DOWNLOAD, DOWNLOAD_WAIT, SEED, STOPPED

# SABnzbd queue `status` -> Transmission status code.
QUEUE_STATUS = {
    "Downloading": DOWNLOAD, "Grabbing": DOWNLOAD_WAIT, "Fetching": DOWNLOAD_WAIT, "Queued": DOWNLOAD_WAIT,
    "Propagating": DOWNLOAD_WAIT, "QuickCheck": CHECK, "Verifying": CHECK, "Repairing": CHECK,
    "Extracting": CHECK, "Moving": CHECK, "Running": CHECK, "Paused": STOPPED,
}


def _seconds(hms: str) -> int:
    """SABnzbd's 'timeleft' as 'H:MM:SS' -> seconds; unknown/blank -> -1 (Transmission's "unknown" sentinel)."""
    try:
        secs = 0
        for part in str(hms).split(":"):
            secs = secs * 60 + int(part)
        return secs
    except ValueError:
        return -1


def map_queue_slot(s: dict, queue_kbps: float = 0.0) -> dict:
    """One queue['slots'] row -> the Transmission-shaped dict the pipeline reads. queue_kbps is the
    queue-wide speed: SABnzbd downloads one job at a time, so only the actively-downloading slot gets it."""
    status = s.get("status", "")
    mb, mbleft = float(s.get("mb") or 0), float(s.get("mbleft") or 0)
    size, left = int(mb * 1_000_000), int(mbleft * 1_000_000)
    return {
        "id": s["nzo_id"], "hashString": s["nzo_id"], "name": s.get("filename", ""),
        "percentDone": (size - left) / size if size else 0.0,
        "rateDownload": int(queue_kbps * 1000) if status == "Downloading" else 0,
        "eta": _seconds(s.get("timeleft", "")), "status": QUEUE_STATUS.get(status, DOWNLOAD_WAIT),
        "sizeWhenDone": size, "haveValid": max(size - left, 0), "leftUntilDone": left,
        "peersConnected": 0, "trackerStats": [], "downloadDir": "",
        "error": 0, "errorString": "", "metadataPercentComplete": 1.0,
    }


def map_history_slot(s: dict) -> dict:
    """One history['slots'] row -> the same shape, always 100% done (queue and history are mutually
    exclusive - a job in history has nothing left to fetch, successful or not)."""
    failed = s.get("status") == "Failed"
    size = int(s.get("bytes") or 0)
    return {
        "id": s["nzo_id"], "hashString": s["nzo_id"], "name": s.get("name", ""),
        "percentDone": 1.0, "rateDownload": 0, "eta": 0, "status": STOPPED if failed else SEED,
        "sizeWhenDone": size, "haveValid": size, "leftUntilDone": 0,
        "peersConnected": 0, "trackerStats": [], "downloadDir": s.get("storage", ""),
        "error": 1 if failed else 0, "errorString": s.get("fail_message", "") if failed else "",
        "metadataPercentComplete": 1.0,
    }


class Sabnzbd:
    def __init__(self, url: str, api_key: str = "", category: str = "", timeout: int = 240):
        self.base = url.rstrip("/") + "/api"
        self.api_key = api_key
        self.category = category
        self.timeout = timeout

    # ---- transport ----------------------------------------------------------------------------------
    def call(self, mode: str, params: dict | None = None, files: dict[str, tuple[str, bytes]] | None = None):
        """Every SABnzbd call is GET-or-POST-interchangeable query params; only a file upload needs a body."""
        q = {"mode": mode, "output": "json", "apikey": self.api_key, **(params or {})}
        url = self.base + "?" + urllib.parse.urlencode(q)
        data, headers = None, {}
        if files:
            boundary = uuid.uuid4().hex
            body = b""
            for k, (fname, blob) in files.items():
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: application/x-nzb+xml\r\n\r\n").encode() + blob + b"\r\n"
            data = body + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if files else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as x:
                raw = x.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"sabnzbd {mode}: HTTP {e.code}")
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"sabnzbd {mode}: non-JSON response (check url/api_key)")
        # SABnzbd's own docs: status is False on real failures, but "always True for some functions,
        # even if the operation failed" - so this check is a floor, not a guarantee.
        if isinstance(resp, dict) and resp.get("status") is False:
            raise RuntimeError(f"sabnzbd {mode}: {resp.get('error', 'failed')}")
        return resp

    # ---- the client interface --------------------------------------------------------------------------
    def version(self) -> str:
        return str(self.call("version").get("version", ""))

    def torrents(self, ids: list[str] | None = None, fields: list[str] | None = None) -> list[dict]:
        idset = set(ids) if ids else None
        q = self.call("queue").get("queue") or {}
        kbps = float(q.get("kbpersec") or 0)
        slots = q.get("slots", [])
        found = {s["nzo_id"] for s in slots}
        out = [map_queue_slot(s, kbps) for s in slots if idset is None or s["nzo_id"] in idset]
        if idset is None or not idset <= found:  # some requested id wasn't live: check history too
            h = self.call("history", {"limit": 200}).get("history") or {}
            out += [map_history_slot(s) for s in h.get("slots", []) if idset is None or s["nzo_id"] in idset]
        return out

    def add(self, download_dir: str, metainfo: bytes | None = None, magnet: str | None = None,
            files_unwanted: list[int] | None = None) -> dict:
        form: dict = {"cat": self.category} if self.category else {}
        if files_unwanted:
            form["priority"] = "Paused"  # so unwanted files never download before we trim them
        if metainfo:
            r = self.call("addfile", form, files={"nzbfile": ("pack.nzb", metainfo)})
        else:
            r = self.call("addurl", {**form, "name": magnet or ""})
        nzo_id = next(iter(r.get("nzo_ids") or []), None)
        if not nzo_id:
            return {}
        if files_unwanted:
            self._trim(nzo_id, files_unwanted)
            self.start([nzo_id])
        rows = self.call("queue", {"nzo_ids": nzo_id}).get("queue", {}).get("slots", [])
        return {"id": nzo_id, "hashString": nzo_id, "name": rows[0]["filename"] if rows else ""}

    def _trim(self, nzo_id: str, unwanted: list[int]) -> None:
        """Remove the given file indices from a job before it starts. Least-verified part of this client -
        get_files/delete_nzf haven't been exercised against a live SABnzbd; a failure here is swallowed
        so a bad trim never blocks the add (the pack just downloads whole instead)."""
        try:
            files = self.call("get_files", {"value": nzo_id}).get("files", [])
            ids = [files[i]["nzf_id"] for i in unwanted if i < len(files)]
            if ids:
                self.call("queue", {"name": "delete_nzf", "value": nzo_id, "value2": ",".join(ids)})
        except Exception:
            pass

    def set_files(self, tid: str, wanted: list[int], unwanted: list[int]) -> None:
        if unwanted:
            self._trim(tid, unwanted)

    def add_trackers(self, tid: str, trackers: list[str]) -> None:
        pass  # usenet has no trackers

    def start(self, ids: list[str]) -> None:
        if ids:
            self.call("queue", {"name": "resume", "value": ",".join(ids)})

    def stop(self, ids: list[str]) -> None:
        if ids:
            self.call("queue", {"name": "pause", "value": ",".join(ids)})

    def reannounce(self, ids: list[str]) -> None:
        pass  # usenet has no trackers to reannounce

    def remove(self, ids: list[str], delete_data: bool = False) -> None:
        if not ids:
            return
        value = ",".join(ids)
        extra = {"del_files": "1"} if delete_data else {}
        # a finished job is in history, not queue, by the time cleanup calls this - try both; SABnzbd
        # answers "status: true" for a delete of an id it doesn't hold, so the miss is silent either way.
        self.call("queue", {"name": "delete", "value": value, **extra})
        self.call("history", {"name": "delete", "value": value, **extra})
