"""Resolver's XEM support: the havemap-gate-before-map/all pattern, per-show disk caching, and graceful
failure. No real network - _xem_get (the one HTTP call site) is monkeypatched throughout."""

from __future__ import annotations

import json
import os

from packarr.mapping import Resolver


def _resolver(tmp_path):
    return Resolver(str(tmp_path))


def test_xem_tvdb_returns_mapping_when_havemap_and_table_agree(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    calls = []

    def fake_get(path, **params):
        calls.append((path, params))
        if path == "havemap":
            return {"result": "success", "data": [77086, 12345]}
        if path == "all":
            return {"result": "success", "data": [
                {"anidb": {"season": 1, "episode": 30}, "tvdb": {"season": 2, "episode": 4}},
                {"anidb": {"season": 1, "episode": 31}, "tvdb": {"season": 2, "episode": 5}},
            ]}
        raise AssertionError(path)

    monkeypatch.setattr(r, "_xem_get", fake_get)
    assert r.xem_tvdb(77086, 30) == (2, 4)
    assert r.xem_tvdb(77086, 31) == (2, 5)
    assert r.xem_tvdb(77086, 999) is None  # in the table, but not this episode


def test_xem_tvdb_never_calls_map_all_when_show_not_in_havemap(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    calls = []

    def fake_get(path, **params):
        calls.append(path)
        if path == "havemap":
            return {"result": "success", "data": [12345]}  # not our show
        raise AssertionError("map/all should never be called for a show absent from havemap")

    monkeypatch.setattr(r, "_xem_get", fake_get)
    assert r.xem_tvdb(77086, 30) is None
    assert calls == ["havemap"]


def test_havemap_is_fetched_once_and_reused_across_shows(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    havemap_calls = []

    def fake_get(path, **params):
        if path == "havemap":
            havemap_calls.append(1)
            return {"result": "success", "data": [77086, 12345]}
        return {"result": "success", "data": []}

    monkeypatch.setattr(r, "_xem_get", fake_get)
    r.xem_tvdb(77086, 1)
    r.xem_tvdb(12345, 1)
    r.xem_tvdb(77086, 2)
    assert len(havemap_calls) == 1


def test_per_show_table_is_cached_to_disk_and_not_refetched(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    all_calls = []

    def fake_get(path, **params):
        if path == "havemap":
            return {"result": "success", "data": [77086]}
        all_calls.append(params)
        return {"result": "success", "data": [{"anidb": {"season": 1, "episode": 30}, "tvdb": {"season": 2, "episode": 4}}]}

    monkeypatch.setattr(r, "_xem_get", fake_get)
    assert r.xem_tvdb(77086, 30) == (2, 4)
    assert r.xem_tvdb(77086, 30) == (2, 4)  # same Resolver instance: in-memory cache
    assert len(all_calls) == 1

    r2 = _resolver(tmp_path)  # a fresh instance: must read the on-disk cache, not refetch
    monkeypatch.setattr(r2, "_xem_get", fake_get)
    assert r2.xem_tvdb(77086, 30) == (2, 4)
    assert len(all_calls) == 1


def test_map_all_failure_is_swallowed_and_cached_as_empty(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    all_calls = []

    def fake_get(path, **params):
        if path == "havemap":
            return {"result": "success", "data": [77086]}
        all_calls.append(1)
        raise RuntimeError("thexem.info is down")

    monkeypatch.setattr(r, "_xem_get", fake_get)
    assert r.xem_tvdb(77086, 30) is None
    assert r.xem_tvdb(77086, 30) is None  # cached as empty, not retried immediately
    assert len(all_calls) == 1


def test_havemap_failure_returns_false_without_raising(tmp_path, monkeypatch):
    r = _resolver(tmp_path)
    monkeypatch.setattr(r, "_xem_get", lambda path, **p: (_ for _ in ()).throw(RuntimeError("down")))
    assert r.xem_tvdb(77086, 30) is None


def test_rows_missing_anidb_or_tvdb_keys_are_skipped_not_crashed(tmp_path, monkeypatch):
    r = _resolver(tmp_path)

    def fake_get(path, **params):
        if path == "havemap":
            return {"result": "success", "data": [77086]}
        return {"result": "success", "data": [
            {"scene": {"season": 1, "episode": 30}},  # no anidb/tvdb at all
            {"anidb": {"season": 1, "episode": 5}},  # no tvdb
            {"anidb": {"season": 1, "episode": 6}, "tvdb": {"season": 2, "episode": 1}},
        ]}

    monkeypatch.setattr(r, "_xem_get", fake_get)
    assert r.xem_tvdb(77086, 5) is None
    assert r.xem_tvdb(77086, 6) == (2, 1)


def test_cache_file_is_valid_json_on_disk(tmp_path, monkeypatch):
    r = _resolver(tmp_path)

    def fake_get(path, **params):
        if path == "havemap":
            return {"result": "success", "data": [77086]}
        return {"result": "success", "data": [{"anidb": {"season": 1, "episode": 30}, "tvdb": {"season": 2, "episode": 4}}]}

    monkeypatch.setattr(r, "_xem_get", fake_get)
    r.xem_tvdb(77086, 30)
    cache_path = os.path.join(str(tmp_path), "anime-lists", "xem-cache.json")
    assert os.path.exists(cache_path)
    with open(cache_path) as fh:
        data = json.load(fh)
    assert data["77086"]["table"] == [[[1, 30], [2, 4]]]
