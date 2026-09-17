# Changelog

## 0.8.0 — 2026-09-17

- **`packarr audit [--series <id>]`** - the last item from the original roadmap. For every episode Sonarr has
  a file for, compares the SxxEyy baked into that file's name against what Sonarr currently thinks that episode
  is numbered; a mismatch means TVDB re-cut a season (or merged/split one) after the file was already imported
  and named, and nothing since has renamed it to match. Reports only - never touches a file or calls Sonarr's
  rename. Exits non-zero when it finds anything, so it's cron/CI-friendly (`packarr audit --series 1 || notify`).
  `Pipeline.audit()` is the underlying method; `docs/field-notes.md`'s "TVDB renumbers seasons" entry, which used
  to say "check for it occasionally," now points here.

## 0.7.1 — 2026-09-17

- **`tests/bats/`** - a bats-core suite that exercises the packaged `packarr` CLI end to end: argument
  parsing, config discovery/validation, local-state commands, and `packarr web` over real HTTP on
  loopback. Complements the pytest suite (which tests internal functions in isolation) rather than
  replacing it; needs no live Sonarr or download client, same as pytest. Wired into CI after `pytest -q`.
- Two small bugs this surfaced and fixed while writing it: `packarr plan <bad index>` and
  `packarr approve <bad index>` used to crash with a raw `IndexError` traceback instead of a clean error;
  and any other uncaught exception from a command (a network failure reaching the wrong host, say) did
  the same. Both now print one clean line and exit non-zero, same as every other validation error already
  did - `main()` gained a thin `except Exception` wrapper around dispatch, deliberately excluding
  `SystemExit`/`KeyboardInterrupt` so nothing already handled correctly changes behavior.

## 0.7.0 — 2026-09-17

- **XEM as a second opinion when anime-lists' AniDB→TVDB table disagrees with TVDB's own season sizes.**
  `mapping.Resolver` gains `xem_tvdb()`, backed by [thexem.info](https://thexem.info)'s `map/all`/`map/havemap`
  API (verified against the real endpoint shapes via XEM's own open-source server and third-party clients, not
  guessed) - the same scene-numbering database Sonarr itself uses. `planner.py` consults it only in the narrow
  window where it already detects a stale table (a mapped range's size no longer matches the real TVDB season
  size), before falling back to the existing positional/by-size/default-season guesses; an XEM answer naming a
  nonexistent episode is rejected, same as any other guess. Per-show results are cached to disk for a week, and a
  show is checked against XEM's `havemap` index once before ever querying its per-show table, so the vast
  majority of shows (no scene/AniDB divergence at all) cost nothing beyond that one shared lookup. Any XEM failure
  is caught and logged - it is strictly additive, never blocking a plan that would otherwise have succeeded.

## 0.6.0 — 2026-09-17

- **Selective pulls (`--abs`/`--dirs`) now work with `download_client: sabnzbd`.** New `packarr/nzbfile.py` reads
  a pack's file list straight out of the NZB's own XML - no bencode-style decoder needed, since NZB is just XML;
  the one real trick is that a file's name isn't a first-class field, so it's pulled from the quoted token in the
  `subject` attribute, the same posting convention independently used by SABnzbd's own parser and several
  third-party ones (verified against real-world samples, not guessed). `start_jobs()` now fetches the NZB locally
  only when a pull is selective - a bare NZB URL has no "stalls at 0 peers" problem the way a magnet does, so the
  non-selective (whole-pack) path is untouched and still passes the URL straight to SABnzbd's `addurl`. When
  selective, the already-fetched bytes are reused as the add itself (`addfile`) rather than fetched a second time.
  `Pipeline.add()` no longer refuses `--abs`/`--dirs` for SABnzbd.
- Not yet exercised against a live SABnzbd - please report what you see, especially around the
  add-paused/`get_files`/`delete_nzf`/resume sequence.

## 0.5.0 — 2026-09-17

- **`packarr web`** — a browser UI for held jobs (`needs-map` / `leftovers`), the same workflow as
  `packarr approve --map` without a terminal: a list of held jobs, a detail page per job showing why it's held
  (or its leftover files) with a dropdown of the series' episodes per row, and an Approve button that calls the
  same `Pipeline.approve()` the CLI uses. Reads the plan Pipeline already writes to `<state_dir>/plans/`, so
  opening a job never touches Sonarr's `ManualImport` or the download client - only approving does. Plain stdlib
  `http.server`, server-rendered HTML, no JS required - stays a one-dependency (PyYAML) tool. No authentication,
  same posture as the existing webhook listener; keep `web.listen` off the open internet. New `web:` config
  section (`web.listen`, default `0.0.0.0:7878`) and `packarr-web` compose service.

## 0.4.0 — 2026-09-17

- **SABnzbd support** (`download_client: sabnzbd`), verified against the official API reference. Usenet has no
  swarm - `add_trackers`/`reannounce` are no-ops - and a finished job disappears from the queue and reappears in
  history the instant post-processing completes, so `torrents()` merges queue + history or the job would look
  vanished to the pipeline for a tick. Job ids are SABnzbd's `nzo_id`. Not yet exercised against a live SABnzbd -
  please report what you see, especially around the selective-file trim path (`get_files`/`delete_nzf`), which is
  the least-verified part of this client.
- **Selective pulls (`--abs`/`--dirs`) are refused up front for `download_client: sabnzbd`**, with a clear error,
  rather than silently sitting in `queued` forever: the pipeline only ever learns a pack's file list by parsing raw
  `.torrent` bytes, and an NZB has no equivalent fast local listing yet. Whole-pack SABnzbd pulls are unaffected.
  Tracked as a follow-up.
- Fixed two regressions from the qBittorrent merge: the download-client-unavailable log line had reverted to
  always saying "transmission", and `packarr adopt`/`--torrent` had reverted to numeric-only ids, which would have
  rejected qBittorrent's hash ids.
- `Transmission` also gained `version()`, so `packarr check`'s version reporting is consistent across all three
  clients instead of qBittorrent-only.

## 0.3.0 — 2026-09-17

- **qBittorrent support** (`download_client: qbittorrent`), 4.x and 5.x Web API. Same interface as the Transmission
  client; selective pulls add the torrent stopped, set unwanted files to priority 0, then start it, so unwanted files
  never allocate. Tested live against qBittorrent 5.2.3: selective add (3 of 12 files excluded, 1.6 MB on disk of a
  440 MB torrent), progress/status/peers/tracker mapping, stop/start (5.x `start`/`stop`, 4.x `resume`/`pause`
  fallback), reannounce, remove with data. Requested in #1.
- `packarr check` names the active download client and its version.


## 0.2.1 — 2026-09-16

Tested against a live Bazarr (1.6.1) and made smarter about where the truth lives.

- **File inspection first.** Packarr now ffprobes library files itself (embedded audio + subtitle tracks *and* sidecar
  files) with a path/size/mtime cache; Jellyfin is a fallback, Sonarr tags the last resort. `paths.library_maps` translates
  Sonarr's roots when Packarr sees them elsewhere.
- Jellyfin 12 ignores `AnyProviderIdEquals` and returns every series — the lookup now matches provider ids client-side
  (it was returning the first series in the library for any id).
- Bazarr: `packarr subs --limit N`; `bazarr.anime_profile_id` puts anime series on your en+ja Bazarr profile; a down or
  restarting Bazarr no longer crashes the command.
- Verified live: Packarr-initiated requests run through Bazarr's job queue and provider search (Gestdown), and Packarr
  recognises the `.eng.hi.srt` Bazarr writes back.


## 0.2.0 — 2026-09-16

First feature request, same day: subtitles.

- `languages.subtitles: [eng, jpn]` — an episode only counts as done when its file also carries those subtitle tracks
  (Jellyfin stream data). `languages.subtitles_required` holds packs whose sampled files lack one.
- Search ranks releases advertising multi-subs higher; the post-download probe reports each file's subtitle languages.
- **Bazarr integration**: `packarr subs [--series ID] [--fetch]` finds every file missing a wanted subtitle language and asks
  Bazarr to fetch exactly those episode/language pairs — no re-download. Runs automatically after each pack import when
  `bazarr:` is configured (`fill_after_import`). Written against Bazarr's API docs (`PATCH /api/episodes/subtitles`); not
  yet exercised against a live Bazarr — please report what you see.
- Planner: hold a pack when Sonarr has no episodes for the series yet (freshly added series); stale anime-lists tables now
  prefer the TVDB season whose size matches the entry, then the default season (Hetalia's merged/renumbered seasons).
- Planner: Roman-numeral title tokens; per-file quality probing for packs without a source tag; `2x11` filenames.

## 0.1.0 — 2026-09-16

Initial release.
