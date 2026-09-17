"""qBittorrent Web API (v2) behind the same interface as the Transmission client.

Torrent ids are the info-hashes (qBittorrent has no numeric ids). Field names are mapped onto the Transmission
vocabulary the pipeline speaks, so nothing outside this module knows which client is in use.

Selective pulls: qBittorrent cannot mark files unwanted in the add call, so a selective add is done stopped, the
unwanted files get priority 0, and only then is the torrent started - the client never allocates them.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ..torrentfile import bdecode
from .transmission import CHECK, DOWNLOAD, DOWNLOAD_WAIT, SEED, STOPPED

# qBittorrent state -> Transmission status code
STATE = {"downloading": DOWNLOAD, "forcedDL": DOWNLOAD, "metaDL": DOWNLOAD, "forcedMetaDL": DOWNLOAD, "stalledDL": DOWNLOAD,
         "queuedDL": DOWNLOAD_WAIT, "allocating": DOWNLOAD_WAIT, "checkingDL": CHECK, "checkingUP": CHECK, "checkingResumeData": CHECK,
         "uploading": SEED, "forcedUP": SEED, "stalledUP": SEED, "queuedUP": SEED,
         "pausedDL": STOPPED, "stoppedDL": STOPPED, "pausedUP": STOPPED, "stoppedUP": STOPPED, "error": STOPPED, "missingFiles": STOPPED}


def info_hash(metainfo: bytes) -> str:
    """SHA-1 of the bencoded info dict - what qBittorrent will call this torrent."""
    start = metainfo.index(b"4:info") + 6
    _, end = bdecode(metainfo, start)
    return hashlib.sha1(metainfo[start:end]).hexdigest()


def map_torrent(t: dict, files: list[dict] | None = None, trackers: list[dict] | None = None) -> dict:
    """One /torrents/info row (+ optional /files and /trackers rows) -> the Transmission-shaped dict the pipeline reads."""
    h = t["hash"]
    state = t.get("state", "")
    out = {"id": h, "hashString": h, "name": t.get("name", ""), "percentDone": float(t.get("progress") or 0.0),
           "rateDownload": t.get("dlspeed", 0), "eta": t.get("eta", -1), "status": STATE.get(state, STOPPED),
           "sizeWhenDone": t.get("size", 0), "haveValid": t.get("completed", 0), "leftUntilDone": t.get("amount_left", 0),
           "peersConnected": (t.get("num_seeds") or 0) + (t.get("num_leechs") or 0), "downloadDir": t.get("save_path", ""),
           "error": 1 if state in ("error", "missingFiles") else 0,
           "errorString": {"error": "qBittorrent reports an error", "missingFiles": "files missing"}.get(state, ""),
           "metadataPercentComplete": 0.0 if state in ("metaDL", "forcedMetaDL") else 1.0}
    if files is not None:
        out["files"] = [{"name": f["name"], "length": f["size"], "bytesCompleted": int(f["size"] * float(f.get("progress") or 0)),
                         "wanted": f.get("priority", 1) != 0} for f in files]
    if trackers is not None:
        real = [x for x in trackers if str(x.get("url", "")).startswith(("http", "udp"))]
        # qBittorrent tracker status 1 = "not contacted yet": exactly what the pipeline treats as a parked announce
        out["trackerStats"] = [{"host": x.get("url", "")[:40], "announceState": 0 if x.get("status") == 1 else 1,
                                "lastAnnounceTime": 0 if x.get("status") == 1 else 1, "seederCount": x.get("num_seeds", -1),
                                "leecherCount": x.get("num_leeches", -1), "lastAnnounceResult": x.get("msg", "")} for x in real]
    return out


class QBittorrent:
    def __init__(self, url: str, username: str = "", password: str = "", timeout: int = 240):
        self.root = url.rstrip("/")
        self.base = self.root + "/api/v2"
        self.username, self.password = username, password
        self.timeout = timeout
        self.sid = ""

    # ---- transport ----------------------------------------------------------------------------------------
    def login(self) -> None:
        req = urllib.request.Request(self.base + "/auth/login", data=urllib.parse.urlencode({"username": self.username, "password": self.password}).encode(),
                                     headers={"Referer": self.root, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as x:
            body = x.read().decode()  # 4.x says "Ok."; 5.x answers 204 with no body
            cookie = x.headers.get("Set-Cookie") or ""
        if body.strip() not in ("", "Ok.") or "SID" not in cookie:
            raise RuntimeError(f"qbittorrent login failed: {body[:80] or 'no session cookie'}")
        self.sid = cookie.split(";", 1)[0].strip()  # "SID=..." (4.x) or "QBT_SID_<port>=..." (5.x)

    def call(self, path: str, form: dict | None = None, params: dict | None = None, files: dict | None = None):
        """GET without a body, POST a form (multipart when `files`) with one. Logs in lazily and once more on 403."""
        for attempt in range(2):
            if not self.sid:
                self.login()
            url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
            headers = {"Referer": self.root, "Cookie": self.sid}
            data = None
            if files:
                boundary = uuid.uuid4().hex
                data = b""
                for k, v in (form or {}).items():
                    data += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                for k, (fname, blob) in files.items():
                    data += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode() + blob + b"\r\n"
                data += f"--{boundary}--\r\n".encode()
                headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            elif form is not None:
                data = urllib.parse.urlencode(form).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as x:
                    raw = x.read()
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt == 0:
                    self.sid = ""
                    continue
                raise RuntimeError(f"qbittorrent {path}: HTTP {e.code} {e.read().decode(errors='replace')[:120]}")
            if raw[:1] in (b"{", b"["):
                return json.loads(raw)
            return raw.decode(errors="replace")
        raise RuntimeError(f"qbittorrent {path}: authentication failed")

    # ---- the client interface --------------------------------------------------------------------------------
    @staticmethod
    def _hashes(ids) -> str:
        return "|".join(str(i) for i in ids)

    def version(self) -> str:
        return str(self.call("/app/version"))

    def torrents(self, ids: list | None = None, fields: list[str] | None = None) -> list[dict]:
        params = {"hashes": self._hashes(ids)} if ids else None
        rows = self.call("/torrents/info", params=params) or []
        want_files = fields is None or "files" in fields
        want_trackers = fields is None or "trackerStats" in fields
        out = []
        for t in rows:
            files = self.call("/torrents/files", params={"hash": t["hash"]}) if want_files else None
            trackers = self.call("/torrents/trackers", params={"hash": t["hash"]}) if want_trackers else None
            out.append(map_torrent(t, files, trackers))
        return out

    def add(self, download_dir: str, metainfo: bytes | None = None, magnet: str | None = None,
            files_unwanted: list[int] | None = None) -> dict:
        form = {"savepath": download_dir, "autoTMM": "false"}
        if files_unwanted:
            form["stopped"] = "true"  # 5.x
            form["paused"] = "true"   # 4.x
        if metainfo:
            h = info_hash(metainfo)
            self.call("/torrents/add", form=form, files={"torrents": ("pack.torrent", metainfo)})
        else:
            before = {t["hash"] for t in self.call("/torrents/info") or []}
            form["urls"] = magnet or ""
            self.call("/torrents/add", form=form)
            h = next((t["hash"] for t in self.call("/torrents/info") or [] if t["hash"] not in before), None)
        if not h:
            return {}
        if files_unwanted:
            self.call("/torrents/filePrio", form={"hash": h, "id": "|".join(str(i) for i in files_unwanted), "priority": 0})
            self.start([h])
        rows = self.call("/torrents/info", params={"hashes": h}) or []
        return {"id": h, "hashString": h, "name": rows[0]["name"] if rows else ""}

    def set_files(self, tid, wanted: list[int], unwanted: list[int]) -> None:
        if unwanted:
            self.call("/torrents/filePrio", form={"hash": tid, "id": "|".join(map(str, unwanted)), "priority": 0})
        if wanted:
            self.call("/torrents/filePrio", form={"hash": tid, "id": "|".join(map(str, wanted)), "priority": 1})

    def add_trackers(self, tid, trackers: list[str]) -> None:
        if trackers:
            self.call("/torrents/addTrackers", form={"hash": tid, "urls": "\n".join(trackers)})
            self.reannounce([tid])

    def _control(self, action5: str, action4: str, ids) -> None:
        try:
            self.call(f"/torrents/{action5}", form={"hashes": self._hashes(ids)})
        except RuntimeError as e:
            if "HTTP 404" not in str(e):
                raise
            self.call(f"/torrents/{action4}", form={"hashes": self._hashes(ids)})  # 4.x endpoint names

    def start(self, ids) -> None:
        self._control("start", "resume", ids)

    def stop(self, ids) -> None:
        self._control("stop", "pause", ids)

    def reannounce(self, ids) -> None:
        self.call("/torrents/reannounce", form={"hashes": self._hashes(ids)})

    def remove(self, ids, delete_data: bool = False) -> None:
        self.call("/torrents/delete", form={"hashes": self._hashes(ids), "deleteFiles": "true" if delete_data else "false"})
