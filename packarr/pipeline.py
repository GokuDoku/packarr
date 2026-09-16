"""The job state machine.

  queued -> downloading -> (plan) -> importing -> (route leftovers) -> cleanup -> done
                     \\-> selecting (metadata wait for selective pulls)
                       plan issues -> needs-map   (packarr approve ... releases it)
                       unresolved leftovers -> leftovers (torrent kept, stopped, for review)

State lives in <state_dir>/jobs.json; every plan is written to <state_dir>/plans/ so a HELD job can be
inspected and hand-mapped without re-downloading anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from . import parsing as P
from . import probe, torrentfile
from .clients.jellyfin import Jellyfin
from .clients.radarr import Radarr
from .clients.sonarr import Sonarr, languages
from .clients.transmission import DOWNLOAD, DOWNLOAD_WAIT, Transmission
from .config import Config
from .gaps import needed
from .log import log
from .mapping import Resolver
from .planner import Planner
from .router import Router

TERMINAL = ("rejected", "lost", "import-failed", "superseded", "done", "cancelled")


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sonarr = Sonarr(cfg.sonarr.url, cfg.sonarr.api_key)
        self.radarr = Radarr(cfg.radarr.url, cfg.radarr.api_key) if cfg.radarr.enabled else None
        self.jellyfin = Jellyfin(cfg.jellyfin.url, cfg.jellyfin.api_key) if cfg.jellyfin.enabled else None
        self.tr = Transmission(cfg.transmission.url, cfg.transmission.username, cfg.transmission.password, cfg.transmission.timeout)
        self.resolver = Resolver(cfg.paths.state_dir, cfg.anidb_cache_dir)
        self.planner = Planner(self.resolver, lambda sid: self.sonarr.episodes(sid), probe.duration_min, self.to_local)
        self.router = Router(self.sonarr, self.radarr, cfg.radarr.anime_root, cfg.radarr.quality_profile)
        self.state_path = os.path.join(cfg.paths.state_dir, "jobs.json")
        self.plans_dir = os.path.join(cfg.paths.state_dir, "plans")
        os.makedirs(self.plans_dir, exist_ok=True)

    # ---- paths ------------------------------------------------------------------------------------
    def to_local(self, sonarr_path: str) -> str:
        return os.path.join(self.cfg.paths.downloads_local, os.path.relpath(sonarr_path, self.cfg.paths.downloads_sonarr))

    def to_sonarr(self, local_path: str) -> str:
        return os.path.join(self.cfg.paths.downloads_sonarr, os.path.relpath(local_path, self.cfg.paths.downloads_local))

    def free_gb(self) -> float:
        return shutil.disk_usage(self.cfg.paths.downloads_local).free / 1e9

    # ---- state ------------------------------------------------------------------------------------
    def load(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        return {"jobs": []}

    def save(self, s: dict) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)
        os.replace(tmp, self.state_path)

    def languages(self, job: dict) -> list[dict]:
        return languages(job.get("languages") or self.cfg.languages.tag)

    # ---- queueing ---------------------------------------------------------------------------------
    def add(self, row: dict, series_id: int, **opts) -> dict:
        """row: {title, guid|magnet, gb, info}. opts: all, languages, map, abs, dirs, gb."""
        series = self.sonarr.series_one(series_id)
        s = self.load()
        magnet = row.get("guid") or row.get("magnet") or ""
        sel = opts.get("abs") or opts.get("dirs")
        for j in s["jobs"]:
            if j["magnet"] == magnet and j["status"] not in TERMINAL and (j.get("abs") or j.get("dirs")) == sel:
                raise SystemExit("already queued")
        job = {"title": row["title"], "magnet": magnet, "gb": float(row.get("gb") or 0), "seriesId": series_id, "series": series["title"],
               "info": row.get("info", ""), "tvdb": series["tvdbId"], "status": "queued", "added": time.strftime("%Y-%m-%dT%H:%M")}
        for k in ("all", "languages", "map", "abs", "dirs"):
            if opts.get(k):
                job[k] = opts[k]
        if opts.get("gb"):
            job["gb"] = float(opts["gb"])
        s["jobs"].append(job)
        self.save(s)
        log(f"queued [{series['title']}] {job['gb']:.1f} GB  {job['title'][:90]}")
        return job

    def adopt(self, torrent_id: int, series_id: int, **opts) -> dict:
        """Take over a torrent that was added to the client by hand."""
        t = self.tr.torrents([torrent_id], ["id", "name", "hashString", "sizeWhenDone"])
        if not t:
            raise SystemExit(f"no torrent #{torrent_id}")
        t = t[0]
        series = self.sonarr.series_one(series_id)
        s = self.load()
        if any(j.get("hash") == t["hashString"] and j["status"] not in TERMINAL for j in s["jobs"]):
            raise SystemExit("already adopted")
        job = {"title": t["name"], "magnet": t["hashString"], "gb": round(t["sizeWhenDone"] / 1e9, 2), "seriesId": series_id, "series": series["title"],
               "info": "", "tvdb": series["tvdbId"], "status": "downloading", "trId": t["id"], "hash": t["hashString"], "adopted": True,
               "added": time.strftime("%Y-%m-%dT%H:%M"), "started": time.strftime("%Y-%m-%dT%H:%M")}
        for k in ("all", "languages"):
            if opts.get(k):
                job[k] = opts[k]
        s["jobs"].append(job)
        self.save(s)
        log(f"adopted #{torrent_id} [{series['title']}] {job['gb']} GB  {t['name'][:70]}")
        return job

    # ---- selective pulls ----------------------------------------------------------------------------
    @staticmethod
    def wanted_file(job: dict, name: str) -> bool:
        if not name.lower().endswith((".mkv", ".mp4")):
            return False
        if job.get("dirs"):
            return any(d.lower() in name.lower() for d in job["dirs"])
        m = P.EPNUM.findall(os.path.basename(name))
        n = int(m[0]) if m else None
        return n is not None and any(lo <= n <= hi for lo, hi in job["abs"])

    @staticmethod
    def selective(job: dict) -> bool:
        return bool(job.get("abs") or job.get("dirs"))

    def select_files(self, job: dict) -> bool:
        """Once metadata is in, keep only wanted files, then start the torrent."""
        t = self.tr.torrents([job["trId"]], ["id", "files", "metadataPercentComplete"])[0]
        if t["metadataPercentComplete"] < 1 or not t["files"]:
            return False
        want, unwant, wb = [], [], 0
        for i, f in enumerate(t["files"]):
            if self.wanted_file(job, f["name"]):
                want.append(i)
                wb += f["length"]
            else:
                unwant.append(i)
        self.tr.set_files(job["trId"], want, unwant)
        self.tr.start([job["trId"]])
        job.update(gb=round(wb / 1e9, 1), status="downloading", wanted=len(want))
        log(f"#{job['trId']} selected {len(want)}/{len(t['files'])} files ({job['gb']} GB)")
        return True

    # ---- starting ----------------------------------------------------------------------------------
    def start_jobs(self, s: dict) -> None:
        lim = self.cfg.limits
        try:
            torrents = self.tr.torrents(fields=["hashString", "percentDone", "sizeWhenDone", "haveValid", "status"])
        except Exception as e:
            log(f"transmission unavailable: {e}")
            return
        done = {t["hashString"] for t in torrents if t["percentDone"] >= 1}
        active = [j for j in s["jobs"] if j["status"] in ("downloading", "selecting") and j.get("hash") not in done]
        # clients pre-allocate every wanted file in full, so bytes not yet downloaded are already committed on disk
        committed = sum(t["sizeWhenDone"] - t["haveValid"] for t in torrents if t["status"] in (DOWNLOAD_WAIT, DOWNLOAD)) / 1e9
        for j in s["jobs"]:
            if j["status"] != "queued":
                continue
            budget = self.free_gb() - lim.min_free_gb - committed
            if len(active) >= lim.max_active or committed + j["gb"] > lim.max_active_gb or j["gb"] > budget:
                continue
            tf = torrentfile.fetch(j.get("info", ""), j.get("magnet", ""))
            unwanted = None
            if tf and self.selective(j):
                try:
                    files = torrentfile.files(tf)
                    want = [i for i, name, _ in files if self.wanted_file(j, name)]
                    unwanted = [i for i, _, _ in files if i not in want]
                    wb = sum(ln for i, _, ln in files if i in want)
                    j.update(gb=round(wb / 1e9, 1), wanted=len(want), preselected=True)
                    if wb / 1e9 > budget:
                        log(f"{j['series']}: selected {wb / 1e9:.1f} GB exceeds budget {budget:.0f} GB - waiting")
                        continue
                except Exception as e:
                    log(f"pre-selection failed for {j['title'][:40]}: {e}")
            elif self.selective(j) and not tf:
                continue  # never add a selective pull as a bare magnet: it would allocate the whole pack
            try:
                t = self.tr.add(self.cfg.paths.downloads_client, metainfo=tf, magnet=None if tf else j["magnet"], files_unwanted=unwanted)
            except Exception as e:
                log(f"add failed for {j['title'][:40]}: {e}")
                continue
            if not t:
                log(f"add failed for {j['title'][:40]}: no torrent returned")
                continue
            try:
                self.tr.add_trackers(t["id"], self.cfg.trackers)  # more peer sources for small old swarms
            except Exception:
                pass
            j.update(status="selecting" if (self.selective(j) and not j.get("preselected")) else "downloading",
                     trId=t["id"], hash=t["hashString"], started=time.strftime("%Y-%m-%dT%H:%M"))
            active.append(j)
            committed += j["gb"]
            log(f"started #{t['id']} {j['title'][:80]}" + (f" ({j['wanted']} files, {j['gb']} GB selected)" if j.get("preselected") else ""))
            if j["status"] == "selecting":
                for _ in range(18):  # wait up to 90 s for metadata so unwanted files are excluded before much downloads
                    time.sleep(5)
                    try:
                        if self.select_files(j):
                            break
                    except Exception as e:
                        log(f"select failed #{t['id']}: {e}")
                        break

    # ---- planning + import ---------------------------------------------------------------------------
    def plan_folder(self, job: dict, torrent_name: str) -> tuple[list[dict], list[str], str]:
        folder = os.path.join(self.cfg.paths.downloads_sonarr, torrent_name)
        items = self.sonarr.manual_import_list(folder)
        eps = self.sonarr.episodes(job["seriesId"])
        series_by_tvdb = {str(x["tvdbId"]): x for x in self.sonarr.series()}
        plan, issues = self.planner.plan(job, items, eps, series_by_tvdb)
        pf = os.path.join(self.plans_dir, f"{job.get('trId', 'x')}-{re.sub(r'[^A-Za-z0-9]+', '_', job['series'])[:30]}.json")
        with open(pf, "w", encoding="utf-8") as fh:
            json.dump({"series": job["series"], "seriesId": job["seriesId"], "torrent": torrent_name, "issues": issues,
                       "folders": job.get("folder_resolution"), "plan": [{k: v for k, v in r.items() if k != "path"} for r in plan]}, fh, indent=1)
        return plan, issues, pf

    def import_job(self, job: dict, t: dict) -> int | None:
        """Plan, verify, submit. Returns files submitted, 0 when held/nothing to do, None on API failure."""
        try:
            plan, issues, pf = self.plan_folder(job, t["name"])
        except Exception as e:
            log(f"[{job['series']}] plan failed: {e}")
            return None
        job["plan"] = pf
        mapped = [r for r in plan if r["epId"]]
        if issues:
            job.update(status="needs-map", issues=issues)
            log(f"[{job['series']}] plan has {len(issues)} issue(s) - HELD ({pf}): {' | '.join(issues[:3])[:200]}")
            try:
                self.tr.stop([t["id"]])
            except Exception:
                pass
            return 0
        eps = self.sonarr.episodes(job["seriesId"])
        need = needed(self.sonarr, self.jellyfin, job["seriesId"], job["tvdb"], eps, self.cfg.languages.wanted)
        files, skipped = [], 0
        q = P.pack_quality(job["title"])
        for r in mapped:
            if r["seriesId"] == job["seriesId"] and not job.get("all") and r["epId"] not in need:
                skipped += 1
                continue
            qq = q or P.quality_from_height(probe.height(self.to_local(r["path"])))  # no source tag: probe each file (a pack can mix 480p TV + 1080p OVA)
            files.append({"path": r["path"], "seriesId": r["seriesId"], "episodeIds": [r["epId"]], "quality": qq,
                          "languages": self.languages(job), "releaseGroup": "", "indexerFlags": 0})
        log(f"[{job['series']}] plan OK: {len(plan)} videos, {len(mapped)} mapped, import {len(files)}, already good {skipped}, extras/unmapped {len(plan) - len(mapped)}")
        job["toImport"] = len(files)
        if not files:
            job.update(cmdId=None, status="importing")
            return 0
        code, cmd = self.sonarr.manual_import(files)
        if code not in (200, 201):
            log(f"import cmd failed {code}: {cmd}")
            return None
        job.update(cmdId=cmd["id"], status="importing")
        return len(files)

    def finish_job(self, job: dict, t: dict) -> None:
        """Episode import done: route leftover films/specials, then schedule cleanup (keep data if anything is unresolved)."""
        root = os.path.join(self.cfg.paths.downloads_local, t["name"])
        left = []
        for dp, _, fs in os.walk(root):
            left += [os.path.join(dp, f) for f in fs if P.is_video(f)]
        log(f"[{job['series']}] {len(left)} video files remain after episode import")
        mapped_rel = set()
        try:
            with open(job["plan"], encoding="utf-8") as fh:
                mapped_rel = {r["rel"] for r in json.load(fh)["plan"] if r.get("epId")}
        except Exception:
            pass
        left = [f for f in left if os.path.relpath(f, root) not in mapped_rel]  # already-good episodes we skipped are not specials
        if (job.get("explicit") or job.get("keep")) and left:
            job.update(status="leftovers", leftovers=[os.path.relpath(f, root) for f in left][:60])
            self.tr.stop([t["id"]])
            log(f"[{job['series']}] explicit map - torrent kept with {len(left)} leftover files for review")
            return
        specials = self.sonarr.episodes(job["seriesId"], season=0)
        unresolved = []
        for f in sorted(left):
            rel = os.path.relpath(f, root)
            mins = probe.duration_min(f)
            q = P.pack_quality(job["title"]) or P.quality_from_height(probe.height(f))
            if P.EXTRA_DIR.search(os.path.dirname(rel)) or re.search(r"interview|commentary|trailer|\bPV\b|\bCM\b|music video|NC(OP|ED)", rel, re.I):
                continue
            if mins < 40 and not re.search(r"movie|film|gekijou|special|ova|oad", rel, re.I):
                continue  # an episode we skipped, or an NC extra
            res = self.router.route(job, self.to_sonarr(f), specials, q, self.languages(job))
            log(f"[{job['series']}] {rel[:60]} ({mins:.0f} min) {res or ('UNRESOLVED - kept' if mins >= 5 else 'unresolved short - dropped')}")
            if res is None and mins >= 5:
                unresolved.append(rel)
        if unresolved:
            job.update(status="leftovers", leftovers=unresolved)
            log(f"[{job['series']}] torrent kept: {len(unresolved)} unresolved file(s)")
            self.tr.stop([t["id"]])
            return
        job.update(status="cleanup", cleanupAt=time.time() + self.cfg.limits.cleanup_delay_s)

    # ---- the tick ----------------------------------------------------------------------------------
    def run(self) -> None:
        s = self.load()
        self.start_jobs(s)
        self.save(s)
        for j in s["jobs"]:
            if j["status"] == "selecting":
                try:
                    self.select_files(j)
                except Exception as e:
                    log(f"select failed #{j.get('trId')}: {e}")
        self.save(s)
        live = [j for j in s["jobs"] if j["status"] in ("downloading", "importing", "cleanup")]
        if live:
            try:
                torrents = self.tr.torrents()
            except Exception as e:
                log(f"transmission unavailable: {e}")
                return
            byhash = {t["hashString"]: t for t in torrents}
            parked = [t["id"] for t in torrents if t["status"] == DOWNLOAD and t["percentDone"] < 1 and t.get("trackerStats")
                      and all(x["announceState"] == 0 and not x["lastAnnounceTime"] for x in t["trackerStats"])]
            if parked:
                try:
                    self.tr.reannounce(parked)
                    log(f"re-announced {len(parked)} parked torrent(s)")
                except Exception:
                    pass
            for j in live:
                t = byhash.get(j.get("hash"))
                if not t:
                    log(f"#{j.get('trId')} vanished: {j['title'][:60]}")
                    j["status"] = "lost"
                    continue
                if j["status"] == "downloading":
                    if t["error"]:
                        log(f"#{t['id']} error: {t['errorString']}")
                    if t["percentDone"] < 1:
                        log(f"#{t['id']} {t['percentDone'] * 100:.1f}% {t['rateDownload'] / 1e6:.1f} MB/s eta {t['eta']}s  {t['name'][:60]}")
                        continue
                    try:
                        log(f"#{t['id']} tracks: {probe.sample_pack(os.path.join(self.cfg.paths.downloads_local, t['name']), self.cfg.languages.wanted)[1]}")
                    except Exception:
                        pass
                    if self.import_job(j, t) is None:
                        j["status"] = "import-failed"
                    self.save(s)
                    continue
                if j["status"] == "importing":
                    if j.get("cmdId"):
                        c = self.sonarr.command(j["cmdId"])
                        if c["status"] not in ("completed", "failed", "aborted"):
                            continue
                        log(f"[{j['series']}] ManualImport {c['status']}")
                        if c["status"] != "completed":
                            j["status"] = "import-failed"
                            self.save(s)
                            continue
                    j["imported"] = j.get("imported", 0) + j.get("toImport", 0)
                    self.finish_job(j, t)
                    self.save(s)
                    continue
                if j["status"] == "cleanup" and time.time() >= j.get("cleanupAt", 0):
                    # forget the torrent only; delete the folder ourselves at idle I/O priority so the daemon never blocks
                    self.tr.remove([t["id"]], delete_data=False)
                    folder = os.path.join(self.cfg.paths.downloads_local, t["name"])
                    if os.path.isdir(folder) and os.path.abspath(folder) != os.path.abspath(self.cfg.paths.downloads_local):
                        subprocess.Popen(["nice", "-n", "19", "rm", "-rf", "--", folder])
                    elif os.path.isfile(folder):
                        os.remove(folder)
                    j.update(status="done", finished=time.strftime("%Y-%m-%dT%H:%M"))
                    log(f"#{t['id']} removed; {j.get('imported', 0)} episodes imported")
                    self.save(s)
            self.start_jobs(s)
        self.save(s)

    # ---- held plans ------------------------------------------------------------------------------------
    def approve(self, index: int, explicit: dict[str, int] | None = None, keep: bool = False) -> dict:
        s = self.load()
        j = s["jobs"][index]
        if j["status"] not in ("needs-map", "leftovers"):
            raise SystemExit(f"job {index} is {j['status']}, not held")
        if explicit:
            j.setdefault("explicit", {}).update(explicit)
        if keep:
            j["keep"] = True
        j["status"] = "downloading"  # re-plan on the next tick
        j.pop("issues", None)
        try:
            self.tr.start([j["trId"]])
        except Exception:
            pass
        self.save(s)
        log(f"released [{j['series']}] {j['title'][:60]}")
        return j
