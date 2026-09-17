# Changelog

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
