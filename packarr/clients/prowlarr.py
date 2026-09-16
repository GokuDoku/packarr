"""Prowlarr v1 search across the configured indexers (Nyaa.si is the one that matters for packs)."""

from __future__ import annotations

from . import Arr


class Prowlarr(Arr):
    def __init__(self, url: str, api_key: str, indexer_ids: list[int]):
        super().__init__(url, api_key, prefix="/api/v1", timeout=120)
        self.indexer_ids = indexer_ids

    def search(self, query: str, limit: int = 100) -> list[dict]:
        params = {"query": query, "type": "search", "limit": limit}
        rows = []
        for iid in self.indexer_ids or [None]:
            if iid is not None:
                params["indexerIds"] = iid
            code, res = self.call("/search", params=params)
            if code == 200 and isinstance(res, list):
                rows += res
        # normalise the handful of fields Packarr uses
        out = []
        for x in rows:
            out.append({"title": x.get("title", ""), "size": x.get("size", 0), "seeders": x.get("seeders") or 0,
                        "guid": x.get("guid") or x.get("downloadUrl") or "", "info": x.get("infoUrl", ""),
                        "download": x.get("downloadUrl", ""), "indexer": x.get("indexer", "")})
        return out
