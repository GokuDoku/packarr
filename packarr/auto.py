"""Request-driven mode: Sonarr adds an anime -> Packarr decides whether a complete pack is the right move.

Gate (all must hold, or Packarr stays out of Sonarr's way):
  * the series is anime (Sonarr seriesType == "anime")
  * it is not currently airing, or every season but the airing one finished > `min_months_ended` ago
  * a pack candidate exists on the configured indexers with a positive score
When it grabs, it cancels Sonarr's queued searches for that series so usenet singles don't race the pack;
when the pack is done, `packarr run` hands the remainder (an airing season, specials) back to Sonarr's search.

Wire it up as a Sonarr Connect -> Webhook -> "On Series Add" pointing at http://packarr:7979/webhook/sonarr.
`auto.dry_run: true` (the default) logs the decision instead of acting on it.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import Config
from .log import log
from .pipeline import Pipeline
from .search import save_last, search


def pack_worthy(series: dict, episodes: list[dict], min_months_ended: int) -> tuple[bool, str]:
    if series.get("seriesType") != "anime":
        return False, "not anime"
    if series.get("status") == "ended":
        return True, "series ended"
    cutoff = time.time() - min_months_ended * 30 * 86400
    aired = [e for e in episodes if e.get("seasonNumber", 0) > 0 and e.get("airDateUtc")]
    if not aired:
        return False, "no aired episodes"
    seasons: dict[int, float] = {}
    for e in aired:
        ts = time.mktime(time.strptime(e["airDateUtc"][:10], "%Y-%m-%d"))
        seasons[e["seasonNumber"]] = max(seasons.get(e["seasonNumber"], 0), ts)
    latest = max(seasons)
    old = [sn for sn, ts in seasons.items() if sn != latest and ts < cutoff]
    if old:
        return True, f"seasons {sorted(old)} finished > {min_months_ended} months ago (S{latest} left to Sonarr)"
    return False, "only the current season exists"


def handle_series_add(pipe: Pipeline, cfg: Config, series_id: int) -> str:
    series = pipe.sonarr.series_one(series_id)
    eps = pipe.sonarr.episodes(series_id)
    ok, why = pack_worthy(series, eps, cfg.auto.min_months_ended)
    if not ok:
        return f"[{series['title']}] skipped: {why}"
    if not cfg.prowlarr.enabled:
        return f"[{series['title']}] pack-worthy ({why}) but prowlarr is not configured"
    from .clients.prowlarr import Prowlarr
    n_eps = sum(1 for e in eps if e["seasonNumber"] > 0)
    rows = search(Prowlarr(cfg.prowlarr.url, cfg.prowlarr.api_key, cfg.prowlarr.indexer_ids), series["title"], cfg.search, episodes=n_eps)
    save_last(rows, cfg.paths.state_dir)
    rows = [r for r in rows if r["score"] > 0]
    if not rows:
        return f"[{series['title']}] pack-worthy ({why}) but no acceptable pack found - left to Sonarr"
    best = rows[0]
    if cfg.auto.dry_run:
        return f"[{series['title']}] DRY RUN would grab: {best['title'][:80]} ({best['gb']} GB, score {best['score']}, {', '.join(best['why'])})"
    n = pipe.sonarr.cancel_searches(series_id)
    pipe.add(best, series_id)
    return f"[{series['title']}] grabbed {best['title'][:80]} ({best['gb']} GB); cancelled {n} Sonarr search(es)"


def notify(cfg: Config, text: str) -> None:
    if not cfg.auto.notify_url:
        return
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(cfg.auto.notify_url, data=text.encode(), headers={"Content-Type": "text/plain"}), timeout=15)
    except Exception as e:
        log(f"notify failed: {e}")


def serve(cfg: Config) -> None:
    pipe = Pipeline(cfg)
    host, port = cfg.auto.listen.rsplit(":", 1)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"packarr ok\n")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            try:
                ev = json.loads(body or b"{}")
            except Exception:
                ev = {}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            if self.path.startswith("/webhook/sonarr") and ev.get("eventType") == "SeriesAdd":
                sid = (ev.get("series") or {}).get("id")
                if sid:
                    try:
                        msg = handle_series_add(pipe, cfg, int(sid))
                    except Exception as e:
                        msg = f"series {sid}: auto failed: {e}"
                    log("auto: " + msg)
                    notify(cfg, "packarr: " + msg)

    log(f"webhook listening on {cfg.auto.listen} (auto.enabled={cfg.auto.enabled}, dry_run={cfg.auto.dry_run})")
    HTTPServer((host, int(port)), H).serve_forever()
