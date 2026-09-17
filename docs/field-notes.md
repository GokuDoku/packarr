# 📓 Field notes

Every rule in Packarr came from a pack that broke the previous rule. These are the ones worth knowing before you
run it on your own library — most apply to *any* pack workflow, not just this tool.

## Sonarr

- **`GET /manualimport?folder=…&seriesId=…` ignores `folder`.** With a `seriesId` it returns the series' *existing
  library files* (the "manage episodes" view). The first two pack imports through an early version of this tool
  "succeeded" by importing a library onto itself. Packarr queries by folder only and assigns `seriesId`/`episodeIds`
  itself.
- **One missing file aborts the whole `ManualImport` command.** Never delete from the download folder while a
  command is queued.
- **Sonarr runs `ManualImport` commands one at a time**, behind whatever else is queued. A big usenet batch ahead of
  your pack is not a stall.
- **Sonarr guesses quality per file.** `[Judas] Show - S01E01.mkv` parses as HDTV-1080p, then the import is "not an
  upgrade". The pack title says `[BD 1080p]`; Packarr sets the quality from there.
- **The per-file `languages` field is whatever the importer said.** Packarr stamps the configured languages
  explicitly; a *language-based* custom format (English + Japanese required) is the reliable way to score dual audio,
  a title regex on "dual" is not.
- **A restart restores cancelled commands.** If you ever need to purge Sonarr's command queue, do it in the database
  with Sonarr stopped.
- **TVDB renumbers seasons after you imported.** Symptom: an episode file named `S02E01` mapped to episode `S02E02`,
  or a special whose "file" is a season episode. `packarr audit` finds these (compares the SxxEyy baked into the
  filename against what Sonarr currently thinks that episode is); Packarr's plan also refuses to stack a second
  file on a shared one going forward.
- **Same-title cross-show imports happen.** Netflix's *Monster* (2022) once landed on top of Urasawa's *Monster*
  (2004) episodes 9–10. A 50-minute file in a 24-minute series is the tell — Packarr's runtime check catches it.

## Transmission

- **Magnets can sit at "0 B / 0 peers" forever** behind a VPN with no inbound port; the same torrent added from its
  `.torrent` file connects in seconds. Packarr fetches `.torrent` files.
- **Pre-allocation reserves every *wanted* file in full at add time.** Packarr's disk budget counts
  `sizeWhenDone − haveValid` of every active torrent as already spent, and marks unwanted files inside the
  `torrent-add` call so they never allocate.
- **Keep the incomplete dir on the same mount as the download dir.** Cross-mount means the daemon *copies* each
  finished pack on its main thread — a 99 GB pack froze RPC for 17 minutes ("gateway timeout" in the web UI).
- **`trackerAdd` parks announces.** Re-announce after adding trackers, or the torrent sits idle.
- **RPC gets slow when the disk is saturated.** Packarr's client waits 240 s rather than retrying into the problem.
- **Match torrents by hash, not by id.** A stale id once removed two fully imported packs' torrents.

## Nyaa packs

- `[Optional Dual Audio]` = **subs only**; the dub is a separate torrent.
- **Don't require "dual" in the title.** English-only sets of dub-era shows are exactly what some people want, and a
  title without the word is not a title without the audio. Search by name; rank, don't filter.
- `[pseudo]` on an old show is a source flag, not proof of an upscale. Rank it down, then look at the file.
- **MB per episode beats GB per pack.** 150–900 MB/episode is the sane 1080p HEVC band; a 45 GB "complete" set of a
  26-episode show is 1.7 GB/episode and probably lossless audio you don't need.
- **Groups name cours as seasons.** "Season 03" on AniList/in a release can be TVDB S2 (Bungou Stray Dogs). The
  resolver is authoritative over the filename.
- **`S3_-_07` is season 3.** An underscore is a word character; `\bS3\b` never matched it, and the fallback mapped
  twelve files to season 1. Every file was "already good" there, so nothing imported — and cleanup deleted the pack.
- **`2x11`** is a real naming style (older DVD rips). Parse it.
- **A pack can number its own season 1 with 28 episodes** where TVDB has 27, shifting every later season by one.
  `--map 2:2:1` fixes it; the runtime and duplicate checks are what flag it.
- **Cover art is a video stream** to ffprobe (`mjpeg`/`png`). Exclude it before counting video tracks.
- **NCED/NCOP are Japanese-only by nature.** Probing one as "the sample" once rejected a perfectly good dual pack.

## Jellyfin / language tags

- Some groups write the full language name (`English`) instead of ISO 639-2 (`eng`). Match both when deciding an
  episode "already has" the audio, or you'll re-download perfect files.
- Subtitle extraction and real-time monitoring on a slow (NTFS/FUSE) media drive can starve imports; pause them
  for a mass run, then rescan once.
