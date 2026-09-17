"""packarr.webui: rendering and the approve-form parser. No HTTP server, no Sonarr, no download client -
_render_list/_render_job build plain strings from plain dicts, and _handle_approve only needs an object with
an .approve(index, explicit, keep) method to record what it was called with.
"""

from __future__ import annotations

import json

from packarr.webui import _episode_label, _handle_approve, _render_job, _render_list


def test_episode_label_regular_episode():
    e = {"seasonNumber": 3, "episodeNumber": 7, "title": "The Long Way Home"}
    assert _episode_label(e) == "S03E07 - The Long Way Home"


def test_episode_label_special():
    e = {"seasonNumber": 0, "episodeNumber": 1, "title": "OVA"}
    assert _episode_label(e) == "Special - OVA"


def test_render_list_shows_only_held_jobs():
    jobs = [
        {"status": "done", "series": "Ignore Me", "title": "x", "gb": 1.0},
        {"status": "needs-map", "series": "Cowboy Bebop", "title": "[Judas] pack", "gb": 12.4, "issues": ["a", "b"]},
        {"status": "leftovers", "series": "Trigun", "title": "[EMBER] pack", "gb": 4.2, "leftovers": ["x.mkv"]},
    ]
    html = _render_list(jobs).decode()
    assert "Ignore Me" not in html
    assert "Cowboy Bebop" in html and "2 issue(s)" in html
    assert "Trigun" in html and "1 leftover file(s)" in html


def test_render_list_empty_says_so():
    html = _render_list([{"status": "done", "series": "x", "title": "y", "gb": 1.0}]).decode()
    assert "No held jobs" in html


class FakeSonarr:
    def __init__(self, eps):
        self.eps = eps

    def episodes(self, series_id):
        return self.eps.get(series_id, [])


class FakePipeline:
    def __init__(self, eps):
        self.sonarr = FakeSonarr(eps)
        self.approved = None

    def approve(self, index, explicit, keep):
        self.approved = (index, explicit, keep)


def test_render_job_leftovers_lists_files_with_dropdowns():
    eps = {42: [{"id": 111, "seasonNumber": 1, "episodeNumber": 1, "title": "Asteroid Blues"}]}
    pipe = FakePipeline(eps)
    job = {"seriesId": 42, "series": "Cowboy Bebop", "title": "pack", "status": "leftovers",
           "leftovers": ["Movie.mkv", "extras/NCOP.mkv"]}
    html = _render_job(pipe, 0, job).decode()
    assert "Movie.mkv" in html and "extras/NCOP.mkv" in html
    assert "S01E01 - Asteroid Blues" in html
    assert "rel_0" in html and "rel_1" in html


def test_render_job_needs_map_reads_the_saved_plan(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"plan": [
        {"rel": "07a.mkv", "se": [1, 7], "epId": 117, "reason": "SxxEyy", "ok": False, "note": "duplicate target"},
        {"rel": "07b.mkv", "se": [1, 7], "epId": 117, "reason": "SxxEyy", "ok": False, "note": "duplicate target"},
    ]}))
    eps = {42: [{"id": 117, "seasonNumber": 1, "episodeNumber": 7, "title": "Heavy Metal Queen"}]}
    pipe = FakePipeline(eps)
    job = {"seriesId": 42, "series": "Cowboy Bebop", "title": "pack", "status": "needs-map",
           "plan": str(plan_file), "issues": ["two files map to S01E07"]}
    html = _render_job(pipe, 0, job).decode()
    assert "07a.mkv" in html and "07b.mkv" in html
    assert "two files map to S01E07" in html
    assert "S01E07 - Heavy Metal Queen" in html


def test_render_job_missing_plan_file_shows_error_not_crash():
    pipe = FakePipeline({42: []})
    job = {"seriesId": 42, "series": "X", "title": "y", "status": "needs-map", "plan": "/no/such/file.json"}
    html = _render_job(pipe, 0, job).decode()
    assert "read the saved plan" in html


def test_handle_approve_builds_explicit_map_skipping_blanks():
    pipe = FakePipeline({})
    form = {"rel_0": ["01.mkv"], "ep_0": [""], "rel_1": ["07b.mkv"], "ep_1": ["118"], "keep": ["on"]}
    _handle_approve(pipe, 3, form)
    assert pipe.approved == (3, {"07b.mkv": 118}, True)


def test_handle_approve_no_selections_passes_none():
    pipe = FakePipeline({})
    form = {"rel_0": ["01.mkv"], "ep_0": [""]}
    _handle_approve(pipe, 0, form)
    assert pipe.approved == (0, None, False)
