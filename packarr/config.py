"""Configuration: one YAML file, every URL/key/path in it, nothing hard-coded.

`${ENV_VAR}` inside any string value is expanded, so secrets can live in the environment.
The config path comes from `--config`, then `$PACKARR_CONFIG`, then `./packarr.yml`, then `/config/packarr.yml`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(v):
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


@dataclass
class Service:
    url: str = ""
    api_key: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key)


@dataclass
class Radarr(Service):
    anime_root: str = ""  # root folder movies from packs are added under
    quality_profile: int = 0  # profile id for those movies


@dataclass
class Prowlarr(Service):
    indexer_ids: list[int] = field(default_factory=list)  # Nyaa (and friends) as configured in Prowlarr


@dataclass
class Bazarr(Service):
    fill_after_import: bool = True  # after a pack imports, ask Bazarr for any wanted subtitle language the files lack
    anime_profile_id: int = 0  # Bazarr language profile to enforce on Sonarr anime series (0 = leave Bazarr's assignment alone)


@dataclass
class Transmission:
    url: str = "http://transmission:9091/transmission/rpc"
    username: str = ""
    password: str = ""
    timeout: int = 240  # RPC can crawl when the disk is saturated; be patient rather than wrong


@dataclass
class QBittorrent:
    url: str = "http://qbittorrent:8080"
    username: str = "admin"
    password: str = ""
    timeout: int = 240


@dataclass
class Sabnzbd(Service):
    category: str = ""  # SABnzbd category to file packarr's downloads under (blank = Default)
    timeout: int = 240


@dataclass
class Paths:
    downloads_local: str = "/downloads"  # the completed-downloads folder as Packarr sees it
    downloads_client: str = "/downloads"  # the same folder as the download client sees it
    downloads_sonarr: str = "/downloads"  # the same folder as Sonarr/Radarr see it
    state_dir: str = "/config"  # jobs, plans, resolver cache, mapping data
    log_file: str = ""  # optional; stdout is always written
    library_maps: dict[str, str] = field(default_factory=dict)  # Sonarr library path prefix -> the same folder as Packarr sees it (empty = identical)


@dataclass
class Limits:
    max_active: int = 6  # torrents transferring at once
    max_active_gb: float = 300  # total GB in flight
    min_free_gb: float = 100  # never let the downloads disk fall below this
    cleanup_delay_s: int = 600  # grace period after import before the folder is deleted


@dataclass
class Languages:
    wanted: str = "eng"  # audio language that marks an episode as "already good"
    tag: list[str] = field(default_factory=lambda: ["English", "Japanese"])  # languages stamped on imported files
    subtitles: list[str] = field(default_factory=list)  # subtitle languages an episode must ALSO carry to count as good, e.g. [eng, jpn]
    subtitles_required: bool = False  # hold a pack whose sampled files lack any wanted subtitle language (otherwise: rank + log)


@dataclass
class Search:
    dual_regex: str = r"dual|eng.?dub|english dub|multi.?audio"
    min_gb: float = 0
    max_gb: float = 1e9
    mb_per_episode: list[int] = field(default_factory=lambda: [150, 900])  # sanity band for a 1080p HEVC anime episode
    prefer_groups: list[str] = field(default_factory=lambda: ["Judas", "EMBER", "Anime Time", "DB", "Cleo", "YakuboEncodes", "bonkai77"])
    avoid_regex: str = r"optional dual|\bpseudo\b|\bupscal|\bAV1\b"  # "[Optional Dual Audio]" packs are subs-only
    min_seeders: int = 2


@dataclass
class Auto:
    enabled: bool = False  # act on Sonarr "On Series Add" webhooks
    dry_run: bool = True  # log what would be grabbed instead of grabbing
    listen: str = "0.0.0.0:7979"
    min_months_ended: int = 2  # a season must have finished this long ago to be pack material
    notify_url: str = ""  # optional: an ntfy / Apprise / plain webhook URL that receives held plans


@dataclass
class Web:
    listen: str = "0.0.0.0:7878"  # `packarr web` - approve/hand-map held jobs from a browser instead of the CLI


@dataclass
class Config:
    sonarr: Service = field(default_factory=Service)
    radarr: Radarr = field(default_factory=Radarr)
    prowlarr: Prowlarr = field(default_factory=Prowlarr)
    jellyfin: Service = field(default_factory=Service)
    bazarr: Bazarr = field(default_factory=Bazarr)
    download_client: str = "transmission"  # "transmission", "qbittorrent" or "sabnzbd"
    transmission: Transmission = field(default_factory=Transmission)
    qbittorrent: QBittorrent = field(default_factory=QBittorrent)
    sabnzbd: Sabnzbd = field(default_factory=Sabnzbd)
    paths: Paths = field(default_factory=Paths)
    limits: Limits = field(default_factory=Limits)
    languages: Languages = field(default_factory=Languages)
    search: Search = field(default_factory=Search)
    auto: Auto = field(default_factory=Auto)
    web: Web = field(default_factory=Web)
    trackers: list[str] = field(default_factory=lambda: [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://exodus.desync.com:6969/announce",
        "http://nyaa.tracker.wf:7777/announce",
    ])
    anidb_cache_dir: str = ""  # optional: Shoko-style Anime_HTTP dump for special-episode titles


def _fill(cls, data: dict):
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        sub = f.default_factory if f.default_factory is not None and is_dataclass(f.default_factory) else None  # type: ignore[arg-type]
        kwargs[f.name] = _fill(sub, v or {}) if sub else v
    return cls(**kwargs)


def find_config(explicit: str | None = None) -> str:
    for p in (explicit, os.environ.get("PACKARR_CONFIG"), "packarr.yml", "/config/packarr.yml"):
        if p and os.path.exists(p):
            return p
    raise SystemExit("no config found: pass --config, set PACKARR_CONFIG, or run `packarr init` to write packarr.yml")


def load(path: str | None = None) -> Config:
    with open(find_config(path), encoding="utf-8") as fh:
        data = _expand(yaml.safe_load(fh) or {})
    cfg = _fill(Config, data)
    if not cfg.sonarr.enabled:
        raise SystemExit("config: sonarr.url and sonarr.api_key are required")
    if cfg.download_client not in ("transmission", "qbittorrent", "sabnzbd"):
        raise SystemExit(f"config: download_client must be transmission, qbittorrent or sabnzbd, not {cfg.download_client!r}")
    os.makedirs(cfg.paths.state_dir, exist_ok=True)
    return cfg


EXAMPLE = """\
# Packarr configuration. Every value can reference an environment variable as ${NAME}.
sonarr:
  url: http://sonarr:8989
  api_key: ${SONARR_API_KEY}

radarr:                       # optional - movies found inside packs are added here
  url: http://radarr:7878
  api_key: ${RADARR_API_KEY}
  anime_root: /movies/anime   # Radarr root folder for those movies
  quality_profile: 1

prowlarr:                     # used by `packarr search` and the auto grabber
  url: http://prowlarr:9696
  api_key: ${PROWLARR_API_KEY}
  indexer_ids: [1]            # Prowlarr indexer ids to search (Nyaa.si, AniDex, ...)

jellyfin:                     # optional - lets Packarr skip episodes that already have the wanted audio
  url: http://jellyfin:8096
  api_key: ${JELLYFIN_API_KEY}

bazarr:                       # optional - fetch missing subtitle languages for files you already have (no re-download)
  url: http://bazarr:6767
  api_key: ${BAZARR_API_KEY}
  fill_after_import: true
  anime_profile_id: 0         # a Bazarr language profile (e.g. English + Japanese) to enforce on Sonarr's anime series

download_client: transmission   # or qbittorrent, or sabnzbd - only the matching block below is used

transmission:
  url: http://transmission:9091/transmission/rpc
  username: ""
  password: ""

qbittorrent:
  url: http://qbittorrent:8080
  username: admin
  password: ${QBITTORRENT_PASSWORD}

sabnzbd:                        # --abs/--dirs supported: paused, trimmed via get_files/delete_nzf, then resumed
  url: http://sabnzbd:8080
  api_key: ${SABNZBD_API_KEY}
  category: ""                  # optional SABnzbd category to file packarr's downloads under

paths:
  downloads_local: /downloads   # completed-downloads folder as Packarr sees it (mount it into the container)
  downloads_client: /downloads  # ...as Transmission sees it
  downloads_sonarr: /downloads  # ...as Sonarr/Radarr see it
  state_dir: /config
  log_file: /config/packarr.log
  library_maps: {}              # Sonarr library root -> where Packarr sees it, e.g. {"/tv": "/media/tv"}; empty = same paths.
                                # Mount your library read-only into Packarr and it will ffprobe files itself (audio + subtitle tracks).

limits:
  max_active: 6
  max_active_gb: 300
  min_free_gb: 100

languages:
  wanted: eng                  # episodes whose file already has this audio are left alone (unless --all)
  tag: [English, Japanese]     # stamped on every imported file so Sonarr's language custom formats fire
  subtitles: []                # e.g. [eng, jpn]: an episode only counts as "good" if its file also has these subtitle tracks
  subtitles_required: false    # true = hold a pack whose files lack a wanted subtitle language instead of importing it

search:
  min_seeders: 2
  prefer_groups: [Judas, EMBER, "Anime Time", DB, Cleo, YakuboEncodes, bonkai77]

auto:
  enabled: false               # flip on to grab complete packs when Sonarr adds an anime (webhook)
  dry_run: true
  listen: 0.0.0.0:7979
  min_months_ended: 2
  notify_url: ""               # ntfy/Apprise/webhook URL that receives held plans

web:
  listen: 0.0.0.0:7878         # `packarr web` - approve/hand-map held jobs from a browser

# anidb_cache_dir: /config/anidb-http   # optional Shoko-style AniDB dump for special-episode titles
"""
