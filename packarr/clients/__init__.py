"""Thin HTTP clients. Every call is a plain urllib request so the whole tool has one dependency (PyYAML)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class Arr:
    """Shared Sonarr/Radarr/Prowlarr v3/v1 API plumbing: returns (status, json-or-text)."""

    def __init__(self, url: str, api_key: str, prefix: str = "/api/v3", timeout: int = 600):
        self.base = url.rstrip("/") + prefix
        self.key = api_key
        self.timeout = timeout

    def call(self, path: str, method: str = "GET", body=None, params: dict | None = None):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Api-Key": self.key, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as x:
                raw = x.read()
                return x.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")[:400]

    def get(self, path: str, **params):
        code, data = self.call(path, params=params or None)
        if code != 200:
            raise RuntimeError(f"GET {path} -> {code}: {data}")
        return data
