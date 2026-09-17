"""Browser UI for held jobs (`needs-map` / `leftovers`): the same approve/hand-map workflow as
`packarr approve --map`, without a terminal.

Plain stdlib http.server, server-rendered HTML, no JS required - one dependency (PyYAML) stays one
dependency. Reads the plan Pipeline already writes to <state_dir>/plans/ (see pipeline.py's docstring:
plans exist precisely so a held job can be inspected without re-downloading or re-planning anything), so
opening a job here never touches the download client or Sonarr's ManualImport - only approving does.

No authentication: same posture as `packarr serve`'s webhook listener. Don't expose this past a trusted
network without your own reverse-proxy auth in front of it.
"""

from __future__ import annotations

import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import Config
from .log import log
from .pipeline import Pipeline

HELD = ("needs-map", "leftovers")

_STYLE = """
body { background:#0b0d10; color:#d8dee4; font:15px/1.5 -apple-system,Segoe UI,sans-serif; margin:0; padding:24px; }
a { color:#7ab7ff; }
h1 { font-size:20px; margin:0 0 16px; }
h1 a { text-decoration:none; color:inherit; }
table { border-collapse:collapse; width:100%; margin-bottom:24px; }
th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #232830; vertical-align:top; }
th { color:#8b96a3; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
tr:hover td { background:#12151a; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; }
.badge.needs-map { background:#4a2b1b; color:#ffb37a; }
.badge.leftovers { background:#1b3a4a; color:#7fd0ff; }
.mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; }
.issues { background:#241417; border:1px solid #4a2b2b; border-radius:6px; padding:10px 14px; margin-bottom:20px; }
.issues li { margin:2px 0; }
select, button, input[type=text] { background:#161a20; color:#d8dee4; border:1px solid #2c333c; border-radius:5px; padding:5px 8px; font-size:13px; }
button { background:#2c5a3a; border-color:#3a7a4d; cursor:pointer; padding:8px 20px; font-size:14px; }
button:hover { background:#347046; }
.ok { color:#6fbf73; }
.bad { color:#e0855a; }
.muted { color:#6b7683; }
.row-actions { margin-top:20px; }
.back { display:inline-block; margin-bottom:16px; }
"""


def _page(title: str, body: str) -> bytes:
    return f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)}</title>" \
           f"<style>{_STYLE}</style></head><body>{body}</body></html>".encode()


def _episode_label(e: dict) -> str:
    se = f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}" if e.get("seasonNumber", 0) > 0 else "Special"
    return f"{se} - {e.get('title', '')}"[:70]


def _render_list(jobs: list[dict]) -> bytes:
    rows = []
    for i, j in enumerate(jobs):
        if j["status"] not in HELD:
            continue
        what = f"{len(j.get('leftovers') or [])} leftover file(s)" if j["status"] == "leftovers" \
            else f"{len(j.get('issues') or [])} issue(s)"
        rows.append(
            f"<tr><td><a href='/job/{i}'>{i}</a></td>"
            f"<td><span class='badge {j['status']}'>{j['status']}</span></td>"
            f"<td>{html.escape(j['series'])}</td>"
            f"<td class='mono'>{html.escape(j['title'][:80])}</td>"
            f"<td>{j['gb']:.1f} GB</td><td>{html.escape(what)}</td></tr>"
        )
    table = ("<table><tr><th>#</th><th>Status</th><th>Series</th><th>Title</th><th>Size</th><th></th></tr>"
              + "".join(rows) + "</table>") if rows else "<p class='muted'>No held jobs right now.</p>"
    return _page("packarr - held jobs", f"<h1>\U0001F5FA\uFE0F Held jobs</h1>{table}")


def _render_job(pipe: Pipeline, index: int, job: dict, error: str = "") -> bytes:
    series_id = job["seriesId"]
    try:
        eps = pipe.sonarr.episodes(series_id)
    except Exception as e:
        eps = []
        error = error or f"couldn't load episodes from Sonarr: {e}"
    eps_by_season = sorted(eps, key=lambda e: (e.get("seasonNumber", 0), e.get("episodeNumber", 0)))
    options_html = "".join(f"<option value='{e['id']}'>{html.escape(_episode_label(e))}</option>" for e in eps_by_season)

    rows_html = []
    if job["status"] == "leftovers":
        for i, rel in enumerate(job.get("leftovers") or []):
            rows_html.append(f"<tr><td class='mono'>{html.escape(rel)}</td><td class='muted'>unresolved leftover</td>"
                              f"<td><input type=hidden name='rel_{i}' value='{html.escape(rel, quote=True)}'>"
                              f"<select name='ep_{i}'><option value=''>-- leave unmapped --</option>{options_html}</select></td></tr>")
    else:
        plan = []
        try:
            with open(job["plan"], encoding="utf-8") as fh:
                plan = json.load(fh).get("plan", [])
        except Exception as e:
            error = error or f"couldn't read the saved plan ({job.get('plan', '?')}): {e}"
        for i, r in enumerate(plan):
            se = f"S{r['se'][0]:02d}E{r['se'][1]:02d}" if r.get("se") else "--"
            cls = "ok" if r.get("ok") else "bad"
            note = html.escape(r.get("reason", "")) + (f" \u2014 {html.escape(r['note'])}" if r.get("note") else "")
            rows_html.append(
                f"<tr><td class='mono'>{html.escape(r['rel'])}</td>"
                f"<td class='{cls}'>{se}</td><td class='muted'>{note}</td>"
                f"<td><input type=hidden name='rel_{i}' value='{html.escape(r['rel'], quote=True)}'>"
                f"<select name='ep_{i}'><option value=''>-- leave as is --</option>{options_html}</select></td></tr>"
            )
        # allow adding a row for a file the plan never saw (e.g. one that arrived after the last plan)
    issues = job.get("issues") or []
    issues_html = ("<div class='issues'><b>Why this is held:</b><ul>" +
                    "".join(f"<li>{html.escape(x)}</li>" for x in issues) + "</ul></div>") if issues else ""
    error_html = f"<div class='issues'>{html.escape(error)}</div>" if error else ""
    table = ("<table><tr><th>File</th><th>Currently</th><th>Reason / note</th><th>Map to</th></tr>"
              + "".join(rows_html) + "</table>") if rows_html else "<p class='muted'>Nothing to map.</p>"

    return _page(f"packarr - job {index}", f"""
<a class='back' href='/'>&larr; all held jobs</a>
<h1>{html.escape(job['series'])}</h1>
<p class='mono muted'>{html.escape(job['title'])}</p>
{error_html}
{issues_html}
<form method=post action='/job/{index}/approve'>
{table}
<div class='row-actions'>
<label><input type=checkbox name=keep> keep the torrent after approving (don't route leftovers away)</label><br><br>
<button type=submit>Approve &amp; release</button>
</div>
</form>
""")


def _handle_approve(pipe: Pipeline, index: int, form: dict[str, list[str]]) -> None:
    explicit = {}
    i = 0
    while f"rel_{i}" in form:
        rel = form[f"rel_{i}"][0]
        ep = (form.get(f"ep_{i}") or [""])[0]
        if ep:
            explicit[rel] = int(ep)
        i += 1
    pipe.approve(index, explicit or None, keep="keep" in form)


def serve(cfg: Config) -> None:
    pipe = Pipeline(cfg)
    host, port = cfg.web.listen.rsplit(":", 1)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet; packarr's own log() covers what matters
            pass

        def _send(self, body: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                self._send(_render_list(pipe.load()["jobs"]))
                return
            if path.startswith("/job/"):
                try:
                    index = int(path.split("/")[2])
                    job = pipe.load()["jobs"][index]
                except (ValueError, IndexError):
                    self._send(_page("packarr - not found", "<p>No such job. <a href='/'>Back</a></p>"), 404)
                    return
                if job["status"] not in HELD:
                    self._send(_page("packarr", f"<p>Job {index} is <b>{job['status']}</b>, not held. "
                                                  f"<a href='/'>Back</a></p>"))
                    return
                self._send(_render_job(pipe, index, job))
                return
            self._send(_page("packarr - not found", "<p>Not found. <a href='/'>Back</a></p>"), 404)

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith("/job/") or not path.endswith("/approve"):
                self._send(_page("packarr - not found", "Not found"), 404)
                return
            try:
                index = int(path.split("/")[2])
            except (ValueError, IndexError):
                self._send(_page("packarr - not found", "Not found"), 404)
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode()
            form = urllib.parse.parse_qs(body)
            try:
                job = pipe.load()["jobs"][index]
                _handle_approve(pipe, index, form)
                log(f"web: approved [{job['series']}] {job['title'][:60]}")
            except SystemExit as e:
                job = pipe.load()["jobs"][index] if index < len(pipe.load()["jobs"]) else {}
                self._send(_render_job(pipe, index, job, error=str(e)) if job else
                           _page("packarr - error", f"<p>{html.escape(str(e))}</p>"))
                return
            except Exception as e:
                self._send(_page("packarr - error", f"<p>approve failed: {html.escape(str(e))}</p><a href='/'>Back</a>"))
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    log(f"web UI listening on {cfg.web.listen}")
    HTTPServer((host, int(port)), H).serve_forever()
