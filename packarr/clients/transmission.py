"""Transmission RPC over plain HTTP, with the 409 session-id dance and a generous timeout.

Why a generous timeout: when the downloads disk is saturated (pre-allocation + imports), Transmission's
RPC thread answers in tens of seconds. Timing out and retrying only makes it worse.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

FIELDS = ["id", "name", "hashString", "percentDone", "rateDownload", "eta", "error", "errorString", "status",
          "sizeWhenDone", "haveValid", "leftUntilDone", "peersConnected", "trackerStats", "downloadDir", "files",
          "metadataPercentComplete"]

# torrent-get `status` values
STOPPED, CHECK_WAIT, CHECK, DOWNLOAD_WAIT, DOWNLOAD, SEED_WAIT, SEED = range(7)


class Transmission:
    def __init__(self, url: str, username: str = "", password: str = "", timeout: int = 240):
        self.url = url
        self.timeout = timeout
        self.auth = base64.b64encode(f"{username}:{password}".encode()).decode() if username or password else ""
        self.sid = ""

    def call(self, method: str, args: dict | None = None) -> dict:
        body = json.dumps({"method": method, "arguments": args or {}}).encode()
        for _ in range(2):
            headers = {"Content-Type": "application/json"}
            if self.auth:
                headers["Authorization"] = "Basic " + self.auth
            if self.sid:
                headers["X-Transmission-Session-Id"] = self.sid
            req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as x:
                    r = json.loads(x.read())
                    if r.get("result") != "success":
                        raise RuntimeError(f"transmission {method}: {r.get('result')}")
                    return r
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    self.sid = e.headers.get("X-Transmission-Session-Id", "")
                    continue
                raise RuntimeError(f"transmission {method}: HTTP {e.code}")
        raise RuntimeError(f"transmission {method}: no session id")

    def torrents(self, ids: list[int] | None = None, fields: list[str] | None = None) -> list[dict]:
        args = {"fields": fields or FIELDS}
        if ids:
            args["ids"] = ids
        return self.call("torrent-get", args)["arguments"]["torrents"]

    def add(self, download_dir: str, metainfo: bytes | None = None, magnet: str | None = None,
            files_unwanted: list[int] | None = None) -> dict:
        args: dict = {"download-dir": download_dir}
        if metainfo:
            args["metainfo"] = base64.b64encode(metainfo).decode()
        else:
            args["filename"] = magnet
        if files_unwanted:
            args["files-unwanted"] = files_unwanted  # set at add time so pre-allocation only touches wanted files
        r = self.call("torrent-add", args)["arguments"]
        return r.get("torrent-added") or r.get("torrent-duplicate") or {}

    def set_files(self, tid: int, wanted: list[int], unwanted: list[int]) -> None:
        self.call("torrent-set", {"ids": [tid], "files-wanted": wanted, "files-unwanted": unwanted})

    def add_trackers(self, tid: int, trackers: list[str]) -> None:
        if trackers:
            self.call("torrent-set", {"ids": [tid], "trackerAdd": trackers})
            self.call("torrent-reannounce", {"ids": [tid]})  # Transmission parks announces after trackerAdd

    def start(self, ids: list[int]) -> None:
        self.call("torrent-start", {"ids": ids})

    def stop(self, ids: list[int]) -> None:
        self.call("torrent-stop", {"ids": ids})

    def reannounce(self, ids: list[int]) -> None:
        self.call("torrent-reannounce", {"ids": ids})

    def remove(self, ids: list[int], delete_data: bool = False) -> None:
        self.call("torrent-remove", {"ids": ids, "delete-local-data": delete_data})
